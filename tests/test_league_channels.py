"""League TV channels: venv/bin/python -m unittest tests.test_league_channels -v."""
import os
import tempfile
import unittest
from unittest.mock import patch

import config
import database
import livesports
from app import app


class LeagueChannelTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        for name, value in {"DB_PATH": os.path.join(self.temp.name, "tv.db"),
                            "LIVE_CONTENT_DIR": os.path.join(self.temp.name, "live")}.items():
            p = patch.object(config, name, value)
            p.start()
            self.addCleanup(p.stop)
        database.init_db()

    def test_every_arena_league_gets_one_channel_and_watch_overlay(self):
        leagues = [{"id": "nfl", "name": "NFL"}, {"id": "wnba", "name": "WNBA"},
                   {"id": "uefa.champions", "name": "Champions League"}]
        first = livesports.ensure_league_channels(leagues)
        self.assertEqual(len(first), 3)
        self.assertEqual(livesports.ensure_league_channels(leagues), [])
        con = database.connect()
        try:
            con.execute("UPDATE channels SET name='My WNBA', enabled=0 WHERE number=?", (first[1]["number"],))
            con.commit()
        finally:
            con.close()
        livesports.ensure_league_channels(leagues)
        con = database.connect()
        try:
            row = con.execute("SELECT name,enabled FROM channels WHERE number=?", (first[1]["number"],)).fetchone()
            self.assertEqual((row["name"], row["enabled"]), ("My WNBA", 0))
        finally:
            con.close()
        self.assertEqual(livesports.league_for_channel(first[2]["number"]), "uefa.champions")
        page = app.test_client().get(f"/watch/{first[0]['number']}")
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"league-broadcast.js", page.data)
        self.assertIn(b"initLeagueBroadcast(\"nfl\")", page.data)

    def test_broadcast_uses_game_detail_field_and_arena_tts(self):
        game = {"id": "123", "league": "nfl", "sport": "football", "status": {"isLive": True},
                "homeTeam": {"displayName": "Bears", "score": 7},
                "awayTeam": {"displayName": "Eagles", "score": 10}}
        detail = {"visualPlays": [{"text": "Touchdown Eagles", "clock": "2:32"}]}
        with patch("livesports.sports.get_game_detail", return_value=detail), \
             patch("livesports.sports.generate_field_svg", return_value="<svg>FIELD</svg>"):
            cards = livesports._build_game_cards([game], self.temp.name)
        self.assertEqual(len(cards), 1)
        self.assertIn("Touchdown Eagles", cards[0]["html"])
        self.assertIn("<svg>FIELD</svg>", cards[0]["html"])
        self.assertIn("Touchdown Eagles", cards[0]["narration"])
        with patch("livesports._render_card_png"), patch("livesports._mux_card"), \
             patch("livesports._build_concat_loop", return_value=300), \
             patch("livesports._upsert_sports_episode"), \
             patch("livesports.sports.synthesize_speech", return_value=(b"RIFF", "audio/wav")) as tts:
            livesports._render_channel_cards(cards, self.temp.name, "Retro Sports · nfl", "NFL Live", 300)
            tts.assert_called_once()


if __name__ == "__main__":
    unittest.main()
