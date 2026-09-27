"""Safe source replacement: venv/bin/python -m unittest tests.test_transcode -v."""
import os
import fcntl
import json
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import config
import database
import phone_transcode
import scanner
import transcode


class TranscodeReplacementTests(unittest.TestCase):
    def test_reprobe_replaces_scanner_duration_before_deleting_source(self):
        with tempfile.TemporaryDirectory() as root:
            source = os.path.join(root, "original.mkv")
            with open(source, "wb") as output:
                output.write(b"original hevc")
            with patch.object(config, "DB_PATH", os.path.join(root, "test.db")), \
                 patch.object(config, "DATA_DIR", root), \
                 patch.object(transcode, "TRANSCODE_DIR", os.path.join(root, "converted")):
                database.init_db()
                con = database.connect()
                try:
                    media_id = con.execute("""INSERT INTO media_files(path,kind,duration,vcodec,transcode_status)
                        VALUES(?,?,?,?,?)""", (source, "episode", 1320, "hevc", "pending")).lastrowid
                    con.commit()
                finally:
                    con.close()

                def encode(_, destination, __, duration):
                    self.assertEqual(duration, 20)
                    with open(destination, "wb") as output:
                        output.write(b"verified h264")
                    return True, ""

                details = {"duration": 20, "vcodec": "h264", "acodec": "aac", "width": 1280,
                           "height": 720, "bitrate": 1000, "size": 13, "mtime": 1}
                with patch.object(transcode, "_ffprobe_duration", return_value=20), \
                     patch.object(phone_transcode, "encode", side_effect=encode), \
                     patch.object(phone_transcode, "finish"), \
                     patch.object(transcode, "_validated_output", return_value=details) as validate:
                    transcode.run_one(media_id, source, 1320, use_phone=True)
                    self.assertEqual(validate.call_args.args[1], 20)
                con = database.connect()
                try:
                    row = con.execute("SELECT path,duration,transcode_status FROM media_files WHERE id=?",
                                      (media_id,)).fetchone()
                finally:
                    con.close()
                self.assertEqual((row["duration"], row["transcode_status"]), (20, "done"))
                self.assertEqual(row["path"], source)
                with open(source, "rb") as replacement:
                    self.assertEqual(replacement.read(), b"verified h264")

    def test_changed_same_size_source_is_pushed_again(self):
        with tempfile.TemporaryDirectory() as root:
            source = os.path.join(root, "source.mkv")
            with open(source, "wb") as output:
                output.write(b"new source")
            stat = os.stat(source)
            parts = os.path.join(root, ".phone_1")
            os.makedirs(parts)
            with open(os.path.join(parts, "manifest.json"), "w") as output:
                json.dump({"path": source, "size": stat.st_size, "mtime_ns": stat.st_mtime_ns - 1,
                           "duration": 10, "chunk_seconds": phone_transcode.CHUNK_SECONDS}, output)
            calls = []

            def adb(*args, **_):
                calls.append(args)
                if args[0] == "push":
                    raise subprocess.CalledProcessError(1, args)
                return subprocess.CompletedProcess(args, 0)

            with patch.object(config, "TRANSCODE_DIR", root), \
                 patch.object(phone_transcode, "_adb", side_effect=adb), \
                 patch.object(phone_transcode, "_source_width", return_value=1280), \
                 patch.object(phone_transcode, "_remote_size", return_value=stat.st_size), \
                 patch.object(phone_transcode, "_too_hot", return_value=(True, 46.0)):
                ok, _ = phone_transcode.encode(source, os.path.join(root, "out.mkv"), 1, 10)
            self.assertFalse(ok)
            self.assertTrue(any(args[0] == "push" for args in calls))

    def test_phone_battery_pause_defaults_off_and_can_be_saved(self):
        with tempfile.TemporaryDirectory() as root, \
             patch.object(config, "DATA_DIR", root), \
             patch.object(config, "DB_PATH", os.path.join(root, "test.db")):
            database.init_db()
            self.assertEqual(database.get_setting("phone_battery_throttle_enabled"), "0")
            database.set_setting("phone_battery_throttle_enabled", "1")
            self.assertTrue(phone_transcode.battery_throttle_enabled())
            database.set_setting("phone_battery_throttle_enabled", "0")
            self.assertFalse(phone_transcode.battery_throttle_enabled())

    def test_phone_uses_android_thermal_status_for_pause(self):
        with patch.object(phone_transcode, "battery_temp_c", return_value=50.0), \
             patch.object(phone_transcode, "thermal_status", return_value=0), \
             patch.object(phone_transcode, "battery_throttle_enabled", return_value=False):
            self.assertEqual(phone_transcode._too_hot(), (False, 50.0))
        with patch.object(phone_transcode, "battery_temp_c", return_value=50.0), \
             patch.object(phone_transcode, "thermal_status", return_value=0), \
             patch.object(phone_transcode, "battery_throttle_enabled", return_value=True):
            self.assertEqual(phone_transcode._too_hot(), (True, 50.0))
        with patch.object(phone_transcode, "battery_temp_c", return_value=40.0), \
             patch.object(phone_transcode, "thermal_status", return_value=2), \
             patch.object(phone_transcode, "battery_throttle_enabled", return_value=False):
            self.assertEqual(phone_transcode._too_hot(), (True, 40.0))
        with patch.object(phone_transcode, "battery_temp_c", return_value=40.0), \
             patch.object(phone_transcode, "thermal_status", return_value=None), \
             patch.object(phone_transcode, "battery_throttle_enabled", return_value=False):
            self.assertEqual(phone_transcode._too_hot(), (True, 40.0))

    def test_verified_copy_replaces_source_and_keeps_media_id(self):
        with tempfile.TemporaryDirectory() as root:
            source = os.path.join(root, "original.mkv")
            with open(source, "wb") as output:
                output.write(b"original hevc")
            with patch.object(config, "DB_PATH", os.path.join(root, "test.db")), \
                 patch.object(config, "DATA_DIR", root), \
                 patch.object(transcode, "TRANSCODE_DIR", os.path.join(root, "converted")):
                database.init_db()
                con = database.connect()
                try:
                    media_id = con.execute("""INSERT INTO media_files(path,kind,size,duration,vcodec,transcode_status)
                        VALUES(?,?,?,?,?,?)""", (source, "episode", 13, 10, "hevc", "pending")).lastrowid
                    con.commit()
                finally:
                    con.close()

                def encode(_, destination, __, ___):
                    with open(destination, "wb") as output:
                        output.write(b"verified h264")
                    return True, ""

                details = {"duration": 10, "vcodec": "h264", "acodec": "aac", "width": 1280,
                           "height": 720, "bitrate": 1000, "size": 13, "mtime": 1}
                with patch.object(transcode, "_ffprobe_duration", return_value=10), \
                     patch.object(transcode, "_encode", side_effect=encode), \
                     patch.object(transcode, "_validated_output", return_value=details):
                    transcode.run_one(media_id, source, 10)

                con = database.connect()
                try:
                    row = con.execute("SELECT * FROM media_files WHERE id=?", (media_id,)).fetchone()
                finally:
                    con.close()
                with open(source, "rb") as replacement:
                    self.assertEqual(replacement.read(), b"verified h264")
                self.assertTrue(os.path.isfile(row["path"]))
                self.assertEqual((row["vcodec"], row["transcode_status"]), ("h264", "done"))

    def test_phone_failure_keeps_original_queued(self):
        with tempfile.TemporaryDirectory() as root:
            source = os.path.join(root, "original.mkv")
            with open(source, "wb") as output:
                output.write(b"original hevc")
            with patch.object(config, "DB_PATH", os.path.join(root, "test.db")), \
                 patch.object(config, "DATA_DIR", root), \
                 patch.object(transcode, "TRANSCODE_DIR", os.path.join(root, "converted")):
                database.init_db()
                con = database.connect()
                try:
                    media_id = con.execute("""INSERT INTO media_files(path,kind,duration,vcodec,transcode_status)
                        VALUES(?,?,?,?,?)""", (source, "episode", 10, "hevc", "pending")).lastrowid
                    con.commit()
                finally:
                    con.close()
                with patch.object(transcode, "_ffprobe_duration", return_value=10), \
                     patch.object(transcode.phone_transcode, "encode", return_value=(False, "phone hot")):
                    self.assertTrue(transcode.run_one(media_id, source, 10, use_phone=True))
                con = database.connect()
                try:
                    row = con.execute("SELECT * FROM media_files WHERE id=?", (media_id,)).fetchone()
                finally:
                    con.close()
                self.assertTrue(os.path.isfile(source))
                self.assertEqual((row["transcode_status"], row["transcode_worker"]),
                                 ("pending", "phone"))

    def test_finished_episode_stays_in_library_after_rescan(self):
        with tempfile.TemporaryDirectory() as root:
            tv_dir = os.path.join(root, "TVShows")
            os.makedirs(tv_dir)
            source = os.path.join(tv_dir, "Example S01E01.mkv")
            with open(source, "wb") as output:
                output.write(b"original hevc")
            with patch.object(config, "MEDIA_ROOT", root), \
                 patch.object(config, "TV_DIR", tv_dir), \
                 patch.object(config, "DB_PATH", os.path.join(root, "test.db")), \
                 patch.object(config, "DATA_DIR", root), \
                 patch.object(transcode, "TRANSCODE_DIR", os.path.join(root, "converted")), \
                 patch.object(scanner, "ssd_storage_healthy", return_value=True):
                database.init_db()
                con = database.connect()
                try:
                    media_id = con.execute("""INSERT INTO media_files(path,kind,duration,vcodec,transcode_status)
                        VALUES(?,?,?,?,?)""", (source, "episode", 10, "hevc", "pending")).lastrowid
                    con.commit()
                finally:
                    con.close()

                def encode(_, destination, __, ___):
                    with open(destination, "wb") as output:
                        output.write(b"verified h264")
                    return True, ""

                details = {"duration": 10, "acodec": "aac", "width": 1280,
                           "height": 720, "bitrate": 1000, "size": 13, "mtime": 1}
                with patch.object(transcode, "_ffprobe_duration", return_value=10), \
                     patch.object(transcode, "_encode", side_effect=encode), \
                     patch.object(transcode, "_validated_output", return_value=details):
                    transcode.run_one(media_id, source, 10)
                scanner.full_scan(light=True)
                con = database.connect()
                try:
                    row = con.execute("SELECT path,vcodec,transcode_status FROM media_files WHERE id=?",
                                      (media_id,)).fetchone()
                    self.assertIsNone(transcode.next_pending(con))
                finally:
                    con.close()
                self.assertEqual((row["path"], row["vcodec"], row["transcode_status"]),
                                 (source, "h264", "done"))

    def test_non_mkv_source_gets_scannable_mkv_sibling(self):
        with tempfile.TemporaryDirectory() as root:
            source = os.path.join(root, "Example S01E01.mp4")
            with open(source, "wb") as output:
                output.write(b"original hevc")
            with patch.object(config, "DB_PATH", os.path.join(root, "test.db")), \
                 patch.object(config, "DATA_DIR", root), \
                 patch.object(transcode, "TRANSCODE_DIR", os.path.join(root, "converted")):
                database.init_db()
                con = database.connect()
                try:
                    media_id = con.execute("""INSERT INTO media_files(path,kind,duration,vcodec,transcode_status)
                        VALUES(?,?,?,?,?)""", (source, "episode", 10, "hevc", "pending")).lastrowid
                    con.commit()
                finally:
                    con.close()

                def encode(_, destination, __, ___):
                    with open(destination, "wb") as output:
                        output.write(b"verified h264")
                    return True, ""

                details = {"duration": 10, "acodec": "aac", "width": 1280,
                           "height": 720, "bitrate": 1000, "size": 13, "mtime": 1}
                with patch.object(transcode, "_ffprobe_duration", return_value=10), \
                     patch.object(transcode, "_encode", side_effect=encode), \
                     patch.object(transcode, "_validated_output", return_value=details):
                    transcode.run_one(media_id, source, 10)
                con = database.connect()
                try:
                    row = con.execute("SELECT path,transcode_status FROM media_files WHERE id=?",
                                      (media_id,)).fetchone()
                finally:
                    con.close()
                self.assertEqual(row["path"], os.path.join(root, "Example S01E01.h264.mkv"))
                self.assertEqual(row["transcode_status"], "done")
                self.assertFalse(os.path.exists(source))
                with open(row["path"], "rb") as replacement:
                    self.assertEqual(replacement.read(), b"verified h264")

    def test_saved_phone_failure_precedes_new_jobs(self):
        with tempfile.TemporaryDirectory() as root, \
             patch.object(config, "DB_PATH", os.path.join(root, "test.db")):
            database.init_db()
            con = database.connect()
            try:
                saved = con.execute("""INSERT INTO media_files(path,kind,transcode_status,transcode_worker,transcode_error)
                    VALUES(?,?,?,?,?)""", ("saved.mkv", "episode", "pending", "phone", "phone: interrupted by SSD")).lastrowid
                con.execute("""INSERT INTO media_files(path,kind,transcode_status)
                    VALUES(?,?,?)""", ("new.mkv", "episode", "pending"))
                con.commit()
                self.assertEqual(transcode.next_pending(con)["id"], saved)
            finally:
                con.close()

    def test_phone_and_pi_cannot_encode_at_once(self):
        with tempfile.TemporaryDirectory() as root, patch.object(config, "DATA_DIR", root):
            with open(os.path.join(root, "transcode.lock"), "a+") as lock_file:
                fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
                with patch.object(transcode, "_run_one_unlocked") as encode:
                    self.assertFalse(transcode.run_one(1, "sample.mkv", 10))
                    encode.assert_not_called()


if __name__ == "__main__":
    unittest.main()
