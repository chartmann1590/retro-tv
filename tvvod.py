"""Native mpv On-Demand (VOD) overlay: renders Retroflix directly onto the TV screen.

The TV display is driven by mpv over HDMI. This module draws the interactive
Retro-styled Netflix / VOD menu (categories, carousels, spotlight banner, and
season/episode selectors) directly as an mpv ASS OSD overlay. The mobile/web
remote acts as the physical remote control sending D-pad and navigation commands.
"""
import json
import logging
import socket
import threading
import time
from datetime import datetime

import config
import metadata
import scheduler
import vod

log = logging.getLogger("retro-tv.tvvod")

_lock = threading.RLock()
_visible = False
_socket = None
_reader = None

VOD_OVERLAY_ID = 42  # Shares the full-screen menu overlay slot with tvguide
CARD_OVERLAY_START = 20
CARD_OVERLAY_COUNT = 5
SPOTLIGHT_OVERLAY_ID = 30
SHOW_OVERLAY_ID = 31

def _clear_bitmap_overlays():
    for oid in range(CARD_OVERLAY_START, CARD_OVERLAY_START + CARD_OVERLAY_COUNT):
        _send(["overlay-remove", oid])
    _send(["overlay-remove", SPOTLIGHT_OVERLAY_ID])
    _send(["overlay-remove", SHOW_OVERLAY_ID])


_mode = "browse"        # "browse" or "show"
_categories = []
_cat_idx = 0
_item_idx = 0

_show_data = None       # Detailed show dictionary when in "show" mode
_season_idx = 0
_ep_idx = 0

_search_query = ""
_search_results = None


def _send(command):
    global _socket, _reader
    try:
        if _socket is None:
            _socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            _socket.settimeout(2)
            _socket.connect(config.MPV_SOCKET)
            _reader = _socket.makefile("r")
        _socket.sendall((json.dumps({"command": command, "request_id": 44}) + "\n").encode())
        for line in _reader:
            response = json.loads(line)
            if response.get("request_id") == 44:
                return response
    except (OSError, ValueError):
        pass
    if _reader:
        _reader.close()
    if _socket:
        _socket.close()
    _socket = _reader = None
    return {"error": "Player unavailable"}


def _text(value):
    return str(value or "").replace("\\", " ").replace("{", "(").replace("}", ")").replace("\n", " ")


def _color(rgb):
    """Convert RRGGBB to ASS BBGGRR."""
    return rgb[4:6] + rgb[2:4] + rgb[0:2]


def is_visible():
    return _visible


def get_status():
    with _lock:
        if not _visible:
            return {"visible": False}
        if _mode == "show" and _show_data:
            show = _show_data.get("show", {})
            seasons = show.get("seasons", [])
            season = seasons[_season_idx] if _season_idx < len(seasons) else {}
            episodes = season.get("episodes", [])
            ep = episodes[_ep_idx] if _ep_idx < len(episodes) else {}
            return {
                "visible": True,
                "mode": "show",
                "show_name": show.get("name", ""),
                "season": season.get("season", 1),
                "title": ep.get("title", show.get("name", "")),
                "subtitle": f"{show.get('name', '')} S{season.get('season', 1)}E{ep.get('episode', 1)}",
                "media_id": ep.get("media_id"),
            }
        # browse mode
        cat = _categories[_cat_idx] if _cat_idx < len(_categories) else None
        items = cat.get("items", []) if cat else []
        item = items[_item_idx] if _item_idx < len(items) else None
        return {
            "visible": True,
            "mode": "browse",
            "category": cat.get("title", "") if cat else "",
            "title": (item.get("title") or item.get("name", "")) if item else "On Demand",
            "subtitle": (item.get("subtitle") or item.get("year", "")) if item else "",
            "kind": item.get("kind", "") if item else "",
            "media_id": item.get("media_id") if item else None,
            "show_id": item.get("id") if (item and item.get("kind") == "show") else None,
        }


def open_vod():
    import playback
    import tvguide

    global _visible, _mode, _categories, _cat_idx, _item_idx, _show_data, _season_idx, _ep_idx, _search_query
    with _lock:
        try:
            tvguide.close()
        except Exception:
            pass

        if not playback.mpv_alive():
            playback.restore_last()

        catalog = vod.get_catalog()
        _categories = catalog.get("categories", [])
        _cat_idx = 0
        _item_idx = 0
        _mode = "browse"
        _show_data = None
        _season_idx = 0
        _ep_idx = 0
        _search_query = ""

        ok = render()
        _visible = ok
        status = get_status()
        status["ok"] = ok
        return status


def close_vod():
    global _visible, _mode, _show_data
    with _lock:
        _visible = False
        _mode = "browse"
        _show_data = None
        _clear_bitmap_overlays()
        res = _send(["osd-overlay", VOD_OVERLAY_ID, "none", ""])
        return res.get("error") == "success"


def nav(action, query=None):
    global _cat_idx, _item_idx, _mode, _show_data, _season_idx, _ep_idx, _search_query, _categories
    with _lock:
        if not _visible:
            return open_vod()

        action = (action or "").lower()

        if action == "search":
            _search_query = (query or "").strip()
            if _search_query:
                results = vod.search_vod(_search_query)
                combined = []
                for it in results.get("movies", []):
                    combined.append(it)
                for it in results.get("shows", []):
                    combined.append(it)
                for it in results.get("episodes", []):
                    combined.append(it)
                search_cat = {
                    "id": "search-results",
                    "title": f"SEARCH: '{_search_query}'",
                    "badge": f"{len(combined)} found",
                    "items": combined,
                }
                # Prepend or replace search category
                catalog = vod.get_catalog()
                _categories = [search_cat] + catalog.get("categories", [])
                _cat_idx = 0
                _item_idx = 0
                _mode = "browse"
            else:
                catalog = vod.get_catalog()
                _categories = catalog.get("categories", [])
                _cat_idx = 0
                _item_idx = 0
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
                    if item.get("kind") == "show":
                        details = vod.get_show_details(item["id"])
                        if details and details.get("show"):
                            _show_data = details
                            _mode = "show"
                            _season_idx = 0
                            _ep_idx = 0
                    else:
                        # Movie or episode: start playing immediately
                        media_id = item.get("media_id")
                        if media_id:
                            close_vod()
                            import playback
                            playback.play_vod(media_id=media_id, title=item.get("title"), subtitle=item.get("subtitle") or str(item.get("year") or ""))
                            return {"ok": True, "playing": True, "title": item.get("title"), "closed": True}
            elif action in ("back", "last"):
                close_vod()
                return {"ok": True, "closed": True}

        elif _mode == "show":
            show = _show_data.get("show", {}) if _show_data else {}
            seasons = show.get("seasons", [])
            season = seasons[_season_idx] if _season_idx < len(seasons) else {}
            episodes = season.get("episodes", [])

            if action == "up":
                if _ep_idx > 0:
                    _ep_idx -= 1
            elif action == "down":
                if _ep_idx < len(episodes) - 1:
                    _ep_idx += 1
            elif action == "left":
                if _season_idx > 0:
                    _season_idx -= 1
                    _ep_idx = 0
            elif action == "right":
                if _season_idx < len(seasons) - 1:
                    _season_idx += 1
                    _ep_idx = 0
            elif action in ("select", "ok"):
                ep = episodes[_ep_idx] if _ep_idx < len(episodes) else None
                if ep and ep.get("media_id"):
                    close_vod()
                    import playback
                    title = ep.get("title")
                    subtitle = f"{show.get('name', '')} S{season.get('season', 1)}E{ep.get('episode', 1)}"
                    playback.play_vod(media_id=ep["media_id"], title=title, subtitle=subtitle)
                    return {"ok": True, "playing": True, "title": title, "subtitle": subtitle, "closed": True}
            elif action in ("back", "last"):
                _mode = "browse"
                _show_data = None

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

        # 1. Full Screen Backdrop (Rich retro CRT charcoal)
        box(0, 0, 1280, 720, "0A0D14")
        # Top red neon line
        box(0, 0, 1280, 4, "E50914")

        # 2. Top Header Bar
        box(40, 20, 1200, 52, "121824")
        # Retroflix Logo
        text(56, 30, "RETRO", 30, "E50914", bold=True)
        text(168, 30, "FLIX", 30, "F8CB63", bold=True)
        text(248, 36, "/   ON DEMAND ENTERTAINMENT", 18, "A2B4C7")

        # Clock & Mode Badge
        text(1000, 36, clock_str(), 20, "F8CB63")
        box(1110, 32, 110, 28, "8A1E1E")
        text(1124, 37, "● VOD", 15, "FFFFFF", bold=True)

        if _mode == "browse":
            _render_browse(ass, box, text)
        else:
            _render_show(ass, box, text)

        result = _send(["osd-overlay", VOD_OVERLAY_ID, "ass-events", "\n".join(ass), 1280, 720])
        _visible = result.get("error") == "success"
        return _visible


def _render_browse(ass, box, text):
    if not _categories:
        box(40, 100, 1200, 200, "151D2A")
        text(60, 130, "NO ON-DEMAND TITLES AVAILABLE", 26, "F8CB63")
        text(60, 170, "Add media to TVShows/ or Movies/ and run a scan in Admin.", 18, "A2B4C7")
        return

    cat = _categories[_cat_idx]
    items = cat.get("items", [])
    item = items[_item_idx] if _item_idx < len(items) else None

    # Category Bar (y: 80..116)
    box(40, 80, 1200, 40, "162030")
    cat_title = f"{cat.get('title', 'All')}  {cat.get('badge', '')}".strip()
    text(56, 88, f"CATEGORY ({_cat_idx + 1}/{len(_categories)}):", 15, "8E9EAF")
    text(230, 85, cat_title.upper(), 20, "F8CB63", bold=True)
    text(1020, 88, "▲ / ▼ CHANGE CATEGORY", 14, "8E9EAF")

    # Spotlight Details Banner (y: 128..278)
    box(40, 128, 1200, 150, "131923")
    box(40, 128, 6, 150, "E50914")  # Red accent line

    if item:
        title = item.get("title") or item.get("name", "Untitled")
        text(62, 138, title, 30, "FFFFFF", clip=(60, 134, 1100, 178), bold=True)

        # Spotlight poster thumbnail on the right
        box(1118, 136, 100, 134, "263347")
        box(1120, 138, 96, 130, "0A0E15")
        spot_art = item.get("raw_artwork") or item.get("artwork") or item.get("poster")
        raw_spot = metadata.get_raw_bitmap(spot_art, 96, 130) if spot_art else None
        if raw_spot:
            _send(["overlay-add", SPOTLIGHT_OVERLAY_ID, 1120, 138, raw_spot, 0, "bgra", 96, 130, 96 * 4, 96, 130])
        else:
            _send(["overlay-remove", SPOTLIGHT_OVERLAY_ID])
            text(1150, 180, (title[0].upper() if title else "?"), 36, "2A384F", bold=True)

        # Meta row
        is_show = item.get("kind") == "show"
        kind_color = "204E8A" if is_show else "8A1E1E"
        kind_label = "TV SERIES" if is_show else "FEATURE FILM"
        box(62, 180, 105, 24, kind_color)
        text(72, 184, kind_label, 13, "FFFFFF", bold=True)

        meta_parts = []
        if is_show:
            eps = item.get("episode_count")
            if eps:
                meta_parts.append(f"{eps} Episodes")
        else:
            yr = item.get("year")
            if yr:
                meta_parts.append(str(yr))
            dur = item.get("duration_min")
            if dur:
                meta_parts.append(f"{dur} min")
        genre = item.get("genre")
        if genre:
            meta_parts.append(genre)

        meta_str = "   •   ".join(meta_parts)
        text(180, 183, meta_str, 16, "F8CB63")

        # Description
        desc = item.get("description") or item.get("subtitle") or "Available on demand."
        text(62, 212, desc, 16, "C5D1DE", clip=(60, 210, 1100, 246))

        # Prompt
        if is_show:
            text(62, 252, "[ ▶ PRESS OK TO BROWSE SEASONS & EPISODES ]", 15, "F8CB63", bold=True)
        else:
            text(62, 252, "[ ▶ PRESS OK TO PLAY MOVIE ON TV ]", 15, "F8CB63", bold=True)
    else:
        _send(["overlay-remove", SPOTLIGHT_OVERLAY_ID])
        text(62, 150, "No items in this category", 22, "A2B4C7")

    # Carousel Row Header
    box(40, 288, 1200, 30, "0E131C")
    text(56, 294, "ROW SELECTION:", 14, "8E9EAF")
    item_counter = f"{_item_idx + 1} of {len(items)}" if items else "0"
    text(1100, 294, item_counter, 14, "F8CB63")

    # Horizontal Carousel Cards (y: 324..624)
    # 5 cards visible across 1200px: 216px width + 24px gap
    card_w = 216
    card_h = 280
    gap = 25
    visible_count = CARD_OVERLAY_COUNT
    start_idx = max(0, min(_item_idx - 2, max(0, len(items) - visible_count)))
    end_idx = min(len(items), start_idx + visible_count)

    used_slots = set()
    for slot, i in enumerate(range(start_idx, end_idx)):
        used_slots.add(slot)
        card = items[i]
        cx = 48 + slot * (card_w + gap)
        cy = 324
        is_selected = (i == _item_idx)

        if is_selected:
            # Gold glowing outline
            box(cx - 3, cy - 3, card_w + 6, card_h + 6, "F8CB63")
            box(cx, cy, card_w, card_h, "1F2A3D")
        else:
            box(cx, cy, card_w, card_h, "131A24")
            box(cx, cy, card_w, 2, "263347")

        # Card Media Box (Art placeholder / Poster slot)
        img_w = card_w - 20
        img_h = 160
        box(cx + 10, cy + 10, img_w, img_h, "0A0E15")

        card_art = card.get("raw_artwork") or card.get("artwork") or card.get("poster")
        raw_card = metadata.get_raw_bitmap(card_art, img_w, img_h) if card_art else None
        if raw_card:
            _send(["overlay-add", CARD_OVERLAY_START + slot, cx + 10, cy + 10, raw_card, 0, "bgra", img_w, img_h, img_w * 4, img_w, img_h])
        else:
            _send(["overlay-remove", CARD_OVERLAY_START + slot])
            card_title = card.get("title") or card.get("name", "")
            initial = card_title[0].upper() if card_title else "?"
            text(cx + 90, cy + 65, initial, 46, "2A384F", bold=True)

        badge = "TV" if card.get("kind") == "show" else "FILM"
        badge_col = "204E8A" if card.get("kind") == "show" else "8A1E1E"
        box(cx + 16, cy + 16, 46, 20, badge_col)
        text(cx + 22, cy + 19, badge, 12, "FFFFFF", bold=True)

        # Card Bottom Info
        text(cx + 10, cy + 182, card.get("title") or card.get("name", ""), 17, "FFFFFF" if is_selected else "EFF2E9",
             clip=(cx + 8, cy + 180, cx + card_w - 8, cy + 224), bold=is_selected)

        card_sub = card.get("subtitle") or (f"{card.get('episode_count')} eps" if card.get("kind") == "show" else str(card.get("year") or ""))
        text(cx + 10, cy + 230, card_sub, 14, "F8CB63" if is_selected else "8E9EAF",
             clip=(cx + 8, cy + 228, cx + card_w - 8, cy + 252))

        if is_selected:
            text(cx + 10, cy + 256, "▶ PRESS OK", 13, "F8CB63", bold=True)

    for s in range(visible_count):
        if s not in used_slots:
            _send(["overlay-remove", CARD_OVERLAY_START + s])

    # Bottom Hint Bar (y: 640..694)
    box(40, 640, 1200, 54, "121824")
    text(56, 656, "▲ / ▼ CATEGORY   •   ◀ / ▶ BROWSE   •   OK SELECT / PLAY   •   PRESS 'LIVE TV' TO RETURN TO CABLE", 16, "F8CB63", bold=True)


def _render_show(ass, box, text):
    # Clear carousel card overlays when in show details mode
    for s in range(CARD_OVERLAY_COUNT):
        _send(["overlay-remove", CARD_OVERLAY_START + s])
    _send(["overlay-remove", SPOTLIGHT_OVERLAY_ID])

    if not _show_data or not _show_data.get("show"):
        box(40, 100, 1200, 200, "151D2A")
        text(60, 130, "Show information unavailable", 24, "F8CB63")
        return

    show = _show_data["show"]
    seasons = show.get("seasons", [])
    season = seasons[_season_idx] if _season_idx < len(seasons) else {}
    episodes = season.get("episodes", [])

    # Show Banner (y: 80..188)
    box(40, 80, 1200, 108, "131A26")
    box(40, 80, 6, 108, "204E8A")  # Blue series accent
    text(58, 90, show.get("name", "TV Series"), 28, "FFFFFF", clip=(56, 88, 1000, 128), bold=True)

    # Show poster on the right
    box(1118, 86, 100, 96, "204E8A")
    box(1120, 88, 96, 92, "0A0E15")
    show_art = show.get("raw_artwork") or show.get("artwork") or show.get("poster")
    raw_show = metadata.get_raw_bitmap(show_art, 96, 92) if show_art else None
    if raw_show:
        _send(["overlay-add", SHOW_OVERLAY_ID, 1120, 88, raw_show, 0, "bgra", 96, 92, 96 * 4, 96, 92])
    else:
        _send(["overlay-remove", SHOW_OVERLAY_ID])

    # Show meta
    meta = f"{len(seasons)} Seasons   •   {show.get('episode_count', 0)} Episodes   •   {show.get('genre', '')}"
    text(58, 128, meta, 16, "F8CB63")
    desc = show.get("description") or "Select a season and episode to play."
    text(58, 154, desc, 15, "C5D1DE", clip=(56, 152, 1210, 180))

    # Season Toolbar (y: 196..240)
    box(40, 196, 1200, 44, "1E283C")
    s_label = f"◄   SEASON {season.get('season', 1)}   ({len(episodes)} Episodes)   ►"
    text(450, 206, s_label, 20, "F8CB63", bold=True)
    text(56, 208, f"Season {_season_idx + 1} of {len(seasons)}", 15, "8E9EAF")
    text(1010, 208, "◀ / ▶ CHANGE SEASON", 15, "8E9EAF")

    # Episodes Vertical List (y: 248..630)
    # 7 rows visible: 50px height + 4px gap
    row_h = 48
    gap = 4
    visible_eps = 7
    start_ep = max(0, min(_ep_idx - 3, max(0, len(episodes) - visible_eps)))
    end_ep = min(len(episodes), start_ep + visible_eps)

    for slot, k in enumerate(range(start_ep, end_ep)):
        ep = episodes[k]
        ey = 248 + slot * (row_h + gap)
        is_sel = (k == _ep_idx)

        if is_sel:
            # Highlight row in gold
            box(40, ey, 1200, row_h, "F8CB63")
            ep_num = f"E{ep.get('episode', k + 1):02d}"
            text(56, ey + 11, ep_num, 20, "0A0E15", bold=True)
            text(120, ey + 11, ep.get("title", f"Episode {k + 1}"), 20, "0A0E15", clip=(116, ey + 8, 880, ey + 42), bold=True)
            dur = f"{ep.get('duration_min', 0)} min" if ep.get("duration_min") else ""
            text(900, ey + 12, dur, 17, "1A2332")
            text(1000, ey + 12, "▶ PRESS OK TO PLAY", 16, "8A1E1E", bold=True)
        else:
            box(40, ey, 1200, row_h, "131A26")
            box(40, ey, 1200, 1, "202A3D")
            ep_num = f"E{ep.get('episode', k + 1):02d}"
            text(56, ey + 13, ep_num, 18, "8E9EAF")
            text(120, ey + 13, ep.get("title", f"Episode {k + 1}"), 18, "EFF2E9", clip=(116, ey + 10, 950, ey + 42))
            dur = f"{ep.get('duration_min', 0)} min" if ep.get("duration_min") else ""
            text(1020, ey + 13, dur, 16, "8E9EAF")

    # Bottom Hint Bar (y: 640..694)
    box(40, 640, 1200, 54, "121824")
    text(56, 656, "▲ / ▼ SELECT EPISODE   •   ◀ / ▶ SEASON   •   OK PLAY   •   LAST BACK TO SHOWS   •   LIVE TV EXIT", 16, "F8CB63", bold=True)
