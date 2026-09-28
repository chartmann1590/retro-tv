"""ArenaPulse start countdown and live action graphics on HDMI broadcasts."""
import json
import logging
import math
import socket
import textwrap
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


def scoring_moment(game, detail, previous_score, previous_play):
    """Return a new score event, never replaying a score on initial tune."""
    import sports
    plays = detail.get("visualPlays") or []
    score = ((game.get("awayTeam") or {}).get("score"), (game.get("homeTeam") or {}).get("score"))
    newest = str(plays[-1].get("id")) if plays else ""
    if previous_score is None:
        return None, score, newest
    index = next((i for i, play in enumerate(plays) if str(play.get("id")) == previous_play), -1)
    candidates = plays[index + 1:] if index >= 0 else plays[-1:]
    for play in reversed(candidates):
        event = sports.play_event_type(play, game.get("sport"))
        if event:
            return {"type": event, "text": play.get("text") or "Scoring play"}, score, newest
    if score != previous_score:
        label = "GOAL" if game.get("sport") in ("soccer", "hockey") else "SCORE"
        return {"type": label, "text": "The scoreboard has changed."}, score, newest
    return None, score, newest


def _draw_live(league_name, game, detail, moment=None, moment_age=0):
    away = game.get("awayTeam") or {}
    home = game.get("homeTeam") or {}
    status = game.get("status") or {}
    latest = (detail.get("visualPlays") or [{}])[-1]
    play = _safe(textwrap.shorten(latest.get("text") or "Play-by-play is updating from ArenaPulse.",
                                width=104, placeholder="…"))
    scoreline = _safe(f"{away.get('abbreviation') or 'AWAY'} {away.get('score', 0)}  –  {home.get('abbreviation') or 'HOME'} {home.get('score', 0)}")
    period = _safe(status.get("shortDetail") or status.get("detail") or status.get("displayClock") or "LIVE")
    ass = [
        r"{\an7\pos(36,566)\bord0\shad0\1c&H172A43&\p1}m 0 0 l 1208 0 1208 96 0 96{\p0}",
        r"{\an7\pos(36,566)\bord0\shad0\1c&H3A42E5&\p1}m 0 0 l 7 0 7 96 0 96{\p0}",
        rf"{{\an7\pos(56,578)\fnDejaVu Sans\fs18\b1\bord0\shad0\1c&HFFFFFF&}}● LIVE  {_safe(league_name.upper())}",
        rf"{{\an7\pos(250,571)\fnDejaVu Sans\fs32\b1\bord0\shad0\1c&H63CBF8&}}{scoreline}",
        rf"{{\an9\pos(1220,598)\fnDejaVu Sans\fs20\b1\bord0\shad0\1c&HBED6EA&}}{period[:30]}",
        rf"{{\an7\pos(57,620)\fnDejaVu Sans\fs20\bord0\shad0\1c&HFFFFFF&\clip(57,616,1215,658)}}{play}",
    ]
    if moment:
        pulse = int(100 + 7 * math.sin(moment_age * 9))
        glow = "&H63CBF8&" if int(moment_age * 3) % 2 else "&HFFFFFF&"
        moment_text = _safe(textwrap.shorten(moment["text"], width=78, placeholder="…"))
        ass.extend([
            r"{\an7\pos(165,195)\bord0\shad0\1c&H10234A&\p1}m 0 0 l 950 0 950 190 0 190{\p0}",
            r"{\an7\pos(165,195)\bord0\shad0\1c&H63CBF8&\p1}m 0 0 l 950 0 950 7 0 7{\p0}",
            r"{\an7\pos(165,378)\bord0\shad0\1c&H63CBF8&\p1}m 0 0 l 950 0 950 7 0 7{\p0}",
            rf"{{\an8\pos(640,220)\fnDejaVu Sans\fs24\b1\bord0\shad0\1c&H63CBF8&}}● LIVE SCORING PLAY",
            rf"{{\an8\pos(640,255)\fnDejaVu Sans\fs70\fscx{pulse}\fscy{pulse}\b1\bord0\shad0\1c{glow}}}{_safe(moment['type'])}",
            rf"{{\an8\pos(640,340)\fnDejaVu Sans\fs20\b1\bord0\shad0\1c&HFFFFFF&\clip(195,331,1080,372)}}{moment_text}",
        ])
    return _send(["osd-overlay", OVERLAY_ID, "ass-events", "\n".join(ass), 1280, 720])


def run_loop():
    import livesports
    import playback
    import sports
    import tvguide
    import tvgames
    import tvsports

    last_channel = None
    games = []
    checked_at = 0
    detail_checked_at = 0
    detail = {}
    active_game = None
    last_score = None
    last_play = ""
    moment = None
    moment_until = 0
    showing = False
    while True:
        try:
            channel = playback._current.get("channel")
            league = livesports.league_for_channel(channel) if channel is not None else None
            hidden = (playback._current.get("is_vod") or tvguide.is_visible()
                      or tvsports.is_visible() or tvgames.is_visible())
            if not league or hidden:
                if showing:
                    _send(["osd-overlay", OVERLAY_ID, "none", ""])
                    showing = False
            else:
                now = time.monotonic()
                if channel != last_channel or now - checked_at >= (12 if active_game else 30):
                    result = sports.get_all_scores(force_refresh=bool(active_game))
                    if not result.get("_unavailable"):
                        games = result.get("games") or []
                    checked_at = now
                    last_channel = channel
                live_games = [game for game in games if game.get("league") == league
                              and (game.get("status") or {}).get("isLive")]
                upcoming = sorted((game for game in games if game.get("league") == league
                                   and (game.get("status") or {}).get("isScheduled")),
                                  key=lambda game: game.get("date") or "")
                pin = livesports.pinned_game(channel)
                if pin:
                    chosen = next((game for game in games if game.get("league") == league
                                   and str(game.get("id")) == pin), None)
                    live_games = [chosen] if chosen and (chosen.get("status") or {}).get("isLive") else []
                    upcoming = [chosen] if chosen and (chosen.get("status") or {}).get("isScheduled") else []
                if live_games:
                    game = live_games[0]
                    game_key = f"{league}:{game.get('id')}"
                    if game_key != active_game:
                        active_game = game_key
                        last_score = None
                        last_play = ""
                        detail = {}
                        detail_checked_at = 0
                        moment = None
                    if now - detail_checked_at >= 12:
                        fresh = sports.get_game_detail(league, game["id"], sport=game.get("sport"), force_refresh=True)
                        if fresh:
                            detail = fresh
                        detail_checked_at = now
                        event, last_score, last_play = scoring_moment(game, detail, last_score, last_play)
                        if event:
                            moment = event
                            moment_until = time.monotonic() + 6
                    if moment_until <= time.monotonic():
                        moment = None
                    showing = _draw_live(league, game, detail, moment,
                                         max(0, 6 - (moment_until - time.monotonic())))
                elif upcoming and countdown_info(upcoming[0]):
                    active_game = None
                    showing = _draw(league, upcoming[0], countdown_info(upcoming[0]))
                else:
                    active_game = None
                    if showing:
                        _send(["osd-overlay", OVERLAY_ID, "none", ""])
                        showing = False
        except Exception:
            log.exception("Sports countdown update failed")
        time.sleep(1)
