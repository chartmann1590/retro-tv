"""Generated weather/news channels: live data -> one continuous narrated loop.

Runs on a timer (run_loop, started as a daemon thread from app.py). Each cycle
fetches fresh data, renders an HTML "card" per topic via headless chromium,
narrates it with Microsoft Edge's free neural TTS, muxes image+audio into an
mp4 per card with ffmpeg, then concatenates+repeats those cards (stream-copy,
no re-encode) into one continuous ~LIVE_CONTENT_REFRESH_SEC-long block -- like
a real local weather/news channel loop -- so a commercial break lands between
full ~30-minute blocks, not after every card. That single block is registered
as the one episode of a synthetic show ("Local Weather" / "Local News") so the
existing channel/scheduler machinery (channel_sources type "show", commercial
interleaving, etc.) handles the rest unmodified. The episode's file path is
fixed and simply overwritten each cycle, so its media_files id -- and
therefore already-generated schedule_entries -- stays valid; whatever slot
airs next just plays back whatever is currently on disk. Best-effort
throughout: a failed fetch/render/TTS logs and skips that card rather than
breaking the whole cycle, matching metadata.py's offline-safe style.
"""
import asyncio
import html
import json
import logging
import os
import re
import subprocess
import time
import xml.etree.ElementTree as ET
from datetime import datetime

import config
import database
import scanner

log = logging.getLogger("retro-tv.livecontent")

WEATHER_SHOW = "Local Weather"
NEWS_SHOW = "Local News"
CARD_W, CARD_H = 1280, 720

COLORS = {"bg": "#071938", "panel": "#122e59", "panel2": "#0c2348",
          "hi": "#f8cb63", "txt": "#eff2e9", "dim": "#aab9cf", "line": "#36527b"}


def _get(url, timeout=12, binary=False):
    import requests
    r = requests.get(url, timeout=timeout, headers={"User-Agent": "retro-tv/1.0 (weather+news channel)"})
    r.raise_for_status()
    return r.content if binary else r.text


def _strip_tags(value):
    return html.unescape(re.sub(r"<[^>]+>", " ", value or "")).strip()


def _card_html(body, extra_css=""):
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{width:{CARD_W}px;height:{CARD_H}px;background:{COLORS['bg']};color:{COLORS['txt']};
font-family:Arial,Helvetica,sans-serif;overflow:hidden}}
.eyebrow{{font-size:16px;font-weight:700;letter-spacing:3px;color:{COLORS['hi']}}}
.bar{{background:linear-gradient(#264c88,#163362);padding:20px 40px;border-bottom:4px solid #071832}}
.bar b{{font-size:26px;letter-spacing:1px;color:#fff}}
{extra_css}
</style></head><body>{body}</body></html>"""


def _render_card(body_html, out_png, extra_css=""):
    fd_path = out_png + ".tmp.html"
    with open(fd_path, "w") as f:
        f.write(_card_html(body_html, extra_css))
    try:
        subprocess.run(
            ["chromium", "--headless=new", "--disable-gpu", "--no-sandbox",
             f"--screenshot={out_png}", f"--window-size={CARD_W},{CARD_H}",
             "--virtual-time-budget=2000", "file://" + fd_path],
            check=True, capture_output=True, timeout=30)
    finally:
        os.unlink(fd_path)


def _tts(text, voice, out_mp3):
    text = text.strip()
    if not text:
        raise ValueError("empty narration")
    import edge_tts

    async def go():
        await edge_tts.Communicate(text, voice).save(out_mp3)
    asyncio.run(go())


def _mux(image_path, audio_path, out_mp4):
    subprocess.run(
        ["ffmpeg", "-y", "-loop", "1", "-i", image_path, "-i", audio_path,
         "-c:v", "libx264", "-tune", "stillimage", "-r", "2", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "128k", "-shortest", out_mp4],
        check=True, capture_output=True, timeout=180)


def _build_loop(card_mp4s, out_path, target_seconds):
    """Concatenate the card mp4s into one pass, then repeat that pass (stream-copy,
    no re-encode) enough times to reach ~target_seconds -- one continuous block,
    like a real local-weather/news channel loop, instead of many short clips."""
    durations = [scanner.probe_info(p)["duration"] or 1 for p in card_mp4s]
    pass_seconds = sum(durations)
    repeats = max(1, round(target_seconds / pass_seconds)) if pass_seconds else 1
    list_path = out_path + ".concat.txt"
    with open(list_path, "w") as f:
        for _ in range(repeats):
            for p in card_mp4s:
                f.write(f"file '{os.path.abspath(p)}'\n")
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_path, "-c", "copy", out_path],
            check=True, capture_output=True, timeout=300)
    finally:
        os.unlink(list_path)
    return pass_seconds * repeats


def _upsert_episode(path, show_name, episode_num, title, description):
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
            VALUES(?,?,?,1,?,?,?,'generated') ON CONFLICT(media_id) DO UPDATE SET show_id=excluded.show_id,
            show_name=excluded.show_name, season=excluded.season, episode=excluded.episode,
            title=excluded.title, description=excluded.description""",
            (media_id, show_id, show_name, episode_num, title, description))
        con.commit()
    finally:
        con.close()


def _prune_stale(show_name, keep_paths):
    """Remove any episode/media_files rows for this show whose path isn't one of
    keep_paths -- handles both a shorter cycle than last time and a filename-
    scheme change (e.g. the old per-card 01.mp4/02.mp4/... files this replaced)."""
    keep = set(keep_paths)
    con = database.connect()
    try:
        rows = con.execute("""SELECT e.media_id, m.path FROM episodes e
            JOIN media_files m ON m.id=e.media_id WHERE e.show_name=?""", (show_name,)).fetchall()
        for r in rows:
            if r["path"] in keep:
                continue
            con.execute("DELETE FROM episodes WHERE media_id=?", (r["media_id"],))
            con.execute("DELETE FROM media_files WHERE id=?", (r["media_id"],))
            try:
                os.remove(r["path"])
            except OSError:
                pass
        con.commit()
    finally:
        con.close()


# ---------- weather ----------

def _geocode_zip(zip_code):
    data = json.loads(_get(f"http://api.zippopotam.us/us/{zip_code}"))
    place = data["places"][0]
    return float(place["latitude"]), float(place["longitude"]), place["place name"], place["state abbreviation"]


def _fetch_weather():
    lat, lon, city, state = _geocode_zip(config.WEATHER_ZIP)
    points = json.loads(_get(f"https://api.weather.gov/points/{lat},{lon}"))["properties"]
    forecast = json.loads(_get(points["forecast"]))["properties"]["periods"]
    hourly = json.loads(_get(points["forecastHourly"]))["properties"]["periods"]
    radar_station = points.get("radarStation") or "KTYX"
    radar_bytes = None
    try:
        radar_bytes = _get(f"https://radar.weather.gov/ridge/standard/{radar_station}_0.gif", binary=True)
    except Exception as e:
        log.warning("radar fetch failed: %s", e)
    return {"city": city, "state": state, "forecast": forecast, "hourly": hourly, "radar": radar_bytes}


def _weather_cards(data):
    now = data["forecast"][0]
    cards = []

    # Card 1: current conditions
    narration = (f"Here is your local weather for {data['city']}, {data['state']}. "
                 f"Right now, {now['name'].lower()}: {now['temperature']} degrees "
                 f"{now['temperatureUnit']}, with {now['shortForecast'].lower()}. "
                 f"{_strip_tags(now.get('detailedForecast', ''))[:280]}")
    body = f"""<div class="bar"><span class="eyebrow">LOCAL WEATHER &middot; {html.escape(data['city'])}, {data['state']}</span></div>
    <div style="padding:60px 60px 0">
      <div style="font-size:180px;font-weight:900;color:{COLORS['hi']};line-height:1">{now['temperature']}&deg;</div>
      <div style="font-size:40px;margin-top:10px">{html.escape(now['shortForecast'])}</div>
      <div style="font-size:20px;color:{COLORS['dim']};margin-top:30px;max-width:1000px;line-height:1.5">{html.escape(_strip_tags(now.get('detailedForecast',''))[:220])}</div>
    </div>"""
    cards.append(("Current Conditions", now["shortForecast"], narration, body))

    # Card 2: hourly
    next_hours = data["hourly"][:6]
    parts = [f"{datetime.fromisoformat(h['startTime']).strftime('%-I %p')}, {h['temperature']} degrees, {h['shortForecast'].lower()}"
             for h in next_hours]
    narration = "Here's the hourly outlook: " + "; then ".join(parts) + "."
    cells = "".join(
        f"""<div style="flex:1;text-align:center;padding:20px 8px;border-right:1px solid {COLORS['line']}">
              <div style="font-size:18px;color:{COLORS['dim']}">{datetime.fromisoformat(h['startTime']).strftime('%-I %p')}</div>
              <div style="font-size:46px;font-weight:800;color:{COLORS['hi']};margin:12px 0">{h['temperature']}&deg;</div>
              <div style="font-size:15px">{html.escape(h['shortForecast'])}</div>
            </div>""" for h in next_hours)
    body = f"""<div class="bar"><span class="eyebrow">HOURLY FORECAST</span></div>
    <div style="display:flex;margin:70px 30px">{cells}</div>"""
    cards.append(("Hourly Forecast", ", ".join(f"{h['temperature']}°" for h in next_hours), narration, body))

    # Card 3: 5-day
    days = [p for p in data["forecast"] if p.get("isDaytime")][:5]
    parts = [f"{d['name']}, {d['shortForecast'].lower()}, with a high near {d['temperature']} degrees" for d in days]
    narration = "And looking ahead: " + "; ".join(parts) + "."
    rows = "".join(
        f"""<div style="flex:1;text-align:center;padding:24px 10px;border-right:1px solid {COLORS['line']}">
              <div style="font-size:20px;font-weight:700;color:{COLORS['hi']}">{html.escape(d['name'][:9])}</div>
              <div style="font-size:15px;margin:14px 0;min-height:40px">{html.escape(d['shortForecast'])}</div>
              <div style="font-size:34px;font-weight:800">{d['temperature']}&deg;</div>
            </div>""" for d in days)
    body = f"""<div class="bar"><span class="eyebrow">5-DAY FORECAST</span></div>
    <div style="display:flex;margin:80px 30px">{rows}</div>"""
    cards.append(("5-Day Forecast", ", ".join(d["name"] for d in days), narration, body))

    # Card 4: radar
    narration = f"And here's a live look at radar for the {data['city']} area."
    if data["radar"]:
        radar_path = os.path.join(config.LIVE_CONTENT_DIR, "weather", "radar.gif")
        with open(radar_path, "wb") as f:
            f.write(data["radar"])
        body = f"""<div class="bar"><span class="eyebrow">LIVE RADAR &middot; {html.escape(data['city'])}, {data['state']}</span></div>
        <div style="display:flex;justify-content:center;padding:30px"><img src="file://{radar_path}" style="width:600px;height:550px;image-rendering:pixelated;border:3px solid {COLORS['line']}"></div>"""
    else:
        body = f"""<div class="bar"><span class="eyebrow">LIVE RADAR</span></div>
        <div style="padding:100px;font-size:28px;color:{COLORS['dim']}">Radar unavailable this update.</div>"""
    cards.append(("Live Radar", f"{data['city']}, {data['state']}", narration, body))
    return cards


def _build_show_loop(cards, out_dir, voice, show_name, title, subtitle):
    """Render/narrate/mux each card, then concat+loop them into one ~30-min
    episode so commercial breaks land between full loops, not every card."""
    parts_dir = os.path.join(out_dir, "parts")
    os.makedirs(parts_dir, exist_ok=True)
    part_mp4s = []
    for i, (_, _, narration, body) in enumerate(cards, 1):
        png = os.path.join(parts_dir, f"{i:02d}.png")
        mp3 = os.path.join(parts_dir, f"{i:02d}.mp3")
        mp4 = os.path.join(parts_dir, f"{i:02d}.mp4")
        try:
            _render_card(body, png)
            _tts(narration, voice, mp3)
            _mux(png, mp3, mp4)
            part_mp4s.append(mp4)
        except Exception:
            log.exception("%s card %d failed", show_name, i)
    if not part_mp4s:
        raise RuntimeError(f"no {show_name} cards succeeded this cycle")
    loop_path = os.path.join(out_dir, "loop.mp4")
    total = _build_loop(part_mp4s, loop_path, config.LIVE_CONTENT_REFRESH_SEC)
    _upsert_episode(loop_path, show_name, 1, title, subtitle)
    _prune_stale(show_name, [loop_path])
    return total


def refresh_weather():
    out_dir = os.path.join(config.LIVE_CONTENT_DIR, "weather")
    os.makedirs(out_dir, exist_ok=True)
    data = _fetch_weather()
    cards = _weather_cards(data)
    total = _build_show_loop(cards, out_dir, config.TTS_VOICE_WEATHER, WEATHER_SHOW,
                              "Local Weather", f"{data['city']}, {data['state']}")
    log.info("Weather channel refreshed: %d cards looped to %.0fs for %s, %s",
              len(cards), total, data["city"], data["state"])


# ---------- news ----------

def _fetch_news_items():
    xml_text = _get(config.NEWS_RSS_URL)
    root = ET.fromstring(xml_text)
    items = root.findall(".//item")[:config.NEWS_ARTICLE_COUNT]
    out = []
    for it in items:
        title = _strip_tags(it.findtext("title") or "")
        desc = _strip_tags(it.findtext("description") or "")
        link = (it.findtext("link") or "").strip()
        image_bytes = None
        if link:
            try:
                page = _get(link, timeout=8)
                m = re.search(r'<meta[^>]+property="og:image"[^>]+content="([^"]+)"', page)
                if m:
                    image_bytes = _get(m.group(1), timeout=8, binary=True)
            except Exception as e:
                log.warning("og:image fetch failed for %s: %s", link, e)
        out.append({"title": title, "desc": desc, "image": image_bytes})
    return out


def _news_cards(items):
    cards = []
    for i, item in enumerate(items, 1):
        narration = f"{item['title']}. {item['desc'][:400]}"
        img_tag = ""
        text_width = "1180px"
        if item["image"]:
            img_path = os.path.join(config.LIVE_CONTENT_DIR, "news", f"{i:02d}.jpg")
            with open(img_path, "wb") as f:
                f.write(item["image"])
            img_tag = f'<img src="file://{img_path}" style="width:460px;height:640px;object-fit:cover;border-left:4px solid {COLORS["hi"]}">'
            text_width = "740px"
        body = f"""<div class="bar"><span class="eyebrow">LOCAL NEWS &middot; STORY {i} OF {len(items)}</span></div>
        <div style="display:flex;height:{CARD_H-80}px">
          <div style="padding:50px 40px;width:{text_width}">
            <div style="font-size:38px;font-weight:800;line-height:1.2;color:{COLORS['hi']}">{html.escape(item['title'])}</div>
            <div style="font-size:19px;color:{COLORS['dim']};margin-top:26px;line-height:1.6">{html.escape(item['desc'][:320])}</div>
          </div>
          {img_tag}
        </div>"""
        cards.append((item["title"][:60] or f"Story {i}", item["desc"][:80], narration, body))
    return cards


def refresh_news():
    out_dir = os.path.join(config.LIVE_CONTENT_DIR, "news")
    os.makedirs(out_dir, exist_ok=True)
    items = _fetch_news_items()
    if not items:
        log.warning("news refresh: no items fetched")
        return
    cards = _news_cards(items)
    total = _build_show_loop(cards, out_dir, config.TTS_VOICE_NEWS, NEWS_SHOW,
                              "Local News", f"{len(items)} stories")
    log.info("News channel refreshed: %d stories looped to %.0fs", len(items), total)


# ---------- loop ----------

def run_loop(stop_event=None):
    import threading
    stop_event = stop_event or threading.Event()
    os.makedirs(config.LIVE_CONTENT_DIR, exist_ok=True)
    while not stop_event.is_set():
        t0 = time.monotonic()
        try:
            refresh_weather()
        except Exception:
            log.exception("weather refresh cycle failed")
        try:
            refresh_news()
        except Exception:
            log.exception("news refresh cycle failed")
        log.info("Live content cycle took %.1fs", time.monotonic() - t0)
        stop_event.wait(config.LIVE_CONTENT_REFRESH_SEC)
