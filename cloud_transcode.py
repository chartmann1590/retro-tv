"""Transfer one queued show to an SSH VM, encode there, and fetch the result."""
import logging
import os
import shlex
import subprocess

import config

log = logging.getLogger("retro-tv.cloud-transcode")


def _ssh(remote_command):
    return ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15",
            config.CLOUD_TRANSCODE_HOST, remote_command]


def _remote_path(media_id, suffix):
    return f"{config.CLOUD_TRANSCODE_DIR.rstrip('/')}/{int(media_id)}.{suffix}"


def _rsync(source, destination):
    return subprocess.run(["rsync", "-a", "-s", "--partial", "--append-verify",
                           "--timeout=120",
                           "-e", "ssh -o BatchMode=yes -o ConnectTimeout=15",
                           source, destination], capture_output=True, text=True)


def encode(src_path, out_path, media_id, source_duration):
    """Keep source/partial transfers for a retry; never alter the local source."""
    host = config.CLOUD_TRANSCODE_HOST.strip()
    remote_dir = config.CLOUD_TRANSCODE_DIR
    if not host or not remote_dir.startswith("/") or remote_dir == "/":
        return False, "cloud host or absolute work directory is not configured"
    remote_src = _remote_path(media_id, "source")
    remote_out = _remote_path(media_id, "h264.mkv")
    try:
        setup = subprocess.run(_ssh("mkdir -p " + shlex.quote(remote_dir) +
                                     " && command -v ffmpeg >/dev/null"),
                               capture_output=True, text=True, timeout=30)
        if setup.returncode:
            return False, (setup.stderr or "cloud SSH/ffmpeg setup failed")[-2000:]
        sent = _rsync(src_path, f"{host}:{remote_src}")
        if sent.returncode:
            return False, (sent.stderr or "cloud upload failed")[-2000:]
        # Audio is copied and the image is capped at 1080p, matching the Pi
        # worker's validated H.264/yuv420p output contract.
        command = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", remote_src,
                   "-map", "0:v:0", "-map", "0:a:0?", "-vf", "scale='min(1920,iw)':-2",
                   "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "veryfast",
                   "-crf", "20", "-c:a", "copy", "-f", "matroska", "-progress", "pipe:1",
                   "-nostats", remote_out]
        progress_path = os.path.join(config.TRANSCODE_DIR, f".progress_{int(media_id)}")
        with open(progress_path, "w", encoding="utf-8") as progress:
            with subprocess.Popen(_ssh(" ".join(shlex.quote(part) for part in command)),
                                  stdout=progress, stderr=subprocess.PIPE, text=True) as proc:
                _, stderr = proc.communicate()
                if proc.returncode:
                    return False, (stderr or "cloud ffmpeg failed")[-2000:]
        fetched = _rsync(f"{host}:{remote_out}", out_path)
        if fetched.returncode:
            return False, (fetched.stderr or "cloud download failed")[-2000:]
        return True, ""
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("Cloud transcode interrupted: media_id=%s: %s", media_id, exc)
        return False, str(exc)[-2000:]


def finish(media_id):
    """Free VM space only after local validation and source replacement."""
    if not config.CLOUD_TRANSCODE_HOST:
        return
    paths = [_remote_path(media_id, "source"), _remote_path(media_id, "h264.mkv")]
    try:
        subprocess.run(_ssh("rm -f -- " + " ".join(shlex.quote(p) for p in paths)),
                       capture_output=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        log.warning("Could not remove completed cloud files for media_id=%s", media_id)
