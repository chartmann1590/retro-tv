"""Video On Demand (VOD) backend: catalog aggregation, categorization, search, and details.
Automated: dynamically queries SQLite media tables populated by scanner.py and metadata.py.
"""
import logging
import random
import time
import database

log = logging.getLogger("retro-tv.vod")

_catalog_cache = {"data": None, "ts": 0}
CACHE_TTL = 30  # seconds

# Curated genres to highlight as dedicated Netflix-style rows if sufficient media exists
POPULAR_GENRES = [
    "Action",
    "Comedy",
    "Drama",
    "Sci-Fi",
    "Adventure",
    "Family",
    "Fantasy",
    "Thriller",
    "Crime",
    "Animation",
    "Horror",
    "Mystery",
]


def clear_cache():
    _catalog_cache["data"] = None
    _catalog_cache["ts"] = 0


def get_all_shows(con):
    """Return all TV shows with episode count, season count, artwork, and description."""
    rows = con.execute("""
        SELECT s.id, s.name,
               COUNT(e.id) AS episode_count,
               COUNT(DISTINCT e.season) AS season_count,
               COALESCE(NULLIF(s.poster, ''), MAX(NULLIF(e.artwork, '')), '') AS artwork,
               COALESCE(NULLIF(s.description, ''), (SELECT description FROM episodes WHERE show_id=s.id AND description <> '' LIMIT 1), '') AS description
        FROM shows s
        JOIN episodes e ON e.show_id = s.id
        GROUP BY s.id
        HAVING COUNT(e.id) > 0
        ORDER BY s.name ASC
    """).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["kind"] = "show"
        d["title"] = d["name"]
        out.append(d)
    return out


def get_all_movies(con):
    """Return all movies joined with media file metadata."""
    rows = con.execute("""
        SELECT mo.id, mo.media_id, mo.title, mo.year, mo.genre, mo.description,
               mo.artwork, mf.duration, mf.resolution, mf.vcodec, mf.container
        FROM movies mo
        JOIN media_files mf ON mf.id = mo.media_id
        ORDER BY mo.title ASC
    """).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["kind"] = "movie"
        out.append(d)
    return out


def get_recently_added(con, limit=24):
    """Return recently added movies and TV show episodes, sorted by media_files.id DESC."""
    rows = con.execute("""
        SELECT mf.id AS media_id, mf.kind, mf.duration, mf.resolution, mf.mtime,
               COALESCE(e.title, mo.title) AS title,
               COALESCE(NULLIF(e.artwork, ''), NULLIF(s.poster, ''), NULLIF(mo.artwork, '')) AS artwork,
               COALESCE(e.description, mo.description) AS description,
               e.id AS episode_id, e.show_id, e.show_name, e.season, e.episode,
               mo.id AS movie_id, mo.year, mo.genre
        FROM media_files mf
        LEFT JOIN episodes e ON e.media_id = mf.id
        LEFT JOIN shows s ON s.id = e.show_id
        LEFT JOIN movies mo ON mo.media_id = mf.id
        WHERE mf.kind IN ('movie', 'episode')
        ORDER BY mf.id DESC
        LIMIT ?
    """, (limit,)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        if d["kind"] == "episode":
            d["subtitle"] = f"{d['show_name']} • S{d.get('season', 1):02d}E{d.get('episode', 1):02d}"
        else:
            d["subtitle"] = f"{d.get('year') or ''} • {d.get('genre') or 'Movie'}".strip(" •")
        out.append(d)
    return out


def get_catalog():
    """Build the categorized Retroflix catalog for on-demand browsing.
    Includes featured hero, recently added, TV series, feature films, and genre carousels.
    """
    now = time.time()
    if _catalog_cache["data"] and (now - _catalog_cache["ts"]) < CACHE_TTL:
        return _catalog_cache["data"]

    con = database.connect()
    try:
        all_shows = get_all_shows(con)
        all_movies = get_all_movies(con)
        recent = get_recently_added(con, limit=20)

        # Featured Billboard Hero: pick an item with good artwork and description
        featured_candidates = [
            m for m in all_movies if m.get("artwork") and m.get("description") and len(m.get("description", "")) > 50
        ]
        if not featured_candidates:
            featured_candidates = [
                s for s in all_shows if s.get("artwork") and s.get("description")
            ] or all_movies or all_shows

        # Rotate featured deterministically per day or pick top
        featured = None
        if featured_candidates:
            # Seed based on day number so it feels like a daily featured spotlight
            day_seed = int(time.time() // 86400)
            featured = featured_candidates[day_seed % len(featured_candidates)]

        categories = []

        # 1. Recently Added
        if recent:
            categories.append({
                "id": "recently-added",
                "title": "Recently Added",
                "badge": "★ NEW ON DEMAND",
                "type": "mixed",
                "items": recent,
            })

        # 2. Feature Films
        if all_movies:
            categories.append({
                "id": "feature-films",
                "title": "Feature Films",
                "badge": f"{len(all_movies)} MOVIES",
                "type": "movie",
                "items": all_movies[:30],
            })

        # 3. TV Series
        if all_shows:
            # Sort shows by episode count descending
            shows_by_eps = sorted(all_shows, key=lambda s: s["episode_count"], reverse=True)
            categories.append({
                "id": "tv-series",
                "title": "Television Series",
                "badge": f"{len(all_shows)} SHOWS",
                "type": "show",
                "items": shows_by_eps,
            })

        # 4. Genre rows for movies (automated based on movie genres)
        genre_movies = {}
        for m in all_movies:
            genre_str = m.get("genre") or ""
            for g in genre_str.split(","):
                g = g.strip()
                if g:
                    genre_movies.setdefault(g, []).append(m)

        for genre in POPULAR_GENRES:
            matches = genre_movies.get(genre, [])
            if len(matches) >= 3:
                categories.append({
                    "id": f"genre-{genre.lower().replace(' ', '-')}",
                    "title": f"{genre} Movies",
                    "badge": f"{len(matches)} TITLES",
                    "type": "movie",
                    "items": matches[:24],
                })

        catalog = {
            "featured": featured,
            "total_movies": len(all_movies),
            "total_shows": len(all_shows),
            "categories": categories,
        }
        _catalog_cache["data"] = catalog
        _catalog_cache["ts"] = now
        return catalog
    finally:
        con.close()


def get_show_details(show_id):
    """Retrieve full details for a TV show, including seasons and grouped episodes."""
    con = database.connect()
    try:
        show = con.execute("SELECT * FROM shows WHERE id=?", (show_id,)).fetchone()
        if not show:
            return None
        show_dict = dict(show)

        episodes = [dict(r) for r in con.execute("""
            SELECT e.id, e.media_id, e.show_id, e.show_name, e.season, e.episode,
                   e.title, e.description, e.runtime, e.artwork,
                   mf.duration, mf.resolution, mf.vcodec, mf.container
            FROM episodes e
            JOIN media_files mf ON mf.id = e.media_id
            WHERE e.show_id = ?
            ORDER BY COALESCE(e.season, 1), COALESCE(e.episode, 1)
        """, (show_id,))]

        # Group episodes by season
        seasons_map = {}
        for ep in episodes:
            s_num = ep.get("season") or 1
            if s_num not in seasons_map:
                seasons_map[s_num] = []
            seasons_map[s_num].append(ep)

        seasons_list = []
        for s_num in sorted(seasons_map.keys()):
            seasons_list.append({
                "season": s_num,
                "episodes": seasons_map[s_num],
            })

        artwork = show_dict.get("poster") or next((e["artwork"] for e in episodes if e.get("artwork")), "")
        desc = show_dict.get("description") or next((e["description"] for e in episodes if e.get("description")), "")

        return {
            "id": show_dict["id"],
            "name": show_dict["name"],
            "title": show_dict["name"],
            "kind": "show",
            "artwork": artwork,
            "description": desc,
            "episode_count": len(episodes),
            "season_count": len(seasons_list),
            "seasons": seasons_list,
        }
    finally:
        con.close()


def get_movie_details(media_id):
    """Retrieve details for a specific movie by media_id or movie_id."""
    con = database.connect()
    try:
        row = con.execute("""
            SELECT mo.id, mo.media_id, mo.title, mo.year, mo.genre, mo.description,
                   mo.artwork, mf.path, mf.duration, mf.resolution, mf.vcodec, mf.acodec, mf.container
            FROM movies mo
            JOIN media_files mf ON mf.id = mo.media_id
            WHERE mo.media_id = ? OR mo.id = ?
        """, (media_id, media_id)).fetchone()
        if not row:
            return None
        d = dict(row)
        d["kind"] = "movie"
        return d
    finally:
        con.close()


def get_media_item(media_id):
    """Get playable media item details (whether it's an episode or a movie)."""
    con = database.connect()
    try:
        mf = con.execute("SELECT * FROM media_files WHERE id=?", (media_id,)).fetchone()
        if not mf:
            return None
        mf = dict(mf)
        if mf["kind"] == "episode":
            ep = con.execute("SELECT * FROM episodes WHERE media_id=?", (media_id,)).fetchone()
            if ep:
                ep = dict(ep)
                return {
                    "media_id": media_id,
                    "kind": "episode",
                    "title": f"{ep['show_name']} S{ep.get('season', 1):02d}E{ep.get('episode', 1):02d}",
                    "subtitle": ep.get("title") or "",
                    "show_name": ep["show_name"],
                    "season": ep.get("season"),
                    "episode": ep.get("episode"),
                    "description": ep.get("description") or "",
                    "artwork": ep.get("artwork") or "",
                    "duration": mf["duration"],
                    "path": mf["path"],
                }
        elif mf["kind"] == "movie":
            mo = con.execute("SELECT * FROM movies WHERE media_id=?", (media_id,)).fetchone()
            if mo:
                mo = dict(mo)
                return {
                    "media_id": media_id,
                    "kind": "movie",
                    "title": mo.get("title") or "Movie",
                    "subtitle": f"{mo.get('year') or ''} • {mo.get('genre') or ''}".strip(" •"),
                    "year": mo.get("year"),
                    "genre": mo.get("genre") or "",
                    "description": mo.get("description") or "",
                    "artwork": mo.get("artwork") or "",
                    "duration": mf["duration"],
                    "path": mf["path"],
                }
        return {
            "media_id": media_id,
            "kind": mf.get("kind", "unknown"),
            "title": "On Demand Video",
            "subtitle": "",
            "description": "",
            "artwork": "",
            "duration": mf["duration"],
            "path": mf["path"],
        }
    finally:
        con.close()


def search_vod(query, limit=40):
    """Full-text search across all movies, TV shows, and episodes.
    Returns categorized results: movies, shows, and episodes matching the query.
    """
    query = (query or "").strip()
    if not query:
        return {"query": "", "movies": [], "shows": [], "episodes": [], "total": 0}

    con = database.connect()
    try:
        like = f"%{query}%"

        # 1. Search movies
        movies = [dict(r) for r in con.execute("""
            SELECT mo.id, mo.media_id, mo.title, mo.year, mo.genre, mo.description,
                   mo.artwork, mf.duration, mf.resolution, 'movie' AS kind
            FROM movies mo
            JOIN media_files mf ON mf.id = mo.media_id
            WHERE mo.title LIKE ? OR mo.genre LIKE ? OR mo.description LIKE ?
            ORDER BY mo.title ASC
            LIMIT ?
        """, (like, like, like, limit))]

        # 2. Search TV shows
        shows = [dict(r) for r in con.execute("""
            SELECT s.id, s.name, COUNT(e.id) AS episode_count,
                   COUNT(DISTINCT e.season) AS season_count,
                   COALESCE(NULLIF(s.poster, ''), MAX(NULLIF(e.artwork, '')), '') AS artwork,
                   'show' AS kind
            FROM shows s
            JOIN episodes e ON e.show_id = s.id
            WHERE s.name LIKE ?
            GROUP BY s.id
            ORDER BY s.name ASC
            LIMIT ?
        """, (like, limit))]

        # 3. Search individual episodes (matching title or description)
        episodes = [dict(r) for r in con.execute("""
            SELECT e.id, e.media_id, e.show_id, e.show_name, e.season, e.episode,
                   e.title, e.description, e.runtime, e.artwork,
                   mf.duration, mf.resolution, 'episode' AS kind
            FROM episodes e
            JOIN media_files mf ON mf.id = e.media_id
            WHERE e.title LIKE ? OR e.description LIKE ?
            ORDER BY e.show_name, e.season, e.episode
            LIMIT ?
        """, (like, like, limit))]

        total = len(movies) + len(shows) + len(episodes)
        return {
            "query": query,
            "movies": movies,
            "shows": shows,
            "episodes": episodes,
            "total": total,
        }
    finally:
        con.close()
