"""Background HEVC replacement with validated H.264 copies on the media SSD."""
import logging
import json
import fcntl
import math
import os
import shutil
import signal
import subprocess
import threading
import time

import config
import database
import phone_transcode

log = logging.getLogger("retro-tv.transcode")

TRANSCODE_DIR = config.TRANSCODE_DIR
THERMAL_PAUSE_C = 80
THERMAL_RESUME_C = 74


def _cpu_temp_c():
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            return int(f.read().strip()) / 1000
    except (OSError, ValueError):
        return None


def _deprioritize():
    try:
        if hasattr(os, "nice"):
            os.nice(10)
    except Exception:
        pass


def playable_path(row):
    """Prefer a finished Pi-safe transcode over the original path, when present."""
    if row.get("transcode_status") == "done":
        alt = row.get("transcode_path")
        if alt and os.path.isfile(alt):
            return alt
    return row.get("path")


def next_pending(con):
    return con.execute(
        """SELECT id, path, duration FROM media_files WHERE transcode_status='pending'
           ORDER BY CASE WHEN transcode_worker='phone' THEN 0
                         WHEN transcode_error LIKE 'phone: phone thermal pause%'
                              OR transcode_error LIKE 'phone: phone paused%'
                              OR transcode_error LIKE 'phone: phone cooling%'
                              OR transcode_error LIKE 'phone: phone too hot%' THEN 0
                         WHEN transcode_error LIKE 'phone:%' THEN 2 ELSE 1 END,
                    id LIMIT 1"""
    ).fetchone()


def _progress_path(media_id):
    return os.path.join(TRANSCODE_DIR, f".progress_{media_id}")


def read_progress(media_id, source_duration, worker="pi"):
    """Parse ffmpeg's -progress key=value stream for the job's most recent
    values (later lines win on repeated keys, since ffmpeg appends a full
    block on every report rather than truncating the file)."""
    if worker == "phone":
        lines = phone_transcode.progress_text(media_id).splitlines()
    else:
        path = _progress_path(media_id)
        try:
            with open(path) as f:
                lines = f.readlines()
        except OSError:
            return None
    if not lines:
        return None
    fields = {}
    for line in lines:
        if "=" in line:
            k, v = line.strip().split("=", 1)
            fields[k] = v
    out_time_us = fields.get("out_time_us") or fields.get("out_time_ms")
    try:
        out_time_sec = max(0.0, float(out_time_us) / 1_000_000)
    except (TypeError, ValueError):
        out_time_sec = 0.0
    percent = min(99.0, out_time_sec / source_duration * 100) if source_duration > 0 else 0.0
    speed_str = (fields.get("speed") or "").strip().rstrip("x")
    try:
        speed = float(speed_str)
    except ValueError:
        speed = 0.0
    eta_sec = int((source_duration - out_time_sec) / speed) if speed > 0 else None
    return {
        "media_id": media_id, "percent": round(percent, 1), "speed": speed,
        "out_time_sec": round(out_time_sec), "source_duration": source_duration,
        "eta_sec": eta_sec, "done": fields.get("progress") == "end",
    }


def current_status():
    """Snapshot for the admin UI: the actively running job's live progress
    (if any) plus what's queued up next."""
    con = database.connect()
    try:
        running = con.execute(
            "SELECT id, path, duration, transcode_worker FROM media_files WHERE transcode_status='running' LIMIT 1").fetchone()
        pending = con.execute(
            "SELECT id, path, duration, transcode_worker, transcode_error FROM media_files WHERE transcode_status='pending' ORDER BY id").fetchall()
        done_phone = con.execute(
            "SELECT COUNT(*) FROM media_files WHERE transcode_status='done' AND transcode_worker='phone'").fetchone()[0]
    finally:
        con.close()
    import scanner
    result = {"running": None, "pending": [], "phone": phone_transcode.status(),
              "ssd_ready": scanner.ssd_storage_healthy(),
              "phone_completed": done_phone, "resume": None}
    if running:
        worker = running["transcode_worker"] or "pi"
        progress = read_progress(running["id"], running["duration"] or 0, worker)
        result["running"] = {"media_id": running["id"], "path": running["path"],
                              "duration": running["duration"], "worker": worker,
                              **(progress or {})}
    result["pending"] = [{"media_id": r["id"], "path": r["path"], "duration": r["duration"]} for r in pending]
    for item in pending:
        if item["transcode_worker"] == "phone" or (item["transcode_error"] or "").startswith("phone:"):
            partial = phone_transcode.partial_status(item["id"], item["duration"])
            if partial:
                result["resume"] = partial
                break
    return result


def _ffprobe_duration(path):
    # No preexec_fn here -- this runs inside run_loop's thread, which has already
    # niced itself; a forked child inherits that automatically. Adding another
    # os.nice(10) on top would compound to the OS-capped floor of 19, starving
    # this (and the ffmpeg encode below) far more than intended.
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration", "-of", "csv=p=0", path],
            capture_output=True, text=True, timeout=30)
        duration = float((out.stdout or "0").strip() or 0)
        return duration if out.returncode == 0 and math.isfinite(duration) and duration > 0 else 0
    except Exception:
        return 0


def _validated_output(path, source_duration):
    """Accept only a complete, browser and Pi compatible replacement."""
    try:
        result = subprocess.run([
            "ffprobe", "-v", "error", "-of", "json", "-show_entries",
            "format=duration,bit_rate:stream=codec_type,codec_name,width,height,pix_fmt", path],
            capture_output=True, text=True, timeout=30)
        if result.returncode:
            return None
        data = json.loads(result.stdout)
        streams = data.get("streams", [])
        video = next((s for s in streams if s.get("codec_type") == "video"), None)
        audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
        duration = float(data.get("format", {}).get("duration") or 0)
        if not video or video.get("codec_name") != "h264" or video.get("pix_fmt") != "yuv420p":
            return None
        if duration <= 0 or (source_duration > 0 and
                             abs(duration - source_duration) >= max(5, source_duration * 0.02)):
            return None
        stat = os.stat(path)
        if stat.st_size <= 0:
            return None
        return {"duration": duration, "vcodec": "h264", "acodec": (audio or {}).get("codec_name", ""),
                "width": int(video.get("width") or 0), "height": int(video.get("height") or 0),
                "bitrate": int(data.get("format", {}).get("bit_rate") or 0),
                "size": stat.st_size, "mtime": stat.st_mtime}
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


def _encode(src_path, out_path, source_duration, media_id):
    """Software-decode (avoids the same buggy hevc_v4l2m2m HW decoder), downscale
    to 1080p max, drop to 8-bit, and hardware-encode back to H.264 -- the exact
    codec/depth/resolution class already proven reliable throughout this library.
    Falls back to libx264 if the Pi's h264_v4l2m2m encoder errors on this input."""
    progress_path = _progress_path(media_id)
    base_cmd = ["ffmpeg", "-y", "-loglevel", "error", "-threads", "2", "-filter_threads", "1",
                "-i", src_path, "-map", "0:v:0", "-map", "0:a:0?",
                "-vf", "scale='min(1920,iw)':-2", "-pix_fmt", "yuv420p",
                "-c:a", "copy", "-f", "matroska", "-progress", progress_path, "-nostats"]
    attempts = [
        base_cmd + ["-c:v", "h264_v4l2m2m", "-b:v", "8M"],
        base_cmd + ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20"],
    ]
    last_err = ""
    for cmd in attempts:
        try:
            # No preexec_fn: run_loop's thread is already niced, and ffmpeg inherits
            # that via fork -- stacking another os.nice(10) here would compound to
            # the OS-capped nice 19 floor instead of the intended nice 10.
            proc = subprocess.Popen(cmd + [out_path], stdout=subprocess.DEVNULL,
                                    stderr=subprocess.PIPE, text=True)
            paused = False
            while True:
                try:
                    _, stderr = proc.communicate(timeout=5)
                    break
                except subprocess.TimeoutExpired:
                    temp = _cpu_temp_c()
                    if temp is not None and not paused and temp >= THERMAL_PAUSE_C:
                        proc.send_signal(signal.SIGSTOP)
                        paused = True
                        log.warning("Pausing transcode at %.1f C to protect playback", temp)
                    elif temp is not None and paused and temp <= THERMAL_RESUME_C:
                        proc.send_signal(signal.SIGCONT)
                        paused = False
                        log.info("Resuming transcode at %.1f C", temp)
        except (OSError, subprocess.SubprocessError) as e:
            last_err = str(e)
            continue
        if proc.returncode == 0 and os.path.isfile(out_path):
            out_duration = _ffprobe_duration(out_path)
            if source_duration <= 0 or abs(out_duration - source_duration) < max(5, source_duration * 0.02):
                return True, ""
            last_err = f"duration mismatch: source={source_duration:.1f}s output={out_duration:.1f}s"
        else:
            last_err = (stderr or "")[-2000:]
        try:
            os.remove(out_path)
        except OSError:
            pass
    return False, last_err


def run_one(media_id, src_path, source_duration, use_phone=False):
    """Serialize Pi and phone jobs even if two app processes are started."""
    os.makedirs(config.DATA_DIR, exist_ok=True)
    with open(os.path.join(config.DATA_DIR, "transcode.lock"), "a+") as lock_file:
        try:
            fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            log.info("Another transcoding worker is already active")
            return False
        try:
            _run_one_unlocked(media_id, src_path, source_duration, use_phone)
            return True
        finally:
            fcntl.flock(lock_file, fcntl.LOCK_UN)


def _run_one_unlocked(media_id, src_path, source_duration, use_phone=False):
    os.makedirs(TRANSCODE_DIR, exist_ok=True)
    tmp_path = os.path.join(TRANSCODE_DIR, f"{media_id}.mkv.tmp")
    # Keep completed media in the scanned library. An MKV can be atomically
    # replaced in place; other containers need an MKV sibling before deletion.
    final_path = (src_path if src_path.lower().endswith(".mkv") else
                  os.path.splitext(src_path)[0] + ".h264.mkv")
    # Scanner durations can be placeholders when probing originally failed.
    # Only a fresh source probe may set the chunk boundary and deletion check.
    measured_duration = _ffprobe_duration(src_path)
    if not measured_duration:
        con = database.connect()
        try:
            con.execute("""UPDATE media_files SET transcode_status='failed',
                transcode_error='source duration unavailable' WHERE id=?""", (media_id,))
            con.commit()
        finally:
            con.close()
        log.warning("Source duration unavailable; preserving media_id=%s", media_id)
        return
    source_duration = measured_duration
    con = database.connect()
    try:
        con.execute("""UPDATE media_files SET duration=?, transcode_status='running',
            transcode_worker=? WHERE id=?""", (source_duration, "phone" if use_phone else "pi", media_id))
        con.commit()
    finally:
        con.close()
    log.info("Transcode starting on %s: media_id=%s %r",
             "phone" if use_phone else "Pi", media_id, src_path)
    t0 = time.monotonic()
    try:
        if use_phone:
            ok, err = phone_transcode.encode(src_path, tmp_path, media_id, source_duration)
        else:
            ok, err = _encode(src_path, tmp_path, source_duration, media_id)
    except Exception:
        log.exception("Transcode crashed: media_id=%s", media_id)
        ok, err = False, "internal error, see log"
    finally:
        try:
            os.remove(_progress_path(media_id))
        except OSError:
            pass
    con = database.connect()
    try:
        details = _validated_output(tmp_path, source_duration) if ok else None
        if ok and details:
            import scanner
            with scanner._scan_lock:
                original = con.execute("SELECT * FROM media_files WHERE id=?", (media_id,)).fetchone()
                if os.stat(tmp_path).st_dev == os.stat(os.path.dirname(final_path)).st_dev:
                    os.replace(tmp_path, final_path)
                else:
                    # A non-SSD source may live on a different filesystem.
                    # Copy beside it and validate before the atomic rename.
                    staged_path = final_path + ".tmp"
                    try:
                        shutil.copyfile(tmp_path, staged_path)
                        if not _validated_output(staged_path, source_duration):
                            raise OSError("copied conversion failed validation")
                        os.replace(staged_path, final_path)
                        os.remove(tmp_path)
                    finally:
                        if os.path.exists(staged_path):
                            os.remove(staged_path)
                final_stat = os.stat(final_path)
                con.execute("""UPDATE media_files SET path=?, size=?, mtime=?, duration=?,
                    container='mkv', vcodec='h264', acodec=?, width=?, height=?, bitrate=?,
                    pix_fmt='yuv420p', bit_depth=8, compat_warning='',
                    transcode_status='done', transcode_path=?, transcode_error=''
                    WHERE id=?""", (final_path, final_stat.st_size, final_stat.st_mtime, details["duration"],
                    details["acodec"], details["width"], details["height"], details["bitrate"],
                    final_path, media_id))
                con.commit()
                converted = True
                if final_path != src_path:
                    try:
                        os.unlink(src_path)
                    except OSError:
                        log.exception("Could not remove converted source %s", src_path)
                        con.execute("""UPDATE media_files SET path=?, size=?, mtime=?, duration=?,
                            container=?, vcodec=?, acodec=?, width=?, height=?, bitrate=?,
                            pix_fmt=?, bit_depth=?, compat_warning=?, transcode_status='pending',
                            transcode_path='' WHERE id=?""",
                            (src_path, original["size"], original["mtime"], original["duration"],
                             original["container"], original["vcodec"], original["acodec"],
                             original["width"], original["height"], original["bitrate"],
                             original["pix_fmt"], original["bit_depth"], original["compat_warning"], media_id))
                        os.remove(final_path)
                        converted = False
                    else:
                        log.info("Transcode replaced source: media_id=%s in %.0fs -> %s",
                                 media_id, time.monotonic() - t0, final_path)
                else:
                    log.info("Transcode replaced source: media_id=%s in %.0fs -> %s",
                             media_id, time.monotonic() - t0, final_path)
                if use_phone and converted:
                    phone_transcode.finish(media_id)
        else:
            for p in (tmp_path,):
                try:
                    os.remove(p)
                except OSError:
                    pass
            status = "pending" if use_phone else "failed"
            error = (("phone: " if use_phone else "") + (err or "output validation failed"))[-2000:]
            if use_phone and ok:
                phone_transcode.reset(media_id)
            con.execute("UPDATE media_files SET transcode_status=?, transcode_error=? WHERE id=?",
                        (status, error, media_id))
            log.warning("Transcode failed: media_id=%s: %s", media_id, err[-500:])
        con.commit()
    finally:
        con.close()


def _has_disk_headroom(source_size):
    import shutil
    try:
        os.makedirs(TRANSCODE_DIR, exist_ok=True)
        free = shutil.disk_usage(TRANSCODE_DIR).free
    except OSError:
        return False
    return free > max(source_size * 2, 4_000_000_000)


def run_loop(stop_event=None):
    stop_event = stop_event or threading.Event()
    _deprioritize()
    # A restart (deploy, crash, reboot) mid-job orphans its row at 'running' forever,
    # since nothing else ever re-queues it -- reclaim those back to 'pending' once,
    # at startup, before the poll loop begins.
    try:
        con = database.connect()
        try:
            con.execute("""UPDATE media_files SET transcode_status='pending'
                WHERE transcode_status='running'""")
            con.commit()
        finally:
            con.close()
    except Exception:
        log.exception("Failed to reclaim orphaned running transcode jobs")
    missing_rechecked = False
    while not stop_event.is_set():
        try:
            phone_transcode.ensure_status_screen()
            import scanner
            if not scanner.ssd_storage_healthy():
                missing_rechecked = False
                log.warning("Media SSD unavailable; pausing show conversion without changing queue state")
                stop_event.wait(120)
                continue
            if not missing_rechecked:
                con = database.connect()
                try:
                    for row in con.execute("""SELECT id, path FROM media_files
                            WHERE transcode_status='failed' AND transcode_error='source file missing'"""):
                        if os.path.isfile(row["path"]):
                            con.execute("""UPDATE media_files SET transcode_status='pending',
                                transcode_error='' WHERE id=?""", (row["id"],))
                    con.commit()
                finally:
                    con.close()
                missing_rechecked = True
            con = database.connect()
            try:
                job = next_pending(con)
            finally:
                con.close()
            if job and os.path.isfile(job["path"]):
                phone_state = phone_transcode.status()
                # Keep a saved phone job on the phone while Android cools. A
                # temporary thermal pause must not restart it on the Pi.
                if ((os.environ.get("RETRO_TV_ADB_SERIAL") and not phone_state["ready"])
                        or (phone_state["connected"] and phone_state["installed"] and not phone_state["ready"])
                        or (not phone_state["ready"] and
                            phone_transcode.partial_status(job["id"], job["duration"]))):
                    stop_event.wait(30)
                    continue
                use_phone = phone_state["ready"]
                temp = _cpu_temp_c()
                if not use_phone and temp is not None and temp >= THERMAL_PAUSE_C:
                    stop_event.wait(30)
                    continue
                src_size = os.path.getsize(job["path"])
                if not _has_disk_headroom(src_size):
                    log.warning("Insufficient disk headroom to transcode media_id=%s (%r); waiting for free space", job["id"], job["path"])
                    stop_event.wait(120)
                    continue
                if not run_one(job["id"], job["path"], job["duration"], use_phone=use_phone):
                    stop_event.wait(30)
                if use_phone:
                    stop_event.wait(30)
                continue
            elif job:
                if not scanner.ssd_storage_healthy():
                    missing_rechecked = False
                    stop_event.wait(120)
                    continue
                # source vanished since being queued
                con = database.connect()
                try:
                    con.execute("UPDATE media_files SET transcode_status='failed', transcode_error='source file missing' WHERE id=?",
                                (job["id"],))
                    con.commit()
                finally:
                    con.close()
                continue
        except Exception:
            log.exception("Transcode loop iteration failed")
        stop_event.wait(60)
