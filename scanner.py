"""Media scanning: filename parsing + ffprobe + SQLite + compat warnings."""
import os
import re
import json
import sqlite3
import subprocess
import threading
import time

_scan_lock = threading.Lock()
import logging
import database
import config

log = logging.getLogger("retro-tv.scanner")


def ssd_mounts():
    return [os.path.join(category, "SSD") for category in
            (config.TV_DIR, config.MOVIES_DIR, config.COMMERCIALS_DIR)]


def wait_for_media_mounts(timeout=90, stop_event=None):
    """Give the external SSD's bind mounts time to appear after user login."""
    deadline = time.monotonic() + timeout
    while True:
        missing = [path for path in ssd_mounts() if not os.path.ismount(path)]
        if not missing:
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            log.warning("Media mounts still unavailable after %ss: %s", timeout, missing)
            return False
        if stop_event:
            if stop_event.wait(min(1, remaining)):
                return False
        else:
            time.sleep(min(1, remaining))

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
    # A file sitting directly under the Movies root has no such subfolder -- parts[-2] would
    # otherwise resolve to "Movies" itself, which isn't a title.
    parent = parts[-2] if len(parts) >= 2 and parts[-2] != os.path.basename(config.MOVIES_DIR) else ""
    source = parent or fname
    year = None
    m = YEAR_RE.search(source) or YEAR_RE.search(fname)
    if m:
        year = int(m.group(1))
    title = clean_name(YEAR_RE.sub("", source))
    title = re.sub(r"(?i)\b(1080p|720p|2160p|4k|bluray|web-?dl|webrip|hdtv|x264|x265|hevc|aac|ac3|dts|proper|extended|remux|director.?s.?cut)\b.*", "", title).strip(" -_.")
    return {"title": title or fname, "year": year}

def _deprioritize():
    try:
        if hasattr(os, "nice"):
            os.nice(10)
    except Exception:
        pass

def ffprobe(path):
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_entries",
             "format=duration,bit_rate:stream=codec_type,codec_name,width,height,pix_fmt", path],
            capture_output=True, text=True, timeout=30, preexec_fn=_deprioritize)
        if out.returncode != 0:
            return {}
        return json.loads(out.stdout or "{}")
    except Exception as e:
        log.warning("ffprobe failed %s: %s", path, e)
        return {}

def probe_info(path):
    info = {"duration": 0, "container": os.path.splitext(path)[1].lower().lstrip("."),
            "width": 0, "height": 0, "resolution": "", "vcodec": "", "acodec": "", "bitrate": 0,
            "pix_fmt": "", "bit_depth": 8}
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
                info["pix_fmt"] = (s.get("pix_fmt") or "").lower()
                info["bit_depth"] = 10 if "10" in info["pix_fmt"] else 8
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

def needs_hw_transcode(info):
    """HEVC needs an H.264 copy for smooth HDMI and browser playback on the Pi 4.

    drm-copy shows a correct HDMI picture while the copy is being prepared, but
    large HEVC files can still drop frames.
    """
    vc = (info.get("vcodec") or "").lower()
    return vc in ("hevc", "h265")

def _transcode_headroom_ok(source_size, reserved=0):
    """Guard against auto-queueing more transcode work than the disk can hold.

    The output is written to config.TRANSCODE_DIR on the media SSD. The original
    stays in place until the output is validated, then it is removed. Require
    headroom for the temporary overlap, already queued work, and a fixed floor;
    compat_warning() still surfaces files that cannot be queued yet.
    """
    import shutil
    try:
        os.makedirs(config.TRANSCODE_DIR, exist_ok=True)
        free = shutil.disk_usage(config.TRANSCODE_DIR).free
    except OSError:
        return False
    effective_free = free - reserved
    return effective_free > max(source_size * 2, 4_000_000_000)

def compat_warning(info, kind):
    warns = []
    h = info.get("height", 0)
    vc = (info.get("vcodec") or "").lower()
    ac = (info.get("acodec") or "").lower()
    cont = (info.get("container") or "").lower()
    br = info.get("bitrate", 0)
    if needs_hw_transcode(info):
        warns.append("HEVC may drop frames on Pi 4; queued for an H.264 replacement when disk space and temperature allow")
    elif h >= 2000:
        warns.append("4K may stutter on Pi 4; prefer 1080p")
    if br and br > 20_000_000:
        warns.append(f"high bitrate {br//1_000_000}Mbps may stutter")
    if vc in ("av1",):
        warns.append("AV1 has no HW decode on Pi 4 (CPU-heavy)")
    if vc in ("vp9",) and h >= 1000:
        warns.append("VP9 1080p+ is CPU-decoded; may struggle")
    if vc in ("hevc", "h265") and h >= 2000 and not needs_hw_transcode(info):
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
        r_res = con.execute("SELECT COALESCE(SUM(size), 0) s FROM media_files WHERE transcode_status IN ('pending', 'running')").fetchone()
        reserved_transcode_bytes = int(r_res["s"] if r_res else 0) * 2
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
        if light and prev and prev["size"] == st.st_size and abs(prev["mtime"] - st.st_mtime) < 1 \
                and not (prev.get("vcodec", "").lower() in ("hevc", "h265") and not prev.get("pix_fmt")):
            continue
        unchanged = prev and prev["size"] == st.st_size and abs(prev["mtime"] - st.st_mtime) < 1
        # One-time backfill: rows probed before pix_fmt/bit_depth existed need a cheap
        # re-probe (ffprobe only, not a re-transcode) so existing HEVC files get checked
        # against needs_hw_transcode() too -- otherwise already-scanned broken files like
        # Sinners would never get flagged.
        needs_backfill = unchanged and prev.get("vcodec", "").lower() in ("hevc", "h265") and not prev.get("pix_fmt")
        needs_probe = not unchanged or not prev["duration"] or needs_backfill
        info = {}
        if needs_probe:
            info = probe_info(path)
        else:
            info = {"duration": prev["duration"], "container": prev["container"],
                    "resolution": prev["resolution"], "width": prev["width"], "height": prev["height"],
                    "vcodec": prev["vcodec"], "acodec": prev["acodec"], "bitrate": prev["bitrate"],
                    "pix_fmt": prev.get("pix_fmt", ""), "bit_depth": prev.get("bit_depth", 8)}
        if not info.get("duration"):
            info["duration"] = 1320 if kind == "episode" else (5400 if kind == "movie" else 30)
        warn = compat_warning(info, kind)
        hw_risk = needs_hw_transcode(info)
        con = database.connect()
        try:
            if prev:
                # Only (re)queue a transcode if this file is newly-flagged or its content
                # changed -- never clobber an in-progress/finished job on an unrelated rescan.
                # Re-read live rather than trusting the `existing` snapshot taken at the start
                # of this (possibly many-minutes-long) scan pass: the transcode worker runs
                # concurrently and can move pending->running->done/failed while we're still
                # iterating, and writing back the stale snapshot value would clobber that.
                live_status = con.execute("SELECT transcode_status FROM media_files WHERE id=?", (prev["id"],)).fetchone()
                prev_status = live_status["transcode_status"] if live_status else ""
                should_queue = hw_risk and (not unchanged or prev_status == "") and _transcode_headroom_ok(st.st_size, reserved=reserved_transcode_bytes)
                if should_queue:
                    new_status = "pending"
                    if prev_status != "pending":
                        reserved_transcode_bytes += st.st_size * 2
                elif not hw_risk:
                    new_status = ""
                else:
                    new_status = prev_status
                con.execute("""UPDATE media_files SET kind=?,size=?,mtime=?,duration=?,container=?,
                    resolution=?,width=?,height=?,vcodec=?,acodec=?,bitrate=?,compat_warning=?,
                    pix_fmt=?,bit_depth=?,transcode_status=? WHERE path=?""",
                    (kind, st.st_size, st.st_mtime, info["duration"], info["container"], info["resolution"],
                     info["width"], info["height"], info["vcodec"], info["acodec"], info["bitrate"], warn,
                     info["pix_fmt"], info["bit_depth"], new_status, path))
                updated += 1
                media_id = prev["id"]
            else:
                queue_insert = hw_risk and _transcode_headroom_ok(st.st_size, reserved=reserved_transcode_bytes)
                if queue_insert:
                    reserved_transcode_bytes += st.st_size * 2
                try:
                    cur = con.execute("""INSERT INTO media_files(path,kind,size,mtime,duration,container,resolution,
                        width,height,vcodec,acodec,bitrate,compat_warning,pix_fmt,bit_depth,transcode_status)
                        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (path, kind, st.st_size, st.st_mtime, info["duration"], info["container"], info["resolution"],
                         info["width"], info["height"], info["vcodec"], info["acodec"], info["bitrate"], warn,
                         info["pix_fmt"], info["bit_depth"],
                         "pending" if queue_insert else ""))
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
    # An SSD can be absent or late to mount during startup. Never treat every
    # file below its bind mount as deleted merely because the mount is missing.
    missing_ssd_roots = [path for path in ssd_mounts() if not os.path.ismount(path)]
    if missing_ssd_roots:
        log.warning("Media mounts unavailable; preserving indexed files under %s", missing_ssd_roots)
    # remove files that are truly gone from accessible media roots
    con = database.connect()
    try:
        for p, row in existing.items():
            if p in found or os.path.exists(p):
                continue
            if any(p.startswith(root + os.sep) for root in missing_ssd_roots):
                continue
            mid = row["id"]
            con.execute("DELETE FROM episodes WHERE media_id=?", (mid,))
            con.execute("DELETE FROM movies WHERE media_id=?", (mid,))
            con.execute("DELETE FROM commercials WHERE media_id=?", (mid,))
            con.execute("DELETE FROM media_files WHERE id=?", (mid,))
        con.commit()
    finally:
        con.close()
    try:
        import vod
        vod.clear_cache()
    except Exception:
        pass
    return {"added": added, "updated": updated, "total": len(found)}

def media_disk_usage():
    """Combined disk space across every distinct filesystem mounted at or under
    MEDIA_ROOT -- not just shutil.disk_usage(MEDIA_ROOT), which only sees whatever
    filesystem MEDIA_ROOT itself sits on and misses expansion drives bind-mounted
    into a subfolder (e.g. TVShows/SSD). Dedupes by device so a drive bind-mounted
    into multiple category folders (TVShows/SSD, Movies/SSD, ...) is only counted
    once, not once per bind."""
    import shutil
    root = os.path.realpath(config.MEDIA_ROOT)
    mounts = {}
    try:
        root_dev = os.stat(root).st_dev
        mounts[root_dev] = root
    except OSError:
        pass
    try:
        with open("/proc/mounts") as f:
            for line in f:
                parts = line.split()
                if len(parts) < 2:
                    continue
                mp = parts[1]
                if mp == root or mp.startswith(root + os.sep):
                    try:
                        dev = os.stat(mp).st_dev
                    except OSError:
                        continue
                    mounts.setdefault(dev, mp)
    except OSError:
        pass
    breakdown = []
    total = used = free = 0
    for path in mounts.values():
        try:
            du = shutil.disk_usage(path)
        except OSError:
            continue
        total += du.total
        used += du.used
        free += du.free
        breakdown.append({"path": path, "total": du.total, "used": du.used, "free": du.free})
    breakdown.sort(key=lambda b: b["path"])
    return {"total": total, "used": used, "free": free, "breakdown": breakdown}


def library_summary():
    con = database.connect()
    try:
        n_ep = con.execute("SELECT COUNT(*) c FROM episodes").fetchone()["c"]
        n_mv = con.execute("SELECT COUNT(*) c FROM movies").fetchone()["c"]
        n_ad = con.execute("SELECT COUNT(*) c FROM commercials").fetchone()["c"]
        shows = [dict(r) for r in con.execute("SELECT s.name, COUNT(e.id) c FROM shows s LEFT JOIN episodes e ON e.show_id=s.id GROUP BY s.id ORDER BY s.name")]
        movie_list = [dict(r) for r in con.execute(
            "SELECT mo.title, mo.year, m.path FROM movies mo JOIN media_files m ON m.id=mo.media_id ORDER BY mo.title")]
        movie_folders = sorted({os.path.dirname(m["path"]) for m in movie_list if os.path.dirname(m["path"]) != config.MOVIES_DIR.rstrip("/")})
        genre_rows = con.execute("SELECT DISTINCT genre FROM movies WHERE genre<>''").fetchall()
        genres = sorted({g.strip() for r in genre_rows for g in r["genre"].split(",") if g.strip()})
        warns = [dict(r) for r in con.execute("SELECT path, compat_warning FROM media_files WHERE compat_warning<>'' ORDER BY path")]
        transcodes = [dict(r) for r in con.execute(
            "SELECT path, transcode_status, transcode_error FROM media_files WHERE transcode_status<>'' ORDER BY id")]
        return {"episodes": n_ep, "movies": n_mv, "commercials": n_ad, "shows": shows,
                "movie_list": movie_list, "movie_folders": movie_folders, "genres": genres, "warnings": warns,
                "transcodes": transcodes}
    finally:
        con.close()
