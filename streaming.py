"""Browser streaming: direct play w/ Range, remux fallback, HLS for non-MP4. No HDMI interference."""
import json
import logging
import os
import re
import subprocess
import threading
import time
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo
import config
import database
import scheduler

log = logging.getLogger("retro-tv.streaming")
TZ = ZoneInfo(config.TIMEZONE)
_sessions = {}
_hls_lock = threading.RLock()

def register_session(ch):
    sid = uuid.uuid4().hex[:12]
    now = time.time()
    _sessions[sid] = {"channel": ch, "created": now, "seen": now}
    # cleanup stale (>4h)
    for k in [k for k, v in _sessions.items() if now - v["seen"] > 14400]:
        _sessions.pop(k, None)
    # persist lightly
    try:
        con = database.connect()
        try:
            con.execute("INSERT OR REPLACE INTO stream_sessions(id,channel_number,created_ts,last_seen_ts) VALUES(?,?,?,?)",
                        (sid, ch, now, now))
            con.commit()
        finally:
            con.close()
    except Exception:
        pass
    return sid

def touch_session(sid):
    if sid in _sessions:
        _sessions[sid]["seen"] = time.time()

def active_sessions():
    return [{"id": k, **v} for k, v in _sessions.items()]

def resolve_live(ch_number, ts=None):
    """Return (entry, offset, filepath, duration) for browser live position."""
    entry = scheduler.now_playing(ch_number, ts)
    if not entry:
        return None, 0, None, 0
    now = (ts or datetime.now(TZ).timestamp())
    offset = max(0, now - entry["start_ts"])
    if entry["kind"] == "commercial_break":
        try:
            cids = json.loads(entry.get("commercial_ids") or "[]")
        except Exception:
            cids = []
        con = database.connect()
        try:
            t = offset
            for slot in cids:
                mid = slot["media_id"] if isinstance(slot, dict) else slot
                r = con.execute("SELECT path, duration FROM media_files WHERE id=?", (mid,)).fetchone()
                if not r:
                    if isinstance(slot, dict):
                        t -= slot["duration"]
                    continue
                source_duration = r["duration"] or 30
                length = slot["duration"] if isinstance(slot, dict) else source_duration
                source_offset = slot.get("offset", 0) if isinstance(slot, dict) else 0
                if t < length:
                    if not r["path"] or not os.path.isfile(r["path"]):
                        return entry, offset, None, length
                    return {**entry, "segment_offset": source_offset}, source_offset + t, r["path"], source_duration
                t -= length
            return entry, offset, None, entry["end_ts"] - entry["start_ts"]
        finally:
            con.close()
    if entry["kind"] == "slate" or not entry.get("media_id"):
        return entry, 0, None, entry["end_ts"] - entry["start_ts"]
    con = database.connect()
    try:
        r = con.execute("SELECT path, duration, vcodec, acodec, container FROM media_files WHERE id=?", (entry["media_id"],)).fetchone()
    finally:
        con.close()
    if not r or not r["path"] or not os.path.exists(r["path"]):
        return entry, offset, None, 0
    return entry, offset, r["path"], r["duration"] or (entry["end_ts"] - entry["start_ts"])

def needs_remux(path, vcodec="", acodec="", container=""):
    c = (container or os.path.splitext(path)[1].lower().lstrip(".")).lower()
    v = (vcodec or "").lower()
    a = (acodec or "").lower()
    # Browsers need both a supported video codec and a compatible audio track.
    return not (c in ("mp4", "m4v") and v in ("h264", "avc", "hevc", "h265")
                and a in ("aac", "mp3", ""))

def ffprobe_codecs(path):
    con = database.connect()
    try:
        r = con.execute("SELECT vcodec, acodec, container FROM media_files WHERE path=?", (path,)).fetchone()
        if r:
            return r["vcodec"], r["acodec"], r["container"]
    finally:
        con.close()
    return "", "", ""

def remux_to_mp4(path):
    with _hls_lock:
        return _remux_to_mp4(path)

def _remux_to_mp4(path):
    """Copy video to MP4; convert incompatible audio only, cached by mtime."""
    import hashlib
    try:
        st = os.stat(path)
    except Exception:
        return None
    key = hashlib.md5(f"{path}:{st.st_mtime}:{st.st_size}:mp4v2".encode()).hexdigest()[:16]
    out = os.path.join(config.HLS_DIR, f"remux-{key}.mp4")
    if os.path.exists(out) and os.path.getsize(out) > 0:
        return out
    tmp = os.path.join(config.HLS_DIR, f"remux-{key}.tmp.mp4")  # keep .mp4 ext so ffmpeg infers format
    try:
        p = subprocess.run(["nice", "-n", "15", "ffmpeg", "-y", "-v", "error", "-i", path,
                            "-map", "0:v:0", "-map", "0:a:0?", "-c:v", "copy",
                            "-c:a", "copy" if ffprobe_codecs(path)[1] == "aac" else "aac", "-ac", "2", "-b:a", "128k", "-threads", "2",
                            "-tag:v", "hvc1" if ffprobe_codecs(path)[0] in ("hevc", "h265") else "avc1",
                            "-movflags", "+faststart", tmp],
                           timeout=120, capture_output=True)
        if p.returncode == 0 and os.path.exists(tmp):
            os.rename(tmp, out)
            return out
    except Exception as e:
        log.warning("remux failed %s: %s", path, e)
    try:
        if os.path.exists(tmp):
            os.unlink(tmp)
    except Exception:
        pass
    return None

def hls_for(path, offset=0):
    """Cache a whole-file HLS timeline; clients seek to their own live offset.

    Copy video only. Never launch an unbounded video transcode on the receiver.
    """
    import hashlib
    try:
        st = os.stat(path)
    except OSError:
        return None
    key = hashlib.sha256(f"{path}:{st.st_mtime_ns}:{st.st_size}:vod2".encode()).hexdigest()[:16]
    directory = os.path.join(config.HLS_DIR, f"hls-{key}")
    playlist = os.path.join(directory, "index.m3u8")
    with _hls_lock:
        if os.path.isfile(playlist):
            return f"hls-{key}/index.m3u8"
        os.makedirs(directory, exist_ok=True)
        vc, ac, _ = ffprobe_codecs(path)
        if vc not in ("h264", "avc", "hevc", "h265"):
            return None
        temporary = os.path.join(directory, "building.m3u8")
        try:
            result = subprocess.run(["nice", "-n", "15", "ffmpeg", "-y", "-v", "error", "-i", path,
                "-map", "0:v:0", "-map", "0:a:0?", "-c:v", "copy",
                "-c:a", "copy" if ac == "aac" else "aac", "-ac", "2", "-threads", "2",
                "-hls_time", "6", "-hls_list_size", "0", "-hls_playlist_type", "vod",
                "-hls_segment_type", "fmp4", "-hls_segment_filename", os.path.join(directory, "seg%05d.m4s"),
                temporary], timeout=120, capture_output=True)
            if result.returncode == 0:
                os.replace(temporary, playlist)
                return f"hls-{key}/index.m3u8"
            log.warning("HLS remux failed: %s", result.stderr.decode(errors="replace")[:300])
        except (OSError, subprocess.TimeoutExpired):
            log.exception("HLS remux failed")
    return None

def cleanup_hls(max_age_h=48):
    now = time.time()
    try:
        for name in os.listdir(config.HLS_DIR):
            p = os.path.join(config.HLS_DIR, name)
            try:
                if now - os.path.getmtime(p) > max_age_h * 3600:
                    if os.path.isdir(p):
                        import shutil
                        shutil.rmtree(p, ignore_errors=True)
                    else:
                        os.unlink(p)
            except Exception:
                pass
    except Exception:
        pass
