"""Dedicated Retro Sports Channel: ArenaPulse live data -> continuous broadcast loop.

Renders high-definition broadcast cards (scores, live action with SVG gameplay fields,
odds, leaders, and sports news) using headless chromium, narrates them with ArenaPulse
Kokoro TTS announcer voices, and muxes them into a seamless continuous television loop.
Registers the loop as a synthetic show in SQLite so the Retro-TV scheduler and
playback engine play it as Channel 38 ("Retro Sports").
"""
import asyncio
import html
import json
import logging
import os
import re
import subprocess
import threading
import time
import wave
from datetime import datetime

import config
import database
import scanner
import scheduler
import sports

log = logging.getLogger("retro-tv.livesports")

CARD_W, CARD_H = 1280, 720
COLORS = {
    "bg": "#071322",
    "panel": "#0d1b30",
    "panel2": "#132540",
    "hi": "#f8cb63",
    "txt": "#eff2e9",
    "dim": "#8e9eb3",
    "line": "#203450",
    "red": "#e50914",
    "green": "#2ecc71"
}
_last_center_refresh = 0
_league_signatures = {}


def _deprioritized():
    """Lower subprocess CPU priority so video generation doesn't disrupt live playback."""
    try:
        os.nice(15)
    except Exception:
        pass


def _download_img(url, dest_path, timeout=8):
    if not url:
        return None
    try:
        req = sports.urllib.request.Request(
            url,
            headers={"User-Agent": "RetroTV/1.0 (Sports Artwork)"}
        )
        with sports.urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
            with open(dest_path, "wb") as f:
                f.write(data)
            return dest_path
    except Exception as e:
        log.debug("download img failed for %s: %s", url, e)
        return None


def _card_html(body, extra_css=""):
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{width:{CARD_W}px;height:{CARD_H}px;background:{COLORS['bg']};color:{COLORS['txt']};
font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;overflow:hidden}}
.top-bar{{display:flex;align-items:center;justify-content:space-between;background:linear-gradient(90deg,#0a192f,#142c4f);padding:14px 36px;border-bottom:3px solid {COLORS['hi']};box-shadow:0 4px 18px rgba(0,0,0,0.5)}}
.brand{{display:flex;align-items:center;gap:12px}}
.brand-logo{{font-size:24px;font-weight:900;letter-spacing:2px;color:{COLORS['red']};text-shadow:0 0 10px rgba(229,9,20,0.6);font-style:italic}}
.brand-logo span{{color:{COLORS['hi']}}}
.badge-tag{{background:{COLORS['panel2']};color:{COLORS['hi']};border:1px solid #314d73;border-radius:4px;font-size:11px;font-weight:900;letter-spacing:2px;padding:4px 10px;text-transform:uppercase}}
.badge-live{{background:#800b0b;color:#fff;border:1px solid {COLORS['red']};animation:pulse 1.5s infinite}}
.clock{{font-size:16px;color:{COLORS['hi']};font-weight:bold;letter-spacing:1px}}
.ticker-bar{{position:absolute;bottom:0;left:0;right:0;height:42px;background:#09121d;border-top:2px solid #1e334d;display:flex;align-items:center;padding:0 30px;font-size:13px;color:{COLORS['dim']};font-weight:bold;gap:24px;overflow:hidden}}
.ticker-lead{{color:{COLORS['hi']};letter-spacing:1px;font-weight:900}}
{extra_css}
</style></head><body>{body}</body></html>"""


def _render_card_png(body_html, out_png, extra_css=""):
    tmp_html = out_png + ".tmp.html"
    with open(tmp_html, "w", encoding="utf-8") as f:
        f.write(_card_html(body_html, extra_css))
    try:
        subprocess.run(
            ["chromium", "--headless=new", "--disable-gpu", "--no-sandbox",
             f"--screenshot={out_png}", f"--window-size={CARD_W},{CARD_H}",
             "--virtual-time-budget=2000", "file://" + tmp_html],
            check=True, capture_output=True, timeout=30, preexec_fn=_deprioritized)
    finally:
        if os.path.exists(tmp_html):
            try:
                os.unlink(tmp_html)
            except OSError:
                pass


def _mux_card(image_path, audio_path, out_mp4):
    """Muxes card image and narration audio into a hardware-friendly H.264 MP4."""
    probe = scanner.probe_info(audio_path)
    duration = probe.get("duration") or 5.0
    subprocess.run(
        ["ffmpeg", "-y", "-filter_threads", "1", "-threads", "1",
         "-framerate", "2", "-loop", "1", "-i", image_path, "-i", audio_path,
         "-c:v", "libx264", "-threads:v", "1", "-preset", "ultrafast", "-tune", "stillimage",
         "-profile:v", "main", "-bf", "0", "-r", "2", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "128k", "-t", f"{duration + 0.4:.2f}", out_mp4],
        check=True, capture_output=True, timeout=120, preexec_fn=_deprioritized)


def _render_score_card(title, lines, out_png):
    """Draw a readable broadcast card without starting a Chromium process."""
    from PIL import Image, ImageDraw, ImageFont

    image = Image.new("RGB", (CARD_W, CARD_H), COLORS["bg"])
    draw = ImageDraw.Draw(image)
    regular = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    bold = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    brand_font = ImageFont.truetype(bold, 46)
    title_font = ImageFont.truetype(bold, 34)
    line_font = ImageFont.truetype(regular, 25)
    foot_font = ImageFont.truetype(regular, 19)
    draw.rectangle((0, 0, CARD_W, 110), fill=COLORS["panel"])
    draw.rectangle((0, 106, CARD_W, 110), fill=COLORS["hi"])
    draw.text((42, 29), "RETRO SPORTS", font=brand_font, fill=COLORS["hi"])
    draw.text((42, 140), title[:48], font=title_font, fill=COLORS["txt"])
    y = 220
    for line in lines[:9]:
        # Fit long team names/headlines without clipping the right edge.
        while draw.textlength(line, font=line_font) > CARD_W - 100 and len(line) > 12:
            line = line[:-4] + "..."
        draw.text((52, y), line, font=line_font, fill=COLORS["txt"])
        y += 47
    draw.rectangle((0, CARD_H - 72, CARD_W, CARD_H), fill=COLORS["panel"])
    timestamp = datetime.now(scheduler.TZ).strftime("Updated %b %-d, %-I:%M %p %Z")
    draw.text((42, CARD_H - 49), timestamp, font=foot_font, fill=COLORS["hi"])
    draw.text((850, CARD_H - 49), "Scores and news", font=foot_font, fill=COLORS["dim"])
    image.save(out_png)


def _silent_narration(path, seconds=12):
    with wave.open(path, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(b"\0\0" * (16000 * seconds))


def _score_card_specs(games, articles):
    """Build a small, current lineup even when the HTML renderer is overloaded."""
    live = [g for g in games if g.get("status", {}).get("isLive")]
    other = [g for g in games if g not in live]
    chosen = (live + other)[:8]
    specs = []
    for index in range(0, len(chosen), 4):
        group = chosen[index:index + 4]
        lines = []
        for game in group:
            away = game.get("awayTeam") or {}
            home = game.get("homeTeam") or {}
            status = game.get("status") or {}
            names = f"{away.get('abbreviation') or away.get('displayName') or 'Away'} @ {home.get('abbreviation') or home.get('displayName') or 'Home'}"
            score = f"{away.get('score', 0)} - {home.get('score', 0)}"
            lines.extend((f"{game.get('leagueName') or (game.get('league') or 'Sports').upper()}: {names}   {score}",
                          f"    {status.get('detail') or 'Scheduled'}"))
        specs.append(("Live Scores" if index == 0 else "More Matchups", lines,
                      "Retro Sports scoreboard. " + ". ".join(lines[::2])))
    if articles:
        headlines = [(a.get("headline") or "Sports news") for a in articles[:4]]
        specs.append(("Sports Headlines", headlines, "Retro Sports headlines. " + ". ".join(headlines)))
    if not specs:
        specs.append(("Retro Sports", ["Scores and headlines are updating.",
                                        "Visit the Sports Center for game details."],
                      "Welcome to Retro Sports. Scores and headlines are updating."))
    return specs


def _build_concat_loop(card_mp4s, out_path, target_seconds):
    """Concatenate card MP4s into a continuous loop meeting target_seconds."""
    durations = [scanner.probe_info(p).get("duration") or 4.0 for p in card_mp4s]
    pass_seconds = sum(durations)
    repeats = max(1, round(target_seconds / pass_seconds)) if pass_seconds else 1
    list_path = out_path + ".concat.txt"
    tmp_path = out_path + ".building.mp4"
    with open(list_path, "w", encoding="utf-8") as f:
        for _ in range(repeats):
            for p in card_mp4s:
                f.write(f"file '{os.path.abspath(p)}'\n")
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_path, "-c", "copy", tmp_path],
            check=True, capture_output=True, timeout=300)
        os.replace(tmp_path, out_path)
    finally:
        if os.path.exists(list_path):
            try:
                os.unlink(list_path)
            except OSError:
                pass
        if os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
    return pass_seconds * repeats


def _ensure_sports_channel():
    """Ensure dedicated Retro Sports channel exists in the channel lineup."""
    show_name = getattr(config, "SPORTS_SHOW", "Retro Sports")
    ch_num = getattr(config, "SPORTS_CHANNEL_NUMBER", 38)
    ch_name = getattr(config, "SPORTS_CHANNEL_NAME", "Retro Sports")

    con = database.connect()
    try:
        # Check if already exists
        row = con.execute("SELECT number FROM channels WHERE number=?", (ch_num,)).fetchone()
        if not row:
            # Check if any channel already points to this show
            existing = con.execute(
                "SELECT channel_number FROM channel_sources WHERE source_type='show' AND source_value=?",
                (show_name,)
            ).fetchone()
            if not existing:
                con.execute("""INSERT INTO channels(number, name, enabled, color, ordering, commercial_mode, sort_order)
                               VALUES(?, ?, 1, '#1e7e34', 'sequential', 'off', ?)""",
                            (ch_num, ch_name, ch_num))
                con.execute("""INSERT INTO channel_sources(channel_number, source_type, source_value)
                               VALUES(?, 'show', ?)""", (ch_num, show_name))
                con.commit()
                log.info("Created dedicated sports channel %d: %s", ch_num, ch_name)
    finally:
        con.close()


def ensure_league_channels(leagues):
    """Add one stable, user-editable channel for every ArenaPulse league."""
    con = database.connect()
    created = []
    try:
        con.execute("BEGIN IMMEDIATE")
        used = {r[0] for r in con.execute("SELECT number FROM channels")}
        next_number = max(used | {config.SPORTS_CHANNEL_NUMBER}) + 1
        for league in leagues:
            league_id = league.get("id") or league.get("league")
            if not league_id or not re.fullmatch(r"[a-z0-9.-]+", league_id):
                continue
            show = f"Retro Sports · {league_id}"
            if con.execute("SELECT 1 FROM channel_sources WHERE source_type='show' AND source_value=?", (show,)).fetchone():
                continue
            while next_number in used:
                next_number += 1
            name = f"{league.get('name') or league_id.upper()} Live"
            color = config.AUTO_CHANNEL_COLORS[len(created) % len(config.AUTO_CHANNEL_COLORS)]
            con.execute("""INSERT INTO channels(number,name,enabled,color,ordering,commercial_mode,
                max_repeats_per_day,sort_order) VALUES(?,?,1,?,'sequential','off',288,?)""",
                (next_number, name, color, next_number))
            con.execute("INSERT INTO channel_sources(channel_number,source_type,source_value) VALUES(?,'show',?)",
                        (next_number, show))
            created.append({"number": next_number, "league": league_id})
            used.add(next_number)
            next_number += 1
        con.commit()
    finally:
        con.close()
    return created


def league_for_channel(channel_number):
    con = database.connect()
    try:
        row = con.execute("""SELECT source_value FROM channel_sources
            WHERE channel_number=? AND source_type='show' AND source_value LIKE 'Retro Sports · %'""",
            (channel_number,)).fetchone()
        return row["source_value"].split(" · ", 1)[1] if row else None
    finally:
        con.close()


def _upsert_sports_episode(path, show_name, title, description):
    info = scanner.probe_info(path)
    warn = scanner.compat_warning(info, "episode")
    st = os.stat(path)
    con = database.connect()
    try:
        row = con.execute("SELECT id FROM media_files WHERE path=?", (path,)).fetchone()
        if row:
            media_id = row["id"]
            con.execute("""UPDATE media_files SET size=?,mtime=?,duration=?,container=?,resolution=?,
                width=?,height=?,vcodec=?,acodec=?,bitrate=?,compat_warning=? WHERE id=?""",
                (st.st_size, st.st_mtime, info["duration"], info["container"], info["resolution"],
                 info["width"], info["height"], info["vcodec"], info["acodec"], info["bitrate"], warn, media_id))
        else:
            cur = con.execute("""INSERT INTO media_files(path,kind,size,mtime,duration,container,resolution,
                width,height,vcodec,acodec,bitrate,compat_warning) VALUES(?,'episode',?,?,?,?,?,?,?,?,?,?,?)""",
                (path, st.st_size, st.st_mtime, info["duration"], info["container"], info["resolution"],
                 info["width"], info["height"], info["vcodec"], info["acodec"], info["bitrate"], warn))
            media_id = cur.lastrowid
        con.execute("INSERT OR IGNORE INTO shows(name) VALUES(?)", (show_name,))
        show_id = con.execute("SELECT id FROM shows WHERE name=?", (show_name,)).fetchone()["id"]
        con.execute("""INSERT INTO episodes(media_id,show_id,show_name,season,episode,title,description,meta_source)
            VALUES(?,?,?,1,1,?,?,'generated') ON CONFLICT(media_id) DO UPDATE SET show_id=excluded.show_id,
            show_name=excluded.show_name, season=excluded.season, episode=excluded.episode,
            title=excluded.title, description=excluded.description""",
            (media_id, show_id, show_name, title, description))
        con.commit()
    finally:
        con.close()


# =========================================================================
# CARD BUILDERS
# =========================================================================

def _build_news_cards(articles, out_dir):
    cards = []
    voice = getattr(config, "TTS_VOICE_SPORTS_NEWS", "af_nicole")
    for i, art in enumerate(articles[:4], 1):
        headline = art.get("headline", "Sports News")
        desc = art.get("description", "")
        img_url = art.get("image")
        league = art.get("league", "SPORTS").upper()
        narration = sports.script_for_news(art)

        img_tag = ""
        content_w = "1180px"
        if img_url:
            local_img = os.path.join(out_dir, f"news_art_{i}.jpg")
            if _download_img(img_url, local_img):
                img_tag = f'<div style="width:480px;height:520px;border-radius:10px;overflow:hidden;border:2px solid {COLORS["line"]};box-shadow:0 8px 24px rgba(0,0,0,0.5);margin-left:30px;flex-shrink:0"><img src="file://{local_img}" style="width:100%;height:100%;object-fit:cover"></div>'
                content_w = "680px"

        body = f"""
        <div class="top-bar">
          <div class="brand">
            <span class="brand-logo">RETRO<span>SPORTS</span></span>
            <span class="badge-tag">HEADLINES · {league}</span>
          </div>
          <span class="clock">NEWS WIRE</span>
        </div>
        <div style="display:flex;align-items:center;padding:40px 50px 30px;height:{CARD_H - 120}px">
          <div style="width:{content_w}">
            <div style="font-size:13px;font-weight:900;letter-spacing:3px;color:{COLORS['hi']};margin-bottom:14px">BREAKING SPORTS REPORT</div>
            <div style="font-size:36px;font-weight:900;line-height:1.2;color:#ffffff;margin-bottom:20px;text-shadow:0 2px 8px rgba(0,0,0,0.8)">{html.escape(headline)}</div>
            <div style="font-size:18px;color:{COLORS['dim']};line-height:1.6;margin-bottom:20px">{html.escape(desc[:320])}</div>
            <div style="font-size:13px;color:{COLORS['hi']};font-weight:bold">By {html.escape(art.get('byline') or 'Retro Sports Desk')}</div>
          </div>
          {img_tag}
        </div>
        <div class="ticker-bar">
          <span class="ticker-lead">RETRO SPORTS WIRE</span>
          <span>● UPCOMING & LIVE MATCHUPS ALL DAY</span>
          <span>● INSTANT SCORES & FIELD RADAR</span>
        </div>
        """
        cards.append({
            "name": f"News_{i}",
            "voice": voice,
            "narration": narration,
            "html": body
        })
    return cards


def _build_game_cards(games, out_dir):
    cards = []
    voice = getattr(config, "TTS_VOICE_SPORTS", "am_michael")

    # Select up to 4 exciting games (live first, then close upcoming matchups, then notable finals)
    live_games = [g for g in games if g.get("status", {}).get("isLive")]
    upcoming_games = [g for g in games if g.get("status", {}).get("isScheduled")]
    final_games = [g for g in games if g.get("status", {}).get("isFinal")]

    selected_games = (live_games[:2] + upcoming_games[:3] + final_games[:2])[:6]

    for i, g in enumerate(selected_games, 1):
        league = g.get("league", "nfl")
        gid = g.get("id")
        sport = g.get("sport", "football")
        league_name = g.get("leagueName") or league.upper()
        home = g.get("homeTeam", {})
        away = g.get("awayTeam", {})
        status = g.get("status", {})
        situation = g.get("situation") or {}

        # Fetch deep detail for visual plays or box score
        detail = sports.get_game_detail(league, gid, sport=sport) or {}
        narration = sports.script_for_game(g, detail=detail)
        plays = detail.get("visualPlays") or detail.get("plays") or []
        latest_play = plays[-1] if plays else {}
        play_text = (latest_play.get("text") or "Play-by-play updates when the game begins.")[:185]
        play_clock = latest_play.get("clock") or latest_play.get("time") or "LATEST PLAY"

        # Generate gameplay field SVG
        field_svg = sports.generate_field_svg(
            sport, home, away,
            situation=situation,
            play=latest_play or None,
            status=status,
            width=1160, height=330
        )

        # Download team logos if available
        home_logo_html = ""
        away_logo_html = ""
        if home.get("logo"):
            hl_path = os.path.join(out_dir, f"logo_h_{i}.png")
            if _download_img(home.get("logo"), hl_path):
                home_logo_html = f'<img src="file://{hl_path}" style="width:48px;height:48px;object-fit:contain;margin-right:12px">'
        if away.get("logo"):
            al_path = os.path.join(out_dir, f"logo_a_{i}.png")
            if _download_img(away.get("logo"), al_path):
                away_logo_html = f'<img src="file://{al_path}" style="width:48px;height:48px;object-fit:contain;margin-right:12px">'

        # Status badge & text
        is_live = status.get("isLive")
        is_final = status.get("isFinal")
        if is_live:
            badge_class = "badge-tag badge-live"
            badge_text = "● LIVE"
            status_desc = f"{status.get('displayClock') or ''} {status.get('period', '')}".strip()
        elif is_final:
            badge_class = "badge-tag"
            badge_text = "FINAL SCORE"
            status_desc = "Final"
        else:
            badge_class = "badge-tag"
            badge_text = "MATCHUP PREVIEW"
            status_desc = status.get("detail") or "Scheduled"

        home_rank = f"#{home['rank']} " if home.get("rank") else ""
        away_rank = f"#{away['rank']} " if away.get("rank") else ""
        odds_details = ((g.get("odds") or {}).get("details")) or ""
        venue_name = ((g.get("venue") or {}).get("name")) or "Stadium"
        broadcast_str = ", ".join(g.get("broadcasts") or []) or "TV"
        weather_temp = ((g.get("weather") or {}).get("temperature")) or "72"

        body = f"""
        <div class="top-bar">
          <div class="brand">
            <span class="brand-logo">RETRO<span>SPORTS</span></span>
            <span class="{badge_class}">{badge_text}</span>
            <span style="font-size:13px;color:{COLORS['dim']};font-weight:bold;margin-left:8px">{html.escape(league_name)}</span>
          </div>
          <span class="clock">{html.escape(status_desc)}</span>
        </div>

        <!-- Scoreboard Header Bug -->
        <div style="display:flex;align-items:center;justify-content:space-between;padding:16px 50px;background:#091524;border-bottom:2px solid #1c2e47">
          <!-- Away Team -->
          <div style="display:flex;align-items:center;width:420px">
            {away_logo_html}
            <div>
              <div style="font-size:22px;font-weight:900;color:#fff">{away_rank}{html.escape(away.get('displayName') or 'Away')}</div>
              <div style="font-size:13px;color:{COLORS['dim']}">{html.escape(away.get('recordSummary') or '')}</div>
            </div>
            <div style="margin-left:auto;font-size:38px;font-weight:900;color:{COLORS['hi']};font-family:monospace">{away.get('score', 0) if (is_live or is_final) else '--'}</div>
          </div>

          <div style="text-align:center;padding:0 24px">
            <span style="font-size:18px;font-weight:900;color:#4f6b8f">VS</span>
            <div style="font-size:11px;color:{COLORS['hi']};margin-top:2px;font-weight:bold">{html.escape(odds_details)}</div>
          </div>

          <!-- Home Team -->
          <div style="display:flex;align-items:center;width:420px;justify-content:flex-end">
            <div style="margin-right:auto;font-size:38px;font-weight:900;color:{COLORS['hi']};font-family:monospace">{home.get('score', 0) if (is_live or is_final) else '--'}</div>
            <div style="text-align:right">
              <div style="font-size:22px;font-weight:900;color:#fff">{home_rank}{html.escape(home.get('displayName') or 'Home')}</div>
              <div style="font-size:13px;color:{COLORS['dim']}">{html.escape(home.get('recordSummary') or '')}</div>
            </div>
            <div style="margin-left:12px">{home_logo_html}</div>
          </div>
        </div>

        <!-- Gameplay Field Visualizer -->
        <div style="padding:12px 50px 0;display:flex;flex-direction:column;align-items:center">
          <div style="width:1160px;height:330px">
            {field_svg}
          </div>
        </div>

        <div style="margin:0 50px;padding:10px 17px;display:flex;gap:16px;align-items:center;background:#102744;border-left:5px solid {COLORS['hi']};height:64px;overflow:hidden">
          <span style="color:{COLORS['hi']};font-size:13px;font-weight:900;white-space:nowrap">{html.escape(str(play_clock))}</span>
          <span style="font-size:17px;font-weight:700;line-height:1.25">{html.escape(play_text)}</span>
        </div>

        <div class="ticker-bar">
          <span class="ticker-lead">GAME CENTER</span>
          <span>VENUE: {html.escape(venue_name)}</span>
          <span>BROADCAST: {html.escape(broadcast_str)}</span>
          <span>{html.escape(status.get('detail') or 'Scores from ArenaPulse')}</span>
        </div>
        """
        cards.append({
            "name": f"Game_{i}",
            "voice": voice,
            "narration": narration,
            "html": body
        })
    return cards


def _refresh_center(games, out_dir, refresh_sec):
    """Keep the existing lightweight Retro Sports score/news channel."""
    cards = _score_card_specs(games, sports.get_all_news())
    part_mp4s = []
    for idx, (title, lines, narration) in enumerate(cards, 1):
        png = os.path.join(out_dir, f"card_{idx:02d}.png")
        wav = os.path.join(out_dir, f"card_{idx:02d}.wav")
        mp4 = os.path.join(out_dir, f"card_{idx:02d}.mp4")
        try:
            _render_score_card(title, lines, png)
            try:
                audio_bytes, _ = sports.synthesize_speech(narration[:900])
                with open(wav, "wb") as audio:
                    audio.write(audio_bytes)
            except Exception:
                log.warning("Sports narration unavailable for %s; using silent card", title)
                _silent_narration(wav)
            _mux_card(png, wav, mp4)
            part_mp4s.append(mp4)
        except Exception:
            log.exception("Sports Center card %d (%s) failed", idx, title)
    if not part_mp4s:
        raise RuntimeError("no Sports Center cards succeeded this cycle")
    loop_path = os.path.join(out_dir, "loop.mp4")
    total_seconds = _build_concat_loop(part_mp4s, loop_path, refresh_sec)
    _upsert_sports_episode(loop_path, config.SPORTS_SHOW, "Sports Center Live",
                           f"Live scores and sports news loop ({len(part_mp4s)} segments)")
    return total_seconds


def _render_channel_cards(cards, out_dir, show_name, title, refresh_sec):
    os.makedirs(out_dir, exist_ok=True)
    part_mp4s = []
    for idx, card in enumerate(cards, 1):
        png = os.path.join(out_dir, f"card_{idx:02d}.png")
        wav = os.path.join(out_dir, f"card_{idx:02d}.wav")
        mp4 = os.path.join(out_dir, f"card_{idx:02d}.mp4")
        try:
            _render_card_png(card["html"], png)
            try:
                audio_bytes, _ = sports.synthesize_speech(card["narration"], voice=card["voice"])
                with open(wav, "wb") as f:
                    f.write(audio_bytes)
            except Exception:
                log.warning("ArenaPulse TTS unavailable for %s; rendering silent card", title)
                with wave.open(wav, "wb") as audio:
                    audio.setnchannels(1)
                    audio.setsampwidth(2)
                    audio.setframerate(16000)
                    audio.writeframes(b"\0\0" * 16000 * 8)
            _mux_card(png, wav, mp4)
            part_mp4s.append(mp4)
        except Exception:
            log.exception("sports card %d (%s) failed", idx, card.get("name"))
    if not part_mp4s:
        raise RuntimeError(f"no sports cards succeeded for {title}")
    loop_path = os.path.join(out_dir, "loop.mp4")
    total_seconds = _build_concat_loop(part_mp4s, loop_path, refresh_sec)
    _upsert_sports_episode(loop_path, show_name, title, f"ArenaPulse scores, field view, and play-by-play for {title}")
    return total_seconds


def _league_slate_card(league):
    name = html.escape(league.get("name") or league.get("id") or "Sports")
    body = f"""
    <div class="top-bar"><span class="brand-logo">RETRO<span>SPORTS</span></span><span class="badge-tag">{name}</span></div>
    <div style="padding:90px 70px;text-align:center;background:radial-gradient(circle at center,#194063,#071322 70%);height:630px">
      <div style="font-size:20px;font-weight:900;letter-spacing:5px;color:{COLORS['hi']}">ARENAPULSE LIVE</div>
      <div style="font-size:66px;font-weight:900;margin:55px auto 25px">{name}</div>
      <div style="font-size:28px;color:#b8cce2">No games on the board right now</div>
      <div style="font-size:18px;color:#8e9eb3;margin-top:28px">Scores and game coverage resume with the next matchup.</div>
    </div>"""
    return {"name": "Off air", "html": body, "voice": config.TTS_VOICE_SPORTS,
            "narration": f"{league.get('name') or 'Sports'} coverage. No games are scheduled right now."}


def refresh_sports_channel():
    """Build every ArenaPulse league as a playable browser and HDMI TV channel."""
    global _last_center_refresh
    _ensure_sports_channel()
    scores_data = sports.get_all_scores(force_refresh=True)
    games = scores_data.get("games") or []
    leagues = sports.get_leagues() or scores_data.get("leagues") or []
    if not leagues and not games:
        log.warning("ArenaPulse unavailable; retaining the last sports broadcasts")
        return 0
    ensure_league_channels(leagues)
    refresh_sec = getattr(config, "SPORTS_REFRESH_SEC", 15 * 60)
    out_dir = os.path.join(config.LIVE_CONTENT_DIR, "sports")
    os.makedirs(out_dir, exist_ok=True)
    total_seconds = 0
    if not _last_center_refresh or time.monotonic() - _last_center_refresh >= config.LIVE_CONTENT_REFRESH_SEC:
        try:
            total_seconds = _refresh_center(games, out_dir, refresh_sec)
            _last_center_refresh = time.monotonic()
        except Exception:
            log.exception("Sports Center broadcast refresh failed")

    for league in leagues:
        league_id = league.get("id") or league.get("league")
        if not league_id or not re.fullmatch(r"[a-z0-9.-]+", league_id):
            continue
        league_games = [g for g in games if g.get("league") == league_id]
        live = [g for g in league_games if (g.get("status") or {}).get("isLive")]
        upcoming = [g for g in league_games if (g.get("status") or {}).get("isScheduled")]
        final = [g for g in league_games if (g.get("status") or {}).get("isFinal")]
        featured = (live + upcoming + final)[:2]
        league_dir = os.path.join(out_dir, "leagues", league_id)
        loop_path = os.path.join(league_dir, "loop.mp4")
        signature = json.dumps(featured, sort_keys=True, default=str)
        if not live and _league_signatures.get(league_id) == signature and os.path.isfile(loop_path):
            continue
        os.makedirs(league_dir, exist_ok=True)
        cards = _build_game_cards(featured, league_dir) if featured else [_league_slate_card(league)]
        try:
            _render_channel_cards(cards, league_dir, f"Retro Sports · {league_id}",
                                  f"{league.get('name') or league_id} Live", refresh_sec)
            _league_signatures[league_id] = signature
        except Exception:
            log.exception("League broadcast failed for %s", league_id)

    try:
        scheduler.ensure_schedules()
    except Exception:
        log.exception("Failed to generate sports channel schedules")
    log.info("Sports channels refreshed: %d leagues", len(leagues))
    return total_seconds
