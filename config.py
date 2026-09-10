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
    # Gotify (https://gotify.net) push for reminders -- self-hosted, needs a server
    # URL and an app token created in its UI. Blank/disabled = reminders still show
    # on the TV and in any open browser tab, just no phone push.
    "gotify_enabled": "0",
    "gotify_url": "",
    "gotify_token": "",
}

# Optional mpv audio-device override; otherwise discover the HDMI sink.
AUDIO_DEVICE = os.environ.get("RETRO_TV_AUDIO_DEVICE", "")

# OMDb API key for movie genre/description enrichment (metadata.enrich_movies).
# Free key from https://www.omdbapi.com/apikey.aspx; enrichment is skipped without one.
OMDB_API_KEY = os.environ.get("OMDB_API_KEY", "")

# A show with at least this many episodes and no channel of its own yet gets one
# auto-created (scheduler.auto_create_channels, run from the periodic background scan).
AUTO_CHANNEL_MIN_EPISODES = 10
AUTO_CHANNEL_COLORS = ["#2e7d5b", "#1e8a8a", "#d4a017", "#c93a6b", "#7a5ab8",
                       "#3a8a3a", "#b8543a", "#3a5ab8", "#8a1e6b", "#5a8a1e"]

# Generated weather/news channels (livecontent.py). Regenerated on this cadence
# from live data, narrated with Microsoft Edge's free neural TTS (needs internet,
# no API key). Segments are stored outside MEDIA_ROOT since scanner.py never
# needs to discover them -- livecontent registers them directly.
WEATHER_ZIP = os.environ.get("WEATHER_ZIP", "12308")
LIVE_CONTENT_DIR = os.path.join(DATA_DIR, "live_content")
LIVE_CONTENT_REFRESH_SEC = 30 * 60
TTS_VOICE_WEATHER = os.environ.get("TTS_VOICE_WEATHER", "en-US-AriaNeural")
TTS_VOICE_NEWS = os.environ.get("TTS_VOICE_NEWS", "en-US-GuyNeural")
NEWS_RSS_URL = os.environ.get("NEWS_RSS_URL", "https://wnyt.com/feed/")
NEWS_ARTICLE_COUNT = 5

# Reminders (reminders.py): user sets one for a specific upcoming airing (from the
# guide or a search result); a background loop fires it this many seconds before
# start -- flashed on the TV via mpv's OSD and, if Gotify is configured above,
# pushed to the phone. The on-screen banner (TV and any open browser tab) auto-hides
# after REMINDER_OSD_MS regardless of whether it was acted on.
REMINDER_LEAD_SEC = 120
REMINDER_CHECK_INTERVAL_SEC = 15
REMINDER_OSD_MS = 10000
