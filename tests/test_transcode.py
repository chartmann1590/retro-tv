"""Safe source replacement: venv/bin/python -m unittest tests.test_transcode -v."""
import os
import fcntl
import tempfile
import unittest
from unittest.mock import patch

import config
import database
import transcode


class TranscodeReplacementTests(unittest.TestCase):
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
