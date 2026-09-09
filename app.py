"""Retro TV - Flask backend. LAN-only by default (0.0.0.0:5000)."""
import json
import logging
import logging.handlers
import os
import re
import threading
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from flask import Flask, jsonify, request, Response, render_template, send_file, send_from_directory, abort, redirect, url_for

import config
import database
import scanner
import scheduler
import playback
import streaming
import remote as remote_mod

TZ = ZoneInfo(config.TIMEZONE)
os.makedirs(config.LOGS_DIR, exist_ok=True)
os.makedirs(config.HLS_DIR, exist_ok=True)

handler = logging.handlers.RotatingFileHandler(config.LOG_PATH, maxBytes=2_000_000, backupCount=5)
logging.basicConfig(level=logging.INFO, handlers=[handler, logging.StreamHandler()],
                    format="%(asctime)s %(name)s %(levelname)s: %(message)s")
log = logging.getLogger("retro-tv")
logging.getLogger("werkzeug").setLevel(logging.WARNING)

app = Flask(__name__, template_folder="templates", static_folder="static")

@app.context_processor
def template_settings():
    remote_url = request.url_root.rstrip('/') + '/remote'
    if request.host.split(':')[0] in ('localhost', '127.0.0.1'):
        import socket
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.connect(('192.0.2.1', 1))  # Route lookup only; sends no packets.
                remote_url = f"http://{sock.getsockname()[0]}:{config.PORT}/remote"
        except OSError:
            remote_url = f"http://{socket.gethostname()}.local:{config.PORT}/remote"
    return {"config_timezone": config.TIMEZONE, "remote_url": remote_url}


def fmt_time(ts):
    return datetime.fromtimestamp(ts, TZ).strftime("%-I:%M %p")

def fmt_range(s, e):
    return f"{fmt_time(s)} - {fmt_time(e)}"

# ---------- background jobs ----------
_bg_stop = threading.Event()

def bg_loop():
    # One worker owns startup and maintenance; never scan/generate twice at boot.
    try:
        scheduler.ensure_schedules()
        playback.restore_last()
    except Exception:
        log.exception("Startup playback failed")
    last_schedule = time.monotonic()
    last_backup = last_schedule
    while not _bg_stop.is_set():
        try:
            scanner.full_scan(light=True)
            if time.monotonic() - last_schedule >= config.SCHEDULER_INTERVAL_SEC:
                scheduler.ensure_schedules()
                streaming.cleanup_hls()
                last_schedule = time.monotonic()
            if not playback._current["channel"]:
                scheduler.ensure_schedules()
                playback.restore_last()
            if time.monotonic() - last_backup >= 86400:
                database.backup_db()
                last_backup = time.monotonic()
        except Exception:
            log.exception("Background maintenance failed")
        _bg_stop.wait(config.SCAN_INTERVAL_SEC)

# ---------- pages ----------
@app.route("/")
def home():
    channels = scheduler.get_channels()
    last = database.get_state("last_channel", "")
    try:
        cur = int(last) if last else (channels[0]["number"] if channels else None)
    except Exception:
        cur = channels[0]["number"] if channels else None
    entry, offset = None, 0
    if cur:
        entry, offset, _, _ = streaming.resolve_live(cur)
    return render_template("home.html", channels=channels, current=cur, entry=entry,
                           offset=int(offset or 0), fmt_time=fmt_time)

@app.route("/watch")
@app.route("/watch/<int:ch>")
def watch(ch=None):
    channels = scheduler.get_channels()
    if not channels:
        return render_template("watch.html", channels=[], current=None, entry=None, offset=0)
    if ch is None:
        last = database.get_state("last_channel", "")
        try:
            ch = int(last) if last else channels[0]["number"]
        except Exception:
            ch = channels[0]["number"]
    entry, offset, path, dur = streaming.resolve_live(ch)
    return render_template("watch.html", channels=channels, current=ch, entry=entry, offset=int(offset or 0))

@app.route("/guide")
def guide():
    try:
        hours = min(12, max(1, int(request.args.get("hours", "4"))))
    except Exception:
        hours = 4
    data = scheduler.guide_data(hours=hours)
    return render_template("guide.html", guide=data, hours=hours, fmt_time=fmt_time)

@app.route("/admin")
def admin():
    return render_template("admin.html")

@app.route("/remote")
def remote_page():
    channels = scheduler.get_channels()
    return render_template("remote.html", channels=channels)

# ---------- media streaming ----------
def range_response(path, mime):
    # Werkzeug handles suffix ranges, invalid ranges, HEAD, and conditional reads.
    response = send_file(path, mimetype=mime, conditional=True)
    response.headers["Accept-Ranges"] = "bytes"
    response.cache_control.no_store = True
    return response

MIME = {".mp4": "video/mp4", ".m4v": "video/mp4", ".mov": "video/quicktime",
        ".mkv": "video/x-matroska", ".avi": "video/x-msvideo", ".webm": "video/webm",
        ".ts": "video/mp2t", ".mpg": "video/mpeg", ".mpeg": "video/mpeg"}

@app.route("/stream/live/<int:ch>")
def stream_live(ch):
    """Direct file for channel with Range support. Player seeks to offset via API."""
    sid = request.args.get("sid") or streaming.register_session(ch)
    streaming.touch_session(sid)
    entry, offset, path, dur = streaming.resolve_live(ch)
    if not path:
        return jsonify({"error": "No media currently (off-air or missing file)", "entry": entry}), 404
    vc, ac, cont = streaming.ffprobe_codecs(path)
    # remux on demand if requested or needed and client wants mp4
    if request.args.get("remux") == "1" or (streaming.needs_remux(path, vc, ac, cont) and request.args.get("direct") != "1"):
        out = streaming.remux_to_mp4(path)
        if out:
            return range_response(out, "video/mp4")
    ext = os.path.splitext(path)[1].lower()
    return range_response(path, MIME.get(ext, "video/mp4"))

@app.route("/stream/file/<int:media_id>")
def stream_file(media_id):
    con = database.connect()
    try:
        r = con.execute("SELECT path, vcodec, acodec, container FROM media_files WHERE id=?", (media_id,)).fetchone()
    finally:
        con.close()
    if not r or not r["path"] or not os.path.exists(r["path"]):
        abort(404)
    if request.args.get("remux") == "1" or (streaming.needs_remux(r["path"], r["vcodec"], r["acodec"], r["container"]) and request.args.get("direct") != "1"):
        out = streaming.remux_to_mp4(r["path"])
        if out:
            return range_response(out, "video/mp4")
    ext = os.path.splitext(r["path"])[1].lower()
    return range_response(r["path"], MIME.get(ext, "video/mp4"))

@app.route("/hls/<path:name>")
def hls(name):
    return send_from_directory(config.HLS_DIR, name)

@app.route("/api/hls/<int:ch>")
def api_hls(ch):
    entry, offset, path, dur = streaming.resolve_live(ch)
    if not path:
        return jsonify({"ok": False, "error": "No media"}), 404
    name = streaming.hls_for(path, offset)
    if not name:
        return jsonify({"ok": False, "error": "HLS failed"}), 500
    return jsonify({"ok": True, "url": f"/hls/{name}", "offset": offset})

# ---------- JSON API ----------
@app.route("/api/now/<int:ch>")
def api_now(ch):
    entry, offset, path, dur = streaming.resolve_live(ch)
    if not entry:
        return jsonify({"ok": False, "error": "No schedule"})
    return jsonify({"ok": True, "entry": entry, "offset": offset, "duration": dur,
                    "range": fmt_range(entry["start_ts"], entry["end_ts"]) if entry.get("start_ts") else "",
                    "has_media": bool(path), "server_time": time.time(),
                    "media_key": __import__("hashlib").sha256(f"{entry['id']}:{path}:{entry.get('segment_offset', 0)}".encode()).hexdigest()[:16]})

@app.route("/api/guide")
def api_guide():
    try:
        hours = min(12, max(1, int(request.args.get("hours", "4"))))
        start = float(request.args.get("start", "0") or 0)
    except Exception:
        hours, start = 4, 0
    if not start:
        start = datetime.now(TZ).timestamp()
    return jsonify({"ok": True, "start": start, "hours": hours, "server_time": time.time(), "current_channel": playback._current["channel"], "guide": scheduler.guide_data(start, hours)})

@app.route("/api/channels")
def api_channels():
    return jsonify({"ok": True, "channels": scheduler.get_channels(enabled_only=False)})

@app.route("/api/channel", methods=["POST"])
def api_channel_save():
    d = request.get_json(force=True)
    try:
        num = int(d["number"])
    except Exception:
        return jsonify({"ok": False, "error": "Invalid channel number"}), 400
    con = database.connect()
    try:
        con.execute("""INSERT INTO channels(number,name,enabled,color,logo,ordering,commercial_mode,max_repeats_per_day,sort_order)
            VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(number) DO UPDATE SET name=excluded.name, enabled=excluded.enabled,
            color=excluded.color, logo=excluded.logo, ordering=excluded.ordering, commercial_mode=excluded.commercial_mode,
            max_repeats_per_day=excluded.max_repeats_per_day""",
            (num, d.get("name", f"Channel {num}"), 1 if d.get("enabled", True) else 0,
             d.get("color", "#1a3a6b"), d.get("logo", ""), d.get("ordering", "shuffle"),
             d.get("commercial_mode", "between"), int(d.get("max_repeats_per_day", 2)), num))
        con.execute("DELETE FROM channel_sources WHERE channel_number=?", (num,))
        for s in d.get("sources", []):
            con.execute("INSERT INTO channel_sources(channel_number,source_type,source_value) VALUES(?,?,?)",
                        (num, s.get("type", "show"), s.get("value", "")))
        con.commit()
    finally:
        con.close()
    # regenerate today (resume-only, never wipes already-aired entries) and future days
    try:
        ch = [c for c in scheduler.get_channels(enabled_only=False) if c["number"] == num]
        if ch:
            today = datetime.now(TZ)
            scheduler.generate_day(ch[0], today.strftime("%Y-%m-%d"))
            for i in range(1, config.SCHEDULE_DAYS_AHEAD):
                day = (today + timedelta(days=i)).strftime("%Y-%m-%d")
                con2 = database.connect()
                try:
                    con2.execute("DELETE FROM schedule_entries WHERE channel_number=? AND day=?", (num, day))
                    con2.commit()
                finally:
                    con2.close()
                scheduler.generate_day(ch[0], day)
    except Exception as e:
        log.exception("regen after save: %s", e)
    return jsonify({"ok": True})

@app.route("/api/channel/<int:num>", methods=["DELETE"])
def api_channel_del(num):
    con = database.connect()
    try:
        con.execute("DELETE FROM channel_sources WHERE channel_number=?", (num,))
        con.execute("DELETE FROM schedule_entries WHERE channel_number=?", (num,))
        con.execute("DELETE FROM channels WHERE number=?", (num,))
        con.commit()
    finally:
        con.close()
    return jsonify({"ok": True})

@app.route("/api/library")
def api_library():
    return jsonify({"ok": True, **scanner.library_summary()})

@app.route("/api/scan", methods=["POST"])
def api_scan():
    full = (request.get_json(silent=True) or {}).get("full", False)
    res = scanner.full_scan(light=not full)
    try:
        from metadata import enrich_episodes, enrich_movies
        en, fail = enrich_episodes()
        mv_en, mv_fail = enrich_movies()
        res.update(metadata_enriched=en + mv_en, metadata_failed=fail + mv_fail)
    except Exception as e:
        res["metadata_error"] = str(e)
    try:
        scheduler.ensure_schedules()
    except Exception as e:
        res["sched_error"] = str(e)
    return jsonify({"ok": True, **res})

@app.route("/api/schedule")
def api_schedule():
    try:
        ch = int(request.args.get("channel", "0"))
        day = request.args.get("day") or scheduler.local_day()
    except Exception:
        return jsonify({"ok": False}), 400
    con = database.connect()
    try:
        rows = [dict(r) for r in con.execute("SELECT * FROM schedule_entries WHERE channel_number=? AND day=? ORDER BY start_ts", (ch, day))]
    finally:
        con.close()
    for r in rows:
        r["start_fmt"] = fmt_time(r["start_ts"])
        r["end_fmt"] = fmt_time(r["end_ts"])
    return jsonify({"ok": True, "day": day, "entries": rows})

@app.route("/api/regen", methods=["POST"])
def api_regen():
    d = request.get_json(silent=True) or {}
    day = d.get("day")
    chnum = d.get("channel")
    if not day:
        day = (datetime.now(TZ) + timedelta(days=1)).strftime("%Y-%m-%d")
    channels = scheduler.get_channels(enabled_only=False)
    if chnum:
        channels = [c for c in channels if c["number"] == int(chnum)]
    # never regen already-aired part of today
    if day == scheduler.local_day():
        return jsonify({"ok": False, "error": "Refusing to regenerate today's aired entries; regenerate a future day."}), 400
    con = database.connect()
    try:
        for c in channels:
            con.execute("DELETE FROM schedule_entries WHERE channel_number=? AND day=?", (c["number"], day))
        con.commit()
    finally:
        con.close()
    n = 0
    for c in channels:
        n += scheduler.generate_day(c, day, seed_extra=int(time.time()) % 100000)
    return jsonify({"ok": True, "entries": n, "day": day})

@app.route("/api/tune", methods=["POST"])
def api_tune():
    d = request.get_json(force=True)
    try:
        ch = int(d["channel"])
    except Exception:
        return jsonify({"ok": False, "error": "bad channel"}), 400
    res = playback.tune(ch, reason="api")
    if res.get("ok"):
        import tvguide
        tvguide.close()
    if res.get("entry"):
        e = res["entry"]
        res["entry_fmt"] = fmt_range(e["start_ts"], e["end_ts"]) if e.get("start_ts") else ""
    return jsonify(res)

@app.route("/api/hdmi")
def api_hdmi():
    last = database.get_state("last_channel", "")
    try:
        ch = int(last) if last else None
    except Exception:
        ch = None
    prev = database.get_state("prev_channel", "")
    try:
        prev = int(prev) if prev else None
    except Exception:
        prev = None
    entry = scheduler.now_playing(ch) if ch else None
    return jsonify({"ok": True, "channel": ch, "prev_channel": prev, "entry": entry,
                    "mpv": bool(playback._find_mpv()), **playback.status(), "server_time": time.time()})

@app.route("/api/volume", methods=["GET", "POST"])
def api_volume():
    d = request.get_json(silent=True) or {}
    if "volume" in d:
        try:
            playback.set_volume(d["volume"])
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "Invalid volume"}), 400
    if "muted" in d:
        playback.set_mute(bool(d["muted"]))
    if d.get("toggle_pause"):
        playback.pause_toggle()
    return jsonify({"ok": True, **playback.status()})

@app.route("/api/tv-guide", methods=["POST"])
def api_tv_guide():
    import tvguide
    d = request.get_json(silent=True) or {}
    if d.get("action") == "close":
        return jsonify({"ok": tvguide.close()})
    try:
        ch = int(d["channel"]) if d.get("channel") is not None else None
        entry_id = int(d["entry_id"]) if d.get("entry_id") is not None else None
        start = float(d["start"]) if d.get("start") is not None else None
        if start is not None and (not __import__("math").isfinite(start) or abs(start - time.time()) > 7 * 86400):
            raise ValueError("Invalid guide time")
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "Invalid guide selection"}), 400
    if not playback.mpv_alive():
        playback.restore_last()
    ok = tvguide.render(ch, entry_id, start)
    return jsonify({"ok": ok, **({} if ok else {"error": "Tune a channel before opening the TV guide"})})

@app.route("/api/info", methods=["POST"])
def api_info():
    return jsonify({"ok": playback.show_info()})

@app.route("/api/remote")
def api_remote():
    return jsonify({"ok": True, "mappings": remote_mod.get_mappings(), "devices": remote_mod.list_input_devices()})

@app.route("/api/remote", methods=["POST"])
def api_remote_save():
    d = request.get_json(force=True)
    remote_mod.set_mapping(d["action"], d["code"])
    return jsonify({"ok": True})

@app.route("/api/settings")
def api_settings():
    con = database.connect()
    try:
        s = {r["key"]: r["value"] for r in con.execute("SELECT * FROM settings")}
    finally:
        con.close()
    return jsonify({"ok": True, "settings": s})

@app.route("/api/settings", methods=["POST"])
def api_settings_save():
    d = request.get_json(force=True)
    for k, v in d.items():
        database.set_setting(k, str(v))
    return jsonify({"ok": True})

@app.route("/api/system")
def api_system():
    import shutil
    du = shutil.disk_usage(config.MEDIA_ROOT)
    return jsonify({"ok": True,
                    "disk": {"total": du.total, "used": du.used, "free": du.free},
                    "mpv": bool(playback._find_mpv()),
                    "mpv_alive": playback.mpv_alive(),
                    "sessions": streaming.active_sessions(),
                    "library": scanner.library_summary()})

@app.route("/api/logs")
def api_logs():
    try:
        with open(config.LOG_PATH) as f:
            lines = f.readlines()[-300:]
    except Exception as e:
        lines = [f"log unavailable: {e}"]
    return jsonify({"ok": True, "lines": lines})

@app.route("/api/reboot", methods=["POST"])
def api_reboot():
    # user service cannot reboot without sudo; attempt systemctl, report accordingly
    import subprocess
    try:
        subprocess.Popen(["systemctl", "reboot"])
        return jsonify({"ok": True, "msg": "Reboot requested"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/restart-playback", methods=["POST"])
def api_restart_playback():
    res = playback.restore_last()
    return jsonify(res)

def init():
    database.init_db()
    playback.start_monitor()
    threading.Thread(target=bg_loop, daemon=True).start()


if __name__ == "__main__":
    init()
    from waitress import serve
    serve(app, host=config.HOST, port=config.PORT, threads=6,
          connection_limit=32, channel_timeout=30, send_bytes=65536)
