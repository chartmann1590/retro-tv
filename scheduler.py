"""Scheduling engine: multi-day lineups, commercial breaks, no-repeat rules, reboot-safe."""
import json
import logging
import random
import re
import sqlite3
import hashlib
import threading
from functools import wraps
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import config
import database

log = logging.getLogger("retro-tv.scheduler")
TZ = ZoneInfo(config.TIMEZONE)
_schedule_lock = threading.RLock()

def serialized(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        with _schedule_lock:
            return fn(*args, **kwargs)
    return wrapped

def local_day(ts=None):
    dt = datetime.fromtimestamp(ts or datetime.now(TZ).timestamp(), TZ)
    return dt.strftime("%Y-%m-%d")

def day_bounds(day_str):
    start = datetime.strptime(day_str, "%Y-%m-%d").replace(tzinfo=TZ)
    return start.timestamp(), (start + timedelta(days=1)).timestamp()

def get_channels(enabled_only=True):
    con = database.connect()
    try:
        q = "SELECT * FROM channels"
        if enabled_only:
            q += " WHERE enabled=1"
        q += " ORDER BY number"
        return [dict(r) for r in con.execute(q)]
    finally:
        con.close()

@serialized
def auto_create_channels():
    """Give any show with enough episodes and no channel of its own yet a dedicated
    channel, matching the sequential single-show pattern used for the hand-created
    ones. Idempotent: a show is skipped once a 'show' source for it exists anywhere."""
    con = database.connect()
    try:
        counts = {r["show_name"]: r["n"] for r in con.execute(
            "SELECT show_name, COUNT(*) n FROM episodes GROUP BY show_name")}
        covered = {r["source_value"] for r in con.execute(
            "SELECT source_value FROM channel_sources WHERE source_type='show'")}
        used_numbers = [r["number"] for r in con.execute("SELECT number FROM channels")]
        used_colors = {r["color"] for r in con.execute("SELECT color FROM channels")}
    finally:
        con.close()
    created = []
    next_num = (max(used_numbers) if used_numbers else 0) + 1
    for show_name, n in counts.items():
        if not show_name or show_name in covered or n < config.AUTO_CHANNEL_MIN_EPISODES:
            continue
        color = next((c for c in config.AUTO_CHANNEL_COLORS if c not in used_colors),
                     config.AUTO_CHANNEL_COLORS[next_num % len(config.AUTO_CHANNEL_COLORS)])
        used_colors.add(color)
        display_name = re.sub(r"\s*\(\d{4}\)$", "", show_name).strip() or show_name
        con = database.connect()
        try:
            con.execute("""INSERT INTO channels(number,name,enabled,color,ordering,commercial_mode,max_repeats_per_day,sort_order)
                VALUES(?,?,1,?,?,?,?,?)""", (next_num, display_name, color, "sequential", "between", 2, next_num))
            con.execute("INSERT INTO channel_sources(channel_number,source_type,source_value) VALUES(?,?,?)",
                        (next_num, "show", show_name))
            con.commit()
        finally:
            con.close()
        ch = {"number": next_num, "ordering": "sequential", "commercial_mode": "between", "max_repeats_per_day": 2}
        today = datetime.now(TZ)
        for i in range(config.SCHEDULE_DAYS_AHEAD):
            generate_day(ch, (today + timedelta(days=i)).strftime("%Y-%m-%d"))
        log.info("Auto-created channel %s %r for show %r (%d episodes)", next_num, display_name, show_name, n)
        created.append({"number": next_num, "name": display_name, "show": show_name, "episodes": n})
        next_num += 1
    return created

def channel_pool(ch_number):
    """Return (episodes, movies, commercials_all) eligible for channel."""
    con = database.connect()
    try:
        srcs = [dict(r) for r in con.execute("SELECT * FROM channel_sources WHERE channel_number=?", (ch_number,))]
        ch = con.execute("SELECT * FROM channels WHERE number=?", (ch_number,)).fetchone()
        ch = dict(ch) if ch else {}
    finally:
        con.close()
    con = database.connect()
    try:
        eps, mvs = [], []
        if not srcs:
            # default: everything
            eps = [dict(r) for r in con.execute("SELECT e.*, m.path, m.duration FROM episodes e JOIN media_files m ON m.id=e.media_id")]
            mvs = [dict(r) for r in con.execute("SELECT mo.*, m.path, m.duration FROM movies mo JOIN media_files m ON m.id=mo.media_id")]
        else:
            for s in srcs:
                t, v = s["source_type"], s["source_value"] or ""
                if t == "show":
                    eps += [dict(r) for r in con.execute("SELECT e.*, m.path, m.duration FROM episodes e JOIN media_files m ON m.id=e.media_id WHERE e.show_name=?", (v,))]
                elif t == "season":
                    # value "Show|S|E" or "Show:season"
                    if "|" in v:
                        show, sn, _ = v.split("|", 2)
                        eps += [dict(r) for r in con.execute("SELECT e.*, m.path, m.duration FROM episodes e JOIN media_files m ON m.id=e.media_id WHERE e.show_name=? AND e.season=?", (show, int(sn)))]
                    elif ":" in v:
                        show, sn = v.split(":", 1)
                        eps += [dict(r) for r in con.execute("SELECT e.*, m.path, m.duration FROM episodes e JOIN media_files m ON m.id=e.media_id WHERE e.show_name=? AND e.season=?", (show, int(sn)))]
                elif t == "all_tv":
                    eps = [dict(r) for r in con.execute("SELECT e.*, m.path, m.duration FROM episodes e JOIN media_files m ON m.id=e.media_id")]
                elif t == "all_movies":
                    mvs = [dict(r) for r in con.execute("SELECT mo.*, m.path, m.duration FROM movies mo JOIN media_files m ON m.id=mo.media_id")]
                elif t == "genre":
                    candidates = (dict(r) for r in con.execute(
                        "SELECT mo.*, m.path, m.duration FROM movies mo JOIN media_files m ON m.id=mo.media_id WHERE mo.genre<>''"))
                    mvs += [c for c in candidates
                            if v.lower() in [g.strip().lower() for g in c["genre"].split(",")]]
                elif t == "movie":
                    mvs += [dict(r) for r in con.execute("SELECT mo.*, m.path, m.duration FROM movies mo JOIN media_files m ON m.id=mo.media_id WHERE m.path=?", (v,))]
                elif t == "movie_folder":
                    mvs += [dict(r) for r in con.execute("SELECT mo.*, m.path, m.duration FROM movies mo JOIN media_files m ON m.id=mo.media_id WHERE m.path LIKE ?", (v + "%",))]
        ads = [dict(r) for r in con.execute("SELECT c.*, m.path, m.duration FROM commercials c JOIN media_files m ON m.id=c.media_id")]
        return eps, mvs, ads
    finally:
        con.close()

def recent_media_ids(ch_number, days=14, limit=500):
    con = database.connect()
    try:
        rows = con.execute("""SELECT media_id, MAX(played_ts) t FROM play_history WHERE channel_number=?
            AND played_ts > strftime('%s','now', ?) GROUP BY media_id""",
            (ch_number, f"-{days} days")).fetchall()
        return {r["media_id"]: r["t"] for r in rows if r["media_id"]}
    finally:
        con.close()

def pick_commercials(ads, rotation, target_min=90, target_max=180):
    """Fair persistent rotation; long compilations continue in 90-second slices.

    rotation is updated after every choice, not just once per generated day.
    Each file gets a turn before an already-selected file is selected again.
    """
    if not ads:
        return [], 0
    candidates = list({a["media_id"]: a for a in ads}.values())
    random.shuffle(candidates)
    candidates.sort(key=lambda a: rotation.get(a["media_id"], {}).get("last_used", 0))
    sequence = max((v["last_used"] for v in rotation.values()), default=0)
    chosen, total = [], 0.0
    for ad in candidates:
        mid = ad["media_id"]
        duration = float(ad.get("duration") or 30)
        state = rotation.get(mid, {"last_used": 0, "source_offset": 0})
        offset = float(state["source_offset"]) % duration if duration > target_max else 0
        length = min(90, duration - offset) if duration > target_max else duration
        if length > target_max - total:
            if total >= target_min:
                break
            continue
        chosen.append({"media_id": mid, "offset": offset, "duration": length})
        total += length
        sequence += 1
        rotation[mid] = {"last_used": sequence,
                         "source_offset": 0 if offset + length >= duration - .001 else offset + length}
        if total >= target_min:
            break
    return chosen, total

@serialized
def generate_day(channel, day_str, seed_extra=0):
    """Generate schedule rows for one channel+day. Does NOT touch already-aired entries."""
    start_ts, end_ts = day_bounds(day_str)
    now_ts = datetime.now(TZ).timestamp()
    con = database.connect()
    try:
        # Extend incomplete days without rewriting programs already shown in the guide.
        existing = con.execute("SELECT MAX(end_ts) AS end FROM schedule_entries WHERE channel_number=? AND day=?",
                               (channel["number"], day_str)).fetchone()["end"]
        if existing is not None and existing >= end_ts:
            return 0
        # find resume point
        last = con.execute("SELECT MAX(end_ts) m FROM schedule_entries WHERE channel_number=? AND day=?", (channel["number"], day_str)).fetchone()["m"]
        t = max(last or start_ts, start_ts)
        if day_str == local_day() and t < now_ts - 86400:
            t = start_ts
        # if resuming mid-day with empty table but time passed, start from day start anyway (guide shows full day)
        if day_str == local_day() and (last is None):
            t = start_ts
    finally:
        con.close()

    eps, mvs, ads = channel_pool(channel["number"])
    if not eps and not mvs:
        # slate: single all-day entry
        con = database.connect()
        try:
            if day_str == local_day():
                cnt = con.execute("SELECT COUNT(*) c FROM schedule_entries WHERE channel_number=? AND day=?", (channel["number"], day_str)).fetchone()["c"]
                if cnt == 0:
                    con.execute("""INSERT INTO schedule_entries(channel_number,start_ts,end_ts,kind,media_id,title,subtitle,description,day)
                        VALUES(?,?,?,?,?,?,?,?,?)""", (channel["number"], start_ts, end_ts, "slate", None,
                        "Off Air", "Add media via Samba", "Copy videos to /srv/media/TVShows, Movies or Commercials, then Rescan.", day_str))
                    con.commit()
                    return 1
                return cnt
            con.execute("""INSERT INTO schedule_entries(channel_number,start_ts,end_ts,kind,media_id,title,subtitle,description,day)
                VALUES(?,?,?,?,?,?,?,?,?)""", (channel["number"], start_ts, end_ts, "slate", None,
                "Off Air", "Add media via Samba", "Copy videos to /srv/media then Rescan.", day_str))
            con.commit()
            return 1
        finally:
            con.close()

    rng = random.Random(f"{channel['number']}-{day_str}-{seed_extra}")
    history = recent_media_ids(channel["number"])
    con = database.connect()
    try:
        ad_rotation = {r["media_id"]: {"last_used": r["last_used"], "source_offset": r["source_offset"]}
                       for r in con.execute("SELECT * FROM commercial_rotation")}
    finally:
        con.close()

    # build content sequence
    items = []
    if eps and mvs:
        # interleave: mostly episodes, movie every ~6 items
        pool_e = sorted(eps, key=lambda e: history.get(e["media_id"], 0))
        pool_m = sorted(mvs, key=lambda m: history.get(m["media_id"], 0))
        rng.shuffle(pool_e)
        # least-recent first but shuffled within tiers
        i_e = i_m = 0
        count = 0
        while count < 60:
            if pool_m and count % 6 == 5:
                items.append(("movie", pool_m[i_m % len(pool_m)]))
                i_m += 1
            else:
                items.append(("episode", pool_e[i_e % len(pool_e)]))
                i_e += 1
            count += 1
            if i_e >= len(pool_e) * 2 and i_m >= len(pool_m) * 2:
                break
    elif eps:
        if channel.get("ordering") == "sequential":
            items = [("episode", e) for e in sorted(eps, key=lambda e: ((e.get("show_name") or ""), (e.get("season") or 0), (e.get("episode") or 0)))]
        else:
            pool = sorted(eps, key=lambda e: history.get(e["media_id"], 0))
            # shuffle but bias to unseen
            unseen = [e for e in pool if e["media_id"] not in history]
            seen = [e for e in pool if e["media_id"] in history]
            rng.shuffle(unseen)
            rng.shuffle(seen)
            ordered = unseen + seen
            if not ordered:
                ordered = pool[:]
                rng.shuffle(ordered)
            # repeat cyclically to fill day, avoiding immediate replay
            items = []
            idx = 0
            while len(items) < 60:
                items.append(("episode", ordered[idx % len(ordered)]))
                idx += 1
                if idx >= len(ordered) * 2:
                    break
    else:
        pool = sorted(mvs, key=lambda m: history.get(m["media_id"], 0))
        rng.shuffle(pool)
        items = [("movie", m) for m in pool] or []

    # avoid same sequence as yesterday: rotate by day hash
    if items:
        rot = (int(hashlib.sha256(day_str.encode()).hexdigest()[:8], 16) + channel["number"]) % max(len(items), 1)
        items = items[rot:] + items[:rot]

    commercial_mode = channel.get("commercial_mode", "between")
    rows = []
    it_idx = 0
    last_media = None
    day_plays = {}  # media_id -> count today
    guard = 0
    unique_media = {obj["media_id"] for _, obj in items}
    while t < end_ts and guard < 10000:
        guard += 1
        if not items:
            break
        kind, obj = items[it_idx % len(items)]
        it_idx += 1
        mid = obj["media_id"]
        # no immediate replay; max repeats per day
        if mid == last_media and len(unique_media) > 1:
            continue
        if day_plays.get(mid, 0) >= (channel.get("max_repeats_per_day") or 2):
            # allow reuse only if everything exhausted
            if any(day_plays.get(candidate, 0) < (channel.get("max_repeats_per_day") or 2) for candidate in unique_media):
                continue
        dur = obj.get("duration") or (1320 if kind == "episode" else 5400)
        try:
            dur = float(dur)
        except Exception:
            dur = 1320
        if dur <= 0:
            dur = 1320
        s, e = t, min(t + dur, end_ts)
        if kind == "episode":
            title = obj.get("show_name") or "TV"
            sub = obj.get("title") or ""
            if obj.get("season") and obj.get("episode"):
                sub = f"S{obj['season']:02d}E{obj['episode']:02d} - {sub}" if sub else f"S{obj['season']:02d}E{obj['episode']:02d}"
            desc = obj.get("description") or ""
            rows.append((channel["number"], s, e, "episode", mid, title, sub, desc or "", day_str))
        else:
            ttl = obj.get("title") or "Movie"
            rows.append((channel["number"], s, e, "movie", mid, ttl or "Movie", "", "", day_str))
        day_plays[mid] = day_plays.get(mid, 0) + 1
        last_media = mid
        t = e
        # commercial break
        insert_break = False
        if commercial_mode == "between" and t < end_ts - 60:
            insert_break = True
        elif commercial_mode == "mid" and kind == "movie" and dur > 3600 and t < end_ts - 120:
            # split long movie with one mid-break: shorten: insert break in middle
            insert_break = True
        if insert_break and commercial_mode != "off":
            chosen, ctotal = pick_commercials(ads, ad_rotation,
                config.COMMERCIAL_TARGET_MIN, min(config.COMMERCIAL_TARGET_MAX, end_ts - t))
            if chosen:
                cs, ce = t, min(t + ctotal, end_ts)
                rows.append((channel["number"], cs, ce, "commercial_break", None,
                             "Commercial Break", "", "", day_str,
                             json.dumps(chosen)))
                t = ce
        if t >= end_ts:
            break
    con = database.connect()
    try:
        for r in rows:
            if len(r) == 9:
                ch, s, e, k, mid, ti, su, de, d = r
                cids = ""
            else:
                ch, s, e, k, mid, ti, su, de, d, cids = r
            con.execute("""INSERT INTO schedule_entries(channel_number,start_ts,end_ts,kind,media_id,
                commercial_ids,title,subtitle,description,day) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (ch, s, e, k, mid, cids, ti, su, de, d))
            if k == "commercial_break":
                for ad in json.loads(cids):
                    con.execute("INSERT INTO commercial_history(commercial_id,played_ts) VALUES(?,?)", (ad["media_id"], s))
            if mid:
                con.execute("INSERT INTO play_history(channel_number,media_id,played_ts) VALUES(?,?,?)", (ch, mid, s))
        for mid, state in ad_rotation.items():
            con.execute("""INSERT INTO commercial_rotation(media_id,last_used,source_offset) VALUES(?,?,?)
                ON CONFLICT(media_id) DO UPDATE SET last_used=excluded.last_used,source_offset=excluded.source_offset""",
                (mid, state["last_used"], state["source_offset"]))
        con.commit()
    finally:
        con.close()
    return len(rows)

def ensure_schedules(days_ahead=config.SCHEDULE_DAYS_AHEAD):
    channels = get_channels()
    today = datetime.now(TZ)
    total = 0
    for i in range(days_ahead):
        day = (today + timedelta(days=i)).strftime("%Y-%m-%d")
        for ch in channels:
            try:
                total += generate_day(ch, day)
            except Exception as e:
                log.exception("schedule gen failed ch=%s day=%s: %s", ch.get("number"), day, e)
    return total

def now_playing(ch_number, ts=None):
    ts = ts or datetime.now(TZ).timestamp()
    con = database.connect()
    try:
        r = con.execute("""SELECT * FROM schedule_entries WHERE channel_number=? AND start_ts<=? AND end_ts>?
            ORDER BY start_ts DESC LIMIT 1""", (ch_number, ts, ts)).fetchone()
        if r:
            return dict(r)
        return None
    finally:
        con.close()

def upcoming(ch_number, ts=None, limit=20):
    ts = ts or datetime.now(TZ).timestamp()
    con = database.connect()
    try:
        return [dict(r) for r in con.execute("""SELECT * FROM schedule_entries WHERE channel_number=?
            AND end_ts>? ORDER BY start_ts LIMIT ?""", (ch_number, ts, limit))]
    finally:
        con.close()

def guide_data(start_ts=None, hours=4):
    start_ts = start_ts or datetime.now(TZ).timestamp()
    end_ts = start_ts + hours * 3600
    channels = get_channels()
    con = database.connect()
    try:
        out = []
        for ch in channels:
            rows = [dict(r) for r in con.execute("""SELECT se.*, COALESCE(e.artwork, mo.artwork, '') AS artwork
                FROM schedule_entries se
                LEFT JOIN episodes e ON e.media_id = se.media_id
                LEFT JOIN movies mo ON mo.media_id = se.media_id
                WHERE se.channel_number=? AND se.end_ts>? AND se.start_ts<? ORDER BY se.start_ts""",
                (ch["number"], start_ts, end_ts))]
            out.append({"channel": ch, "entries": rows})
        return out
    finally:
        con.close()
