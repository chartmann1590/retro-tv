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
  name TEXT UNIQUE NOT NULL,
  poster TEXT DEFAULT '',
  description TEXT DEFAULT '',
  meta_source TEXT DEFAULT ''
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
  sort_order INTEGER DEFAULT 0,
  favorite INTEGER DEFAULT 0
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
CREATE TABLE IF NOT EXISTS reminders (
  id INTEGER PRIMARY KEY,
  entry_id INTEGER,
  channel_number INTEGER NOT NULL,
  title TEXT NOT NULL,
  subtitle TEXT DEFAULT '',
  start_ts REAL NOT NULL,
  created_ts REAL NOT NULL,
  notified INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_reminders_start ON reminders(start_ts);
CREATE TABLE IF NOT EXISTS pair_codes (
  code TEXT PRIMARY KEY,
  created_ts REAL NOT NULL,
  expires_ts REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS paired_devices (
  token TEXT PRIMARY KEY,
  device_name TEXT NOT NULL,
  paired_ts REAL NOT NULL,
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
        cols = {r["name"] for r in con.execute("PRAGMA table_info(channels)")}
        if "favorite" not in cols:
            con.execute("ALTER TABLE channels ADD COLUMN favorite INTEGER DEFAULT 0")
        show_cols = {r["name"] for r in con.execute("PRAGMA table_info(shows)")}
        if "poster" not in show_cols:
            con.execute("ALTER TABLE shows ADD COLUMN poster TEXT DEFAULT ''")
        if "description" not in show_cols:
            con.execute("ALTER TABLE shows ADD COLUMN description TEXT DEFAULT ''")
        if "meta_source" not in show_cols:
            con.execute("ALTER TABLE shows ADD COLUMN meta_source TEXT DEFAULT ''")
        mf_cols = {r["name"] for r in con.execute("PRAGMA table_info(media_files)")}
        if "pix_fmt" not in mf_cols:
            con.execute("ALTER TABLE media_files ADD COLUMN pix_fmt TEXT DEFAULT ''")
        if "bit_depth" not in mf_cols:
            con.execute("ALTER TABLE media_files ADD COLUMN bit_depth INTEGER DEFAULT 8")
        if "transcode_status" not in mf_cols:
            con.execute("ALTER TABLE media_files ADD COLUMN transcode_status TEXT DEFAULT ''")
        if "transcode_path" not in mf_cols:
            con.execute("ALTER TABLE media_files ADD COLUMN transcode_path TEXT DEFAULT ''")
        if "transcode_error" not in mf_cols:
            con.execute("ALTER TABLE media_files ADD COLUMN transcode_error TEXT DEFAULT ''")
        if "transcode_worker" not in mf_cols:
            con.execute("ALTER TABLE media_files ADD COLUMN transcode_worker TEXT DEFAULT ''")
        for k, v in config.DEFAULT_SETTINGS.items():
            con.execute("INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)", (k, v))
        defaults = [
            ("last_channel", ""),
            ("prev_channel", ""),
            ("volume", "80"),
            ("muted", "0"),
            ("cc_enabled", "0"),
        ]
        for k, v in defaults:
            con.execute("INSERT OR IGNORE INTO playback_state(key,value) VALUES(?,?)", (k, v))
        # No default remote_mappings seeding here: remote.get_mappings() already merges
        # remote.DEFAULTS (correct browser KeyboardEvent.key values, e.g. "ArrowDown")
        # under any DB rows. A duplicate seed list used to live here with wrong,
        # X11-keysym-style values ("Down", "Return", "Page_Up", "BackSpace", "space",
        # "plus", "minus") that silently broke every browser-side remote/guide keyboard
        # action, since a pre-existing DB row always wins over remote.DEFAULTS and
        # INSERT OR IGNORE re-planted those wrong rows on every restart.
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


def create_pair_code(code, expires_sec=600):
    con = connect()
    try:
        now = time.time()
        con.execute("DELETE FROM pair_codes WHERE expires_ts < ?", (now,))
        con.execute("INSERT OR REPLACE INTO pair_codes(code, created_ts, expires_ts) VALUES(?,?,?)",
                    (str(code), now, now + expires_sec))
        con.commit()
    finally:
        con.close()


def verify_and_consume_pair_code(code, device_name="Android Companion"):
    import uuid
    con = connect()
    try:
        now = time.time()
        r = con.execute("SELECT code FROM pair_codes WHERE code=? AND expires_ts >= ?", (str(code).strip(), now)).fetchone()
        if not r:
            return None
        con.execute("DELETE FROM pair_codes WHERE code=?", (str(code).strip(),))
        token = uuid.uuid4().hex
        con.execute("INSERT OR REPLACE INTO paired_devices(token, device_name, paired_ts, last_seen_ts) VALUES(?,?,?,?)",
                    (token, device_name, now, now))
        con.commit()
        return token
    finally:
        con.close()


def is_device_paired(token):
    if not token:
        return False
    con = connect()
    try:
        now = time.time()
        r = con.execute("SELECT token FROM paired_devices WHERE token=?", (token,)).fetchone()
        if r:
            con.execute("UPDATE paired_devices SET last_seen_ts=? WHERE token=?", (now, token))
            con.commit()
            return True
        return False
    finally:
        con.close()


def list_paired_devices():
    con = connect()
    try:
        rows = con.execute("SELECT rowid AS device_id, device_name, paired_ts, last_seen_ts FROM paired_devices ORDER BY paired_ts DESC").fetchall()
        return [dict(r) for r in rows]
    finally:
        con.close()


def revoke_paired_device(identifier):
    if not identifier:
        return False
    con = connect()
    try:
        if isinstance(identifier, int) or (isinstance(identifier, str) and identifier.isdigit()):
            cur = con.execute("DELETE FROM paired_devices WHERE rowid=?", (int(identifier),))
        else:
            cur = con.execute("DELETE FROM paired_devices WHERE token=?", (str(identifier),))
        con.commit()
        return cur.rowcount > 0
    finally:
        con.close()
