"""Run queued HEVC conversions on the ADB-connected Android phone."""
import logging
import json
import math
import os
import re
import shutil
import socket
import subprocess
import threading
import time

import config
import database

log = logging.getLogger("retro-tv.phone-transcode")

PHONE_FFMPEG = "/data/local/tmp/retro-tv-ffmpeg"
PHONE_DIR = "/data/local/tmp/retro-tv-transcode"
BATTERY_PAUSE_C = 46.5
CHUNK_SECONDS = 60
SEEK_PREROLL_SECONDS = 10
_status_lock = threading.Lock()
_status_cache = (0.0, None)
_screen_opened = False


def _adb(*args, timeout=15, **kwargs):
    serial = os.environ.get("RETRO_TV_ADB_SERIAL")
    cmd = ["adb"] + (["-s", serial] if serial else []) + list(args)
    return subprocess.run(cmd, timeout=timeout, **kwargs)


def battery_temp_c():
    try:
        result = _adb("shell", "dumpsys", "battery", timeout=8,
                      capture_output=True, text=True)
        match = re.search(r"^\s*temperature:\s*(\d+)", result.stdout, re.MULTILINE)
        return int(match.group(1)) / 10 if result.returncode == 0 and match else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def thermal_status():
    try:
        result = _adb("shell", "dumpsys", "thermalservice", timeout=8,
                      capture_output=True, text=True)
        match = re.search(r"^Thermal Status:\s*(\d+)", result.stdout, re.MULTILINE)
        return int(match.group(1)) if result.returncode == 0 and match else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def battery_throttle_enabled():
    return database.get_setting("phone_battery_throttle_enabled", "0") == "1"


def _too_hot():
    temp = battery_temp_c()
    severity = thermal_status()
    battery_pause = battery_throttle_enabled() and (temp is None or temp >= BATTERY_PAUSE_C)
    return battery_pause or severity is None or severity >= 2, temp


def status():
    """Return a short-lived snapshot for both scheduling and the admin page."""
    global _status_cache, _screen_opened
    battery_enabled = battery_throttle_enabled()
    with _status_lock:
        if (time.monotonic() - _status_cache[0] < 5 and _status_cache[1] is not None
                and _status_cache[1]["battery_throttle_enabled"] == battery_enabled):
            return dict(_status_cache[1])
    result = {"connected": False, "installed": False, "ready": False,
              "model": "", "temperature_c": None, "thermal_status": None,
              "battery_throttle_enabled": battery_enabled,
              "pause_at_c": BATTERY_PAUSE_C if battery_enabled else None}
    try:
        state = _adb("get-state", timeout=5, capture_output=True, text=True)
        serial = os.environ.get("RETRO_TV_ADB_SERIAL", "")
        if (state.returncode != 0 or state.stdout.strip() != "device") and ":" in serial:
            subprocess.run(["adb", "connect", serial], timeout=8,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            state = _adb("get-state", timeout=5, capture_output=True, text=True)
        if state.returncode == 0 and state.stdout.strip() == "device":
            result["connected"] = True
            binary = _adb("shell", "test", "-x", PHONE_FFMPEG,
                          timeout=5, capture_output=True)
            result["installed"] = binary.returncode == 0
            model = _adb("shell", "getprop", "ro.product.model", timeout=5,
                         capture_output=True, text=True)
            result["model"] = model.stdout.strip() if model.returncode == 0 else "Android phone"
            result["temperature_c"] = battery_temp_c()
            result["thermal_status"] = thermal_status()
            result["ready"] = (result["installed"] and result["thermal_status"] is not None
                               and result["thermal_status"] < 2
                               and (not battery_enabled or (result["temperature_c"] is not None
                                    and result["temperature_c"] < BATTERY_PAUSE_C)))
    except (OSError, subprocess.TimeoutExpired):
        pass
    with _status_lock:
        _status_cache = (time.monotonic(), result)
        if not result["connected"]:
            _screen_opened = False
    return dict(result)


def available():
    return status()["ready"]


def ensure_status_screen():
    """Keep a low-load status page reachable on the physical phone via USB."""
    global _screen_opened
    if _screen_opened or not status()["connected"]:
        return
    try:
        for _ in range(10):
            try:
                with socket.create_connection(("127.0.0.1", config.PORT), timeout=1):
                    break
            except OSError:
                time.sleep(1)
        else:
            return
        _adb("reverse", "tcp:5000", "tcp:5000", timeout=10, check=True,
             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        _adb("shell", "am", "start", "-a", "android.intent.action.VIEW", "-d",
             "http://127.0.0.1:5000/phone-transcode", "-p", "com.android.chrome",
             timeout=10, check=True, stdout=subprocess.DEVNULL,
             stderr=subprocess.DEVNULL)
        _screen_opened = True
    except (OSError, subprocess.SubprocessError):
        log.warning("Could not open phone transcoding status screen")


def progress_text(media_id):
    state_path = os.path.join(_parts_dir(media_id), "active.json")
    try:
        with open(state_path) as state_file:
            base_seconds = float(json.load(state_file).get("start", 0))
    except (OSError, ValueError):
        base_seconds = 0
    try:
        result = _adb("shell", "cat", f"{PHONE_DIR}/{int(media_id)}.progress",
                      timeout=5, capture_output=True, text=True)
        if result.returncode:
            return f"out_time_us={int(base_seconds * 1_000_000)}\n"
        text = result.stdout
        matches = re.findall(r"^out_time_us=(\d+)$", text, re.MULTILINE)
        if matches:
            return text + f"\nout_time_us={int(base_seconds * 1_000_000) + int(matches[-1])}\n"
        return f"out_time_us={int(base_seconds * 1_000_000)}\n"
    except (OSError, subprocess.TimeoutExpired):
        return f"out_time_us={int(base_seconds * 1_000_000)}\n"


def _source_width(path):
    result = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                             "-show_entries", "stream=width", "-of", "csv=p=0", path],
                            capture_output=True, text=True, timeout=30)
    return int(result.stdout.strip())


def _stop_remote_encoder():
    try:
        result = _adb("shell", "pidof", "retro-tv-ffmpeg", timeout=5,
                      capture_output=True, text=True)
        for pid in result.stdout.split():
            if pid.isdigit():
                _adb("shell", "kill", "-TERM", pid, timeout=5,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        pass


def _parts_dir(media_id):
    return os.path.join(config.TRANSCODE_DIR, f".phone_{int(media_id)}")


def partial_status(media_id, duration):
    parts_dir = _parts_dir(media_id)
    try:
        with open(os.path.join(parts_dir, "manifest.json")) as manifest_file:
            chunk_seconds = int(json.load(manifest_file).get("chunk_seconds") or CHUNK_SECONDS)
    except (OSError, ValueError):
        return None
    completed = 0
    while os.path.isfile(os.path.join(parts_dir, f"part_{completed:05d}.mkv")):
        completed += 1
    if not completed:
        return None
    done_seconds = min(float(duration or 0), completed * chunk_seconds)
    return {"media_id": int(media_id), "completed_sections": completed,
            "encoded_seconds": done_seconds,
            "percent": round(done_seconds / float(duration) * 100, 1) if duration else 0}


def _valid_chunk(path, expected_duration):
    try:
        result = subprocess.run(["ffprobe", "-v", "error", "-of", "json",
                                 "-show_entries", "format=duration:stream=codec_type,codec_name,pix_fmt", path],
                                capture_output=True, text=True, timeout=30)
        data = json.loads(result.stdout) if result.returncode == 0 else {}
        video = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), {})
        duration = float(data.get("format", {}).get("duration") or 0)
        return (video.get("codec_name") == "h264" and video.get("pix_fmt") == "yuv420p"
                and duration > 0 and abs(duration - expected_duration) < max(2, expected_duration * 0.07))
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return False


def _remote_size(path):
    try:
        result = _adb("shell", "stat", "-c", "%s", path, timeout=8,
                      capture_output=True, text=True)
        return int(result.stdout.strip()) if result.returncode == 0 else -1
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return -1


def finish(media_id):
    """Reclaim scratch files only after the caller validated the final output."""
    shutil.rmtree(_parts_dir(media_id), ignore_errors=True)
    try:
        _adb("shell", "rm", "-f", f"{PHONE_DIR}/{int(media_id)}.source",
             timeout=15, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        pass


def reset(media_id):
    finish(media_id)


def encode(src_path, out_path, media_id, source_duration):
    """Resume saved phone sections, then join them without re-encoding on Pi."""
    remote_src = f"{PHONE_DIR}/{media_id}.source"
    remote_out = f"{PHONE_DIR}/{media_id}.part.mkv"
    remote_progress = f"{PHONE_DIR}/{media_id}.progress"
    parts_dir = _parts_dir(media_id)
    source_stat = os.stat(src_path)
    source_fingerprint = {"path": src_path, "size": source_stat.st_size,
                          "mtime_ns": source_stat.st_mtime_ns, "duration": source_duration}
    try:
        manifest_path = os.path.join(parts_dir, "manifest.json")
        try:
            with open(manifest_path) as manifest_file:
                existing = json.load(manifest_file)
        except (OSError, ValueError):
            existing = None
        # Keep the section length of an existing job so a tuning change never
        # discards already validated sections on the SSD.
        chunk_seconds = CHUNK_SECONDS
        if isinstance(existing, dict) and all(
                existing.get(key) == value for key, value in source_fingerprint.items()):
            try:
                saved_chunk_seconds = int(existing.get("chunk_seconds"))
                if 5 <= saved_chunk_seconds <= 300:
                    chunk_seconds = saved_chunk_seconds
            except (TypeError, ValueError):
                pass
        fingerprint = {**source_fingerprint, "chunk_seconds": chunk_seconds}
        source_changed = existing != fingerprint
        if source_changed:
            shutil.rmtree(parts_dir, ignore_errors=True)
            os.makedirs(parts_dir, exist_ok=True)
            with open(manifest_path, "w") as manifest_file:
                json.dump(fingerprint, manifest_file)
        duration = float(source_duration)
        if duration <= 0:
            return False, "source duration unavailable"
        total_chunks = math.ceil(duration / chunk_seconds)
        if total_chunks > 1 and duration - (total_chunks - 1) * chunk_seconds < 1:
            total_chunks -= 1
        completed = 0
        while completed < total_chunks:
            part = os.path.join(parts_dir, f"part_{completed:05d}.mkv")
            expected = (duration - completed * chunk_seconds if completed == total_chunks - 1
                        else chunk_seconds)
            if not os.path.isfile(part) or not _valid_chunk(part, expected):
                break
            completed += 1
        for name in os.listdir(parts_dir):
            match = re.fullmatch(r"part_(\d{5})\.mkv", name)
            if match and int(match.group(1)) >= completed:
                os.remove(os.path.join(parts_dir, name))
        log.info("Phone resume: media_id=%s section %s/%s", media_id, completed, total_chunks)
        width = _source_width(src_path)
        _adb("shell", "mkdir", "-p", PHONE_DIR, timeout=10, check=True,
             stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        if completed < total_chunks and (source_changed or _remote_size(remote_src) != source_stat.st_size):
            _adb("push", src_path, remote_src, timeout=3600, check=True,
                 stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        if _too_hot()[0]:
            return False, "phone thermal pause after transfer"
        while completed < total_chunks:
            start = completed * chunk_seconds
            length = duration - start if completed == total_chunks - 1 else chunk_seconds
            seek_start = max(0, start - SEEK_PREROLL_SECONDS)
            decode_skip = start - seek_start
            if _too_hot()[0]:
                return False, f"phone thermal pause after {completed}/{total_chunks} sections"
            with open(os.path.join(parts_dir, "active.json"), "w") as state_file:
                json.dump({"start": start, "total": duration}, state_file)
            command = ["shell", PHONE_FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
                       "-threads", "6", "-filter_threads", "1", "-ss", str(seek_start),
                       "-i", remote_src, "-ss", str(decode_skip)]
            if completed != total_chunks - 1:
                command += ["-t", str(length)]
            command += ["-map", "0:v:0",
                       "-map", "0:a:0?", "-pix_fmt", "nv12"]
            if width > 1280:
                command += ["-vf", "scale=1280:-2"]
            command += ["-c:v", "h264_mediacodec", "-b:v", "4M", "-c:a", "copy",
                        "-f", "matroska", "-progress", remote_progress, "-nostats", remote_out]
            serial = os.environ.get("RETRO_TV_ADB_SERIAL")
            process = subprocess.Popen(["adb"] + (["-s", serial] if serial else []) + command,
                                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                       text=True)
            while True:
                try:
                    _, stderr = process.communicate(timeout=10)
                    break
                except subprocess.TimeoutExpired:
                    hot, temp = _too_hot()
                    if hot:
                        _stop_remote_encoder()
                        process.terminate()
                        try:
                            process.communicate(timeout=5)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.communicate()
                        return False, f"phone thermal pause with battery at {temp} C after {completed}/{total_chunks} sections"
            if process.returncode:
                return False, (stderr or "phone encoder failed")[-2000:]
            local_tmp = os.path.join(parts_dir, f"part_{completed:05d}.tmp")
            _adb("pull", remote_out, local_tmp, timeout=3600, check=True,
                 stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            if not _valid_chunk(local_tmp, length):
                os.remove(local_tmp)
                return False, f"phone section {completed} failed validation"
            os.replace(local_tmp, os.path.join(parts_dir, f"part_{completed:05d}.mkv"))
            completed += 1
            log.info("Phone encoded media_id=%s section %s/%s", media_id, completed, total_chunks)
        list_path = os.path.join(parts_dir, "concat.txt")
        with open(list_path, "w") as listing:
            for index in range(total_chunks):
                listing.write(f"file 'part_{index:05d}.mkv'\n")
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0",
                        "-i", list_path, "-c", "copy", "-f", "matroska", out_path],
                       check=True, timeout=3600, stdout=subprocess.DEVNULL,
                       stderr=subprocess.PIPE)
        return True, ""
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        return False, str(exc)[-2000:]
    finally:
        try:
            _adb("shell", "rm", "-f", remote_out, remote_progress,
                 timeout=15, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except (OSError, subprocess.TimeoutExpired):
            pass
