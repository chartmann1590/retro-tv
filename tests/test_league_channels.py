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
import sports
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
        self.assertIn(f'initLeagueBroadcast("nfl", "America/New_York", {first[0]["number"]})'.encode(), page.data)
        self.assertIn(b"leagueCountdown", page.data)
        self.assertIn(b"leagueWatchTv", page.data)

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

    def test_scoring_moment_only_animates_new_plays(self):
        game = {"sport": "football", "awayTeam": {"score": 7}, "homeTeam": {"score": 0}}
        play = {"id": "touchdown", "text": "Rushing TOUCHDOWN", "type": "Rushing Touchdown",
                "scoringPlay": True}
        detail = {"visualPlays": [{"id": "old", "text": "Run"}, play]}
        event, score, newest = tvcountdown.scoring_moment(game, detail, (0, 0), "old")
        self.assertEqual(event["type"], "TOUCHDOWN")
        self.assertEqual((score, newest), ((7, 0), "touchdown"))
        self.assertIsNone(tvcountdown.scoring_moment(game, detail, score, newest)[0])
        self.assertEqual(sports.play_event_type({"text": "Goal! Liverpool score", "scoringPlay": False}, "soccer"), "GOAL")
        self.assertIsNone(sports.play_event_type({"text": "Shot saved in front of goal", "scoringPlay": False}, "soccer"))
        self.assertEqual(sports.play_event_type({"type": "Field Goal Good", "scoringPlay": True}, "football"), "FIELD GOAL")

    def test_final_recap_uses_score_highlights_and_leader(self):
        game = {"id": "final", "league": "nfl", "sport": "football",
                "status": {"isFinal": True},
                "awayTeam": {"displayName": "Eagles", "score": 21},
                "homeTeam": {"displayName": "Bears", "score": 14},
                "leaders": [{"displayName": "Rushing Leader", "leaders": [
                    {"athlete": {"name": "Alex Runner"}, "displayValue": "120 YDS"}]}]}
        detail = {"visualPlays": [
            {"id": "1", "text": "Alex Runner 2 Yd Rush TOUCHDOWN", "type": "Rushing Touchdown",
             "clock": "2:33", "scoringPlay": True},
            {"id": "2", "text": "END GAME", "scoringPlay": False}]}
        recap = sports.build_game_recap(game, detail)
        self.assertIn("Eagles", recap["headline"])
        self.assertIn("21–14", recap["result"])
        self.assertEqual(recap["highlights"][0]["label"], "TOUCHDOWN")
        self.assertEqual(recap["leader"]["name"], "Alex Runner")
        with patch("livesports.sports.get_game_detail", return_value=detail), \
             patch("livesports.sports.generate_field_svg", return_value="<svg>FIELD</svg>"):
            cards = livesports._build_game_cards([game], self.temp.name)
        self.assertEqual(len(cards), 2)
        self.assertIn("FINAL RECAP", cards[1]["html"])
        self.assertIn("TOUCHDOWN", cards[1]["html"])
        with patch("sports.get_game_detail", return_value=detail), \
             patch("sports.get_all_scores", return_value={"games": [game]}):
            response = app.test_client().get("/api/sports/game/nfl/final?sport=football")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["recap"]["leader"]["name"], "Alex Runner")

    def test_hdmi_live_field_tracks_one_featured_game(self):
        games = [{"id": "one", "league": "nfl", "status": {"isLive": True}},
                 {"id": "two", "league": "nfl", "status": {"isLive": True}}]
        with patch("livesports.sports.get_all_scores", return_value={"games": games}), \
             patch("livesports.sports.get_leagues", return_value=[{"id": "nfl", "name": "NFL"}]), \
             patch("livesports._refresh_center", return_value=600), \
             patch("livesports._build_game_cards", return_value=[{"html": "card"}]) as build, \
             patch("livesports._render_channel_cards"), \
             patch("livesports.scheduler.ensure_schedules"):
            livesports.refresh_sports_channel()
        self.assertEqual([game["id"] for game in build.call_args.args[0]], ["one"])

    def test_pinned_game_stays_featured_when_other_games_are_live(self):
        channel = livesports.ensure_league_channels([{"id": "nfl", "name": "NFL"}])[0]["number"]
        games = [{"id": "one", "league": "nfl", "status": {"isLive": True}},
                 {"id": "two", "league": "nfl", "status": {"isLive": True}}]
        livesports.set_pinned_game(channel, "two")
        self.assertEqual(livesports.pinned_game(channel), "two")
        with patch("livesports._build_game_cards", return_value=[{"html": "card"}]) as build, \
             patch("livesports._render_channel_cards"):
            self.assertTrue(livesports._refresh_league_channel({"id": "nfl", "name": "NFL"}, games,
                                                                 300, self.temp.name, force=True))
        self.assertEqual([game["id"] for game in build.call_args.args[0]], ["two"])

    def test_game_picker_selects_and_persists_arena_game(self):
        import tvgames
        channel = livesports.ensure_league_channels([{"id": "nfl", "name": "NFL"}])[0]["number"]
        games = [{"id": "one", "league": "nfl", "date": "2026-09-28T20:00Z",
                  "status": {"isScheduled": True}, "awayTeam": {"abbreviation": "A"},
                  "homeTeam": {"abbreviation": "B"}},
                 {"id": "two", "league": "nfl", "date": "2026-09-28T19:00Z",
                  "status": {"isLive": True}, "awayTeam": {"abbreviation": "C"},
                  "homeTeam": {"abbreviation": "D"}}]
        with patch("tvgames.sports.get_all_scores", return_value={"games": games}), \
             patch("tvgames._send", return_value=True), \
             patch("tvguide.close"), patch("tvsports.close_sports"), patch("tvvod.close_vod"), \
             patch("tvgames.threading.Thread") as worker:
            opened = tvgames.open_picker(channel)
            self.assertTrue(opened["ok"])
            self.assertEqual(opened["selected_game_id"], "two")
            result = tvgames.navigate("ok")
            self.assertTrue(result["ok"])
            self.assertEqual(livesports.pinned_game(channel), "two")
            worker.assert_called_once()
            tvgames.close_picker()

    def test_game_selection_reports_tune_completion_or_failure(self):
        import playback
        import tvgames
        channel = livesports.ensure_league_channels([{"id": "nfl", "name": "NFL"}])[0]["number"]
        game = {"id": "one", "league": "nfl", "status": {"isLive": True}}
        with patch("tvgames.sports.get_all_scores", return_value={"games": [game]}), \
             patch("tvgames.threading.Thread") as worker, \
             patch("tvgames._send", return_value=True), \
             patch.object(playback, "_current", {"channel": channel}):
            selected = tvgames.pin_game(channel, "one", force_tune=True)
            self.assertEqual(selected["request_status"], "preparing")
            args = worker.call_args.kwargs["args"]
            with patch("tvgames.livesports.refresh_selected_channel", return_value=True), \
                 patch("tvgames.playback.tune", return_value={"ok": True}) as tune:
                tvgames._prepare(*args)
            tune.assert_called_once_with(channel, reason="game")
            self.assertEqual(tvgames.get_status()["request_status"], "ready")

            selected = tvgames.pin_game(channel, "one", force_tune=True)
            args = worker.call_args.kwargs["args"]
            with patch("tvgames.livesports.refresh_selected_channel", return_value=False), \
                 patch("tvgames.playback.tune") as tune:
                tvgames._prepare(*args)
            tune.assert_not_called()
            status = tvgames.get_status()
            self.assertEqual(status["request_id"], selected["request_id"])
            self.assertEqual(status["request_status"], "failed")
            self.assertIn("could not be prepared", status["request_error"])

    def test_cards_stay_on_screen_for_twenty_to_thirty_seconds(self):
        with patch("livesports.scanner.probe_info", return_value={"duration": 4.0}), \
             patch("livesports.subprocess.run") as run:
            livesports._mux_card("card.png", "narration.wav", "card.mp4")
            self.assertEqual(run.call_args.args[0][-2], "20.00")
        with patch("livesports.scanner.probe_info", return_value={"duration": 27.0}), \
             patch("livesports.subprocess.run") as run:
            livesports._mux_card("card.png", "narration.wav", "card.mp4")
            self.assertEqual(run.call_args.args[0][-2], "29.00")

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
