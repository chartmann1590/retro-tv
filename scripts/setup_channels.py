"""Add library-based channels without replacing existing channel numbers or sources."""
from pathlib import Path
import re
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import database
import scheduler


def create_lineup():
    database.init_db()
    con = database.connect()
    try:
        shows = [r["show_name"] for r in con.execute("SELECT DISTINCT show_name FROM episodes ORDER BY show_name")]
        existing_names = {r["name"].casefold() for r in con.execute("SELECT name FROM channels")}
        used_numbers = {r["number"] for r in con.execute("SELECT number FROM channels")}
        candidates = [(re.sub(r"\s*\(\d{4}\)$", "", show), [("show", show)]) for show in shows]
        if len(shows) > 1:
            candidates.append(("Random TV", [("all_tv", "")]))
        sitcoms = [s for s in shows if any(n in s for n in ("Boy Meets World", "How I Met Your Mother"))]
        cartoons = [s for s in shows if any(n in s for n in ("Family Guy", "Hey Arnold"))]
        if len(sitcoms) > 1:
            candidates.append(("Sitcom Central", [("show", s) for s in sitcoms]))
        if len(cartoons) > 1:
            candidates.append(("Toon Time", [("show", s) for s in cartoons]))
        # Only create a movie channel when there are actual films, not misplaced episodes.
        films = [r["path"] for r in con.execute("SELECT m.path,mo.title FROM movies mo JOIN media_files m ON m.id=mo.media_id")
                 if not re.search(r"(?i)s\d{1,2}e\d{1,3}", r["title"])]
        if films:
            candidates.append(("Movie House", [("movie", p) for p in films]))
        added = []
        colors = ["#81aecb", "#d6a3ac", "#aca0d6", "#a8c38d", "#d9b16c", "#95b6d9"]
        for name, sources in candidates:
            if name.casefold() in existing_names:
                continue
            number = next(n for n in range(2, 1000) if n not in used_numbers)
            con.execute("""INSERT INTO channels(number,name,enabled,color,ordering,commercial_mode,sort_order)
                           VALUES(?,?,1,?,'shuffle','between',?)""", (number, name, colors[len(added) % len(colors)], number))
            con.executemany("INSERT INTO channel_sources(channel_number,source_type,source_value) VALUES(?,?,?)",
                            [(number, kind, value) for kind, value in sources])
            used_numbers.add(number)
            existing_names.add(name.casefold())
            added.append((number, name))
        con.commit()
        return added
    finally:
        con.close()


if __name__ == "__main__":
    print("Added channels:", create_lineup())
    print("Schedule entries generated:", scheduler.ensure_schedules())
