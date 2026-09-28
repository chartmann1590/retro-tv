"""Ticking ArenaPulse start countdown on the HDMI league broadcasts."""
import json
import logging
import socket
import threading
import time
from datetime import datetime

import config
import scheduler

log = logging.getLogger("retro-tv.tvcountdown")
OVERLAY_ID = 54
_socket = None
_reader = None


def countdown_info(game, now=None):
    """Return the local start label and remaining time for a scheduled game."""
    if not game or not (game.get("status") or {}).get("isScheduled"):
        return None
    try:
        start = datetime.fromisoformat(game["date"].replace("Z", "+00:00"))
        if start.tzinfo is None:
            return None
    except (KeyError, TypeError, ValueError):
        return None
    remaining = max(0, int(start.timestamp() - (now or time.time()) + .999))
    days, rem = divmod(remaining, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, seconds = divmod(rem, 60)
    clock = f"{days}D {hours:02d}:{minutes:02d}:{seconds:02d}" if days else f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    label = start.astimezone(scheduler.TZ).strftime("%a %b %-d  %-I:%M %p %Z").upper()
    return label, f"LIVE IN {clock}" if remaining else "STARTING NOW"


def _send(command):
    # mpv owns an OSD overlay for the lifetime of its IPC connection.
    global _socket, _reader
    try:
        if _socket is None:
            _socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            _socket.settimeout(2)
            _socket.connect(config.MPV_SOCKET)
            _reader = _socket.makefile("r")
        _socket.sendall((json.dumps({"command": command, "request_id": OVERLAY_ID}) + "\n").encode())
        for line in _reader:
            response = json.loads(line)
            if response.get("request_id") == OVERLAY_ID:
                return response.get("error") == "success"
    except (OSError, ValueError):
        pass
    if _reader:
        _reader.close()
    if _socket:
        _socket.close()
    _socket = _reader = None
    return False


def _safe(text):
    return str(text).replace("\\", " ").replace("{", "(").replace("}", ")").replace("\n", " ")


def _draw(league_name, game, info):
    away = (game.get("awayTeam") or {}).get("abbreviation") or "AWAY"
    home = (game.get("homeTeam") or {}).get("abbreviation") or "HOME"
    title = _safe(f"{league_name.upper()}  ·  {away} @ {home}")
    start, countdown = map(_safe, info)
    ass = [
        r"{\an7\pos(36,566)\bord0\shad0\1c&H172A43&\p1}m 0 0 l 1208 0 1208 96 0 96{\p0}",
        r"{\an7\pos(36,566)\bord0\shad0\1c&H63CBF8&\p1}m 0 0 l 6 0 6 96 0 96{\p0}",
        rf"{{\an7\pos(57,576)\fnDejaVu Sans\fs24\b1\bord0\shad0\1c&H63CBF8&}}{title}",
        rf"{{\an7\pos(57,617)\fnDejaVu Sans\fs23\bord0\shad0\1c&HCCDCEA&}}{start}",
        rf"{{\an9\pos(1223,621)\fnDejaVu Sans\fs35\b1\bord0\shad0\1c&H68C9F4&}}{countdown}",
    ]
    return _send(["osd-overlay", OVERLAY_ID, "ass-events", "\n".join(ass), 1280, 720])


def run_loop():
    import livesports
    import playback
    import sports
    import tvguide
    import tvsports

    last_channel = None
    games = []
    checked_at = 0
    showing = False
    while True:
        try:
            channel = playback._current.get("channel")
            league = livesports.league_for_channel(channel) if channel is not None else None
            hidden = playback._current.get("is_vod") or tvguide.is_visible() or tvsports.is_visible()
            if not league or hidden:
                if showing:
                    _send(["osd-overlay", OVERLAY_ID, "none", ""])
                    showing = False
            else:
                if channel != last_channel or time.monotonic() - checked_at >= 30:
                    result = sports.get_all_scores()
                    if not result.get("_unavailable"):
                        games = result.get("games") or []
                    checked_at = time.monotonic()
                    last_channel = channel
                upcoming = sorted((game for game in games if game.get("league") == league
                                   and (game.get("status") or {}).get("isScheduled")),
                                  key=lambda game: game.get("date") or "")
                # The video card features live games first, then the next scheduled game.
                live = any(game.get("league") == league and (game.get("status") or {}).get("isLive") for game in games)
                game = upcoming[0] if upcoming and not live else None
                info = countdown_info(game)
                if info:
                    showing = _draw(league, game, info)
                elif showing:
                    _send(["osd-overlay", OVERLAY_ID, "none", ""])
                    showing = False
        except Exception:
            log.exception("Sports countdown update failed")
        time.sleep(1)
