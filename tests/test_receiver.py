"""Run with venv/bin/python -m unittest discover -s tests -v."""
from contextlib import contextmanager
import os
import tempfile
import time
import unittest
from unittest.mock import patch, Mock

import config
import database
import playback
import scheduler
import streaming
import tvguide
from app import app, range_response


@contextmanager
def connection():
    con = database.connect()
    try:
        with con:
            yield con
    finally:
        con.close()


class ReceiverTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        for key, value in {"DB_PATH": os.path.join(self.tmp.name, "test.db"), "DATA_DIR": self.tmp.name}.items():
            patcher = patch.object(config, key, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        database.init_db()
        with connection() as con:
            con.execute("INSERT INTO channels(number,name,enabled,commercial_mode) VALUES(2,'Test TV',1,'off')")
        self.channel = scheduler.get_channels()[0]
        self.client = app.test_client()

    def add_episode(self, number=1):
        path = os.path.join(self.tmp.name, f"episode{number}.mp4")
        with open(path, "wb") as f:
            f.write(b"0123456789")
        with connection() as con:
            cur = con.execute("INSERT INTO media_files(path,duration,kind) VALUES(?,1320,'episode')", (path,))
            mid = cur.lastrowid
            con.execute("INSERT INTO episodes(media_id,show_name,season,episode,title) VALUES(?,'Test Show',1,?,'Episode')", (mid, number))
        return path

    def entries(self):
        with connection() as con:
            return [dict(r) for r in con.execute("SELECT * FROM schedule_entries ORDER BY start_ts")]

    def test_single_episode_channel_fills_entire_day(self):
        self.add_episode()
        scheduler.generate_day(self.channel, scheduler.local_day())
        entries = self.entries()
        start, end = scheduler.day_bounds(scheduler.local_day())
        self.assertEqual(entries[0]["start_ts"], start)
        self.assertEqual(entries[-1]["end_ts"], end)
        self.assertTrue(all(a["end_ts"] == b["start_ts"] for a, b in zip(entries, entries[1:])))

    def test_maintenance_keeps_program_ids_and_future_lineup(self):
        self.add_episode(); self.add_episode(2)
        scheduler.generate_day(self.channel, scheduler.local_day())
        before = self.entries()
        self.assertEqual(scheduler.generate_day(self.channel, scheduler.local_day()), 0)
        self.assertEqual(before, self.entries())
        self.assertTrue(all(a["media_id"] != b["media_id"] for a, b in zip(before, before[1:])))

    def test_future_program_is_not_reported_live(self):
        self.add_episode()
        scheduler.generate_day(self.channel, scheduler.local_day())
        start = self.entries()[0]["start_ts"]
        self.assertIsNone(scheduler.now_playing(2, start - 1))

    def test_invalid_channel_does_not_change_last_channel(self):
        database.set_state("last_channel", "2")
        result = self.client.post('/api/tune', json={"channel": 999}).get_json()
        self.assertFalse(result["ok"])
        self.assertEqual(database.get_state("last_channel"), "2")

    def test_channel_change_reuses_mpv_and_resumes_at_live_offset(self):
        path = self.add_episode()
        proc = Mock(); proc.poll.return_value = None
        with patch.object(playback, '_proc', proc), patch.object(playback, '_ipc', return_value={"error": "success"}) as ipc, patch.object(playback.subprocess, 'Popen') as popen, patch.dict(playback._current):
            self.assertTrue(playback.play_file(path, 123.5, 2, 'Test'))
            popen.assert_not_called()
            ipc.assert_any_call(['loadfile', path, 'replace', -1, {'start': '123.5'}])
            ipc.assert_any_call(['set_property', 'pause', False])

    def test_ranges_support_suffix_and_reject_out_of_bounds(self):
        path = self.add_episode()
        with app.test_request_context('/', headers={'Range': 'bytes=-3'}):
            response = range_response(path, 'video/mp4')
            response.direct_passthrough = False
            self.assertEqual(response.status_code, 206)
            self.assertEqual(response.get_data(), b'789')
            response.close()
        from werkzeug.exceptions import RequestedRangeNotSatisfiable
        with app.test_request_context('/', headers={'Range': 'bytes=99-100'}):
            with self.assertRaises(RequestedRangeNotSatisfiable):
                range_response(path, 'video/mp4')

    def test_pages_render_without_launching_playback_or_streaming(self):
        with patch.object(playback, 'play_file') as play:
            for path in ('/', '/guide', '/remote', '/watch', '/admin'):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200, path)
                if path in ('/', '/guide', '/remote'):
                    self.assertNotIn(b'<video', response.data)
            play.assert_not_called()

    def test_native_guide_uses_overlay_not_another_player(self):
        self.add_episode();scheduler.generate_day(self.channel, scheduler.local_day())
        with patch.object(tvguide, '_send', return_value={'error': 'success'}) as ipc:
            self.assertTrue(tvguide.render(2))
            command = ipc.call_args.args[0]
            self.assertEqual(command[:3], ['osd-overlay', 42, 'ass-events'])
            self.assertIn('Test Show', command[3])
            self.assertTrue(tvguide.close())

    def test_commercial_break_resolves_current_ad_and_offset(self):
        first = self.add_episode()
        second = self.add_episode(2)
        import json
        now = time.time()
        with connection() as con:
            ids = [r["id"] for r in con.execute("SELECT id FROM media_files ORDER BY id")]
            con.execute("UPDATE media_files SET duration=30")
            con.execute("""INSERT INTO schedule_entries(channel_number,start_ts,end_ts,kind,commercial_ids,title,day)
                           VALUES(2,?,?,'commercial_break',?,'Commercial Break',?)""",
                        (now - 35, now + 25, json.dumps(ids), scheduler.local_day()))
        entry, offset, path, duration = streaming.resolve_live(2, now)
        self.assertEqual(path, second)
        self.assertAlmostEqual(offset, 5)
        self.assertEqual(duration, 30)

    def test_failed_playback_does_not_persist_a_channel_change(self):
        self.add_episode();scheduler.generate_day(self.channel, scheduler.local_day())
        database.set_state("last_channel", "3")
        with patch.object(playback, 'play_file', return_value=False):
            self.assertFalse(playback.tune(2)["ok"])
        self.assertEqual(database.get_state("last_channel"), "3")

    def test_hdmi_selection_prefers_sink_name_over_numeric_id(self):
        import json
        result = Mock(stdout=json.dumps([{'info': {'props': {'media.class': 'Audio/Sink', 'node.name': 'alsa_output.test.hdmi-stereo'}}}]))
        with patch.object(config, 'AUDIO_DEVICE', ''), patch.object(playback.subprocess, 'run', return_value=result):
            self.assertEqual(playback.audio_device(), 'pulse/alsa_output.test.hdmi-stereo')


if __name__ == '__main__':
    unittest.main()
