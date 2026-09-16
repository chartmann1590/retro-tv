"""Unit tests for free metadata and artwork enrichment (TVMaze and Wikipedia).
Run with: venv/bin/python -m unittest tests.test_metadata -v
"""
import os
import tempfile
import unittest
from unittest.mock import patch

import config
import database
import metadata


class MetadataTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        for key, value in {"DB_PATH": os.path.join(self.tmp.name, "test.db"), "DATA_DIR": self.tmp.name}.items():
            patcher = patch.object(config, key, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        database.init_db()

    def test_title_matching(self):
        """Verify strict title matching heuristics to prevent mismatched artwork."""
        self.assertTrue(metadata.is_title_match("Schindler's List", "Schindler's List (film)"))
        self.assertTrue(metadata.is_title_match("The Super Mario Bros Movie", "The Super Mario Bros. Movie"))
        self.assertTrue(metadata.is_title_match("The Bikeriders", "The Bikeriders"))
        self.assertTrue(metadata.is_title_match("Borat Cultural Learnings of America", "Borat"))
        self.assertTrue(metadata.is_title_match("Fresh Off the Boat", "Fresh Off the Boat (TV series)"))
        self.assertTrue(metadata.is_title_match("Hey Arnold (1996)", "Hey Arnold!"))

        # Must reject completely unrelated titles
        self.assertFalse(metadata.is_title_match("Oscar Shaw", "The Cocoanuts"))
        self.assertFalse(metadata.is_title_match("Oscar Shaw", "Moana (2026 film)"))
        self.assertFalse(metadata.is_title_match("Friends", "Boy Meets World"))

    def test_clean_titles(self):
        """Verify title cleaning for searches."""
        self.assertEqual(metadata.clean_show_search_name("Boy Meets World (1993)"), "Boy Meets World")
        self.assertEqual(metadata.clean_show_search_name("Jersey Shore - Family Vacation"), "Jersey Shore Family Vacation")
        self.assertEqual(metadata.clean_movie_title("Schindlers List (1993) [1080p]"), "Schindlers List")

    def test_genre_extraction(self):
        """Verify movie genre extraction from summary text."""
        text = "Schindler's List is a 1993 American epic historical drama film directed by Steven Spielberg."
        genres = metadata.extract_genres_from_text(text)
        self.assertIn("Drama", genres)

        text2 = "The Super Mario Bros. Movie is an animated adventure comedy film."
        genres2 = metadata.extract_genres_from_text(text2)
        self.assertIn("Animation", genres2)
        self.assertIn("Adventure", genres2)
        self.assertIn("Comedy", genres2)

    @patch("metadata._get")
    def test_fetch_tvmaze_show_and_episodes(self, mock_get):
        """Test fetching show poster and batch episodes without API keys."""
        mock_get.side_effect = [
            [{"show": {"id": 123, "name": "Test Show", "image": {"original": "https://example.com/show.jpg"}, "summary": "<p>A great show</p>", "genres": ["Comedy"]}}],
            [
                {"season": 1, "number": 1, "name": "Pilot", "summary": "<p>First ep</p>", "runtime": 30, "image": {"medium": "https://example.com/ep1.jpg"}},
                {"season": 1, "number": 2, "name": "Second", "summary": "<p>Second ep</p>", "runtime": 30, "image": None},
            ]
        ]
        show = metadata.fetch_tvmaze_show("Test Show")
        self.assertEqual(show["id"], 123)
        self.assertEqual(show["poster"], "https://example.com/show.jpg")
        self.assertEqual(show["description"], "A great show")

        ep_map = metadata.fetch_tvmaze_episodes(123)
        self.assertEqual(len(ep_map), 2)
        self.assertEqual(ep_map["1:1"]["name"], "Pilot")
        self.assertEqual(ep_map["1:1"]["artwork"], "https://example.com/ep1.jpg")
        self.assertEqual(ep_map["1:2"]["name"], "Second")

    @patch("metadata._get")
    def test_fetch_wikipedia_movie(self, mock_get):
        """Test movie poster and plot retrieval via Wikipedia REST API."""
        mock_get.return_value = {
            "title": "Test Movie",
            "extract": "Test Movie is a 2024 American action comedy film.",
            "originalimage": {"source": "https://upload.wikimedia.org/test.jpg"}
        }
        res = metadata.fetch_wikipedia_movie("Test Movie", 2024)
        self.assertEqual(res["poster"], "https://upload.wikimedia.org/test.jpg")
        self.assertIn("Action", res["genre"])
        self.assertEqual(res["source"], "wikipedia")

    @patch("metadata.fetch_tvmaze_show")
    @patch("metadata.fetch_tvmaze_episodes")
    def test_enrich_shows(self, mock_episodes, mock_show):
        """Test enrich_shows populating shows.poster and batch updating episodes."""
        con = database.connect()
        try:
            con.execute("INSERT INTO shows(name) VALUES(?)", ("Sitcom 90s",))
            sid = con.execute("SELECT id FROM shows WHERE name='Sitcom 90s'").fetchone()["id"]
            con.execute("INSERT INTO media_files(path,duration,kind) VALUES('test1.mp4', 1800, 'episode')")
            mid1 = con.execute("SELECT id FROM media_files WHERE path='test1.mp4'").fetchone()["id"]
            con.execute("INSERT INTO episodes(media_id,show_id,show_name,season,episode,title) VALUES(?,?,?,1,1,'e1')",
                        (mid1, sid, "Sitcom 90s"))
            con.commit()
        finally:
            con.close()

        mock_show.return_value = {
            "id": 999,
            "name": "Sitcom 90s",
            "poster": "https://example.com/sitcom_poster.jpg",
            "description": "Hilarious 90s sitcom",
        }
        mock_episodes.return_value = {
            "1:1": {
                "name": "The Beginning",
                "summary": "Where it all started",
                "runtime": 1800,
                "artwork": "https://example.com/sitcom_ep1.jpg",
            }
        }

        en, fail = metadata.enrich_shows()
        self.assertEqual(en, 1)
        self.assertEqual(fail, 0)

        con = database.connect()
        try:
            show = dict(con.execute("SELECT * FROM shows WHERE name='Sitcom 90s'").fetchone())
            self.assertEqual(show["poster"], "https://example.com/sitcom_poster.jpg")
            self.assertEqual(show["description"], "Hilarious 90s sitcom")

            ep = dict(con.execute("SELECT * FROM episodes WHERE show_id=?", (sid,)).fetchone())
            self.assertEqual(ep["title"], "The Beginning")
            self.assertEqual(ep["description"], "Where it all started")
            self.assertEqual(ep["artwork"], "https://example.com/sitcom_ep1.jpg")
        finally:
            con.close()

    @patch("metadata.fetch_movie_metadata")
    def test_enrich_movies(self, mock_fetch):
        """Test enrich_movies populating movie poster, genre, and description."""
        con = database.connect()
        try:
            con.execute("INSERT INTO media_files(path,duration,kind) VALUES('movie.mp4', 5400, 'movie')")
            mid = con.execute("SELECT id FROM media_files WHERE path='movie.mp4'").fetchone()["id"]
            con.execute("INSERT INTO movies(media_id,title,year) VALUES(?,?,?)", (mid, "Great Movie", 2020))
            con.commit()
        finally:
            con.close()

        mock_fetch.return_value = {
            "poster": "https://upload.wikimedia.org/great_movie.jpg",
            "description": "An inspiring tale.",
            "genre": "Drama",
            "source": "wikipedia",
        }

        en, fail = metadata.enrich_movies()
        self.assertEqual(en, 1)

        con = database.connect()
        try:
            mov = dict(con.execute("SELECT * FROM movies WHERE title='Great Movie'").fetchone())
            self.assertEqual(mov["artwork"], "https://upload.wikimedia.org/great_movie.jpg")
            self.assertEqual(mov["genre"], "Drama")
            self.assertEqual(mov["description"], "An inspiring tale.")
            self.assertEqual(mov["meta_source"], "wikipedia")
        finally:
            con.close()


if __name__ == "__main__":
    unittest.main()
