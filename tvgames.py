"""On-TV game picker and persistent ArenaPulse league game selection."""
import json
import logging
import socket
import threading
from datetime import datetime

import config
import livesports
import playback
import scheduler
import sports

log = logging.getLogger("retro-tv.tvgames")
OVERLAY_ID = 55
_lock = threading.RLock()
_socket = None
_reader = None
_visible = False
_loading = False
_error = ""
_channel = None
_league = None
_items = []
_index = 0
_pending_id = None


def _send(command):
    global _socket, _reader
    try:
        if _socket is None:
            _socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            _socket.settimeout(2)
            _socket.connect(config.MPV_SOCKET)
            _reader = _socket.makefile("r")
        _socket.sendall((json.dumps({"command": command, "request_id": OVERLAY_ID}) + "\n").encode())
        for line in _reader:
            result = json.loads(line)
            if result.get("request_id") == OVERLAY_ID:
                return result.get("error") == "success"
    except (OSError, ValueError):
        pass
    if _reader:
        _reader.close()
    if _socket:
        _socket.close()
    _reader = _socket = None
    return False


def _safe(value):
    return str(value or "").replace("\\", " ").replace("{", "(").replace("}", ")").replace("\n", " ")


def _color(rgb):
    return rgb[4:6] + rgb[2:4] + rgb[0:2]


def _game_label(game):
    away = game.get("awayTeam") or {}
    home = game.get("homeTeam") or {}
    return f"{away.get('abbreviation') or away.get('shortDisplayName') or 'AWAY'} @ {home.get('abbreviation') or home.get('shortDisplayName') or 'HOME'}"


def _status_label(game):
    status = game.get("status") or {}
    if status.get("isLive"):
        away = (game.get("awayTeam") or {}).get("score", 0)
        home = (game.get("homeTeam") or {}).get("score", 0)
        return f"● LIVE  {away} – {home}  {status.get('shortDetail') or ''}"
    if status.get("isFinal"):
        return "FINAL · RECAP AVAILABLE"
    try:
        date = datetime.fromisoformat(game["date"].replace("Z", "+00:00"))
        return date.astimezone(scheduler.TZ).strftime("%a %b %-d  %-I:%M %p %Z")
    except (KeyError, TypeError, ValueError):
        return status.get("detail") or "UPCOMING"


def _render():
    if not _visible:
        return _send(["osd-overlay", OVERLAY_ID, "none", ""])
    ass = []

    def box(x, y, w, h, color):
        ass.append(f"{{\\an7\\pos({x},{y})\\bord0\\shad0\\1c&H{_color(color)}&\\p1}}m 0 0 l {w} 0 {w} {h} 0 {h}{{\\p0}}")

    def text(x, y, value, size=23, color="EFF2E9", bold=False):
        ass.append(f"{{\\an7\\pos({x},{y})\\fnDejaVu Sans\\fs{size}\\b{int(bold)}\\bord0\\shad0\\1c&H{_color(color)}&}}{_safe(value)}")

    box(0, 0, 1280, 720, "071322")
    box(35, 28, 1210, 74, "173453")
    box(35, 99, 1210, 4, "F8CB63")
    text(58, 44, f"RETRO SPORTS  /  CH {_channel:02d}  /  PICK A GAME", 29, "F8CB63", True)
    if _loading:
        text(70, 180, "LOCKING THIS GAME TO YOUR TV", 36, "FFFFFF", True)
        text(70, 245, _game_label(_items[_index]) if _items else "League coverage", 55, "F8CB63", True)
        text(70, 330, "Preparing the field, play-by-play, and narration…", 25, "B8CCDE")
        text(70, 647, "The channel will switch to this game when it is ready.", 22, "F8CB63")
    else:
        text(58, 120, "LIVE GAMES FIRST · SELECT A MATCHUP TO KEEP IT ON THIS CHANNEL", 20, "B8CCDE")
        if _error:
            text(58, 155, _error, 19, "F16A6A")
        if not _items:
            text(70, 250, "NO LIVE OR UPCOMING GAMES", 38, "FFFFFF", True)
            text(70, 310, "ArenaPulse will add games as they are scheduled.", 23, "B8CCDE")
        else:
            start = max(0, min(_index - 2, len(_items) - 6))
            for index in range(start, min(start + 6, len(_items))):
                game = _items[index]
                y = 164 + (index - start) * 76
                selected = index == _index
                box(55, y, 1170, 70, "F8CB63" if selected else "152F4C")
                box(59, y + 4, 1162, 62, "152F4C" if selected else "152F4C")
                if selected:
                    box(55, y, 6, 70, "F8CB63")
                text(77, y + 8, _game_label(game), 26, "FFFFFF", True)
                text(79, y + 40, _status_label(game), 17,
                     "F16A6A" if (game.get("status") or {}).get("isLive") else "F8CB63")
                if str(game.get("id")) == livesports.pinned_game(_channel):
                    text(1040, y + 22, "● LOCKED", 18, "71E38F", True)
        text(58, 651, "▲ ▼ CHOOSE GAME   •   OK WATCH THIS GAME   •   BACK CLOSE   •   BACK AGAIN RETURN TO ROTATION", 18, "F8CB63")
    return _send(["osd-overlay", OVERLAY_ID, "ass-events", "\n".join(ass), 1280, 720])


def is_visible():
    return _visible


def get_status():
    with _lock:
        channel = _channel if _visible else playback._current.get("channel")
        league = livesports.league_for_channel(channel) if channel is not None else None
        selected = _items[_index] if _visible and _items and _index < len(_items) else None
        return {"visible": _visible, "loading": _loading, "channel": channel, "league": league,
                "pinned_game_id": livesports.pinned_game(channel) if league else "",
                "selected_game_id": selected.get("id") if selected else None,
                "selected_title": _game_label(selected) if selected else "", "total": len(_items),
                "index": _index, "error": _error}


def open_picker(channel=None):
    import tvguide
    import tvsports
    import tvvod
    global _visible, _loading, _error, _channel, _league, _items, _index
    channel = channel if channel is not None else playback._current.get("channel")
    league = livesports.league_for_channel(channel) if channel is not None else None
    if not league:
        return {"ok": False, "error": "Tune a league channel to choose a game"}
    data = sports.get_all_scores()
    if data.get("_unavailable"):
        return {"ok": False, "error": "ArenaPulse scores are unavailable"}
    games = [game for game in data.get("games") or [] if game.get("league") == league
             and ((game.get("status") or {}).get("isLive") or (game.get("status") or {}).get("isScheduled"))]
    games.sort(key=lambda game: (0 if (game.get("status") or {}).get("isLive") else 1,
                                 game.get("date") or ""))
    tvguide.close()
    tvsports.close_sports()
    tvvod.close_vod()
    with _lock:
        _channel, _league, _items = channel, league, games
        pin = livesports.pinned_game(channel)
        _index = next((index for index, game in enumerate(games) if str(game.get("id")) == pin), 0)
        _loading = False
        _error = ""
        _visible = True
        ok = _render()
        return {"ok": ok, **get_status()}


def close_picker():
    global _visible, _loading
    with _lock:
        _visible = False
        _loading = False
        return {"ok": _render(), "visible": False}


def _prepare(channel, expected_pin, initial_channel, force_tune):
    global _visible, _loading, _error
    try:
        ok = livesports.refresh_selected_channel(channel)
        if not ok:
            raise RuntimeError("The selected game could not be prepared")
        if livesports.pinned_game(channel) != expected_pin:
            return
        with _lock:
            _visible = False
            _loading = False
            _error = ""
            _render()
        if playback._current.get("channel") == channel or (force_tune and playback._current.get("channel") == initial_channel):
            playback.tune(channel, reason="game")
    except Exception:
        log.exception("Selected game preparation failed")
        with _lock:
            _loading = False
            _error = "Could not prepare this game. Press BACK and try again."
            if _visible:
                _render()


def pin_game(channel, game_id, force_tune=False):
    global _visible, _loading, _pending_id, _error
    league = livesports.league_for_channel(channel)
    if not league:
        return {"ok": False, "error": "This is not a league channel"}
    data = sports.get_all_scores()
    if data.get("_unavailable"):
        return {"ok": False, "error": "ArenaPulse scores are unavailable"}
    game = next((item for item in data.get("games") or []
                 if item.get("league") == league and str(item.get("id")) == str(game_id)), None)
    if not game:
        return {"ok": False, "error": "Game is no longer available"}
    initial_channel = playback._current.get("channel")
    livesports.set_pinned_game(channel, game_id)
    with _lock:
        _pending_id = str(game_id)
        _loading = True
        _error = ""
        if _visible:
            _render()
    threading.Thread(target=_prepare, args=(channel, str(game_id), initial_channel, force_tune),
                     daemon=True, name="sports-game-lock").start()
    return {"ok": True, "game": _game_label(game), "status": "preparing", **get_status()}


def unpin_game(channel):
    if not livesports.league_for_channel(channel):
        return {"ok": False, "error": "This is not a league channel"}
    livesports.set_pinned_game(channel, "")
    return pin_rotation(channel)


def pin_rotation(channel):
    global _visible, _loading, _error, _channel, _league, _items
    initial_channel = playback._current.get("channel")
    with _lock:
        _channel = channel
        _league = livesports.league_for_channel(channel)
        _items = []
        _visible = True
        _loading = True
        _error = ""
        _render()
    threading.Thread(target=_prepare, args=(channel, "", initial_channel, False),
                     daemon=True, name="sports-game-rotation").start()
    return {"ok": True, "status": "preparing", **get_status()}


def navigate(action):
    global _index
    action = (action or "").lower()
    with _lock:
        if not _visible:
            return open_picker()
        if action in ("back", "close"):
            return close_picker()
        if _loading:
            return {"ok": True, **get_status()}
        if action in ("up", "left") and _items:
            _index = max(0, _index - 1)
        elif action in ("down", "right") and _items:
            _index = min(len(_items) - 1, _index + 1)
        elif action in ("ok", "select") and _items:
            return pin_game(_channel, _items[_index]["id"])
        ok = _render()
        return {"ok": ok, **get_status()}
