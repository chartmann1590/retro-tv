"""Free metadata via TVMaze (TV only), cached, polite, offline-safe."""
import json
import time
import logging
import urllib.parse
import database

log = logging.getLogger("retro-tv.metadata")
LAST_CALL = [0.0]

def _polite():
    dt = time.time() - LAST_CALL[0]
    if dt < 0.6:
        time.sleep(0.6 - dt)
    LAST_CALL[0] = time.time()

def _get(url, timeout=12):
    import requests
    _polite()
    r = requests.get(url, timeout=timeout, headers={"User-Agent": "retro-tv/1.0"})
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.json()

def cache_get(key):
    con = database.connect()
    try:
        r = con.execute("SELECT value FROM metadata_cache WHERE key=?", (key,)).fetchone()
        return json.loads(r["value"]) if r else None
    finally:
        con.close()

def cache_set(key, value):
    con = database.connect()
    try:
        con.execute("INSERT INTO metadata_cache(key,value,updated_ts) VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_ts=excluded.updated_ts",
                    (key, json.dumps(value), time.time()))
        con.commit()
    finally:
        con.close()

def enrich_episodes(limit=60):
    """Enrich episodes missing descriptions. Returns (enriched, failed)."""
    con = database.connect()
    try:
        rows = con.execute("""SELECT e.id, e.show_name, e.season, e.episode, e.title FROM episodes e
            WHERE (e.description IS NULL OR e.description='') LIMIT ?""", (limit,)).fetchall()
        rows = [dict(r) for r in rows]
    finally:
        con.close()
    enriched = failed = 0
    for row in rows:
        if not row["show_name"] or not row["season"] or not row["episode"]:
            failed += 1
            continue
        show_key = f"tvmaze:show:{row['show_name'].lower()}"
        show = cache_get(show_key)
        if not show:
            try:
                res = _get(f"https://api.tvmaze.com/search/shows?q={urllib.parse.quote(row['show_name'])}")
                if not res:
                    failed += 1
                    continue
                # best match: case-insensitive name match else first
                best = res[0]["show"]
                for cand in res:
                    if cand["show"]["name"].lower() == row["show_name"].lower():
                        best = cand["show"]
                        break
                show = best
                cache_set(show_key, show)
            except Exception as e:
                log.warning("tvmaze show lookup failed %s: %s", row["show_name"], e)
                failed += 1
                continue
        ep_key = f"tvmaze:ep:{show.get('id')}:{row['season']}:{row['episode']}"
        ep = cache_get(ep_key)
        if not ep:
            try:
                ep = _get(f"https://api.tvmaze.com/shows/{show.get('id')}/episodebynumber?season={row['season']}&number={row['episode']}")
                if not ep:
                    failed += 1
                    continue
                cache_set(ep_key, ep)
            except Exception as e:
                log.warning("tvmaze ep lookup failed %s: %s", row, e)
                failed += 1
                continue
        try:
            import re
            summary = re.sub(r"<[^>]+>", "", ep.get("summary") or "").strip()
            con = database.connect()
            try:
                con.execute("UPDATE episodes SET title=COALESCE(NULLIF(title,''),?), description=?, runtime=?, artwork=?, meta_source='tvmaze' WHERE id=?",
                            (ep.get("name") or row["title"], summary, (ep.get("runtime") or 30) * 60,
                             (ep.get("image") or {}).get("medium", "") if ep.get("image") else "", row["id"]))
                con.commit()
            finally:
                con.close()
            enriched += 1
        except Exception as e:
            log.warning("db update failed: %s", e)
            failed += 1
    return enriched, failed
