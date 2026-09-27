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
        detail = sports.get_game_detail(league, gid, sport=sport)
        narration = sports.script_for_game(g, detail=detail)

        # Generate gameplay field SVG
        field_svg = sports.generate_field_svg(
            sport, home, away,
            situation=situation,
            play=(detail.get("visualPlays")[-1] if detail.get("visualPlays") else None),
            status=status,
            width=1160, height=360
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
            badge_text = f"● LIVE · {status.get('detail') or 'IN PROGRESS'}"
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
        <div style="padding:16px 50px 0;display:flex;flex-direction:column;align-items:center">
          <div style="width:1160px;height:350px">
            {field_svg}
          </div>
        </div>

        <div class="ticker-bar">
          <span class="ticker-lead">GAME CENTER</span>
          <span>VENUE: {html.escape(venue_name)}</span>
          <span>BROADCAST: {html.escape(broadcast_str)}</span>
          <span>WEATHER: {html.escape(str(weather_temp))}&deg;F</span>
        </div>
        """
        cards.append({
            "name": f"Game_{i}",
            "voice": voice,
            "narration": narration,
            "html": body
        })
    return cards


def refresh_sports_channel():
    """Main generation cycle: fetch live data, render cards, synthesize TTS, and build loop."""
    out_dir = os.path.join(config.LIVE_CONTENT_DIR, "sports")
    os.makedirs(out_dir, exist_ok=True)
    _ensure_sports_channel()

    log.info("Refreshing Retro Sports channel content from ArenaPulse...")
    scores_data = sports.get_all_scores(force_refresh=True)
    games = scores_data.get("games", [])
    news_articles = sports.get_all_news()

    all_card_specs = []
    all_card_specs.extend(_build_news_cards(news_articles, out_dir))
    all_card_specs.extend(_build_game_cards(games, out_dir))

    if not all_card_specs:
        log.warning("No sports cards to render")
        return 0

    part_mp4s = []
    for idx, card in enumerate(all_card_specs, 1):
        png = os.path.join(out_dir, f"card_{idx:02d}.png")
        wav = os.path.join(out_dir, f"card_{idx:02d}.wav")
        mp4 = os.path.join(out_dir, f"card_{idx:02d}.mp4")
        try:
            _render_card_png(card["html"], png)
            # Synthesize narration via Kokoro TTS
            audio_bytes, ctype = sports.synthesize_speech(card["narration"], voice=card["voice"])
            with open(wav, "wb") as f:
                f.write(audio_bytes)
            _mux_card(png, wav, mp4)
            part_mp4s.append(mp4)
        except Exception:
            log.exception("sports card %d (%s) failed", idx, card.get("name"))

    if not part_mp4s:
        raise RuntimeError("no sports cards succeeded this cycle")

    loop_path = os.path.join(out_dir, "loop.mp4")
    refresh_sec = getattr(config, "SPORTS_REFRESH_SEC", 15 * 60)
    total_seconds = _build_concat_loop(part_mp4s, loop_path, refresh_sec)

    show_name = getattr(config, "SPORTS_SHOW", "Retro Sports")
    _ensure_sports_channel()
    _upsert_sports_episode(loop_path, show_name, "Sports Center Live", f"Live scores, field radar, and sports news loop ({len(part_mp4s)} segments)")

    try:
        import scheduler
        scheduler.ensure_schedules()
    except Exception:
        log.exception("Failed to generate schedule for sports channel")

    log.info("Sports channel refreshed successfully: %d segments looped to %.0fs", len(part_mp4s), total_seconds)
    return total_seconds
