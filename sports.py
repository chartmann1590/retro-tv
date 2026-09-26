"""ArenaPulse Sports Integration & Visual Gameplay Field Engine.

Connects to ArenaPulse REST API at http://10.0.0.110:3000 to fetch real-time
scores, deep game details, visual play-by-play events, team statistics, and
sports news across NFL, College Football, MLB, NBA, WNBA, NHL, and Soccer leagues.
Includes an SVG gameplay field generator for Football, Baseball, Basketball,
Hockey, and Soccer, plus announcer TTS speech synthesis via Kokoro.
"""
import html
import json
import logging
import os
import re
import threading
import time
import urllib.parse
import urllib.request

import config

log = logging.getLogger("retro-tv.sports")

# In-memory TTL caches
_cache = {}
_cache_lock = threading.RLock()


def _get_json(url, timeout=10, ttl=30):
    now = time.monotonic()
    with _cache_lock:
        if url in _cache:
            val, expire = _cache[url]
            if now < expire:
                return val

    req = urllib.request.Request(
        url,
        headers={"User-Agent": "RetroTV/1.0 (Sports Desk)"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    with _cache_lock:
        _cache[url] = (data, now + ttl)
    return data


def _post_binary(url, payload, timeout=25):
    data_bytes = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data_bytes,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "RetroTV/1.0 (Sports Announcer)"
        }
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        content_type = resp.headers.get("Content-Type", "audio/wav")
        return resp.read(), content_type


def get_base_url():
    return getattr(config, "ARENAPULSE_URL", "http://localhost:3000").rstrip("/")


def get_leagues():
    url = f"{get_base_url()}/api/leagues"
    try:
        data = _get_json(url, ttl=300)
        return data.get("leagues", [])
    except Exception as e:
        log.warning("get_leagues failed: %s", e)
        return []


def get_all_scores(force_refresh=False):
    """Fetch aggregated scoreboard across all leagues."""
    url = f"{get_base_url()}/api/scores/all"
    if force_refresh:
        with _cache_lock:
            _cache.pop(url, None)
    try:
        return _get_json(url, ttl=30)
    except Exception as e:
        log.warning("get_all_scores failed: %s", e)
        return {"stats": {"totalGames": 0, "liveTotal": 0, "upcomingTotal": 0, "finalTotal": 0}, "leagues": [], "games": []}


def get_league_scores(league, sport=None, date=None):
    params = {"league": league}
    if sport:
        params["sport"] = sport
    if date:
        params["date"] = date
    qs = urllib.parse.urlencode(params)
    url = f"{get_base_url()}/api/scores?{qs}"
    try:
        return _get_json(url, ttl=30)
    except Exception as e:
        log.warning("get_league_scores failed for %s: %s", league, e)
        return {"events": []}


def get_game_detail(league, game_id, sport=None, force_refresh=False):
    """Fetch deep game details including plays, visualPlays, boxscore, odds, venue."""
    if sport:
        url = f"{get_base_url()}/api/game/{urllib.parse.quote(sport)}/{urllib.parse.quote(league)}/{urllib.parse.quote(str(game_id))}"
    else:
        url = f"{get_base_url()}/api/game/{urllib.parse.quote(league)}/{urllib.parse.quote(str(game_id))}"

    if force_refresh:
        with _cache_lock:
            _cache.pop(url, None)
    try:
        return _get_json(url, ttl=20)
    except Exception as e:
        log.warning("get_game_detail failed for %s/%s: %s", league, game_id, e)
        return {}


def get_news(league="nfl", sport=None):
    params = {"league": league}
    if sport:
        params["sport"] = sport
    qs = urllib.parse.urlencode(params)
    url = f"{get_base_url()}/api/news?{qs}"
    try:
        data = _get_json(url, ttl=300)
        return data.get("articles", [])
    except Exception as e:
        log.warning("get_news failed for %s: %s", league, e)
        return []


def get_all_news():
    """Aggregate top news across major sports."""
    articles = []
    seen = set()
    for lg in ["nfl", "mlb", "college-football", "nba", "nhl", "eng.1"]:
        try:
            for art in get_news(league=lg)[:4]:
                aid = art.get("id") or art.get("headline")
                if aid not in seen:
                    seen.add(aid)
                    art["league"] = lg
                    articles.append(art)
        except Exception:
            pass
    return articles


def check_tts_health():
    url = f"{get_base_url()}/api/tts/health"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "RetroTV/1.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return bool(data.get("available"))
    except Exception:
        return False


def synthesize_speech(text, voice=None, speed=1.05):
    """Synthesize play-by-play commentary or news text using ArenaPulse Kokoro TTS."""
    if not voice:
        voice = getattr(config, "TTS_VOICE_SPORTS", "am_michael")

    clean_text = (text or "").strip()
    if not clean_text:
        raise ValueError("empty speech text")

    # Max length allowed by Kokoro endpoint is 1000 characters
    if len(clean_text) > 950:
        clean_text = clean_text[:940].rsplit(" ", 1)[0] + "..."

    url = f"{get_base_url()}/api/tts"
    payload = {
        "text": clean_text,
        "voice": voice,
        "speed": float(speed)
    }
    return _post_binary(url, payload, timeout=30)


# =========================================================================
# COLOR & FORMATTING HELPERS
# =========================================================================

def _norm_hex(color_val, fallback="334466"):
    if not color_val:
        return "#" + fallback.lstrip("#")
    val = str(color_val).strip()
    if not val.startswith("#"):
        val = "#" + val
    if len(val) in (4, 7):
        return val
    return "#" + fallback.lstrip("#")


def _esc(val):
    return html.escape(str(val or ""))


# =========================================================================
# GAMEPLAY FIELD SVG GENERATORS
# =========================================================================

def generate_football_field_svg(home_team, away_team, situation=None, play=None, width=1100, height=420):
    """Generate high-definition SVG of an American football gridiron.
    
    Shows team-painted endzones, 10-yard lines, hash marks, line of scrimmage,
    first down yellow marker, ball position, red zone tint, and down & distance.
    """
    situation = situation or {}
    home_name = home_team.get("abbreviation") or home_team.get("shortDisplayName") or home_team.get("name") or "HOME"
    away_name = away_team.get("abbreviation") or away_team.get("shortDisplayName") or away_team.get("name") or "AWAY"
    home_color = _norm_hex(home_team.get("color"), "ff8200")
    away_color = _norm_hex(away_team.get("color"), "af5c37")

    # Extract yard line (0 to 100). 0 = away goal line, 100 = home goal line
    yard_line = 50
    down = situation.get("down")
    distance = situation.get("distance")
    is_red_zone = situation.get("isRedZone", False)
    down_dist_text = situation.get("downDistanceText") or ""

    if play and isinstance(play, dict):
        start = play.get("start") or {}
        end = play.get("end") or {}
        down = start.get("down") or down
        distance = start.get("distance") or distance
        down_dist_text = end.get("downDistanceText") or start.get("downDistanceText") or down_dist_text
        if "yardLine" in start:
            yard_line = start["yardLine"]
        elif "yardLine" in end:
            yard_line = end["yardLine"]

    # Field coordinates: margin 50 on left/right for endzones (each 70px)
    # Playing field: 100 yards across 840px (8.4px per yard)
    field_x = 130
    field_w = 840
    field_y = 50
    field_h = 320
    px_per_yd = field_w / 100.0

    ball_x = field_x + (yard_line * px_per_yd)
    first_down_x = ball_x + ((distance or 10) * px_per_yd)
    if first_down_x > field_x + field_w:
        first_down_x = field_x + field_w

    svg_parts = [
        f'<svg viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg" style="width:100%;height:100%;border-radius:10px;overflow:hidden;box-shadow:0 8px 30px rgba(0,0,0,0.6)">',
        # Background / Sideline turf border
        f'<rect width="{width}" height="{height}" fill="#0b1b10"/>',
        # Outer track / turf apron
        f'<rect x="40" y="30" width="1020" height="360" rx="8" fill="#133d1c" stroke="#2a6635" stroke-width="2"/>',
        # Left Endzone (Away Team)
        f'<rect x="60" y="{field_y}" width="70" height="{field_h}" fill="{away_color}"/>',
        f'<text x="95" y="{field_y + field_h/2}" font-family="Arial,sans-serif" font-weight="900" font-size="22" fill="#ffffff" text-anchor="middle" transform="rotate(-90 95 {field_y + field_h/2})" letter-spacing="4">{_esc(away_name.upper()[:8])}</text>',
        # Right Endzone (Home Team)
        f'<rect x="{field_x + field_w}" y="{field_y}" width="70" height="{field_h}" fill="{home_color}"/>',
        f'<text x="{field_x + field_w + 35}" y="{field_y + field_h/2}" font-family="Arial,sans-serif" font-weight="900" font-size="22" fill="#ffffff" text-anchor="middle" transform="rotate(90 {field_x + field_w + 35} {field_y + field_h/2})" letter-spacing="4">{_esc(home_name.upper()[:8])}</text>',
        # Main Playing Field (Green grass with alternating 5-yard mowed stripes)
        f'<rect x="{field_x}" y="{field_y}" width="{field_w}" height="{field_h}" fill="#195427"/>'
    ]

    # Alternating 5-yard stripes
    for yd in range(0, 100, 10):
        sx = field_x + (yd * px_per_yd)
        sw = 5 * px_per_yd
        svg_parts.append(f'<rect x="{sx}" y="{field_y}" width="{sw}" height="{field_h}" fill="#1d5f2c" opacity="0.6"/>')

    # Red zone tint (0-20yd and 80-100yd)
    if is_red_zone or yard_line <= 20:
        svg_parts.append(f'<rect x="{field_x}" y="{field_y}" width="{20 * px_per_yd}" height="{field_h}" fill="#e50914" opacity="0.18"/>')
    if is_red_zone or yard_line >= 80:
        svg_parts.append(f'<rect x="{field_x + 80 * px_per_yd}" y="{field_y}" width="{20 * px_per_yd}" height="{field_h}" fill="#e50914" opacity="0.18"/>')

    # Yard lines every 5 yards, numbers every 10 yards
    for yd in range(0, 105, 5):
        lx = field_x + (yd * px_per_yd)
        is_major = (yd % 10 == 0)
        line_col = "#ffffff" if is_major else "#a1dca9"
        line_w = 3 if is_major else 1.5
        line_op = "0.9" if is_major else "0.4"
        svg_parts.append(f'<line x1="{lx}" y1="{field_y}" x2="{lx}" y2="{field_y + field_h}" stroke="{line_col}" stroke-width="{line_w}" stroke-opacity="{line_op}"/>')

        # Yard numbers (10, 20, 30, 40, 50, 40, 30, 20, 10)
        if is_major and 0 < yd < 100:
            num = yd if yd <= 50 else (100 - yd)
            svg_parts.append(f'<text x="{lx}" y="{field_y + 35}" font-family="Arial,sans-serif" font-weight="bold" font-size="16" fill="#ffffff" fill-opacity="0.85" text-anchor="middle">{num}</text>')
            svg_parts.append(f'<text x="{lx}" y="{field_y + field_h - 20}" font-family="Arial,sans-serif" font-weight="bold" font-size="16" fill="#ffffff" fill-opacity="0.85" text-anchor="middle">{num}</text>')

        # Hash marks
        if 0 < yd < 100:
            svg_parts.append(f'<line x1="{lx}" y1="{field_y + field_h * 0.38}" x2="{lx + 6}" y2="{field_y + field_h * 0.38}" stroke="#ffffff" stroke-width="1.5" stroke-opacity="0.6"/>')
            svg_parts.append(f'<line x1="{lx}" y1="{field_y + field_h * 0.62}" x2="{lx + 6}" y2="{field_y + field_h * 0.62}" stroke="#ffffff" stroke-width="1.5" stroke-opacity="0.6"/>')

    # Yellow First Down Line
    if 0 <= first_down_x <= field_x + field_w:
        svg_parts.append(f'<line x1="{first_down_x}" y1="{field_y}" x2="{first_down_x}" y2="{field_y + field_h}" stroke="#ffdf00" stroke-width="4" stroke-dasharray="8,4"/>')

    # Blue Line of Scrimmage
    if 0 <= ball_x <= field_x + field_w:
        svg_parts.append(f'<line x1="{ball_x}" y1="{field_y}" x2="{ball_x}" y2="{field_y + field_h}" stroke="#2196f3" stroke-width="4.5"/>')
        # Football Icon / Ball Marker
        ball_y = field_y + field_h / 2
        svg_parts.append(f'''
        <g transform="translate({ball_x}, {ball_y})">
          <ellipse rx="15" ry="9" fill="#7a3e1d" stroke="#f8cb63" stroke-width="2"/>
          <line x1="-12" y1="0" x2="12" y2="0" stroke="#ffffff" stroke-width="1.5"/>
          <line x1="-5" y1="-4" x2="-5" y2="4" stroke="#ffffff" stroke-width="1.5"/>
          <line x1="0" y1="-4" x2="0" y2="4" stroke="#ffffff" stroke-width="1.5"/>
          <line x1="5" y1="-4" x2="5" y2="4" stroke="#ffffff" stroke-width="1.5"/>
          <circle cx="0" cy="0" r="19" fill="none" stroke="#f8cb63" stroke-width="2" opacity="0.8">
            <animate attributeName="r" values="17;24;17" dur="1.8s" repeatCount="indefinite"/>
            <animate attributeName="opacity" values="0.8;0.1;0.8" dur="1.8s" repeatCount="indefinite"/>
          </circle>
        </g>
        ''')

    # Down & Distance Situation Banner at bottom
    if down_dist_text or down:
        def _ord(n):
            if n == 1: return "1st"
            if n == 2: return "2nd"
            if n == 3: return "3rd"
            if n == 4: return "4th"
            return f"{n}th"
        label = down_dist_text or f"{_ord(down)} & {distance} YDS"
        svg_parts.append(f'''
        <g transform="translate({width/2}, {height - 24})">
          <rect x="-180" y="-16" width="360" height="30" rx="15" fill="#0d1829" stroke="#f8cb63" stroke-width="1.5"/>
          <text x="0" y="4" font-family="Arial,sans-serif" font-weight="900" font-size="14" fill="#f8cb63" text-anchor="middle" letter-spacing="1">🏈 {html.escape(label.upper())}</text>
        </g>
        ''')

    svg_parts.append('</svg>')
    return "".join(svg_parts)


def generate_baseball_field_svg(home_team, away_team, situation=None, play=None, count=None, outs=None, width=1100, height=420):
    """Generate high-definition SVG of a baseball diamond with outfield & runners."""
    situation = situation or {}
    home_name = home_team.get("shortDisplayName") or "HOME"
    away_name = away_team.get("shortDisplayName") or "AWAY"
    home_color = _norm_hex(home_team.get("color"), "ba0c2f")

    # Runners on base
    on_first = situation.get("onFirst", False)
    on_second = situation.get("onSecond", False)
    on_third = situation.get("onThird", False)

    # Balls, Strikes, Outs
    b = 0
    s = 0
    o = 0
    if count and isinstance(count, dict):
        b = count.get("balls", 0)
        s = count.get("strikes", 0)
    elif "count" in situation and isinstance(situation["count"], dict):
        b = situation["count"].get("balls", 0)
        s = situation["count"].get("strikes", 0)

    if outs is not None:
        o = int(outs)
    elif "outs" in situation:
        o = int(situation.get("outs") or 0)

    # Field coordinates
    cx = width / 2.0
    cy = height - 70  # Home plate
    diamond_size = 110  # Base distance in px

    # Base coords
    home_p = (cx, cy)
    first_p = (cx + diamond_size, cy - diamond_size)
    second_p = (cx, cy - diamond_size * 2)
    third_p = (cx - diamond_size, cy - diamond_size)
    mound_p = (cx, cy - diamond_size)

    svg_parts = [
        f'<svg viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg" style="width:100%;height:100%;border-radius:10px;overflow:hidden;box-shadow:0 8px 30px rgba(0,0,0,0.6)">',
        f'<rect width="{width}" height="{height}" fill="#081829"/>',
        # Outfield Grass Arc
        f'<path d="M {cx - 380} {cy - 30} Q {cx} {cy - 480} {cx + 380} {cy - 30} L {cx} {cy} Z" fill="#1b6329" stroke="#258238" stroke-width="3"/>',
        # Outfield cut grass circular stripes
        f'<path d="M {cx - 300} {cy - 30} Q {cx} {cy - 380} {cx + 300} {cy - 30}" fill="none" stroke="#258238" stroke-width="32" opacity="0.35"/>',
        # Infield Dirt Arc & Diamond
        f'<path d="M {cx - 180} {cy - 20} Q {cx} {cy - 300} {cx + 180} {cy - 20} L {cx} {cy} Z" fill="#a46d3e"/>',
        # Infield Grass Diamond (inner turf)
        f'<polygon points="{home_p[0]},{home_p[1]-15} {first_p[0]-15},{first_p[1]} {second_p[0]},{second_p[1]+15} {third_p[0]+15},{third_p[1]}" fill="#227431"/>',
        # Foul lines
        f'<line x1="{home_p[0]}" y1="{home_p[1]}" x2="{cx - 370}" y2="{cy - 30}" stroke="#ffffff" stroke-width="2.5"/>',
        f'<line x1="{home_p[0]}" y1="{home_p[1]}" x2="{cx + 370}" y2="{cy - 30}" stroke="#ffffff" stroke-width="2.5"/>',
        # Pitcher\'s Mound
        f'<circle cx="{mound_p[0]}" cy="{mound_p[1]}" r="18" fill="#8e582d" stroke="#c48a58" stroke-width="2"/>',
        f'<rect x="{mound_p[0]-6}" y="{mound_p[1]-2}" width="12" height="4" fill="#ffffff"/>'
    ]

    # Home Plate
    svg_parts.append(f'''
    <polygon points="{home_p[0]},{home_p[1]-6} {home_p[0]+8},{home_p[1]} {home_p[0]+8},{home_p[1]+8} {home_p[0]-8},{home_p[1]+8} {home_p[0]-8},{home_p[1]}" fill="#ffffff"/>
    ''')

    # Bases (1st, 2nd, 3rd) with active runner glow
    def _base(pt, is_runner, label):
        fill = "#f8cb63" if is_runner else "#ffffff"
        stroke = "#e50914" if is_runner else "#334466"
        sw = 3 if is_runner else 1.5
        pulse = ""
        if is_runner:
            pulse = f'<circle cx="{pt[0]}" cy="{pt[1]}" r="16" fill="none" stroke="#f8cb63" stroke-width="2"><animate attributeName="r" values="12;20;12" dur="1.5s" repeatCount="indefinite"/><animate attributeName="opacity" values="0.9;0.2;0.9" dur="1.5s" repeatCount="indefinite"/></circle>'
        return f'''
        <g>
          {pulse}
          <rect x="{pt[0]-9}" y="{pt[1]-9}" width="18" height="18" transform="rotate(45 {pt[0]} {pt[1]})" fill="{fill}" stroke="{stroke}" stroke-width="{sw}"/>
          <text x="{pt[0]}" y="{pt[1]+24}" font-family="Arial,sans-serif" font-weight="bold" font-size="12" fill="#eff2e9" text-anchor="middle">{label}</text>
        </g>
        '''

    svg_parts.append(_base(first_p, on_first, "1ST"))
    svg_parts.append(_base(second_p, on_second, "2ND"))
    svg_parts.append(_base(third_p, on_third, "3RD"))

    # Count & Outs Scoreboard Bug (Top Right)
    svg_parts.append(f'''
    <g transform="translate({width - 230}, 30)">
      <rect width="190" height="90" rx="8" fill="#101a2b" stroke="#253a56" stroke-width="1.5"/>
      <text x="15" y="26" font-family="Arial,sans-serif" font-weight="bold" font-size="13" fill="#8e9eaf">BALLS</text>
      <circle cx="95" cy="22" r="6" fill="{'#2ecc71' if b>=1 else '#253549'}"/>
      <circle cx="115" cy="22" r="6" fill="{'#2ecc71' if b>=2 else '#253549'}"/>
      <circle cx="135" cy="22" r="6" fill="{'#2ecc71' if b>=3 else '#253549'}"/>
      <circle cx="155" cy="22" r="6" fill="{'#2ecc71' if b>=4 else '#253549'}"/>

      <text x="15" y="52" font-family="Arial,sans-serif" font-weight="bold" font-size="13" fill="#8e9eaf">STRIKES</text>
      <circle cx="95" cy="48" r="6" fill="{'#e74c3c' if s>=1 else '#253549'}"/>
      <circle cx="115" cy="48" r="6" fill="{'#e74c3c' if s>=2 else '#253549'}"/>
      <circle cx="135" cy="48" r="6" fill="{'#e74c3c' if s>=3 else '#253549'}"/>

      <text x="15" y="76" font-family="Arial,sans-serif" font-weight="bold" font-size="13" fill="#8e9eaf">OUTS</text>
      <circle cx="95" cy="72" r="6" fill="{'#f1c40f' if o>=1 else '#253549'}"/>
      <circle cx="115" cy="72" r="6" fill="{'#f1c40f' if o>=2 else '#253549'}"/>
      <circle cx="135" cy="72" r="6" fill="{'#f1c40f' if o>=3 else '#253549'}"/>
    </g>
    ''')

    # Matchup summary tag (Top Left)
    svg_parts.append(f'''
    <g transform="translate(40, 30)">
      <rect width="240" height="46" rx="8" fill="#101a2b" stroke="#253a56" stroke-width="1.5"/>
      <text x="20" y="28" font-family="Arial,sans-serif" font-weight="900" font-size="15" fill="#f8cb63">⚾ {html.escape(away_name.upper())} @ {html.escape(home_name.upper())}</text>
    </g>
    ''')

    svg_parts.append('</svg>')
    return "".join(svg_parts)


def generate_basketball_court_svg(home_team, away_team, situation=None, status=None, width=1100, height=420):
    """Generate high-definition SVG of a hardwood basketball court."""
    home_name = home_team.get("shortDisplayName") or "HOME"
    away_name = away_team.get("shortDisplayName") or "AWAY"
    home_color = _norm_hex(home_team.get("color"), "007a33")
    away_color = _norm_hex(away_team.get("color"), "ba0c2f")

    court_x = 70
    court_y = 40
    court_w = 960
    court_h = 340
    cx = court_x + court_w / 2.0
    cy = court_y + court_h / 2.0

    svg_parts = [
        f'<svg viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg" style="width:100%;height:100%;border-radius:10px;overflow:hidden;box-shadow:0 8px 30px rgba(0,0,0,0.6)">',
        f'<rect width="{width}" height="{height}" fill="#0a121e"/>',
        # Outer court apron
        f'<rect x="50" y="25" width="1000" height="370" rx="8" fill="#1c1611" stroke="#332418" stroke-width="3"/>',
        # Hardwood floor
        f'<rect x="{court_x}" y="{court_y}" width="{court_w}" height="{court_h}" fill="#c68b59"/>'
    ]

    # Parquet floor planks pattern
    for i in range(court_x, court_x + court_w, 40):
        svg_parts.append(f'<line x1="{i}" y1="{court_y}" x2="{i}" y2="{court_y + court_h}" stroke="#b57a48" stroke-width="1.5" opacity="0.4"/>')

    # Court border
    svg_parts.append(f'<rect x="{court_x}" y="{court_y}" width="{court_w}" height="{court_h}" fill="none" stroke="#ffffff" stroke-width="3"/>')
    # Center court line
    svg_parts.append(f'<line x1="{cx}" y1="{court_y}" x2="{cx}" y2="{court_y + court_h}" stroke="#ffffff" stroke-width="3"/>')
    # Center court circle
    svg_parts.append(f'<circle cx="{cx}" cy="{cy}" r="50" fill="{home_color}" fill-opacity="0.35" stroke="#ffffff" stroke-width="3"/>')
    svg_parts.append(f'<text x="{cx}" y="{cy + 6}" font-family="Arial,sans-serif" font-weight="900" font-size="18" fill="#ffffff" text-anchor="middle" opacity="0.9">{_esc(home_name.upper()[:6])}</text>')

    # Left Key (Away paint)
    svg_parts.append(f'<rect x="{court_x}" y="{cy - 60}" width="160" height="120" fill="{away_color}" fill-opacity="0.6" stroke="#ffffff" stroke-width="2.5"/>')
    svg_parts.append(f'<path d="M {court_x + 160} {cy - 50} A 50 50 0 0 1 {court_x + 160} {cy + 50}" fill="none" stroke="#ffffff" stroke-width="2.5"/>')
    # Left 3-point arc
    svg_parts.append(f'<path d="M {court_x} {court_y + 35} L {court_x + 50} {court_y + 35} A 190 190 0 0 1 {court_x + 50} {court_y + court_h - 35} L {court_x} {court_y + court_h - 35}" fill="none" stroke="#ffffff" stroke-width="3"/>')
    # Left Basket / Backboard
    svg_parts.append(f'<line x1="{court_x + 25}" y1="{cy - 20}" x2="{court_x + 25}" y2="{cy + 20}" stroke="#ffffff" stroke-width="4"/>')
    svg_parts.append(f'<circle cx="{court_x + 35}" cy="{cy}" r="10" fill="none" stroke="#ff5722" stroke-width="3"/>')

    # Right Key (Home paint)
    svg_parts.append(f'<rect x="{court_x + court_w - 160}" y="{cy - 60}" width="160" height="120" fill="{home_color}" fill-opacity="0.6" stroke="#ffffff" stroke-width="2.5"/>')
    svg_parts.append(f'<path d="M {court_x + court_w - 160} {cy - 50} A 50 50 0 0 0 {court_x + court_w - 160} {cy + 50}" fill="none" stroke="#ffffff" stroke-width="2.5"/>')
    # Right 3-point arc
    svg_parts.append(f'<path d="M {court_x + court_w} {court_y + 35} L {court_x + court_w - 50} {court_y + 35} A 190 190 0 0 0 {court_x + court_w - 50} {court_y + court_h - 35} L {court_x + court_w} {court_y + court_h - 35}" fill="none" stroke="#ffffff" stroke-width="3"/>')
    # Right Basket / Backboard
    svg_parts.append(f'<line x1="{court_x + court_w - 25}" y1="{cy - 20}" x2="{court_x + court_w - 25}" y2="{cy + 20}" stroke="#ffffff" stroke-width="4"/>')
    svg_parts.append(f'<circle cx="{court_x + court_w - 35}" cy="{cy}" r="10" fill="none" stroke="#ff5722" stroke-width="3"/>')

    svg_parts.append('</svg>')
    return "".join(svg_parts)


def generate_soccer_pitch_svg(home_team, away_team, situation=None, status=None, width=1100, height=420):
    """Generate high-definition SVG of an association football (soccer) pitch."""
    home_name = home_team.get("shortDisplayName") or "HOME"
    away_name = away_team.get("shortDisplayName") or "AWAY"

    pitch_x = 70
    pitch_y = 40
    pitch_w = 960
    pitch_h = 340
    cx = pitch_x + pitch_w / 2.0
    cy = pitch_y + pitch_h / 2.0

    svg_parts = [
        f'<svg viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg" style="width:100%;height:100%;border-radius:10px;overflow:hidden;box-shadow:0 8px 30px rgba(0,0,0,0.6)">',
        f'<rect width="{width}" height="{height}" fill="#081829"/>',
        f'<rect x="50" y="25" width="1000" height="370" rx="8" fill="#143c1c" stroke="#256430" stroke-width="2"/>',
        f'<rect x="{pitch_x}" y="{pitch_y}" width="{pitch_w}" height="{pitch_h}" fill="#1a5327"/>'
    ]

    # Alternating mowed grass strips
    stripe_w = pitch_w / 12.0
    for i in range(12):
        if i % 2 == 1:
            svg_parts.append(f'<rect x="{pitch_x + i * stripe_w}" y="{pitch_y}" width="{stripe_w}" height="{pitch_h}" fill="#1f622e" opacity="0.6"/>')

    # Pitch markings (Touchlines, halfway line, center circle)
    svg_parts.append(f'<rect x="{pitch_x}" y="{pitch_y}" width="{pitch_w}" height="{pitch_h}" fill="none" stroke="#ffffff" stroke-width="2.5"/>')
    svg_parts.append(f'<line x1="{cx}" y1="{pitch_y}" x2="{cx}" y2="{pitch_y + pitch_h}" stroke="#ffffff" stroke-width="2.5"/>')
    svg_parts.append(f'<circle cx="{cx}" cy="{cy}" r="55" fill="none" stroke="#ffffff" stroke-width="2.5"/>')
    svg_parts.append(f'<circle cx="{cx}" cy="{cy}" r="4" fill="#ffffff"/>')

    # Left Penalty Box & Goal
    svg_parts.append(f'<rect x="{pitch_x}" y="{cy - 85}" width="140" height="170" fill="none" stroke="#ffffff" stroke-width="2.5"/>')
    svg_parts.append(f'<rect x="{pitch_x}" y="{cy - 45}" width="50" height="90" fill="none" stroke="#ffffff" stroke-width="2.5"/>')
    svg_parts.append(f'<circle cx="{pitch_x + 95}" cy="{cy}" r="3.5" fill="#ffffff"/>')
    svg_parts.append(f'<path d="M {pitch_x + 140} {cy - 35} A 45 45 0 0 1 {pitch_x + 140} {cy + 35}" fill="none" stroke="#ffffff" stroke-width="2.5"/>')
    svg_parts.append(f'<rect x="{pitch_x - 15}" y="{cy - 30}" width="15" height="60" fill="#ffffff" fill-opacity="0.3" stroke="#ffffff" stroke-width="2"/>')

    # Right Penalty Box & Goal
    svg_parts.append(f'<rect x="{pitch_x + pitch_w - 140}" y="{cy - 85}" width="140" height="170" fill="none" stroke="#ffffff" stroke-width="2.5"/>')
    svg_parts.append(f'<rect x="{pitch_x + pitch_w - 50}" y="{cy - 45}" width="50" height="90" fill="none" stroke="#ffffff" stroke-width="2.5"/>')
    svg_parts.append(f'<circle cx="{pitch_x + pitch_w - 95}" cy="{cy}" r="3.5" fill="#ffffff"/>')
    svg_parts.append(f'<path d="M {pitch_x + pitch_w - 140} {cy - 35} A 45 45 0 0 0 {pitch_x + pitch_w - 140} {cy + 35}" fill="none" stroke="#ffffff" stroke-width="2.5"/>')
    svg_parts.append(f'<rect x="{pitch_x + pitch_w}" y="{cy - 30}" width="15" height="60" fill="#ffffff" fill-opacity="0.3" stroke="#ffffff" stroke-width="2"/>')

    svg_parts.append('</svg>')
    return "".join(svg_parts)


def generate_hockey_rink_svg(home_team, away_team, situation=None, status=None, width=1100, height=420):
    """Generate high-definition SVG of an ice hockey rink."""
    home_name = home_team.get("shortDisplayName") or "HOME"
    away_name = away_team.get("shortDisplayName") or "AWAY"

    rink_x = 70
    rink_y = 40
    rink_w = 960
    rink_h = 340
    cx = rink_x + rink_w / 2.0
    cy = rink_y + rink_h / 2.0

    svg_parts = [
        f'<svg viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg" style="width:100%;height:100%;border-radius:10px;overflow:hidden;box-shadow:0 8px 30px rgba(0,0,0,0.6)">',
        f'<rect width="{width}" height="{height}" fill="#081423"/>',
        # Outer boards
        f'<rect x="{rink_x}" y="{rink_y}" width="{rink_w}" height="{rink_h}" rx="65" fill="#e8f3fa" stroke="#d52b1e" stroke-width="4"/>',
        # Center Red Line
        f'<line x1="{cx}" y1="{rink_y}" x2="{cx}" y2="{rink_y + rink_h}" stroke="#c8102e" stroke-width="5" stroke-dasharray="12,6"/>',
        f'<circle cx="{cx}" cy="{cy}" r="45" fill="none" stroke="#0033a0" stroke-width="3"/>',
        f'<circle cx="{cx}" cy="{cy}" r="4" fill="#0033a0"/>',
        # Blue Lines
        f'<line x1="{cx - 150}" y1="{rink_y}" x2="{cx - 150}" y2="{rink_y + rink_h}" stroke="#0033a0" stroke-width="6"/>',
        f'<line x1="{cx + 150}" y1="{rink_y}" x2="{cx + 150}" y2="{rink_y + rink_h}" stroke="#0033a0" stroke-width="6"/>',
        # Goal lines & creases (Left & Right)
        f'<line x1="{rink_x + 65}" y1="{rink_y + 15}" x2="{rink_x + 65}" y2="{rink_y + rink_h - 15}" stroke="#c8102e" stroke-width="3"/>',
        f'<path d="M {rink_x + 65} {cy - 25} A 25 25 0 0 1 {rink_x + 65} {cy + 25}" fill="#69b3e7" fill-opacity="0.4" stroke="#c8102e" stroke-width="2.5"/>',
        f'<line x1="{rink_x + rink_w - 65}" y1="{rink_y + 15}" x2="{rink_x + rink_w - 65}" y2="{rink_y + rink_h - 15}" stroke="#c8102e" stroke-width="3"/>',
        f'<path d="M {rink_x + rink_w - 65} {cy - 25} A 25 25 0 0 0 {rink_x + rink_w - 65} {cy + 25}" fill="#69b3e7" fill-opacity="0.4" stroke="#c8102e" stroke-width="2.5"/>',
        # Faceoff circles
        f'<circle cx="{rink_x + 200}" cy="{cy - 85}" r="35" fill="none" stroke="#c8102e" stroke-width="2.5"/>',
        f'<circle cx="{rink_x + 200}" cy="{cy + 85}" r="35" fill="none" stroke="#c8102e" stroke-width="2.5"/>',
        f'<circle cx="{rink_x + rink_w - 200}" cy="{cy - 85}" r="35" fill="none" stroke="#c8102e" stroke-width="2.5"/>',
        f'<circle cx="{rink_x + rink_w - 200}" cy="{cy + 85}" r="35" fill="none" stroke="#c8102e" stroke-width="2.5"/>'
    ]

    svg_parts.append('</svg>')
    return "".join(svg_parts)


def generate_field_svg(sport, home_team, away_team, situation=None, play=None, status=None, count=None, outs=None, width=1100, height=420):
    """Master dispatcher: returns appropriate SVG gameplay field for the sport."""
    sport = (sport or "").lower()
    if sport in ("football", "nfl", "college-football"):
        return generate_football_field_svg(home_team, away_team, situation=situation, play=play, width=width, height=height)
    elif sport in ("baseball", "mlb"):
        return generate_baseball_field_svg(home_team, away_team, situation=situation, play=play, count=count, outs=outs, width=width, height=height)
    elif sport in ("basketball", "nba", "wnba", "mens-college-basketball"):
        return generate_basketball_court_svg(home_team, away_team, situation=situation, status=status, width=width, height=height)
    elif sport in ("hockey", "nhl"):
        return generate_hockey_rink_svg(home_team, away_team, situation=situation, status=status, width=width, height=height)
    else:
        # Default soccer / general pitch
        return generate_soccer_pitch_svg(home_team, away_team, situation=situation, status=status, width=width, height=height)


# =========================================================================
# TTS SCRIPT GENERATORS
# =========================================================================

def script_for_news(article):
    """Generate sportscaster narration script for a news headline."""
    headline = (article.get("headline") or "").strip()
    desc = (article.get("description") or "").strip()
    byline = (article.get("byline") or "").strip()

    parts = [headline]
    if desc:
        parts.append(desc[:350])
    if byline:
        parts.append(f"Reported by {byline}.")
    return " ".join(parts)


def script_for_game(game, detail=None):
    """Generate play-by-play or recap narration for a game."""
    home = game.get("homeTeam", {})
    away = game.get("awayTeam", {})
    home_name = home.get("displayName") or "Home"
    away_name = away.get("displayName") or "Away"
    home_score = home.get("score", 0)
    away_score = away.get("score", 0)
    status = game.get("status", {})
    league_name = game.get("leagueName") or game.get("league", "").upper()

    # If game is live
    if status.get("isLive"):
        clock = status.get("displayClock") or ""
        period = status.get("detail") or f"Period {status.get('period', 1)}"
        text = f"Live action in {league_name}: The {away_name} with {away_score}, and the {home_name} with {home_score}. {period} on the game clock."
        if detail and detail.get("visualPlays"):
            recent_play = detail["visualPlays"][-1]
            p_text = recent_play.get("text")
            if p_text:
                text += f" On the latest play: {p_text}"
        return text

    # If game is completed
    if status.get("isFinal") or status.get("completed"):
        winner = home_name if home_score > away_score else away_name
        loser = away_name if home_score > away_score else home_name
        w_score = max(home_score, away_score)
        l_score = min(home_score, away_score)
        text = f"Final score in {league_name}: The {winner} defeat the {loser}, {w_score} to {l_score}."
        if detail and detail.get("scoringPlays"):
            key_play = detail["scoringPlays"][-1].get("text")
            if key_play:
                text += f" Sealing the matchup: {key_play}"
        return text

    # Upcoming game
    date_str = status.get("detail") or "today"
    venue = game.get("venue", {}).get("name") if game.get("venue") else None
    odds = game.get("odds", {}).get("details") if game.get("odds") else None
    text = f"Upcoming {league_name} matchup: The {away_name} take on the {home_name}, scheduled for {date_str}."
    if venue:
        text += f" Live from {venue}."
    if odds:
        text += f" Betting lines currently stand at {odds}."
    return text
