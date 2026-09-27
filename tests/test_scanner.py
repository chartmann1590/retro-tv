"""Media mount safety: venv/bin/python -m unittest tests.test_scanner -v."""
import os
import tempfile
import unittest
from unittest.mock import patch

import config
import database
import scanner


class ScannerMountTests(unittest.TestCase):
    def test_missing_ssd_mount_keeps_indexed_media(self):
        with tempfile.TemporaryDirectory() as root:
            patches = [
                patch.object(config, "DB_PATH", os.path.join(root, "test.db")),
                patch.object(config, "MEDIA_ROOT", root),
                patch.object(config, "TV_DIR", os.path.join(root, "TVShows")),
                patch.object(config, "MOVIES_DIR", os.path.join(root, "Movies")),
                patch.object(config, "COMMERCIALS_DIR", os.path.join(root, "Commercials")),
                patch("scanner.os.path.ismount", return_value=False),
            ]
            for item in patches:
                item.start()
                self.addCleanup(item.stop)
            database.init_db()
            ssd_path = os.path.join(root, "TVShows", "SSD", "episode.mkv")
            local_path = os.path.join(root, "TVShows", "removed.mkv")
            con = database.connect()
            try:
                for path in (ssd_path, local_path):
                    con.execute("INSERT INTO media_files(path,kind) VALUES(?,?)", (path, "episode"))
                con.commit()
            finally:
                con.close()
            scanner.full_scan(light=True)
            con = database.connect()
            try:
                paths = {row["path"] for row in con.execute("SELECT path FROM media_files")}
            finally:
                con.close()
            self.assertEqual(paths, {ssd_path})


if __name__ == "__main__":
    unittest.main()
