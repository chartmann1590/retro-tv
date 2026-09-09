"""SQLite schema + helpers. Uses sqlite3 directly (no SQLAlchemy)."""
import os
import sqlite3
import time
import config

SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS media_files (
  id INTEGER PRIMARY KEY,
  path TEXT UNIQUE NOT NULL,
  kind TEXT NOT NULL,            -- episode|movie|commercial|unknown
  size INTEGER DEFAULT 0,
  mtime REAL DEFAULT 0,
  duration REAL DEFAULT 0,
  container TEXT DEFAULT '',
  resolution TEXT DEFAULT '',
  width INTEGER DEFAULT 0,
  height INTEGER DEFAULT 0,
  vcodec TEXT DEFAULT '',
  acodec TEXT DEFAULT '',
  bitrate INTEGER DEFAULT 0,
  compat_warning TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_media_kind ON media_files(kind);
CREATE TABLE IF NOT EXISTS shows (
  id INTEGER PRIMARY KEY,
  name TEXT UNIQUE NOT NULL
);
CREATE TABLE IF NOT EXISTS episodes (
  id INTEGER PRIMARY KEY,
  media_id INTEGER UNIQUE NOT NULL REFERENCES media_files(id) ON DELETE CASCADE,
  show_id INTEGER REFERENCES shows(id),
  show_name TEXT DEFAULT '',
  season INTEGER,
  episode INTEGER,
  title TEXT DEFAULT '',
  description TEXT DEFAULT '',
  runtime REAL DEFAULT 0,
  artwork TEXT DEFAULT '',
  meta_source TEXT DEFAULT 'filename'
);
CREATE INDEX IF NOT EXISTS idx_ep_show ON episodes(show_id);
CREATE TABLE IF NOT EXISTS movies (
  id INTEGER PRIMARY KEY,
  media_id INTEGER UNIQUE NOT NULL REFERENCES media_files(id) ON DELETE CASCADE,
  title TEXT DEFAULT '',
  year INTEGER,
  description TEXT DEFAULT '',
  runtime REAL DEFAULT 0,
  artwork TEXT DEFAULT '',
  genre TEXT DEFAULT '',
  meta_source TEXT DEFAULT 'filename'
);
CREATE TABLE IF NOT EXISTS commercials (
  id INTEGER PRIMARY KEY,
  media_id INTEGER UNIQUE NOT NULL REFERENCES media_files(id) ON DELETE CASCADE,
  name TEXT DEFAULT '',
  category TEXT DEFAULT '',
  duration REAL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS channels (
  number INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  enabled INTEGER DEFAULT 1,
  color TEXT DEFAULT '#1a3a6b',
  logo TEXT DEFAULT '',
  ordering TEXT DEFAULT 'shuffle',   -- shuffle|sequential
  commercial_mode TEXT DEFAULT 'between', -- off|between|mid
  max_repeats_per_day INTEGER DEFAULT 2,
  sort_order INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS channel_sources (
  id INTEGER PRIMARY KEY,
  channel_number INTEGER NOT NULL REFERENCES channels(number) ON DELETE CASCADE,
  source_type TEXT NOT NULL,  -- show|season|movie_folder|movie|commercials|all_tv|all_movies|genre
  source_value TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_src_ch ON channel_sources(channel_number);
CREATE TABLE IF NOT EXISTS schedule_entries (
  id INTEGER PRIMARY KEY,
  channel_number INTEGER NOT NULL,
  start_ts REAL NOT NULL,
  end_ts REAL NOT NULL,
  kind TEXT NOT NULL,        -- episode|movie|commercial_break
  media_id INTEGER,          -- for episode/movie; NULL for break
  commercial_ids TEXT DEFAULT '',  -- JSON list for breaks
  title TEXT DEFAULT '',
  subtitle TEXT DEFAULT '',
  description TEXT DEFAULT '',
  day TEXT NOT NULL          -- YYYY-MM-DD in America/New_York
);
CREATE INDEX IF NOT EXISTS idx_sched_ch_day ON schedule_entries(channel_number, day);
CREATE INDEX IF NOT EXISTS idx_sched_start ON schedule_entries(start_ts);
CREATE TABLE IF NOT EXISTS metadata_cache (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  updated_ts REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS playback_state (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS remote_mappings (
  action TEXT PRIMARY KEY,
  code TEXT NOT NULL,
  label TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS play_history (
  id INTEGER PRIMARY KEY,
  channel_number INTEGER,
  media_id INTEGER,
  played_ts REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_hist_ch ON play_history(channel_number, played_ts);
CREATE TABLE IF NOT EXISTS commercial_history (
  id INTEGER PRIMARY KEY,
  commercial_id INTEGER,
  played_ts REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS commercial_rotation (
  media_id INTEGER PRIMARY KEY REFERENCES media_files(id) ON DELETE CASCADE,
  last_used INTEGER NOT NULL DEFAULT 0,
  source_offset REAL NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS stream_sessions (
  id TEXT PRIMARY KEY,
  channel_number INTEGER,
  created_ts REAL NOT NULL,
  last_seen_ts REAL NOT NULL
);
"""

def connect():
    os.makedirs(config.DATA_DIR, exist_ok=True)
    con = sqlite3.connect(config.DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL;")
    con.execute("PRAGMA foreign_keys=ON;")
    return con

def init_db():
    con = connect()
    try:
        con.executescript(SCHEMA)
        # migration: genre column added after initial release, existing DBs predate it
        cols = {r["name"] for r in con.execute("PRAGMA table_info(movies)")}
        if "genre" not in cols:
            con.execute("ALTER TABLE movies ADD COLUMN genre TEXT DEFAULT ''")
        for k, v in config.DEFAULT_SETTINGS.items():
            con.execute("INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)", (k, v))
        defaults = [
            ("last_channel", ""),
            ("prev_channel", ""),
            ("volume", "80"),
            ("muted", "0"),
        ]
        for k, v in defaults:
            con.execute("INSERT OR IGNORE INTO playback_state(key,value) VALUES(?,?)", (k, v))
        # default remote mappings (keyboard codes)
        remotes = {
            "GUIDE": "g", "CHANNEL_UP": "Page_Up", "CHANNEL_DOWN": "Page_Down",
            "PREV_CHANNEL": "BackSpace", "INFO": "i", "PLAY_PAUSE": "space",
            "BACK": "Escape", "UP": "Up", "DOWN": "Down", "LEFT": "Left",
            "RIGHT": "Right", "OK": "Return", "VOLUME_UP": "plus",
            "VOLUME_DOWN": "minus", "MUTE": "m", "POWER_MENU": "p",
            "HOME": "Home", "MENU": "Menu",
        }
        for a, c in remotes.items():
            con.execute("INSERT OR IGNORE INTO remote_mappings(action,code) VALUES(?,?)", (a, c))
        con.commit()
    finally:
        con.close()

def get_setting(key, default=""):
    con = connect()
    try:
        r = con.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return r["value"] if r else default
    finally:
        con.close()

def set_setting(key, value):
    con = connect()
    try:
        con.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
        con.commit()
    finally:
        con.close()

def get_state(key, default=""):
    con = connect()
    try:
        r = con.execute("SELECT value FROM playback_state WHERE key=?", (key,)).fetchone()
        return r["value"] if r else default
    finally:
        con.close()

def set_state(key, value):
    con = connect()
    try:
        con.execute("INSERT INTO playback_state(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))
        con.commit()
    finally:
        con.close()

def backup_db():
    import shutil, datetime
    try:
        dst = os.path.join(config.DATA_DIR, "retro_tv.backup.db")
        con = connect()
        try:
            b = sqlite3.connect(dst, timeout=30)
            con.backup(b)
            b.close()
        finally:
            con.close()
    except Exception:
        pass
