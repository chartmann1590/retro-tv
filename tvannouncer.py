"""Read new ArenaPulse plays over the HDMI sports broadcast."""
import logging
import os
import subprocess
import tempfile
import threading
import time
from collections import deque

import config
import database
import sports

log = logging.getLogger("retro-tv.tvannouncer")
_lock = threading.RLock()
_queue = deque(maxlen=4)
_game_key = ""
_thread = None
_playing = False
_last_text = ""
_last_spoken_at = 0
_last_error = ""
_serial = 0


def set_game(game_key):
    """Discard speech for a channel or matchup the viewer has left."""
    global _game_key
    with _lock:
        if game_key != _game_key:
            _queue.clear()
            _game_key = game_key


def enqueue(game_key, text, priority=False):
    global _thread, _serial
    clean = " ".join(str(text or "").split())[:320]
    if not clean:
        return False
    with _lock:
        if game_key != _game_key:
            return False
        if priority:
            _queue.clear()
        else:
            while _queue and not _queue[-1][2]:
                _queue.pop()
        _serial += 1
        _queue.append((game_key, clean, priority, _serial))
        if _thread is None or not _thread.is_alive():
            _thread = threading.Thread(target=_run, name="sports-live-announcer", daemon=True)
            _thread.start()
    return True


def status():
    with _lock:
        return {"game": _game_key, "playing": _playing, "queued": len(_queue),
                "last_text": _last_text, "last_spoken_at": _last_spoken_at,
                "error": _last_error}


def _current(game_key):
    with _lock:
        return game_key == _game_key


def _audible(game_key):
    return _current(game_key) and database.get_state("muted", "0") != "1"


def _run():
    global _playing, _last_text, _last_spoken_at, _last_error
    import playback

    while True:
        with _lock:
            if not _queue:
                _playing = False
                return
            game_key, narration, priority, serial = _queue.popleft()
        if not _audible(game_key):
            continue
        path = None
        process = None
        ducked = False
        cancelled = False
        try:
            for attempt in range(2):
                try:
                    audio, content_type = sports.synthesize_speech(narration, voice=config.TTS_VOICE_SPORTS)
                    break
                except Exception as exc:
                    with _lock:
                        stale = not priority and serial < _serial
                    if attempt or stale or not _audible(game_key) or "timed out" not in str(exc).lower():
                        raise
                    log.warning("ArenaPulse play narration timed out; retrying current play")
            if "wav" not in content_type.lower() or not audio.startswith(b"RIFF"):
                raise RuntimeError("ArenaPulse did not return WAV narration")
            with _lock:
                if not priority and serial < _serial:
                    continue
            if not _audible(game_key):
                continue
            with tempfile.NamedTemporaryFile(prefix="retro-sports-play-", suffix=".wav", delete=False) as out:
                out.write(audio)
                path = out.name
            command = ["pw-play", "--volume", str(max(0.01, min(1, int(database.get_state("volume", "80")) / 100))), path]
            target = config.AUDIO_DEVICE.removeprefix("pipewire/") if config.AUDIO_DEVICE.startswith("pipewire/") else ""
            if target:
                command[1:1] = ["--target", target]
            playback._ipc(["set_property", "volume", 15])
            ducked = True
            with _lock:
                _playing = True
                _last_text = narration
                _last_error = ""
            process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            deadline = time.monotonic() + 45
            while process.poll() is None:
                if not _audible(game_key):
                    cancelled = True
                    process.terminate()
                    break
                if time.monotonic() >= deadline:
                    process.terminate()
                    break
                time.sleep(.2)
            _, error = process.communicate(timeout=3)
            if process.returncode and not cancelled:
                raise RuntimeError((error or b"pw-play failed").decode(errors="replace")[:200])
            if not cancelled:
                with _lock:
                    _last_spoken_at = time.time()
        except Exception as exc:
            log.warning("Live play narration failed: %s", exc)
            with _lock:
                _last_error = str(exc)
        finally:
            if process and process.poll() is None:
                process.kill()
                process.wait()
            if ducked:
                playback._ipc(["set_property", "volume", int(database.get_state("volume", "80"))])
            if path:
                try:
                    os.unlink(path)
                except OSError:
                    pass
            with _lock:
                _playing = False
