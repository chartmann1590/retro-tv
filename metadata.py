"""Free metadata via TVMaze (TV) and OMDb (movies), cached, polite, offline-safe."""
import json
import re
import time
import logging
import urllib.parse
import config
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
            # TVMaze's search chokes on a trailing "(YYYY)" -- scanner.py appends
            # that to show_name when a season folder's parent already has one
            # (e.g. "Boy Meets World (1993)") to disambiguate, but TVMaze wants
            # just the bare title; searching with the year returns zero results.
            search_name = re.sub(r"\s*\(\d{4}\)$", "", row["show_name"]).strip()
            try:
                res = _get(f"https://api.tvmaze.com/search/shows?q={urllib.parse.quote(search_name)}")
                if not res:
                    failed += 1
                    continue
                # best match: case-insensitive name match else first
                best = res[0]["show"]
                for cand in res:
                    if cand["show"]["name"].lower() == search_name.lower():
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
            summary = re.sub(r"<[^>]+>", "", ep.get("summary") or "").strip()
            # Not every show has per-episode stills on TVMaze (older/less
            # popular shows especially) -- fall back to the show's own poster
            # rather than leaving the guide with no image at all.
            artwork = (ep.get("image") or {}).get("medium", "") or (show.get("image") or {}).get("medium", "")
            con = database.connect()
            try:
                con.execute("UPDATE episodes SET title=COALESCE(NULLIF(title,''),?), description=?, runtime=?, artwork=?, meta_source='tvmaze' WHERE id=?",
                            (ep.get("name") or row["title"], summary, (ep.get("runtime") or 30) * 60,
                             artwork, row["id"]))
                con.commit()
            finally:
                con.close()
            enriched += 1
        except Exception as e:
            log.warning("db update failed: %s", e)
            failed += 1
    return enriched, failed

_ROMAN_PART = {"I": "1", "II": "2", "III": "3", "IV": "4", "V": "5"}

def _omdb(params):
    return _get("https://www.omdbapi.com/?apikey=" + config.OMDB_API_KEY + "&" + params)

def _omdb_best_match(title, year):
    """Exact title+year lookup, retrying with roman->arabic "Part N" and a fuzzy
    search fallback -- OMDb's exact-title endpoint otherwise misses/mismatches
    variants like "Part II" vs "Part 2", or picks an unrelated same-titled short."""
    y = f"&y={year}" if year else ""
    candidates = [title]
    m = re.search(r"\bPart\s+([IVX]+)$", title, re.I)
    if m and m.group(1).upper() in _ROMAN_PART:
        candidates.append(title[:m.start()] + "Part " + _ROMAN_PART[m.group(1).upper()])
    for cand in candidates:
        data = _omdb(f"t={urllib.parse.quote(cand)}{y}")
        if data and data.get("Response") != "False" and data.get("Type") == "movie":
            return data
    search = _omdb(f"s={urllib.parse.quote(title)}{y}")
    if search and search.get("Response") != "False":
        movies = [r for r in search.get("Search", []) if r.get("Type") == "movie"]
        if movies:
            best = next((r for r in movies if r["Title"].lower() == title.lower()), movies[0])
            data = _omdb(f"i={best['imdbID']}")
            if data and data.get("Response") != "False":
                return data
    return None

def enrich_movies(limit=60):
    """Enrich movies missing genre via OMDb. Returns (enriched, failed). No-op without an API key."""
    if not config.OMDB_API_KEY:
        return 0, 0
    con = database.connect()
    try:
        rows = con.execute("""SELECT id, title, year FROM movies
            WHERE genre IS NULL OR genre='' LIMIT ?""", (limit,)).fetchall()
        rows = [dict(r) for r in rows]
    finally:
        con.close()
    enriched = failed = 0
    for row in rows:
        if not row["title"]:
            failed += 1
            continue
        key = f"omdb:{row['title'].lower()}:{row['year'] or ''}"
        data = cache_get(key)
        if not data:
            try:
                data = _omdb_best_match(row["title"], row["year"])
                if not data:
                    failed += 1
                    continue
                cache_set(key, data)
            except Exception as e:
                log.warning("omdb lookup failed %s: %s", row["title"], e)
                failed += 1
                continue
        try:
            genre = data.get("Genre") or ""
            plot = data.get("Plot") or ""
            poster = data.get("Poster") or ""
            con = database.connect()
            try:
                con.execute("""UPDATE movies SET genre=?, description=COALESCE(NULLIF(description,''),?),
                    artwork=COALESCE(NULLIF(artwork,''),?), meta_source='omdb' WHERE id=?""",
                    (genre, "" if plot == "N/A" else plot, "" if poster == "N/A" else poster, row["id"]))
                con.commit()
            finally:
                con.close()
            enriched += 1
        except Exception as e:
            log.warning("db update failed: %s", e)
            failed += 1
    return enriched, failed
