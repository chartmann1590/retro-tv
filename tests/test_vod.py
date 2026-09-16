"""Unit tests for Video On Demand (VOD) catalog, search, playback, and API endpoints.
Run with: venv/bin/python -m unittest tests.test_vod -v
"""
import os
import tempfile
import unittest
from unittest.mock import patch, Mock

import config
import database
import playback
import scheduler
import vod
from app import app


class VodTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        for key, value in {"DB_PATH": os.path.join(self.tmp.name, "test.db"), "DATA_DIR": self.tmp.name}.items():
            patcher = patch.object(config, key, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        database.init_db()
        vod.clear_cache()
        self.client = app.test_client()

    def add_movie(self, title="Test Film", year=1999, genre="Action, Sci-Fi"):
        path = os.path.join(self.tmp.name, f"{title.replace(' ', '_')}.mp4")
        with open(path, "wb") as f:
            f.write(b"video_data_12345")
        con = database.connect()
        try:
            cur = con.execute("""
                INSERT INTO media_files(path,duration,kind,resolution,vcodec,container)
                VALUES(?,5400,'movie','1080p','h264','mp4')
            """, (path,))
            mid = cur.lastrowid
            con.execute("""
                INSERT INTO movies(media_id,title,year,genre,description,artwork)
                VALUES(?,?,?,?,?,?)
            """, (mid, title, year, genre, f"Description for {title}", "https://example.com/poster.jpg"))
            con.commit()
            return mid, path
        finally:
            con.close()

    def add_show_with_episodes(self, show_name="Retro Comedy", num_episodes=3):
        con = database.connect()
        try:
            con.execute("INSERT OR IGNORE INTO shows(name) VALUES(?)", (show_name,))
            sid = con.execute("SELECT id FROM shows WHERE name=?", (show_name,)).fetchone()["id"]
            con.commit()
        finally:
            con.close()

        media_ids = []
        for i in range(1, num_episodes + 1):
            path = os.path.join(self.tmp.name, f"{show_name.replace(' ', '_')}_S01E{i:02d}.mp4")
            with open(path, "wb") as f:
                f.write(b"ep_data_123")
            con = database.connect()
            try:
                cur = con.execute("""
                    INSERT INTO media_files(path,duration,kind,resolution,vcodec,container)
                    VALUES(?,1500,'episode','1080p','h264','mp4')
                """, (path,))
                mid = cur.lastrowid
                con.execute("""
                    INSERT INTO episodes(media_id,show_id,show_name,season,episode,title,description,artwork)
                    VALUES(?,?,?,1,?,?,?,?)
                """, (mid, sid, show_name, i, f"Episode {i}", f"Synopsis for episode {i}", "https://example.com/ep.jpg"))
                con.commit()
                media_ids.append(mid)
            finally:
                con.close()
        return sid, media_ids

    def test_catalog_returns_movies_shows_and_categories(self):
        self.add_movie("The Matrix", 1999, "Action, Sci-Fi")
        self.add_show_with_episodes("Seinfeld", 4)
        cat = vod.get_catalog()
        self.assertIsNotNone(cat)
        self.assertEqual(cat["total_movies"], 1)
        self.assertEqual(cat["total_shows"], 1)
        self.assertTrue(len(cat["categories"]) >= 2)
        cat_ids = [c["id"] for c in cat["categories"]]
        self.assertIn("recently-added", cat_ids)
        self.assertIn("feature-films", cat_ids)
        self.assertIn("tv-series", cat_ids)

    def test_show_details_groups_seasons_and_episodes(self):
        sid, mids = self.add_show_with_episodes("The Twilight Zone", 3)
        details = vod.get_show_details(sid)
        self.assertIsNotNone(details)
        self.assertEqual(details["name"], "The Twilight Zone")
        self.assertEqual(details["episode_count"], 3)
        self.assertEqual(len(details["seasons"]), 1)
        self.assertEqual(details["seasons"][0]["season"], 1)
        self.assertEqual(len(details["seasons"][0]["episodes"]), 3)
        self.assertEqual(details["seasons"][0]["episodes"][0]["title"], "Episode 1")

    def test_movie_details(self):
        mid, path = self.add_movie("Jurassic Park", 1993, "Adventure, Sci-Fi")
        movie = vod.get_movie_details(mid)
        self.assertIsNotNone(movie)
        self.assertEqual(movie["title"], "Jurassic Park")
        self.assertEqual(movie["year"], 1993)
        self.assertEqual(movie["genre"], "Adventure, Sci-Fi")

    def test_search_vod_across_movies_shows_and_episodes(self):
        self.add_movie("Back to the Future", 1985, "Sci-Fi, Comedy")
        self.add_show_with_episodes("Futurama", 2)
        res = vod.search_vod("Futur")
        self.assertTrue(res["total"] >= 2)
        movie_titles = [m["title"] for m in res["movies"]]
        show_names = [s["name"] for s in res["shows"]]
        self.assertIn("Back to the Future", movie_titles)
        self.assertIn("Futurama", show_names)

    def test_playback_play_and_stop_vod(self):
        mid, path = self.add_movie("Blade Runner", 1982, "Sci-Fi")
        proc = Mock()
        proc.poll.return_value = None

        with patch.object(playback, "_proc", proc), \
             patch.object(playback, "_ipc", return_value={"error": "success"}) as ipc, \
             patch.dict(playback._current):
            res = playback.play_vod(media_id=mid)
            self.assertTrue(res["ok"])
            self.assertTrue(playback._current["is_vod"])
            self.assertEqual(playback._current["media"], path)
            self.assertIn("Blade Runner", playback._current["vod_info"]["title"])

            st = playback.status()
            self.assertTrue(st["is_vod"])
            self.assertEqual(st["vod_info"]["media_id"], mid)

            # Stopping VOD calls restore_last()
            with patch.object(playback, "restore_last", return_value={"ok": True}) as restore_mock:
                stop_res = playback.stop_vod()
                self.assertTrue(stop_res["ok"])
                self.assertFalse(playback._current["is_vod"])
                restore_mock.assert_called_once()

    def test_api_vod_endpoints(self):
        mid, _ = self.add_movie("Ghostbusters", 1984, "Comedy")
        sid, _ = self.add_show_with_episodes("Cheers", 2)

        # 1. /api/vod/catalog
        r = self.client.get("/api/vod/catalog")
        self.assertEqual(r.status_code, 200)
        data = r.get_json()
        self.assertTrue(data["ok"])
        self.assertTrue(data["total_movies"] >= 1)

        # 2. /api/vod/show/<id>
        r_show = self.client.get(f"/api/vod/show/{sid}")
        self.assertEqual(r_show.status_code, 200)
        self.assertEqual(r_show.get_json()["show"]["name"], "Cheers")

        # 3. /api/vod/movie/<id>
        r_movie = self.client.get(f"/api/vod/movie/{mid}")
        self.assertEqual(r_movie.status_code, 200)
        self.assertEqual(r_movie.get_json()["movie"]["title"], "Ghostbusters")

        # 4. /api/vod/search?q=Ghost
        r_search = self.client.get("/api/vod/search?q=Ghost")
        self.assertEqual(r_search.status_code, 200)
        self.assertTrue(len(r_search.get_json()["movies"]) >= 1)

        # 5. /api/vod/play and /api/vod/stop
        with patch.object(playback, "play_vod", return_value={"ok": True, "title": "Ghostbusters"}):
            r_play = self.client.post("/api/vod/play", json={"media_id": mid})
            self.assertEqual(r_play.status_code, 200)
            self.assertTrue(r_play.get_json()["ok"])

        with patch.object(playback, "stop_vod", return_value={"ok": True}):
            r_stop = self.client.post("/api/vod/stop")
            self.assertEqual(r_stop.status_code, 200)
            self.assertTrue(r_stop.get_json()["ok"])

        # 6. /vod page renders
        r_page = self.client.get("/vod")
        self.assertEqual(r_page.status_code, 200)
        self.assertIn(b"ON DEMAND", r_page.data)

        # 7. /watch?vod=<mid> renders
        r_watch = self.client.get(f"/watch?vod={mid}")
        self.assertEqual(r_watch.status_code, 200)
        self.assertIn(b"ON DEMAND", r_watch.data)

        # 8. /remote page has ON DEMAND button but NO embedded catalog or modals
        r_remote = self.client.get("/remote")
        self.assertEqual(r_remote.status_code, 200)
        self.assertIn(b"ON DEMAND", r_remote.data)
        self.assertNotIn(b'id="remoteVod"', r_remote.data)
        self.assertNotIn(b'id="rvodShowModal"', r_remote.data)

    def test_on_tv_vod_overlay_and_navigation(self):
        import tvvod
        mid, _ = self.add_movie("Blade Runner", 1982, "Sci-Fi")
        sid, _ = self.add_show_with_episodes("Futurama", 3)

        with patch.object(tvvod, "_send", return_value={"error": "success"}) as ipc, \
             patch.object(playback, "mpv_alive", return_value=True):
            # 1. Open on-TV VOD
            res = tvvod.open_vod()
            self.assertTrue(res["ok"])
            self.assertTrue(tvvod.is_visible())
            command = ipc.call_args.args[0]
            self.assertEqual(command[:3], ["osd-overlay", 42, "ass-events"])
            ass_content = command[3]
            self.assertIn("RETRO", ass_content)
            self.assertIn("FLIX", ass_content)

            # 2. Navigation: move right on carousel
            res_right = tvvod.nav("right")
            self.assertTrue(res_right["visible"])

            # 3. Navigation: move down to TV series category
            while tvvod._cat_idx < len(tvvod._categories) - 1 and tvvod._categories[tvvod._cat_idx].get("id") != "tv-series":
                tvvod.nav("down")
            self.assertEqual(tvvod._categories[tvvod._cat_idx]["id"], "tv-series")

            # 4. Select a show: enters show view
            with patch.object(vod, "get_show_details", return_value={
                "show": {
                    "id": sid,
                    "name": "Futurama",
                    "episode_count": 3,
                    "seasons": [{"season": 1, "episodes": [
                        {"media_id": 999, "title": "Space Pilot 3000", "episode": 1, "duration_min": 23}
                    ]}]
                }
            }):
                res_sel = tvvod.nav("select")
                self.assertEqual(res_sel["mode"], "show")
                self.assertEqual(res_sel["show_name"], "Futurama")
                # Selecting episode plays it
                with patch.object(playback, "play_vod", return_value={"ok": True}) as play_mock:
                    res_play = tvvod.nav("select")
                    self.assertTrue(res_play.get("playing"))
                    play_mock.assert_called_once()
                    self.assertFalse(tvvod.is_visible())

            # 5. Close VOD
            self.assertTrue(tvvod.close_vod())
            last_cmd = ipc.call_args.args[0]
            self.assertEqual(last_cmd, ["osd-overlay", 42, "none", ""])

    def test_api_tv_vod_endpoints(self):
        import tvvod
        self.add_movie("Alien", 1979, "Horror, Sci-Fi")

        with patch.object(tvvod, "_send", return_value={"error": "success"}), \
             patch.object(playback, "mpv_alive", return_value=True):
            # POST /api/tv-vod open
            r_open = self.client.post("/api/tv-vod", json={"action": "open"})
            self.assertEqual(r_open.status_code, 200)
            self.assertTrue(r_open.get_json()["visible"])

            # POST /api/tv-vod/nav right
            r_nav = self.client.post("/api/tv-vod/nav", json={"action": "right"})
            self.assertEqual(r_nav.status_code, 200)
            self.assertTrue(r_nav.get_json()["visible"])

            # GET /api/tv-vod/status
            r_stat = self.client.get("/api/tv-vod/status")
            self.assertEqual(r_stat.status_code, 200)
            self.assertTrue(r_stat.get_json()["visible"])

            # POST /api/tv-vod close
            r_close = self.client.post("/api/tv-vod", json={"action": "close"})
            self.assertEqual(r_close.status_code, 200)
            self.assertFalse(r_close.get_json()["visible"])


if __name__ == "__main__":
    unittest.main()

