"""Native mpv Sports Center overlay: interactive sports scoreboard & game viewer on TV.

Draws the interactive Retro Sports scoreboard, live action fields, and game details
directly onto the TV screen as an mpv ASS OSD overlay. Controlled seamlessly by the
web remote or physical remote control sending D-pad and navigation commands.
"""
import json
import logging
import os
import socket
import threading
import time
from datetime import datetime

import config
import scheduler
import sports

log = logging.getLogger("retro-tv.tvsports")

_lock = threading.RLock()
_visible = False
_socket = None
_reader = None

SPORTS_OVERLAY_ID = 42  # Shares the full-screen menu overlay slot with tvvod/tvguide

_mode = "browse"        # "browse" or "game"
_categories = []
_cat_idx = 0
_item_idx = 0

_game_data = None       # Detailed game dictionary when in "game" mode
_game_detail = None
_search_query = ""


def _send(command):
    global _socket, _reader
    try:
        if _socket is None:
            _socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            _socket.settimeout(2)
            _socket.connect(config.MPV_SOCKET)
            _reader = _socket.makefile("r")
        _socket.sendall((json.dumps({"command": command, "request_id": 48}) + "\n").encode())
        for line in _reader:
            response = json.loads(line)
            if response.get("request_id") == 48:
                return response
    except (OSError, ValueError):
        pass
    if _reader:
        try:
            _reader.close()
        except Exception:
            pass
    if _socket:
        try:
            _socket.close()
        except Exception:
            pass
    _socket = _reader = None
    return {"error": "Player unavailable"}


def _text(value):
    return str(value or "").replace("\\", " ").replace("{", "(").replace("}", ")").replace("\n", " ")


def _color(rgb):
    """Convert RRGGBB to ASS BBGGRR."""
    clean = str(rgb or "EFF2E9").lstrip("#")
    if len(clean) != 6:
        clean = "EFF2E9"
    return clean[4:6] + clean[2:4] + clean[0:2]


def is_visible():
    return _visible


def get_status():
    with _lock:
        if not _visible:
            return {"visible": False}
        if _mode == "game" and _game_data:
            home = _game_data.get("homeTeam", {})
            away = _game_data.get("awayTeam", {})
            status = _game_data.get("status", {})
            return {
                "visible": True,
                "mode": "game",
                "game_id": _game_data.get("id"),
                "league": _game_data.get("league"),
                "sport": _game_data.get("sport"),
                "title": f"{away.get('abbreviation') or 'AWAY'} @ {home.get('abbreviation') or 'HOME'}",
                "subtitle": f"{status.get('detail') or 'Live'} · {away.get('score', 0)} - {home.get('score', 0)}",
                "home_team": home.get("displayName"),
                "away_team": away.get("displayName"),
                "is_live": status.get("isLive", False),
            }

        # browse mode
        cat = _categories[_cat_idx] if _cat_idx < len(_categories) else None
        items = cat.get("items", []) if cat else []
        item = items[_item_idx] if _item_idx < len(items) else None
        title = "Retro Sports"
        subtitle = "Select a game"
        if item:
            home = item.get("homeTeam", {})
            away = item.get("awayTeam", {})
            status = item.get("status", {})
            title = f"{away.get('abbreviation', 'AWAY')} @ {home.get('abbreviation', 'HOME')}"
            subtitle = f"{status.get('detail') or 'Today'} · {away.get('score', 0)} - {home.get('score', 0)}"

        return {
            "visible": True,
            "mode": "browse",
            "category": cat.get("title", "") if cat else "Sports",
            "title": title,
            "subtitle": subtitle,
            "item_idx": _item_idx,
            "total_items": len(items),
        }


def _build_categories(games, query=""):
    if query:
        q = query.lower()
        matches = [
            g for g in games 
            if q in (g.get("name") or "").lower() 
            or q in (g.get("league") or "").lower() 
            or q in (g.get("sport") or "").lower()
            or q in (g.get("homeTeam", {}).get("displayName") or "").lower()
            or q in (g.get("awayTeam", {}).get("displayName") or "").lower()
        ]
        return [{
            "id": "search",
            "title": f"SEARCH: '{query}'",
            "badge": f"{len(matches)} MATCHES",
            "items": matches
        }]

    cats = []

    live_games = [g for g in games if g.get("status", {}).get("isLive")]
    if live_games:
        cats.append({
            "id": "live",
            "title": "LIVE IN PROGRESS",
            "badge": f"{len(live_games)} LIVE",
            "items": live_games
        })

    football = [g for g in games if g.get("sport") == "football"]
    if football:
        cats.append({
            "id": "football",
            "title": "FOOTBALL (NFL & NCAA)",
            "badge": f"{len(football)} GAMES",
            "items": football
        })

    baseball = [g for g in games if g.get("sport") == "baseball"]
    if baseball:
        cats.append({
            "id": "baseball",
            "title": "BASEBALL (MLB)",
            "badge": f"{len(baseball)} GAMES",
            "items": baseball
        })

    basketball = [g for g in games if g.get("sport") == "basketball"]
    if basketball:
        cats.append({
            "id": "basketball",
            "title": "BASKETBALL (NBA & NCAA)",
            "badge": f"{len(basketball)} GAMES",
            "items": basketball
        })

    soccer = [g for g in games if g.get("sport") == "soccer"]
    if soccer:
        cats.append({
            "id": "soccer",
            "title": "SOCCER (EPL, MLS & UEFA)",
            "badge": f"{len(soccer)} MATCHES",
            "items": soccer
        })

    hockey = [g for g in games if g.get("sport") == "hockey"]
    if hockey:
        cats.append({
            "id": "hockey",
            "title": "HOCKEY (NHL)",
            "badge": f"{len(hockey)} GAMES",
            "items": hockey
        })

    upcoming = [g for g in games if g.get("status", {}).get("isScheduled")]
    if upcoming:
        cats.append({
            "id": "upcoming",
            "title": "UPCOMING MATCHUPS",
            "badge": f"{len(upcoming)} SCHEDULED",
            "items": upcoming
        })

    finals = [g for g in games if g.get("status", {}).get("isFinal")]
    if finals:
        cats.append({
            "id": "final",
            "title": "FINAL SCORES",
            "badge": f"{len(finals)} COMPLETED",
            "items": finals
        })

    if not cats:
        cats.append({
            "id": "all",
            "title": "ALL SPORTS",
            "badge": f"{len(games)} GAMES",
            "items": games
        })
    return cats


def open_sports():
    import playback
    import tvguide
    import tvvod

    global _visible, _mode, _categories, _cat_idx, _item_idx, _game_data, _game_detail, _search_query
    with _lock:
        try:
            tvguide.close()
        except Exception:
            pass
        try:
            tvvod.close_vod()
        except Exception:
            pass

        if not playback.mpv_alive():
            playback.restore_last()

        scores_resp = sports.get_all_scores()
        games = scores_resp.get("games", [])
        _categories = _build_categories(games)
        _cat_idx = 0
        _item_idx = 0
        _mode = "browse"
        _game_data = None
        _game_detail = None
        _search_query = ""

        ok = render()
        _visible = ok
        st = get_status()
        st["ok"] = ok
        return st


def close_sports():
    global _visible, _mode, _game_data, _game_detail
    with _lock:
        _visible = False
        _mode = "browse"
        _game_data = None
        _game_detail = None
        res = _send(["osd-overlay", SPORTS_OVERLAY_ID, "none", ""])
        return res.get("error") == "success"


def nav(action, query=None):
    global _cat_idx, _item_idx, _mode, _game_data, _game_detail, _search_query, _categories
    with _lock:
        if not _visible:
            return open_sports()

        action = (action or "").lower()

        if action == "search":
            _search_query = (query or "").strip()
            scores_resp = sports.get_all_scores()
            games = scores_resp.get("games", [])
            _categories = _build_categories(games, query=_search_query)
            _cat_idx = 0
            _item_idx = 0
            _mode = "browse"
            render()
            return get_status()

        if _mode == "browse":
            cat = _categories[_cat_idx] if _cat_idx < len(_categories) else None
            items = cat.get("items", []) if cat else []

            if action == "up":
                if _cat_idx > 0:
                    _cat_idx -= 1
                    next_cat = _categories[_cat_idx]
                    next_items = next_cat.get("items", [])
                    _item_idx = max(0, min(_item_idx, len(next_items) - 1))
            elif action == "down":
                if _cat_idx < len(_categories) - 1:
                    _cat_idx += 1
                    next_cat = _categories[_cat_idx]
                    next_items = next_cat.get("items", [])
                    _item_idx = max(0, min(_item_idx, len(next_items) - 1))
            elif action == "left":
                if _item_idx > 0:
                    _item_idx -= 1
            elif action == "right":
                if _item_idx < len(items) - 1:
                    _item_idx += 1
            elif action in ("select", "ok"):
                item = items[_item_idx] if _item_idx < len(items) else None
                if item:
                    _game_data = item
                    _game_detail = sports.get_game_detail(item.get("league"), item.get("id"), sport=item.get("sport"))
                    _mode = "game"
            elif action in ("back", "last"):
                close_sports()
                return {"ok": True, "closed": True}

        elif _mode == "game":
            if action in ("select", "ok"):
                # Synthesize & announce the current play-by-play commentary over HDMI via Kokoro TTS
                if _game_data:
                    narration = sports.script_for_game(_game_data, detail=_game_detail)
                    try:
                        audio_bytes, ctype = sports.synthesize_speech(narration, voice="am_michael")
                        out_dir = os.path.join(config.DATA_DIR, "sports_audio")
                        os.makedirs(out_dir, exist_ok=True)
                        wav_path = os.path.join(out_dir, "announcer.wav")
                        with open(wav_path, "wb") as f:
                            f.write(audio_bytes)
                        # Tell mpv to play the synthesized narration audio
                        _send(["loadfile", wav_path, "append-play"])
                        return {**get_status(), "announced": True, "narration": narration}
                    except Exception as e:
                        log.warning("Announcer playback failed: %s", e)
            elif action in ("back", "last"):
                _mode = "browse"
                _game_data = None
                _game_detail = None

        render()
        return get_status()


def render():
    global _visible
    with _lock:
        ass = []

        def box(x, y, w, h, color):
            ass.append(f"{{\\an7\\pos({x},{y})\\bord0\\shad0\\1c&H{_color(color)}&\\p1}}m 0 0 l {w} 0 {w} {h} 0 {h}{{\\p0}}")

        def text(x, y, value, size=24, color="EFF2E9", clip=None, bold=False):
            clipping = f"\\clip({clip[0]},{clip[1]},{clip[2]},{clip[3]})" if clip else ""
            bld = "\\b1" if bold else "\\b0"
            ass.append(f"{{\\an7\\pos({x},{y})\\fnDejaVu Sans\\fs{size}{bld}\\bord0\\shad0\\1c&H{_color(color)}&{clipping}}}{_text(value)}")

        def clock_str():
            return datetime.now(scheduler.TZ).strftime("%-I:%M %p")

        # 1. Full Screen Backdrop (Sports Dark Charcoal)
        box(0, 0, 1280, 720, "08121E")
        # Gold neon top highlight
        box(0, 0, 1280, 4, "F8CB63")

        # 2. Top Header Bar
        box(40, 20, 1200, 52, "0D1C30")
        text(56, 30, "RETRO", 30, "E50914", bold=True)
        text(168, 30, "SPORTS", 30, "F8CB63", bold=True)
        text(296, 36, "/   INTERACTIVE SCOREBOARD & GAMES", 18, "8E9EAF")

        text(990, 36, clock_str(), 20, "F8CB63")
        box(1100, 32, 120, 28, "1E7E34")
        text(1116, 37, "● SPORTS", 15, "FFFFFF", bold=True)

        if _mode == "browse":
            _render_browse(ass, box, text)
        else:
            _render_game(ass, box, text)

        result = _send(["osd-overlay", SPORTS_OVERLAY_ID, "ass-events", "\n".join(ass), 1280, 720])
        _visible = result.get("error") == "success"
        return _visible


def _render_browse(ass, box, text):
    if not _categories:
        box(40, 100, 1200, 200, "151D2A")
        text(60, 130, "NO GAMES CURRENTLY AVAILABLE", 26, "F8CB63")
        text(60, 170, "Connecting to sports scoreboard...", 18, "8E9EAF")
        return

    cat = _categories[_cat_idx]
    items = cat.get("items", [])
    item = items[_item_idx] if _item_idx < len(items) else None

    # Category Bar (y: 80..120)
    box(40, 80, 1200, 40, "13253C")
    cat_title = f"{cat.get('title', 'All')}  [{cat.get('badge', '')}]".strip()
    text(56, 88, f"CATEGORY ({_cat_idx + 1}/{len(_categories)}):", 15, "8E9EAF")
    text(230, 85, cat_title.upper(), 20, "F8CB63", bold=True)
    text(1020, 88, "▲ / ▼ CHANGE CATEGORY", 14, "8E9EAF")

    # Spotlight Details Banner (y: 128..310)
    box(40, 128, 1200, 180, "0F1A2A")
    box(40, 128, 6, 180, "F8CB63")  # Gold accent line

    if item:
        home = item.get("homeTeam", {})
        away = item.get("awayTeam", {})
        status = item.get("status", {})
        is_live = status.get("isLive", False)
        is_final = status.get("isFinal", False)
        league = (item.get("leagueName") or item.get("league") or "").upper()

        away_name = away.get("displayName") or "Away"
        home_name = home.get("displayName") or "Home"
        away_score = away.get("score", 0) if (is_live or is_final) else "--"
        home_score = home.get("score", 0) if (is_live or is_final) else "--"

        # League & Status Badge
        badge_color = "800B0B" if is_live else ("1E7E34" if is_final else "253A56")
        badge_text = "● LIVE NOW" if is_live else ("FINAL SCORE" if is_final else "UPCOMING")
        box(62, 142, 110, 24, badge_color)
        text(72, 146, badge_text, 13, "FFFFFF", bold=True)
        text(184, 145, league, 14, "8E9EAF", bold=True)

        # Matchup Title
        matchup_str = f"{away_name}  at  {home_name}"
        text(62, 172, matchup_str, 28, "FFFFFF", bold=True, clip=(60, 168, 860, 210))

        # Score display (Right side)
        box(890, 142, 330, 90, "15253C")
        text(910, 152, away.get("abbreviation", "AWY"), 24, "8E9EAF", bold=True)
        text(990, 146, str(away_score), 34, "F8CB63", bold=True)
        text(1050, 154, "-", 24, "8E9EAF")
        text(1080, 146, str(home_score), 34, "F8CB63", bold=True)
        text(1150, 152, home.get("abbreviation", "HME"), 24, "8E9EAF", bold=True)
        text(910, 196, status.get("detail") or "", 14, "8E9EAF", clip=(900, 190, 1210, 225))

        # Meta row (records, odds, broadcast, venue)
        meta_parts = []
        if away.get("recordSummary"):
            meta_parts.append(f"Away ({away['recordSummary']})")
        if home.get("recordSummary"):
            meta_parts.append(f"Home ({home['recordSummary']})")
        if item.get("odds", {}).get("details"):
            meta_parts.append(f"Line: {item['odds']['details']}")
        if item.get("broadcasts"):
            meta_parts.append(f"TV: {', '.join(item['broadcasts'])}")
        venue = item.get("venue", {}).get("name")
        if venue:
            meta_parts.append(f"Site: {venue}")

        text(62, 218, "   •   ".join(meta_parts), 15, "8E9EAF", clip=(60, 214, 880, 245))

        # Prompt
        text(62, 268, "[ ▶ PRESS OK TO OPEN GAME CENTER & FIELD RADAR ]", 15, "F8CB63", bold=True)
    else:
        text(62, 180, "No items in this category", 22, "8E9EAF")

    # Carousel Row Header (y: 318)
    box(40, 318, 1200, 30, "0A121E")
    text(56, 324, "ROW SELECTION:", 14, "8E9EAF")
    item_counter = f"{_item_idx + 1} of {len(items)}" if items else "0"
    text(1100, 324, item_counter, 14, "F8CB63")

    # Horizontal Carousel Cards (y: 354..660)
    card_w = 216
    card_h = 300
    gap = 25
    visible_count = 5
    start_idx = max(0, min(_item_idx - 2, max(0, len(items) - visible_count)))
    end_idx = min(len(items), start_idx + visible_count)

    for slot, i in enumerate(range(start_idx, end_idx)):
        card = items[i]
        cx = 48 + slot * (card_w + gap)
        cy = 354
        is_selected = (i == _item_idx)

        c_home = card.get("homeTeam", {})
        c_away = card.get("awayTeam", {})
        c_status = card.get("status", {})
        c_live = c_status.get("isLive", False)
        c_final = c_status.get("isFinal", False)

        if is_selected:
            box(cx - 3, cy - 3, card_w + 6, card_h + 6, "F8CB63")
            box(cx, cy, card_w, card_h, "162B45")
        else:
            box(cx, cy, card_w, card_h, "0F1B2C")
            box(cx, cy, card_w, 2, "203450")

        # League tag & Status badge
        badge_col = "800B0B" if c_live else ("1E7E34" if c_final else "1E334D")
        box(cx + 8, cy + 8, card_w - 16, 24, badge_col)
        b_txt = "● LIVE" if c_live else ("FINAL" if c_final else (card.get("league") or "").upper()[:12])
        text(cx + 14, cy + 12, b_txt, 12, "FFFFFF", bold=True)

        # Matchup
        text(cx + 14, cy + 46, (c_away.get("shortDisplayName") or c_away.get("abbreviation") or "AWAY")[:14], 16, "EFF2E9", bold=True)
        text(cx + 14, cy + 70, (c_home.get("shortDisplayName") or c_home.get("abbreviation") or "HOME")[:14], 16, "EFF2E9", bold=True)

        # Scores
        if c_live or c_final:
            text(cx + card_w - 50, cy + 46, str(c_away.get("score", 0)), 18, "F8CB63", bold=True)
            text(cx + card_w - 50, cy + 70, str(c_home.get("score", 0)), 18, "F8CB63", bold=True)
        else:
            text(cx + 14, cy + 98, c_status.get("shortDetail") or "Scheduled", 13, "8E9EAF")

        # Mini Sport Field / Graphic Box
        box(cx + 14, cy + 130, card_w - 28, 110, "09121D")
        s_icon = "🏈" if card.get("sport") == "football" else ("⚾" if card.get("sport") == "baseball" else ("🏀" if card.get("sport") == "basketball" else ("🏒" if card.get("sport") == "hockey" else "⚽")))
        text(cx + 90, cy + 165, s_icon, 34, "F8CB63")
        text(cx + 20, cy + 215, (card.get("shortName") or card.get("name", ""))[:20], 12, "8E9EAF", clip=(cx+14, cy+210, cx+card_w-14, cy+235))

        # Bottom info
        sub_text = c_status.get("detail") or ""
        text(cx + 14, cy + 260, sub_text[:24], 12, "8E9EAF", clip=(cx + 14, cy + 256, cx + card_w - 14, cy + 285))


def _render_game(ass, box, text):
    """Render full Game Center with field radar, box score, and play narration on TV."""
    if not _game_data:
        return

    g = _game_data
    home = g.get("homeTeam", {})
    away = g.get("awayTeam", {})
    status = g.get("status", {})
    situation = g.get("situation", {}) or {}
    league = (g.get("leagueName") or g.get("league") or "").upper()
    sport = (g.get("sport") or "football").lower()

    is_live = status.get("isLive", False)
    is_final = status.get("isFinal", False)

    # 1. Header Bar for Game Mode
    box(40, 80, 1200, 40, "13253C")
    text(56, 88, f"GAME CENTER: {league}", 16, "F8CB63", bold=True)
    text(1020, 88, "◄ PRESS BACK TO RETURN", 14, "8E9EAF")

    # 2. Scoreboard Banner
    box(40, 128, 1200, 120, "0F1B2C")
    box(40, 128, 6, 120, "E50914")

    # Away team
    text(62, 142, (away.get("displayName") or "Away").upper(), 26, "FFFFFF", bold=True)
    text(62, 178, f"Record: {away.get('recordSummary', '--')}", 15, "8E9EAF")
    text(420, 150, str(away.get("score", 0) if (is_live or is_final) else "--"), 48, "F8CB63", bold=True)

    # Status / Clock Center
    box(520, 140, 240, 96, "152840")
    text(640, 152, "● LIVE" if is_live else ("FINAL" if is_final else "SCHEDULED"), 14, "E50914" if is_live else "F8CB63", bold=True)
    text(640, 178, status.get("detail") or "Today", 15, "FFFFFF", bold=True)
    if is_live and status.get("displayClock"):
        text(640, 204, status["displayClock"], 14, "8E9EAF")

    # Home team
    text(800, 150, str(home.get("score", 0) if (is_live or is_final) else "--"), 48, "F8CB63", bold=True)
    text(890, 142, (home.get("displayName") or "Home").upper(), 26, "FFFFFF", bold=True)
    text(890, 178, f"Record: {home.get('recordSummary', '--')}", 15, "8E9EAF")

    # 3. Field Radar / Vector Gameplay Field
    box(40, 260, 1200, 230, "0A1524")
    box(40, 260, 1200, 28, "13253C")
    text(56, 266, f"FIELD RADAR · {sport.upper()}", 13, "F8CB63", bold=True)

    # Render ASS field representation
    if sport == "football":
        # Green gridiron field
        fx, fy, fw, fh = 120, 305, 1040, 160
        # Endzones
        box(fx, fy, 80, fh, "8A1E1E")  # Away endzone
        text(fx + 15, fy + 70, (away.get("abbreviation") or "AWY")[:4], 16, "FFFFFF", bold=True)
        box(fx + fw - 80, fy, 80, fh, "1E7E34")  # Home endzone
        text(fx + fw - 65, fy + 70, (home.get("abbreviation") or "HME")[:4], 16, "FFFFFF", bold=True)
        # Playing field
        box(fx + 80, fy, fw - 160, fh, "185324")
        # 10 yard lines
        yard_step = (fw - 160) / 10.0
        for y_idx in range(1, 10):
            lx = fx + 80 + int(y_idx * yard_step)
            box(lx, fy, 2, fh, "FFFFFF")
            yd_num = y_idx * 10 if y_idx <= 5 else (100 - y_idx * 10)
            text(lx - 8, fy + 8, str(yd_num), 10, "A2DCA9")

        # Line of scrimmage & ball
        yd = situation.get("yardLine", 50)
        bx = int(fx + 80 + (yd / 100.0) * (fw - 160))
        box(bx - 2, fy, 4, fh, "2196F3")  # Blue line of scrimmage
        # Football
        box(bx - 8, fy + int(fh / 2) - 6, 16, 12, "F8CB63")
        text(bx - 4, fy + int(fh / 2) - 5, "🏈", 10, "000000")

        # Down & distance banner
        down_dist = situation.get("downDistanceText") or "1st & 10"
        text(560, 440, f"🏈 {down_dist.upper()}", 15, "F8CB63", bold=True)

    elif sport == "baseball":
        # Diamond representation
        fx, fy, fw, fh = 460, 295, 360, 180
        box(fx, fy, fw, fh, "1A4D22")
        # Base runners
        on_1 = situation.get("onFirst", False)
        on_2 = situation.get("onSecond", False)
        on_3 = situation.get("onThird", False)
        # 2nd Base
        box(fx + 170, fy + 20, 20, 20, "F8CB63" if on_2 else "334466")
        # 3rd Base
        box(fx + 100, fy + 80, 20, 20, "F8CB63" if on_3 else "334466")
        # 1st Base
        box(fx + 240, fy + 80, 20, 20, "F8CB63" if on_1 else "334466")
        # Home Plate
        box(fx + 170, fy + 140, 20, 20, "FFFFFF")
        text(fx + 140, fy + 165, "HOME PLATE", 11, "8E9EAF")

    else:
        # Court / Pitch representation
        fx, fy, fw, fh = 120, 305, 1040, 160
        box(fx, fy, fw, fh, "1A4D22" if sport == "soccer" else "8E582D")
        box(fx + int(fw / 2), fy, 3, fh, "FFFFFF")
        text(fx + int(fw / 2) - 40, fy + 70, "CENTER", 14, "FFFFFF")

    # 4. Action & Commentary Bar
    box(40, 502, 1200, 180, "0E1A2B")
    box(40, 502, 1200, 28, "13253C")
    text(56, 508, "LATEST PLAY-BY-PLAY & ANNOUNCER", 13, "F8CB63", bold=True)

    # Show latest play text if available
    latest_play = None
    if _game_detail and _game_detail.get("visualPlays"):
        latest_play = _game_detail["visualPlays"][-1].get("text")
    elif _game_detail and _game_detail.get("plays"):
        latest_play = _game_detail["plays"][-1].get("text")

    play_display = latest_play or sports.script_for_game(g, detail=_game_detail)
    text(60, 542, play_display, 17, "EFF2E9", clip=(60, 538, 1200, 620))

    # Announcer Prompt
    box(60, 630, 480, 36, "1E7E34")
    text(76, 638, "🔊 PRESS OK TO HEAR ANNOUNCER COMMENTARY", 15, "FFFFFF", bold=True)
    text(560, 638, "DraftKings Odds: " + (g.get("odds", {}).get("details") or "N/A"), 14, "F8CB63")
    text(920, 638, "Site: " + (g.get("venue", {}).get("name") or "Stadium")[:24], 14, "8E9EAF")
