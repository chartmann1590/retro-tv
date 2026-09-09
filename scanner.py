"""Media scanning: filename parsing + ffprobe + SQLite + compat warnings."""
import os
import re
import json
import sqlite3
import subprocess
import threading

_scan_lock = threading.Lock()
import logging
import database
import config

log = logging.getLogger("retro-tv.scanner")

SE_PATTERNS = [
    re.compile(r"[Ss](\d{1,2})[Ee](\d{1,3})"),           # S05E02
    re.compile(r"(\d{1,2})[xX](\d{1,3})"),                # 5x02
    re.compile(r"[Ss]eason\s*0*(\d+)[^\d]+0*(\d+)", re.I),# Season 05 ... 02
    re.compile(r"\b(\d{1,2})-(\d{1,3})\b"),               # 05-02 fallback
]
YEAR_RE = re.compile(r"[\(\[]?(19\d{2}|20\d{2})[\)\]]?")
TITLE_CLEAN = re.compile(r"[._]+")

def clean_name(s):
    s = TITLE_CLEAN.sub(" ", s).strip()
    s = re.sub(r"\s+", " ", s)
    return s

def parse_episode(relpath):
    """Return dict(show, season, episode, title) best-effort from path."""
    parts = relpath.split(os.sep)
    fname = os.path.splitext(parts[-1])[0]
    parent = parts[-2] if len(parts) >= 2 else ""
    grand = parts[-3] if len(parts) >= 3 else ""
    show = None
    # directory heuristic: TVShows/<Show>/[Season 05]/file
    if len(parts) >= 2:
        if re.search(r"season|series|disk|disc", parent, re.I) and grand:
            show = clean_name(grand)
        elif parent and parent.lower() not in ("tvshows",):
            # if filename has SxxExx, parent dir is likely the show
            show = clean_name(parent)
    season = ep = None
    for pat in SE_PATTERNS:
        m = pat.search(fname)
        if m:
            try:
                season, ep = int(m.group(1)), int(m.group(2))
                break
            except Exception:
                pass
    # title = text after SxxExx, stripped of quality tags
    title = ""
    if season is not None:
        m = re.search(r"[Ss]\d{1,2}[Ee]\d{1,3}\s*[-–.]?\s*(.*)", fname)
        if m:
            title = clean_name(re.sub(r"(?i)\b(1080p|720p|2160p|4k|bluray|web-?dl|webrip|hdtv|x264|x265|hevc|aac|ac3|dts|proper|extended|remux)\b.*", "", m.group(1)).strip(" -_."))
    if not show:
        # try "Show - S05E02 - Title"
        m = re.match(r"\s*(.+?)\s*[-–]\s*[Ss]\d{1,2}[Ee]\d{1,3}", fname)
        if m:
            show = clean_name(m.group(1))
    if not show:
        show = clean_name(parent) if parent else "Unknown Show"
    if not title:
        title = clean_name(re.sub(r"(?i)\b(S\d+E\d+|\d+x\d+|1080p|720p|2160p|4k)\b", "", fname).strip(" -_."))
    return {"show": show or "Unknown Show", "season": season, "episode": ep, "title": title or fname}

def parse_movie(relpath):
    parts = relpath.split(os.sep)
    fname = os.path.splitext(parts[-1])[0]
    # A per-movie subfolder (the common "Movies/Title (Year)/file.ext" layout) names the
    # film properly even when the file inside it doesn't (rips, screen recordings, etc.).
    parent = parts[-2] if len(parts) >= 2 else ""
    source = parent or fname
    year = None
    m = YEAR_RE.search(source) or YEAR_RE.search(fname)
    if m:
        year = int(m.group(1))
    title = clean_name(YEAR_RE.sub("", source))
    title = re.sub(r"(?i)\b(1080p|720p|2160p|4k|bluray|web-?dl|webrip|hdtv|x264|x265|hevc|aac|ac3|dts|proper|extended|remux|director.?s.?cut)\b.*", "", title).strip(" -_.")
    return {"title": title or fname, "year": year}

def ffprobe(path):
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", "-show_streams", path],
            capture_output=True, text=True, timeout=30)
        if out.returncode != 0:
            return {}
        return json.loads(out.stdout or "{}")
    except Exception as e:
        log.warning("ffprobe failed %s: %s", path, e)
        return {}

def probe_info(path):
    info = {"duration": 0, "container": os.path.splitext(path)[1].lower().lstrip("."),
            "width": 0, "height": 0, "resolution": "", "vcodec": "", "acodec": "", "bitrate": 0}
    data = ffprobe(path)
    try:
        fmt = data.get("format", {})
        info["duration"] = float(fmt.get("duration") or 0)
        if fmt.get("bit_rate"):
            info["bitrate"] = int(fmt["bit_rate"])
        for s in data.get("streams", []):
            if s.get("codec_type") == "video" and not info["vcodec"]:
                info["vcodec"] = (s.get("codec_name") or "").lower()
                info["width"] = int(s.get("width") or 0)
                info["height"] = int(s.get("height") or 0)
            if s.get("codec_type") == "audio" and not info["acodec"]:
                info["acodec"] = (s.get("codec_name") or "").lower()
        h = info["height"]
        if h >= 2000:
            info["resolution"] = "4K"
        elif h >= 1000:
            info["resolution"] = "1080p"
        elif h >= 650:
            info["resolution"] = "720p"
        elif h > 0:
            info["resolution"] = "SD"
        if not info["duration"]:
            # fallback: estimate 22min episodes / 90min movies later
            info["duration"] = 0
    except Exception as e:
        log.warning("probe parse failed %s: %s", path, e)
    return info

def compat_warning(info, kind):
    warns = []
    h = info.get("height", 0)
    vc = (info.get("vcodec") or "").lower()
    ac = (info.get("acodec") or "").lower()
    cont = (info.get("container") or "").lower()
    br = info.get("bitrate", 0)
    if h >= 2000:
        warns.append("4K may stutter on Pi 4; prefer 1080p")
    if br and br > 20_000_000:
        warns.append(f"high bitrate {br//1_000_000}Mbps may stutter")
    if vc in ("av1",):
        warns.append("AV1 has no HW decode on Pi 4 (CPU-heavy)")
    if vc in ("vp9",) and h >= 1000:
        warns.append("VP9 1080p+ is CPU-decoded; may struggle")
    if vc in ("hevc", "h265") and h >= 2000:
        warns.append("HEVC 4K is heavy; 1080p HEVC OK via V4L2")
    if vc not in ("h264", "avc", "hevc", "h265", "mpeg2video", "mpeg4", ""):
        warns.append(f"codec {vc or '?'} may need transcode for browsers")
    if ac not in ("aac", "mp3", "ac3", "eac3", "opus", "vorbis", ""):
        warns.append(f"audio {ac} may need remux/transcode in browser")
    if cont in ("avi", "wmv", "flv"):
        warns.append(f"container .{cont} usually needs remux for browsers")
    if cont == "mkv" and vc in ("h264", "avc", "hevc", "h265"):
        warns.append("MKV plays locally; browsers prefer MP4 (remux)")
    return "; ".join(warns)

def iter_media_files():
    for root, dirs, files in os.walk(config.MEDIA_ROOT):
        # skip the prompt txt itself
        for f in files:
            p = os.path.join(root, f)
            ext = os.path.splitext(f)[1].lower()
            if ext in config.VIDEO_EXTS:
                yield p

def classify(path):
    rel = os.path.relpath(path, config.MEDIA_ROOT)
    top = rel.split(os.sep)[0].lower() if os.sep in rel else ""
    if top.startswith("commercial"):
        return "commercial"
    if top.startswith("movie"):
        return "movie"
    if top.startswith("tv"):
        return "episode"
    return "unknown"

def full_scan(light=False):
    with _scan_lock:
        return _full_scan(light)

def _full_scan(light=False):
    """Full scan (ffprobe new/changed only). light=True skips unchanged mtime/size."""
    database.init_db()
    found = set()
    added = updated = 0
    con = database.connect()
    try:
        existing = {r["path"]: dict(r) for r in con.execute("SELECT * FROM media_files")}
    finally:
        con.close()
    for path in iter_media_files():
        try:
            st = os.stat(path)
        except FileNotFoundError:
            continue
        found.add(path)
        kind = classify(path)
        prev = existing.get(path)
        if light and prev and prev["size"] == st.st_size and abs(prev["mtime"] - st.st_mtime) < 1:
            continue
        needs_probe = True
        if prev and prev["size"] == st.st_size and abs(prev["mtime"] - st.st_mtime) < 1 and prev["duration"]:
            needs_probe = False
        info = {}
        if needs_probe:
            info = probe_info(path)
        else:
            info = {"duration": prev["duration"], "container": prev["container"],
                    "resolution": prev["resolution"], "width": prev["width"], "height": prev["height"],
                    "vcodec": prev["vcodec"], "acodec": prev["acodec"], "bitrate": prev["bitrate"]}
        if not info.get("duration"):
            info["duration"] = 1320 if kind == "episode" else (5400 if kind == "movie" else 30)
        warn = compat_warning(info, kind)
        con = database.connect()
        try:
            if prev:
                con.execute("""UPDATE media_files SET kind=?,size=?,mtime=?,duration=?,container=?,
                    resolution=?,width=?,height=?,vcodec=?,acodec=?,bitrate=?,compat_warning=? WHERE path=?""",
                    (kind, st.st_size, st.st_mtime, info["duration"], info["container"], info["resolution"],
                     info["width"], info["height"], info["vcodec"], info["acodec"], info["bitrate"], warn, path))
                updated += 1
                media_id = prev["id"]
            else:
                try:
                    cur = con.execute("""INSERT INTO media_files(path,kind,size,mtime,duration,container,resolution,
                        width,height,vcodec,acodec,bitrate,compat_warning) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (path, kind, st.st_size, st.st_mtime, info["duration"], info["container"], info["resolution"],
                         info["width"], info["height"], info["vcodec"], info["acodec"], info["bitrate"], warn))
                    media_id = cur.lastrowid
                    added += 1
                except sqlite3.IntegrityError:
                    # concurrent scan inserted it first; adopt that row
                    con.rollback()
                    r = con.execute("SELECT id FROM media_files WHERE path=?", (path,)).fetchone()
                    media_id = r["id"]
                    updated += 1
            rel = os.path.relpath(path, config.MEDIA_ROOT)
            if kind == "episode":
                ep = parse_episode(rel)
                con.execute("INSERT OR IGNORE INTO shows(name) VALUES(?)", (ep["show"],))
                r = con.execute("SELECT id FROM shows WHERE name=?", (ep["show"],)).fetchone()
                sid = r["id"] if r else None
                con.execute("""INSERT INTO episodes(media_id,show_id,show_name,season,episode,title,meta_source)
                    VALUES(?,?,?,?,?,?,?) ON CONFLICT(media_id) DO UPDATE SET show_id=excluded.show_id,
                    show_name=excluded.show_name, season=excluded.season, episode=excluded.episode,
                    title=excluded.title""",
                    (media_id, sid, ep["show"], ep["season"], ep["episode"], ep["title"], "filename"))
            elif kind == "movie":
                mv = parse_movie(rel)
                con.execute("""INSERT INTO movies(media_id,title,year,meta_source) VALUES(?,?,?,?)
                    ON CONFLICT(media_id) DO UPDATE SET title=excluded.title, year=excluded.year""",
                    (media_id, mv["title"], mv["year"], "filename"))
            elif kind == "commercial":
                con.execute("""INSERT INTO commercials(media_id,name,duration) VALUES(?,?,?)
                    ON CONFLICT(media_id) DO UPDATE SET name=excluded.name, duration=excluded.duration""",
                    (media_id, os.path.basename(path), info["duration"]))
            con.commit()
        finally:
            con.close()
    # remove missing files (keep schedule history intact; mark schedules referencing them)
    con = database.connect()
    try:
        for p, row in existing.items():
            if p not in found and os.path.exists(p) is False:
                # only delete if file truly gone
                if not os.path.exists(p):
                    mid = row["id"]
                    con.execute("DELETE FROM episodes WHERE media_id=?", (mid,))
                    con.execute("DELETE FROM movies WHERE media_id=?", (mid,))
                    con.execute("DELETE FROM commercials WHERE media_id=?", (mid,))
                    con.execute("DELETE FROM media_files WHERE id=?", (mid,))
        con.commit()
    finally:
        con.close()
    return {"added": added, "updated": updated, "total": len(found)}

def library_summary():
    con = database.connect()
    try:
        n_ep = con.execute("SELECT COUNT(*) c FROM episodes").fetchone()["c"]
        n_mv = con.execute("SELECT COUNT(*) c FROM movies").fetchone()["c"]
        n_ad = con.execute("SELECT COUNT(*) c FROM commercials").fetchone()["c"]
        shows = [dict(r) for r in con.execute("SELECT s.name, COUNT(e.id) c FROM shows s LEFT JOIN episodes e ON e.show_id=s.id GROUP BY s.id ORDER BY s.name")]
        warns = [dict(r) for r in con.execute("SELECT path, compat_warning FROM media_files WHERE compat_warning<>'' ORDER BY path")]
        return {"episodes": n_ep, "movies": n_mv, "commercials": n_ad, "shows": shows, "warnings": warns}
    finally:
        con.close()
