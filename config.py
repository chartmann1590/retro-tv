"""Retro TV configuration."""
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
LOGS_DIR = os.path.join(BASE_DIR, "logs")
HLS_DIR = os.path.join(BASE_DIR, "hls_cache")
MPV_SOCKET = "/tmp/retro-tv-mpv.sock"

DB_PATH = os.path.join(DATA_DIR, "retro_tv.db")
LOG_PATH = os.path.join(LOGS_DIR, "retro-tv.log")

MEDIA_ROOT = "/srv/media"
TV_DIR = os.path.join(MEDIA_ROOT, "TVShows")
MOVIES_DIR = os.path.join(MEDIA_ROOT, "Movies")
COMMERCIALS_DIR = os.path.join(MEDIA_ROOT, "Commercials")

TIMEZONE = "America/New_York"
HOST = "0.0.0.0"
PORT = 5000

VIDEO_EXTS = {".mkv", ".mp4", ".m4v", ".avi", ".ts", ".m2ts", ".mov", ".wmv", ".flv", ".webm", ".ogv", ".mpg", ".mpeg", ".vob"}

MPV_LOCAL = os.path.join(BASE_DIR, "mpv-local", "usr", "bin", "mpv")
MPV_CANDIDATES = [
    "/usr/bin/mpv",                          # distro build: real Pi HW video decode (V4L2 M2M)
    os.path.join(BASE_DIR, "bin", "mpv"),    # bundled wrapper (sets LD_LIBRARY_PATH); software decode only
    os.path.join(BASE_DIR, "mpv-local", "usr", "bin", "mpv"),
    "mpv",
]
LD_LIB_DIR = os.path.join(BASE_DIR, "mpv-local", "usr", "lib", "aarch64-linux-gnu")

COMMERCIAL_TARGET_MIN = 90
COMMERCIAL_TARGET_MAX = 180
SCHEDULE_DAYS_AHEAD = 3
SCAN_INTERVAL_SEC = 15 * 60  # lightweight rescan every 15 min
SCHEDULER_INTERVAL_SEC = 3600  # ensure future schedules hourly

DEFAULT_SETTINGS = {
    "osd_timeout_sec": "4",
    "static_effect": "1",
    "channel_black_frame": "1",
    "default_commercial_mode": "between",  # off|between|mid
    "timezone": TIMEZONE,
}

# Optional mpv audio-device override; otherwise discover the HDMI sink.
AUDIO_DEVICE = os.environ.get("RETRO_TV_AUDIO_DEVICE", "")
