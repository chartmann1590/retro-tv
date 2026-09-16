"""Free metadata via TVMaze (TV), Wikipedia (Movies/TV), and OMDb (optional), cached, polite, offline-safe."""
import difflib
import json
import logging
import re
import time
import urllib.parse
import config
import database

log = logging.getLogger("retro-tv.metadata")
LAST_CALL = [0.0]

GENRES = [
    "Action", "Adventure", "Animation", "Biography", "Comedy", "Crime",
    "Documentary", "Drama", "Family", "Fantasy", "History", "Horror",
    "Music", "Musical", "Mystery", "Romance", "Sci-Fi", "Sport",
    "Thriller", "War", "Western"
]

def _polite():
    dt = time.time() - LAST_CALL[0]
    if dt < 0.4:
        time.sleep(0.4 - dt)
    LAST_CALL[0] = time.time()

def _get(url, timeout=12, headers=None):
    import requests
    _polite()
    h = {"User-Agent": "retro-tv/1.0 (contact: admin@retro-tv.local)"}
    if headers:
        h.update(headers)
    try:
        r = requests.get(url, timeout=timeout, headers=h)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()
    except Exception as e:
        log.debug("HTTP GET %s failed: %s", url, e)
        return None

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

def normalize_title(s):
    if not s:
        return ""
    s = re.sub(r"\[.*?\]|\(.*?\)", "", s)
    s = re.sub(r"[\':,!\?.-]", " ", s)
    s = re.sub(r"\s*\b(film|movie|series|tv series)\b", " ", s, flags=re.I)
    return re.sub(r"\s+", " ", s).strip().lower()

def is_title_match(target, candidate):
    t_norm = normalize_title(target)
    c_norm = normalize_title(candidate)
    if not t_norm or not c_norm:
        return False
    if t_norm == c_norm:
        return True
    if t_norm.startswith(c_norm) or c_norm.startswith(t_norm):
        return True
    t_words = set(t_norm.split())
    c_words = set(c_norm.split())
    if t_words and c_words:
        overlap = len(t_words & c_words)
        if overlap >= min(len(t_words), len(c_words)) and min(len(t_words), len(c_words)) >= 1:
            return True
    sim = difflib.SequenceMatcher(None, t_norm, c_norm).ratio()
    return sim >= 0.7

GENRE_PATTERNS = [
    ("Action", r"\baction\b"),
    ("Adventure", r"\b(adventure|adventurous)\b"),
    ("Animation", r"\b(animation|animated)\b"),
    ("Biography", r"\b(biography|biographical|biopic)\b"),
    ("Comedy", r"\b(comedy|comedic)\b"),
    ("Crime", r"\bcrime\b"),
    ("Documentary", r"\b(documentary|docuseries)\b"),
    ("Drama", r"\b(drama|dramatic)\b"),
    ("Family", r"\bfamily\b"),
    ("Fantasy", r"\bfantasy\b"),
    ("History", r"\b(history|historical)\b"),
    ("Horror", r"\bhorror\b"),
    ("Music", r"\b(music|musical)\b"),
    ("Mystery", r"\bmystery\b"),
    ("Romance", r"\b(romance|romantic)\b"),
    ("Sci-Fi", r"\b(sci-fi|science fiction)\b"),
    ("Sport", r"\b(sport|sports)\b"),
    ("Thriller", r"\bthriller\b"),
    ("War", r"\bwar\b"),
    ("Western", r"\bwestern\b"),
]

def extract_genres_from_text(text):
    if not text:
        return ""
    found = []
    text_lower = text.lower()
    for g, pattern in GENRE_PATTERNS:
        if re.search(pattern, text_lower):
            found.append(g)
    return ", ".join(found[:3])

# ---------- TV Shows (TVMaze + Wikipedia fallback) ----------

def clean_show_search_name(name):
    clean = re.sub(r"\s*\(\d{4}\)$", "", name).strip()
    clean = re.sub(r"\s*-\s*Family Vacation", " Family Vacation", clean)
    return clean

def fetch_tvmaze_show(show_name):
    """Fetch show metadata and poster from TVMaze (free, no API key)."""
    search_name = clean_show_search_name(show_name)
    show_key = f"tvmaze:show:{search_name.lower()}"
    cached = cache_get(show_key)
    if cached is not None:
        if isinstance(cached, dict) and cached:
            if "poster" not in cached and "image" in cached:
                cached["poster"] = (cached.get("image") or {}).get("original") or (cached.get("image") or {}).get("medium") or ""
            if "description" not in cached and "summary" in cached:
                cached["description"] = re.sub(r"<[^>]+>", "", cached.get("summary") or "").strip()
        return cached

    url = f"https://api.tvmaze.com/search/shows?q={urllib.parse.quote(search_name)}"
    data = _get(url)
    if not data:
        cache_set(show_key, {})
        return {}

    best = None
    for cand in data:
        s = cand.get("show", {})
        c_name = s.get("name", "")
        if is_title_match(search_name, c_name):
            best = s
            break
    if not best and data:
        best = data[0].get("show", {})

    if not best:
        cache_set(show_key, {})
        return {}

    poster = (best.get("image") or {}).get("original") or (best.get("image") or {}).get("medium") or ""
    summary = re.sub(r"<[^>]+>", "", best.get("summary") or "").strip()
    genres = ", ".join(best.get("genres") or [])

    res = {
        "id": best.get("id"),
        "name": best.get("name"),
        "poster": poster,
        "description": summary,
        "genres": genres,
        "premiered": best.get("premiered", ""),
    }
    cache_set(show_key, res)
    return res

def fetch_tvmaze_episodes(tvmaze_show_id):
    """Fetch all episodes for a show from TVMaze in one batch (free, no API key)."""
    if not tvmaze_show_id:
        return {}
    ep_key = f"tvmaze:episodes:{tvmaze_show_id}"
    cached = cache_get(ep_key)
    if cached is not None:
        return cached

    url = f"https://api.tvmaze.com/shows/{tvmaze_show_id}/episodes"
    data = _get(url)
    if not data:
        cache_set(ep_key, {})
        return {}

    ep_map = {}
    for ep in data:
        s_num = ep.get("season")
        e_num = ep.get("number")
        if s_num is not None and e_num is not None:
            img = (ep.get("image") or {}).get("medium") or (ep.get("image") or {}).get("original") or ""
            summary = re.sub(r"<[^>]+>", "", ep.get("summary") or "").strip()
            ep_map[f"{s_num}:{e_num}"] = {
                "name": ep.get("name") or "",
                "summary": summary,
                "runtime": (ep.get("runtime") or 30) * 60,
                "artwork": img,
            }
    cache_set(ep_key, ep_map)
    return ep_map

def fetch_wikipedia_show(show_name):
    """Fallback show poster and description from Wikipedia (free, no API key)."""
    clean = clean_show_search_name(show_name)
    key = f"wiki:show:{clean.lower()}"
    cached = cache_get(key)
    if cached is not None:
        return cached

    candidates = [f"{clean} (TV series)", clean]
    for c in candidates:
        slug = urllib.parse.quote(c.replace(" ", "_"))
        url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{slug}"
        data = _get(url)
        if data and is_title_match(clean, data.get("title", "")):
            art = (data.get("originalimage") or {}).get("source") or (data.get("thumbnail") or {}).get("source") or ""
            desc = data.get("extract", "")
            if art:
                res = {"poster": art, "description": desc, "source": "wikipedia"}
                cache_set(key, res)
                return res

    cache_set(key, {})
    return {}

def enrich_shows(limit=50):
    """Enrich shows missing poster/description and batch-update their episodes. Returns (enriched, failed)."""
    con = database.connect()
    try:
        rows = con.execute("""SELECT id, name FROM shows
                              WHERE (poster IS NULL OR poster = '')
                              AND name NOT IN ('Local News', 'Local Weather', 'Season 7', 'Specials')
                              LIMIT ?""", (limit,)).fetchall()
        rows = [dict(r) for r in rows]
    finally:
        con.close()

    enriched = failed = 0
    for s in rows:
        name = s["name"]
        if name in ("Local News", "Local Weather", "Season 7", "Specials"):
            con = database.connect()
            try:
                con.execute("UPDATE shows SET meta_source='checked' WHERE id=?", (s["id"],))
                con.commit()
            finally:
                con.close()
            continue

        show_meta = fetch_tvmaze_show(name)
        poster = show_meta.get("poster") or ""
        desc = show_meta.get("description") or ""
        source = "tvmaze"

        if not poster:
            wiki = fetch_wikipedia_show(name)
            if wiki.get("poster"):
                poster = wiki["poster"]
                desc = desc or wiki.get("description") or ""
                source = "wikipedia"

        con = database.connect()
        try:
            if poster:
                con.execute("UPDATE shows SET poster=?, description=COALESCE(NULLIF(description,''),?), meta_source=? WHERE id=?",
                            (poster, desc, source, s["id"]))
                con.commit()
                enriched += 1
            else:
                con.execute("UPDATE shows SET meta_source='checked' WHERE id=?", (s["id"],))
                con.commit()
                failed += 1

            # If we have a TVMaze show ID, batch-update episodes
            if show_meta.get("id"):
                ep_map = fetch_tvmaze_episodes(show_meta["id"])
                if ep_map:
                    ep_rows = con.execute("SELECT id, season, episode, title, description, artwork FROM episodes WHERE show_id=?",
                                          (s["id"],)).fetchall()
                    for ep_row in ep_rows:
                        k = f"{ep_row['season']}:{ep_row['episode']}"
                        ep_info = ep_map.get(k)
                        if ep_info:
                            ep_art = ep_info.get("artwork") or poster
                            con.execute("""UPDATE episodes SET
                                title=COALESCE(NULLIF(?,''), title),
                                description=COALESCE(NULLIF(?,''), description),
                                runtime=COALESCE(NULLIF(?,0), runtime),
                                artwork=COALESCE(NULLIF(?,''), artwork),
                                meta_source='tvmaze'
                                WHERE id=?""",
                                (ep_info.get("name") or "",
                                 ep_info.get("summary") or "",
                                 ep_info.get("runtime") or 1800,
                                 ep_art,
                                 ep_row["id"]))
                    con.commit()

            # Ensure all episodes for this show have at least the show's poster as fallback artwork
            if poster:
                con.execute("UPDATE episodes SET artwork=? WHERE show_id=? AND (artwork IS NULL OR artwork='')",
                            (poster, s["id"]))
                con.commit()
        except Exception as e:
            log.warning("enrich_shows DB update failed for %s: %s", name, e)
            failed += 1
        finally:
            con.close()

    return enriched, failed

def enrich_episodes(limit=60):
    """Enrich remaining individual episodes missing descriptions or artwork."""
    con = database.connect()
    try:
        rows = con.execute("""SELECT e.id, e.show_id, e.show_name, e.season, e.episode, e.title, e.description, e.artwork, s.poster AS show_poster
            FROM episodes e
            LEFT JOIN shows s ON s.id = e.show_id
            WHERE ((e.description IS NULL OR e.description='') OR (e.artwork IS NULL OR e.artwork=''))
            AND e.meta_source NOT IN ('checked', 'not_found')
            LIMIT ?""", (limit,)).fetchall()
        rows = [dict(r) for r in rows]
    finally:
        con.close()

    enriched = failed = 0
    for row in rows:
        if not row["show_name"] or not row["season"] or not row["episode"]:
            con = database.connect()
            try:
                con.execute("UPDATE episodes SET meta_source='checked' WHERE id=?", (row["id"],))
                con.commit()
            finally:
                con.close()
            failed += 1
            continue

        show_meta = fetch_tvmaze_show(row["show_name"])
        show_poster = row.get("show_poster") or show_meta.get("poster") or ""
        ep_info = None
        if show_meta.get("id"):
            ep_map = fetch_tvmaze_episodes(show_meta["id"])
            ep_info = ep_map.get(f"{row['season']}:{row['episode']}")

        con = database.connect()
        try:
            if ep_info:
                ep_art = ep_info.get("artwork") or show_poster
                con.execute("""UPDATE episodes SET
                    title=COALESCE(NULLIF(?,''), title),
                    description=COALESCE(NULLIF(?,''), description),
                    runtime=COALESCE(NULLIF(?,0), runtime),
                    artwork=COALESCE(NULLIF(?,''), artwork),
                    meta_source='tvmaze' WHERE id=?""",
                    (ep_info.get("name") or "",
                     ep_info.get("summary") or "",
                     ep_info.get("runtime") or 1800,
                     ep_art,
                     row["id"]))
                con.commit()
                enriched += 1
            elif show_poster:
                con.execute("UPDATE episodes SET artwork=?, meta_source='show_poster' WHERE id=?",
                            (show_poster, row["id"]))
                con.commit()
                enriched += 1
            else:
                con.execute("UPDATE episodes SET meta_source='checked' WHERE id=?", (row["id"],))
                con.commit()
                failed += 1
        except Exception as e:
            log.warning("enrich_episodes update failed %s: %s", row["id"], e)
            failed += 1
        finally:
            con.close()

    return enriched, failed

# ---------- Movies (Wikipedia REST API + OMDb fallback) ----------

_ROMAN_PART = {"I": "1", "II": "2", "III": "3", "IV": "4", "V": "5"}

def _omdb(params):
    if not config.OMDB_API_KEY:
        return None
    return _get("https://www.omdbapi.com/?apikey=" + config.OMDB_API_KEY + "&" + params)

def _omdb_best_match(title, year):
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

def clean_movie_title(t):
    t = re.sub(r"\[.*?\]|\(.*?\)", "", t)
    return re.sub(r"\s+", " ", t).strip()

def fetch_wikipedia_movie(title, year=None):
    """Fetch movie poster, plot description, and genre from Wikipedia (100% free, NO API key)."""
    clean = clean_movie_title(title)
    if not clean:
        return {}

    # 1. Try direct page summary endpoints
    candidates = []
    if year:
        candidates.append(f"{clean} ({year} film)")
    candidates.append(f"{clean} (film)")
    candidates.append(clean)

    for c in candidates:
        slug = urllib.parse.quote(c.replace(" ", "_"))
        url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{slug}"
        data = _get(url)
        if data and is_title_match(clean, data.get("title", "")):
            art = (data.get("originalimage") or {}).get("source") or (data.get("thumbnail") or {}).get("source") or ""
            desc = data.get("extract") or ""
            genre = extract_genres_from_text(desc)
            if art:
                return {"poster": art, "description": desc, "genre": genre, "source": "wikipedia"}

    # 2. Try Wikipedia search API with strict title matching
    q = f'"{clean}" {year} film' if year else f'"{clean}" film'
    s_url = f"https://en.wikipedia.org/w/api.php?action=query&list=search&srsearch={urllib.parse.quote(q)}&format=json&srlimit=4"
    search_data = _get(s_url)
    if search_data:
        for hit in search_data.get("query", {}).get("search", []):
            htitle = hit.get("title", "")
            if is_title_match(clean, htitle):
                slug = urllib.parse.quote(htitle.replace(" ", "_"))
                data = _get(f"https://en.wikipedia.org/api/rest_v1/page/summary/{slug}")
                if data:
                    art = (data.get("originalimage") or {}).get("source") or (data.get("thumbnail") or {}).get("source") or ""
                    desc = data.get("extract") or ""
                    genre = extract_genres_from_text(desc)
                    if art:
                        return {"poster": art, "description": desc, "genre": genre, "source": "wikipedia"}

    return {}

def fetch_movie_metadata(title, year=None):
    """Retrieve movie metadata and poster via OMDb (if key present) or Wikipedia (free, no API key)."""
    clean = clean_movie_title(title)
    key = f"movie:{clean.lower()}:{year or ''}"
    cached = cache_get(key)
    if cached is not None:
        return cached

    # Try OMDb first if API key configured
    if config.OMDB_API_KEY:
        try:
            omdb_data = _omdb_best_match(clean, year)
            if omdb_data:
                poster = omdb_data.get("Poster") or ""
                if poster == "N/A":
                    poster = ""
                plot = omdb_data.get("Plot") or ""
                if plot == "N/A":
                    plot = ""
                genre = omdb_data.get("Genre") or ""
                if genre == "N/A":
                    genre = ""
                if poster:
                    res = {"poster": poster, "description": plot, "genre": genre, "source": "omdb"}
                    cache_set(key, res)
                    return res
        except Exception as e:
            log.warning("OMDb lookup error for %s: %s", title, e)

    # Free Wikipedia REST API (no API key required)
    wiki = fetch_wikipedia_movie(title, year)
    if wiki and wiki.get("poster"):
        cache_set(key, wiki)
        return wiki

    cache_set(key, {})
    return {}

def enrich_movies(limit=60):
    """Enrich movies missing artwork, description, or genre using free web sources. Returns (enriched, failed)."""
    con = database.connect()
    try:
        rows = con.execute("""SELECT id, title, year, artwork, description, genre FROM movies
            WHERE ((artwork IS NULL OR artwork='') OR (description IS NULL OR description='') OR (genre IS NULL OR genre=''))
            AND meta_source NOT IN ('checked', 'not_found')
            LIMIT ?""", (limit,)).fetchall()
        rows = [dict(r) for r in rows]
    finally:
        con.close()

    enriched = failed = 0
    for row in rows:
        if not row["title"]:
            failed += 1
            continue
        data = fetch_movie_metadata(row["title"], row["year"])
        poster = data.get("poster") or ""
        plot = data.get("description") or ""
        genre = data.get("genre") or ""
        source = data.get("source") or "wikipedia"

        con = database.connect()
        try:
            if poster or plot or genre:
                con.execute("""UPDATE movies SET
                    genre=COALESCE(NULLIF(genre,''),?),
                    description=COALESCE(NULLIF(description,''),?),
                    artwork=COALESCE(NULLIF(artwork,''),?),
                    meta_source=? WHERE id=?""",
                    (genre, plot, poster, source, row["id"]))
                con.commit()
                enriched += 1
            else:
                con.execute("UPDATE movies SET meta_source='checked' WHERE id=?", (row["id"],))
                con.commit()
                failed += 1
        except Exception as e:
            log.warning("Movie DB update failed %s: %s", row["title"], e)
            failed += 1
        finally:
            con.close()

    return enriched, failed

def enrich_all():
    """Run full enrichment for shows, episodes, and movies."""
    sh_en, sh_fail = enrich_shows(limit=100)
    ep_en, ep_fail = enrich_episodes(limit=200)
    mv_en, mv_fail = enrich_movies(limit=100)
    return {
        "shows_enriched": sh_en, "shows_failed": sh_fail,
        "episodes_enriched": ep_en, "episodes_failed": ep_fail,
        "movies_enriched": mv_en, "movies_failed": mv_fail,
    }
