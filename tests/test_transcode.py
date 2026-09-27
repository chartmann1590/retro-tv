"""Safe source replacement: venv/bin/python -m unittest tests.test_transcode -v."""
import os
import fcntl
import tempfile
import unittest
from unittest.mock import patch

import config
import database
import phone_transcode
import transcode


class TranscodeReplacementTests(unittest.TestCase):
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
                with patch.object(transcode, "_encode", side_effect=encode), \
                     patch.object(transcode, "_validated_output", return_value=details):
                    transcode.run_one(media_id, source, 10)

                con = database.connect()
                try:
                    row = con.execute("SELECT * FROM media_files WHERE id=?", (media_id,)).fetchone()
                finally:
                    con.close()
                self.assertFalse(os.path.exists(source))
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
                with patch.object(transcode.phone_transcode, "encode", return_value=(False, "phone hot")):
                    self.assertTrue(transcode.run_one(media_id, source, 10, use_phone=True))
                con = database.connect()
                try:
                    row = con.execute("SELECT * FROM media_files WHERE id=?", (media_id,)).fetchone()
                finally:
                    con.close()
                self.assertTrue(os.path.isfile(source))
                self.assertEqual((row["transcode_status"], row["transcode_worker"]),
                                 ("pending", "phone"))

    def test_phone_and_pi_cannot_encode_at_once(self):
        with tempfile.TemporaryDirectory() as root, patch.object(config, "DATA_DIR", root):
            with open(os.path.join(root, "transcode.lock"), "a+") as lock_file:
                fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
                with patch.object(transcode, "_run_one_unlocked") as encode:
                    self.assertFalse(transcode.run_one(1, "sample.mkv", 10))
                    encode.assert_not_called()


if __name__ == "__main__":
    unittest.main()
