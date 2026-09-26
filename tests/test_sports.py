"""Unit and integration tests for ArenaPulse Sports integration, Sports Channel, and Interactive Screen.
Run with: venv/bin/python -m unittest tests.test_sports -v
"""
import json
import os
import tempfile
import unittest
from unittest.mock import patch, Mock

import config
import database
import remote
import sports
import livesports
import tvsports
from app import app


class SportsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        for key, value in {
            "DB_PATH": os.path.join(self.tmp.name, "test.db"),
            "DATA_DIR": self.tmp.name,
            "MPV_SOCKET": os.path.join(self.tmp.name, "mpv.sock"),
            "LIVE_CONTENT_DIR": os.path.join(self.tmp.name, "live_content")
        }.items():
            patcher = patch.object(config, key, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        database.init_db()
        self.client = app.test_client()

    def test_sports_api_live_queries(self):
        """Test live connection to ArenaPulse REST API."""
        scores = sports.get_all_scores()
        self.assertIsInstance(scores, dict)
        self.assertIn("games", scores)
        self.assertIn("stats", scores)
        self.assertIn("leagues", scores)
        self.assertGreater(len(scores["games"]), 0)

        news = sports.get_all_news()
        self.assertIsInstance(news, list)
        self.assertGreater(len(news), 0)
        self.assertIn("headline", news[0])

    def test_svg_field_generators(self):
        """Test SVG field rendering across football, baseball, basketball, hockey, and soccer."""
        home = {"name": "Packers", "displayName": "Green Bay Packers", "shortDisplayName": "Green Bay", "abbreviation": "GB", "color": "#203731"}
        away = {"name": "Falcons", "displayName": "Atlanta Falcons", "shortDisplayName": "Atlanta", "abbreviation": "ATL", "color": "#a71930"}

        # Football
        fb_svg = sports.generate_football_field_svg(home, away, situation={"yardLine": 35, "down": 2, "distance": 6, "isRedZone": False})
        self.assertIn("<svg", fb_svg)
        self.assertIn("</svg>", fb_svg)
        self.assertIn("GB", fb_svg)
        self.assertIn("ATL", fb_svg)
        self.assertTrue("2ND & 6" in fb_svg.upper() or "2ND &AMP; 6" in fb_svg.upper())

        # Baseball
        bb_svg = sports.generate_baseball_field_svg(home, away, situation={"onFirst": True, "onSecond": False, "onThird": True}, count={"balls": 2, "strikes": 1}, outs=1)
        self.assertIn("<svg", bb_svg)
        self.assertIn("1ST", bb_svg)
        self.assertIn("BALLS", bb_svg)

        # Basketball
        bball_svg = sports.generate_basketball_court_svg(home, away)
        self.assertIn("<svg", bball_svg)
        self.assertIn("stroke", bball_svg)

        # Soccer
        soc_svg = sports.generate_soccer_pitch_svg(home, away)
        self.assertIn("<svg", soc_svg)

        # Hockey
        hoc_svg = sports.generate_hockey_rink_svg(home, away)
        self.assertIn("<svg", hoc_svg)

        # Master dispatcher
        disp_svg = sports.generate_field_svg("football", home, away)
        self.assertIn("<svg", disp_svg)

    def test_tts_script_and_synthesis(self):
        """Test speech narration generation and Kokoro speech synthesis."""
        sample_game = {
            "league": "nfl",
            "leagueName": "NFL",
            "homeTeam": {"displayName": "Kansas City Chiefs", "score": 24},
            "awayTeam": {"displayName": "Buffalo Bills", "score": 21},
            "status": {"isFinal": True, "detail": "Final"}
        }
        script = sports.script_for_game(sample_game)
        self.assertIn("Final", script)
        self.assertIn("Chiefs", script)

        # Speech synthesis test
        audio_bytes, ctype = sports.synthesize_speech("Touchdown Kansas City!", voice="am_michael")
        self.assertGreater(len(audio_bytes), 1000)
        self.assertEqual(ctype, "audio/wav")

    def test_ensure_sports_channel(self):
        """Verify dedicated sports channel is created in SQLite."""
        livesports._ensure_sports_channel()
        con = database.connect()
        try:
            ch = con.execute("SELECT * FROM channels WHERE number=?", (config.SPORTS_CHANNEL_NUMBER,)).fetchone()
            self.assertIsNotNone(ch)
            self.assertEqual(ch["name"], config.SPORTS_CHANNEL_NAME)

            src = con.execute("SELECT * FROM channel_sources WHERE channel_number=?", (config.SPORTS_CHANNEL_NUMBER,)).fetchone()
            self.assertIsNotNone(src)
            self.assertEqual(src["source_type"], "show")
            self.assertEqual(src["source_value"], config.SPORTS_SHOW)
        finally:
            con.close()

    def test_tvsports_categories_and_nav(self):
        """Verify tvsports category building and navigation."""
        sample_games = [
            {"id": "1", "name": "Texas at Tennessee", "sport": "football", "league": "college-football", "status": {"isLive": False, "isScheduled": True}, "homeTeam": {"abbreviation": "TENN"}, "awayTeam": {"abbreviation": "TEX"}},
            {"id": "2", "name": "Cubs at Red Sox", "sport": "baseball", "league": "mlb", "status": {"isFinal": True}, "homeTeam": {"abbreviation": "BOS"}, "awayTeam": {"abbreviation": "CHC"}},
            {"id": "3", "name": "Roma at Fenerbahce", "sport": "soccer", "league": "uefa.champions", "status": {"isLive": True}, "homeTeam": {"abbreviation": "FEN"}, "awayTeam": {"abbreviation": "ROMA"}}
        ]
        cats = tvsports._build_categories(sample_games)
        cat_ids = [c["id"] for c in cats]
        self.assertIn("live", cat_ids)
        self.assertIn("football", cat_ids)
        self.assertIn("baseball", cat_ids)
        self.assertIn("soccer", cat_ids)

        # Search query
        search_cats = tvsports._build_categories(sample_games, query="Tennessee")
        self.assertEqual(len(search_cats), 1)
        self.assertEqual(search_cats[0]["id"], "search")
        self.assertEqual(len(search_cats[0]["items"]), 1)

    def test_remote_actions_and_mapping(self):
        """Verify SPORTS remote action and default key binding."""
        self.assertIn("SPORTS", remote.ACTIONS)
        self.assertEqual(remote.DEFAULTS.get("SPORTS"), "s")
        mappings = remote.get_mappings()
        self.assertIn("SPORTS", mappings)
        self.assertEqual(mappings["SPORTS"], "s")

    def test_flask_routes(self):
        """Test Flask web endpoints for Sports."""
        # 1. HTML page
        resp = self.client.get("/sports")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"RETRO", resp.data)
        self.assertIn(b"SPORTS", resp.data)
        self.assertIn(b"GAME CENTER", resp.data)

        # 2. Scores API
        scores_res = self.client.get("/api/sports/scores")
        self.assertEqual(scores_res.status_code, 200)
        scores_json = scores_res.get_json()
        self.assertIn("games", scores_json)
        self.assertIn("leagues", scores_json)

        # 3. News API
        news_res = self.client.get("/api/sports/news")
        self.assertEqual(news_res.status_code, 200)
        news_json = news_res.get_json()
        self.assertIn("articles", news_json)

        # 4. Field API
        if scores_json["games"]:
            g = scores_json["games"][0]
            field_res = self.client.get(f"/api/sports/field/{g.get('sport')}/{g.get('league')}/{g.get('id')}")
            self.assertEqual(field_res.status_code, 200)
            field_json = field_res.get_json()
            self.assertTrue(field_json.get("ok"))
            self.assertIn("<svg", field_json.get("svg", ""))

        # 5. TTS Endpoint
        tts_res = self.client.post("/api/sports/tts", json={"text": "Hello sports fans!"})
        self.assertEqual(tts_res.status_code, 200)
        self.assertIn(b"RIFF", tts_res.data[:4])  # WAV header

        # 6. HDMI status includes tv_sports
        hdmi_res = self.client.get("/api/hdmi")
        self.assertEqual(hdmi_res.status_code, 200)
        hdmi_json = hdmi_res.get_json()
        self.assertIn("tv_sports", hdmi_json)


if __name__ == "__main__":
    unittest.main()
