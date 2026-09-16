"""Genre channel scheduling: venv/bin/python -m unittest tests.test_movie_channels -v."""
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

import config
import database
import scheduler


class MovieChannelTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        for key, value in {"DB_PATH": os.path.join(self.tmp.name, "test.db"),
                           "AUTO_MOVIE_CHANNEL_GENRES": ("Action", "Drama"),
                           "AUTO_MOVIE_CHANNEL_MIN_MOVIES": 5}.items():
            patcher = patch.object(config, key, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        database.init_db()

    def media(self, title, genre="Action", kind="movie", exists=True):
        path = os.path.join(self.tmp.name, title + ".mp4")
        if exists:
            with open(path, "wb") as output:
                output.write(b"test")
        con = database.connect()
        try:
            mid = con.execute("INSERT INTO media_files(path,duration,kind) VALUES(?,?,?)",
                              (path, 5400 if kind == "movie" else 30, kind)).lastrowid
            if kind == "movie":
                con.execute("INSERT INTO movies(media_id,title,genre) VALUES(?,?,?)", (mid, title, genre))
            else:
                con.execute("INSERT INTO commercials(media_id,name) VALUES(?,?)", (mid, title))
            con.commit()
            return mid
        finally:
            con.close()

    def rows(self, query, params=()):
        con = database.connect()
        try:
            return [dict(row) for row in con.execute(query, params)]
        finally:
            con.close()

    def test_creates_only_populated_genres_without_duplicate_channels(self):
        for i in range(5):
            self.media(f"Action {i}", " action , ACTION, Adventure")
        self.media("Missing drama", "Drama", exists=False)
        self.media("Drama only", "Drama")
        created = scheduler.auto_create_movie_channels()
        self.assertEqual([row["genre"] for row in created], ["Action"])
        self.assertEqual(created[0]["movies"], 5)
        self.assertEqual(scheduler.auto_create_movie_channels(), [])
        con = database.connect()
        try:
            con.execute("UPDATE channels SET enabled=0, name='My films', commercial_mode='off'")
            con.execute("UPDATE channel_sources SET source_value=' ACTION '")
            con.commit()
        finally:
            con.close()
        self.assertEqual(scheduler.auto_create_movie_channels(), [])
        channel = scheduler.get_channels(enabled_only=False)[0]
        self.assertEqual((channel["name"], channel["commercial_mode"]), ("My films", "off"))

    def test_daily_lineups_match_genre_with_commercials_and_no_gaps(self):
        movie_ids = {self.media(f"Film {i}", "Action, Adventure") for i in range(8)}
        self.media("Unrelated", "Drama")
        ad_ids = {self.media(f"Ad {i}", kind="commercial") for i in range(6)}
        self.assertGreater(scheduler.ensure_schedules(days_ahead=3), 0)
        channel = scheduler.get_channels()[0]
        self.assertEqual(channel["commercial_mode"], "between")
        all_rows = self.rows("SELECT * FROM schedule_entries ORDER BY start_ts")
        sequences = []
        for offset in range(3):
            day = (datetime.now(scheduler.TZ) + timedelta(days=offset)).strftime('%Y-%m-%d')
            entries = [row for row in all_rows if row["day"] == day]
            start, end = scheduler.day_bounds(day)
            self.assertEqual((entries[0]["start_ts"], entries[-1]["end_ts"]), (start, end))
            for first, second in zip(entries, entries[1:]):
                self.assertEqual(first["end_ts"], second["start_ts"])
                self.assertNotEqual(first["kind"], second["kind"])
            movies = [row["media_id"] for row in entries if row["kind"] == "movie"]
            self.assertTrue(set(movies) <= movie_ids)
            self.assertTrue(all(a != b for a, b in zip(movies, movies[1:])))
            sequences.append(movies)
            for row in entries:
                if row["kind"] == "commercial_break":
                    self.assertTrue({ad['media_id'] for ad in json.loads(row['commercial_ids'])} <= ad_ids)
                    self.assertGreaterEqual(row["end_ts"] - row["start_ts"], 90)
                    self.assertLessEqual(row["end_ts"] - row["start_ts"], 180)
        self.assertNotEqual(sequences[0], sequences[1])
        self.assertEqual(scheduler.ensure_schedules(days_ahead=3), 0)
        self.assertEqual(self.rows("SELECT * FROM schedule_entries ORDER BY start_ts"), all_rows)
        added = self.media("New arrival", "Action")
        self.assertIn(added, {movie["media_id"] for movie in scheduler.channel_pool(channel["number"])[1]})
        scheduler.ensure_schedules(days_ahead=4)
        fourth_day = (datetime.now(scheduler.TZ) + timedelta(days=3)).strftime('%Y-%m-%d')
        self.assertIn(added, {row["media_id"] for row in self.rows(
            "SELECT media_id FROM schedule_entries WHERE day=? AND kind='movie'", (fourth_day,))})


if __name__ == "__main__":
    unittest.main()
