"""League TV channels: venv/bin/python -m unittest tests.test_league_channels -v."""
import os
import tempfile
import time
import unittest
from unittest.mock import patch

import config
import database
import livesports
import scheduler
import tvcountdown
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
        self.assertIn(b"initLeagueBroadcast(\"nfl\", \"America/New_York\")", page.data)
        self.assertIn(b"leagueCountdown", page.data)

    def test_upcoming_start_time_and_countdown(self):
        game = {"date": "2026-09-29T00:15Z", "status": {"isScheduled": True}}
        label, countdown = tvcountdown.countdown_info(game, now=1790640000)
        self.assertIn("SEP 28  8:15 PM EDT", label)
        self.assertTrue(countdown.startswith("LIVE IN "))
        self.assertIsNone(tvcountdown.countdown_info({"date": game["date"], "status": {"isLive": True}}))

    def test_league_guide_collapses_slots_and_labels_pending_channel(self):
        channels = livesports.ensure_league_channels([{"id": "nfl", "name": "NFL"},
                                                       {"id": "wnba", "name": "WNBA"}])
        nfl, wnba = channels[0]["number"], channels[1]["number"]
        con = database.connect()
        try:
            for index in range(3):
                con.execute("""INSERT INTO schedule_entries(channel_number,start_ts,end_ts,kind,
                    title,subtitle,description,day) VALUES(?,?,?,'episode','Old','Old','','2026-09-28')""",
                    (nfl, 1000 + index * 300, 1300 + index * 300))
            con.commit()
        finally:
            con.close()
        guide = scheduler.guide_data(1100, hours=1)
        nfl_entries = next(row["entries"] for row in guide if row["channel"]["number"] == nfl)
        self.assertEqual(len(nfl_entries), 1)
        self.assertEqual(nfl_entries[0]["end_ts"], 1900)
        self.assertEqual(nfl_entries[0]["title"], "NFL Live")
        with patch("scheduler.channel_pool", return_value=([], [], [])):
            scheduler.generate_day({"number": wnba, "name": "WNBA Live"}, scheduler.local_day())
        guide = scheduler.guide_data(hours=1)
        wnba_entries = next(row["entries"] for row in guide if row["channel"]["number"] == wnba)
        self.assertEqual(wnba_entries[0]["title"], "WNBA Live")
        self.assertIn("ArenaPulse", wnba_entries[0]["subtitle"])

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
             patch("livesports._build_concat_loop", return_value=600) as loop, \
             patch("livesports._upsert_sports_episode") as upsert, \
             patch("livesports.sports.synthesize_speech", return_value=(b"RIFF", "audio/wav")) as tts:
            livesports._render_channel_cards(cards, self.temp.name, "Retro Sports · nfl", "NFL Live", 300)
            tts.assert_called_once()
            self.assertEqual(loop.call_args.args[2], 600)
            self.assertEqual(upsert.call_args.kwargs["airing_seconds"], 300)

    def test_scores_outage_preserves_existing_broadcasts(self):
        with patch("livesports.sports.get_all_scores", return_value={"games": [], "_unavailable": True}), \
             patch("livesports.sports.get_leagues") as leagues, \
             patch("livesports._render_channel_cards") as render:
            self.assertEqual(livesports.refresh_sports_channel(), 0)
            leagues.assert_not_called()
            render.assert_not_called()

    def test_duration_change_rebuilds_future_slots_only(self):
        channel = livesports.ensure_league_channels([{"id": "nfl", "name": "NFL"}])[0]["number"]
        path = os.path.join(self.temp.name, "loop.mp4")
        with open(path, "wb") as video:
            video.write(b"video")
        info = {"duration": 700, "container": "mp4", "resolution": "1280x720",
                "width": 1280, "height": 720, "vcodec": "h264", "acodec": "aac", "bitrate": 1000}
        with patch("livesports.scanner.probe_info", return_value=info), \
             patch("livesports.scanner.compat_warning", return_value=""):
            livesports._upsert_sports_episode(path, "Retro Sports · nfl", "NFL Live", "Game", airing_seconds=330)
            con = database.connect()
            try:
                media_id = con.execute("SELECT id FROM media_files WHERE path=?", (path,)).fetchone()["id"]
                now = time.time()
                for start, end in ((now - 60, now + 270), (now + 270, now + 600)):
                    con.execute("""INSERT INTO schedule_entries(channel_number,start_ts,end_ts,kind,media_id,
                        title,subtitle,description,day) VALUES(?,?,?,'episode',?,'NFL Live','','','2026-09-28')""",
                        (channel, start, end, media_id))
                con.commit()
            finally:
                con.close()
            livesports._upsert_sports_episode(path, "Retro Sports · nfl", "NFL Live", "Game", airing_seconds=300)
        con = database.connect()
        try:
            self.assertEqual(con.execute("SELECT duration FROM media_files WHERE path=?", (path,)).fetchone()[0], 300)
            slots = list(con.execute("SELECT start_ts FROM schedule_entries WHERE channel_number=?", (channel,)))
            self.assertEqual(len(slots), 1)
            self.assertLess(slots[0][0], now)
        finally:
            con.close()


if __name__ == "__main__":
    unittest.main()
