"""Persistent HDMI player with serialized tuning and schedule-driven live playback."""
import glob
import json
import logging
import os
import shutil
import socket
import subprocess
import threading
import time

import config
import database
import scheduler

log = logging.getLogger("retro-tv.playback")
_lock = threading.RLock()
_proc = None
_monitor_stop = threading.Event()
_current = {"channel": None, "media": None, "entry_id": None, "started_ts": 0}


def _find_mpv():
    return next((p for p in config.MPV_CANDIDATES if os.path.isfile(p) and os.access(p, os.X_OK)), shutil.which("mpv"))


def _mpv_env():
    env = dict(os.environ)
    if os.path.isdir(config.LD_LIB_DIR):
        env["LD_LIBRARY_PATH"] = config.LD_LIB_DIR + (":" + env["LD_LIBRARY_PATH"] if env.get("LD_LIBRARY_PATH") else "")
    runtime = f"/run/user/{os.getuid()}"
    env.setdefault("XDG_RUNTIME_DIR", runtime)
    for display in ("wayland-0", "wayland-1"):
        if os.path.exists(os.path.join(runtime, display)):
            env.setdefault("WAYLAND_DISPLAY", display)
            break
    return env


def _ipc(command):
    """Wait for the command response; writing to a socket alone is not success."""
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(1)
            sock.connect(config.MPV_SOCKET)
            sock.sendall((json.dumps({"command": command, "request_id": 1}) + "\n").encode())
            with sock.makefile("r") as reader:
                for line in reader:
                    response = json.loads(line)
                    if response.get("request_id") == 1:
                        return response
    except (OSError, ValueError):
        pass
    return {"error": "Player unavailable"}


def _ipc_send(cmd):
    return _ipc(cmd["command"]).get("error") == "success"


def mpv_alive():
    # Never waitpid(-1): other threads own ffprobe/ffmpeg subprocesses.
    with _lock:
        return _proc is not None and _proc.poll() is None


def _hdmi_display_connected():
    """True if any HDMI connector currently reports a display attached (DRM
    connector status, not the audio ELD check _connected_hdmi_card does).
    Goes false when the TV is powered off (HDMI hotplug-detect drops) and
    true again once it powers back on."""
    for status_path in glob.glob("/sys/class/drm/card*-HDMI-A-*/status"):
        try:
            with open(status_path) as f:
                if f.read().strip() == "connected":
                    return True
        except OSError:
            continue
    return False


def _connected_hdmi_card():
    """ALSA card index of the HDMI output with an actual display attached (valid EDID/ELD).

    A Pi has one ALSA card per HDMI port; only the connected one reports a monitor_name.
    """
    for eld in sorted(glob.glob("/proc/asound/card*/eld*")):
        try:
            with open(eld) as f:
                if any(line.startswith("monitor_name") and line.split()[1:] for line in f):
                    return int(eld.split("/proc/asound/card")[1].split("/")[0])
        except (OSError, ValueError):
            continue
    return None


def audio_device():
    """Select the connected HDMI sink by ALSA card index, with an explicit override.

    Matching by card index (not just an alphabetical "hdmi" name match) matters on a
    Pi4: it has two HDMI ports/cards, and picking the wrong one sends audio to a port
    with nothing plugged in -- silent "HDMI audio" that never reaches the TV.
    """
    if config.AUDIO_DEVICE:
        return config.AUDIO_DEVICE
    connected_card = _connected_hdmi_card()
    try:
        result = subprocess.run(["pw-dump"], capture_output=True, text=True, timeout=3)
        sinks = []
        for obj in json.loads(result.stdout):
            props = obj.get("info", {}).get("props", {})
            name = props.get("node.name", "")
            if props.get("media.class") == "Audio/Sink" and "hdmi" in name:
                sinks.append((props.get("alsa.card"), name))
        if connected_card is not None:
            matched = [name for card, name in sinks if card == connected_card]
            if matched:
                return "pipewire/" + matched[0]
        if sinks:
            return "pipewire/" + sorted(name for _, name in sinks)[0]
    except (OSError, ValueError, subprocess.TimeoutExpired):
        pass
    # Standalone ALSA is useful when no desktop audio server is available.
    if connected_card is not None:
        try:
            with open(f"/proc/asound/card{connected_card}/id") as f:
                return f"alsa/hdmi:CARD={f.read().strip()},DEV=0"
        except OSError:
            pass
    return "auto"


def stop():
    global _proc
    with _lock:
        if mpv_alive():
            _ipc(["quit"])
            try:
                _proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                _proc.terminate()
                try:
                    _proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    _proc.kill()
                    _proc.wait(timeout=2)
        _proc = None
        _current.update(media=None, entry_id=None)


def play_file(path, offset=0, channel=None, title=None):
    """Reuse the player so channel changes do not rebuild video/audio devices."""
    global _proc
    if not path or not os.path.isfile(path):
        return False
    with _lock:
        if mpv_alive():
            # mpv 0.40 loadfile: filename, mode, playlist index, per-file options.
            result = _ipc(["loadfile", path, "replace", -1, {"start": str(max(0, offset))}])
            if result.get("error") == "success":
                _ipc(["set_property", "pause", False])
                _ipc(["set_property", "force-window", True])
                _ipc(["set_property", "title", title or "RETRO-TV"])
                # A freshly loaded file can bring its own embedded subtitle track back into
                # view regardless of what the viewer last chose, so re-assert it every load.
                _ipc(["set_property", "sub-visibility", database.get_state("cc_enabled", "0") == "1"])
                _current.update(channel=channel, media=path, started_ts=time.time())
                return True
        stop()
        mpv = _find_mpv()
        if not mpv:
            return False
        # hwdec=drm-copy, not auto/drm: zero-copy drm dmabuf frames decode fine but
        # labwc/vc4 silently fail to composite them, leaving the desktop background on
        # screen while mpv itself reports normal playback the whole time.
        cmd = [mpv, "--no-config", "--fullscreen", "--no-terminal", "--idle=yes",
               "--force-window=yes", "--keep-open=no", "--osc=no", "--osd-level=0",
               "--no-input-default-bindings", "--input-ipc-server=" + config.MPV_SOCKET,
               "--profile=fast", "--hwdec=drm-copy", "--vd-lavc-threads=2",
               "--demuxer-max-bytes=32MiB", "--demuxer-max-back-bytes=8MiB",
               "--audio-device=" + audio_device(), "--audio-channels=stereo", "--audio-samplerate=48000",
               "--volume=" + database.get_state("volume", "80"),
               "--mute=" + ("yes" if database.get_state("muted", "0") == "1" else "no"),
               "--sub-visibility=" + ("yes" if database.get_state("cc_enabled", "0") == "1" else "no"),
               f"--start={max(0, offset)}", "--title=" + (title or "RETRO-TV"), path]
        try:
            os.makedirs(config.LOGS_DIR, exist_ok=True)
            with open(os.path.join(config.LOGS_DIR, "mpv.log"), "a") as output:
                log.info("Starting HDMI player: %s", cmd)
                _proc = subprocess.Popen(cmd, env=_mpv_env(), stdout=output, stderr=output, start_new_session=True)
            for _ in range(30):
                if _proc.poll() is not None:
                    return False
                if _ipc(["get_property", "idle-active"]).get("data") is False:
                    _current.update(channel=channel, media=path, started_ts=time.time())
                    try:
                        import tvguide
                        if tvguide.is_visible():
                            tvguide.render()
                    except Exception:
                        pass
                    return True
                time.sleep(0.1)
        except (OSError, subprocess.SubprocessError):
            log.exception("HDMI player launch failed")
        return False


def tune(channel_number, reason="user"):
    import streaming
    with _lock:
        channel_number = int(channel_number)
        if channel_number not in {c["number"] for c in scheduler.get_channels()}:
            return {"ok": False, "error": "Channel is unavailable"}
        entry, offset, path, duration = streaming.resolve_live(channel_number)
        if not entry:
            return {"ok": False, "error": "No program scheduled"}
        slate = entry["kind"] == "slate"
        if slate:
            stop()
            ok = True
        elif path:
            title = f"CH {channel_number:02d}  {entry.get('title', '')}  {entry.get('subtitle', '')}"
            ok = play_file(path, offset, channel_number, title)
        else:
            return {"ok": False, "error": "Scheduled media is missing"}
        if ok:
            previous = database.get_state("last_channel", "")
            if previous and previous != str(channel_number):
                database.set_state("prev_channel", previous)
            if previous != str(channel_number):
                database.set_state("last_channel", channel_number)
            _current.update(channel=channel_number, media=path, entry_id=entry["id"], started_ts=time.time())
            if reason == "api":
                import tvguide
                tvguide.flash_channel(channel_number, entry)
            log.info("Tuned ch=%s reason=%s offset=%.1f", channel_number, reason, offset)
        return {"ok": ok, "entry": entry, "offset": offset, "slate": slate,
                **({} if ok else {"error": "HDMI player could not start; check playback logs"})}


def set_volume(vol):
    vol = max(0, min(100, int(vol)))
    database.set_state("volume", vol)
    return _ipc(["set_property", "volume", vol]).get("error") == "success"


def set_mute(muted):
    database.set_state("muted", "1" if muted else "0")
    return _ipc(["set_property", "mute", bool(muted)]).get("error") == "success"


def pause_toggle():
    return _ipc(["cycle", "pause"]).get("error") == "success"


def set_cc(enabled):
    database.set_state("cc_enabled", "1" if enabled else "0")
    return _ipc(["set_property", "sub-visibility", bool(enabled)]).get("error") == "success"


def restore_last():
    channels = scheduler.get_channels()
    if not channels:
        return {"ok": False, "error": "No channels yet"}
    try:
        last = int(database.get_state("last_channel", "0"))
    except ValueError:
        last = 0
    ch = last if last in {c["number"] for c in channels} else channels[0]["number"]
    return tune(ch, reason="restore")


def status():
    with _lock:
        alive = mpv_alive()
        return {"mpv_alive": alive, "paused": _ipc(["get_property", "pause"]).get("data", False) if alive else False,
                "volume": database.get_state("volume", "80"), "muted": database.get_state("muted", "0"),
                "cc": database.get_state("cc_enabled", "0")}


def show_info():
    entry = scheduler.now_playing(_current["channel"]) if _current["channel"] else None
    if not entry:
        return False
    return _ipc(["show-text", f"CH {_current['channel']:02d}  {entry.get('title', '')}\n{entry.get('subtitle', '')}", 5000]).get("error") == "success"


def osd_message(text, duration_ms=10000):
    """Non-interactive banner over whatever's currently on screen -- mpv hides it on
    its own after duration_ms, no server-side timer needed."""
    return _ipc(["show-text", text, duration_ms]).get("error") == "success"


def _check_reconnect(was_connected):
    """Returns the current HDMI-connected state, forcing a clean playback
    restart if it just transitioned from disconnected to connected (TV was
    off and powered back on). mpv doesn't reliably re-attach to a connector
    that came back after being torn down, and the audio sink can come back
    bound to the wrong device -- stop() here lets the normal changed/idle
    handling below relaunch mpv fresh, re-discovering the audio device."""
    now_connected = _hdmi_display_connected()
    if now_connected and not was_connected:
        log.info("HDMI display reconnected, forcing clean playback restart")
        stop()
    return now_connected


def monitor_loop(stop_event, poll=1):
    import streaming
    failures = 0
    retry_at = 0
    display_connected = _hdmi_display_connected()
    while not stop_event.wait(poll):
        try:
            display_connected = _check_reconnect(display_connected)
            import tvguide
            tvguide.refresh_if_visible()
            with _lock:
                ch = _current["channel"]
                if ch is None or time.monotonic() < retry_at:
                    continue
                entry, offset, path, duration = streaming.resolve_live(ch)
                if not entry or entry["kind"] == "slate":
                    if _current["media"]:
                        stop()
                    continue
                changed = entry["id"] != _current["entry_id"] or path != _current["media"]
                if not mpv_alive():
                    idle = True
                else:
                    # A timed-out/errored IPC call means mpv is busy (e.g. decoding under
                    # load), not idle -- treating it as idle here caused a restart storm
                    # that killed audio and any TV-guide overlay every few seconds.
                    probe = _ipc(["get_property", "idle-active"])
                    idle = probe.get("error") == "success" and probe.get("data") is True
                if changed or idle:
                    result = tune(ch, reason="schedule" if changed else "recovery")
                    failures = failures + 1 if not result["ok"] or time.time() - _current["started_ts"] < 2 else 0
                    retry_at = time.monotonic() + (min(60, 2 ** min(failures, 6)) if failures else 0)
                else:
                    failures = 0
        except Exception:
            log.exception("Playback monitor failed")
            retry_at = time.monotonic() + 5


def start_monitor():
    thread = threading.Thread(target=monitor_loop, args=(_monitor_stop,), daemon=True)
    thread.start()
    return thread
