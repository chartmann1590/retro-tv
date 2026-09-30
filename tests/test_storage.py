"""Storage safety: venv/bin/python -m unittest tests.test_storage -v."""
import fcntl
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import config
import database
import livecontent
import livesports
import scanner
import scheduler
import storage
import transcode


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.source_dir = tempfile.TemporaryDirectory()
        self.ssd_dir = tempfile.TemporaryDirectory(dir="/dev/shm")
        self.addCleanup(self.source_dir.cleanup)
        self.addCleanup(self.ssd_dir.cleanup)
        self.root = Path(self.source_dir.name)
        settings = {"MEDIA_ROOT": str(self.root), "DATA_DIR": str(self.root / "data"),
                    "DB_PATH": str(self.root / "data" / "test.db"),
                    "TV_DIR": str(self.root / "TVShows"),
                    "MOVIES_DIR": str(self.root / "Movies"),
                    "COMMERCIALS_DIR": str(self.root / "Commercials")}
        for name, value in settings.items():
            item = patch.object(config, name, value)
            item.start()
            self.addCleanup(item.stop)
        for category in (config.TV_DIR, config.MOVIES_DIR, config.COMMERCIALS_DIR):
            Path(category).mkdir()
            target = Path(self.ssd_dir.name) / Path(category).name
            target.mkdir()
            (Path(category) / "SSD").symlink_to(target, target_is_directory=True)
        for item in [patch.object(scanner, "ssd_storage_healthy", return_value=True),
                     patch.object(storage, "UPLOAD_SETTLE_SECONDS", 0),
                     patch.object(storage, "SSD_FREE_FLOOR", 0)]:
            item.start()
            self.addCleanup(item.stop)
        database.init_db()
        self.source = Path(config.MOVIES_DIR) / "Example (2000)" / "film.mp4"
        self.source.parent.mkdir()
        self.source.write_bytes(b"original media" * 200)
        con = database.connect()
        try:
            self.media_id = con.execute("INSERT INTO media_files(path,kind,size) VALUES(?,'movie',?)",
                                        (str(self.source), self.source.stat().st_size)).lastrowid
            con.execute("INSERT INTO movies(media_id,title) VALUES(?,'Example')", (self.media_id,))
            con.execute("INSERT INTO channels(number,name) VALUES(2,'Movies')")
            con.execute("INSERT INTO channel_sources(channel_number,source_type,source_value) VALUES(2,'movie',?)",
                        (str(self.source),))
            con.execute("""INSERT INTO schedule_entries(channel_number,start_ts,end_ts,kind,media_id,day)
                VALUES(2,0,100,'movie',?,'test')""", (self.media_id,))
            con.commit()
        finally:
            con.close()
        self.destination = Path(storage.media_destination(str(self.source)))

    def indexed_path(self):
        con = database.connect()
        try:
            return con.execute("SELECT path FROM media_files WHERE id=?", (self.media_id,)).fetchone()["path"]
        finally:
            con.close()

    def test_verified_migration_preserves_ids_metadata_and_schedule(self):
        original = self.source.read_bytes()
        original_stat = self.source.stat()
        result = storage.migrate_sd_media()
        self.assertEqual(result["files"], 1)
        self.assertEqual(result["bytes"], len(original))
        self.assertFalse(self.source.exists())
        self.assertEqual(self.destination.read_bytes(), original)
        self.assertEqual(self.destination.stat().st_mtime_ns, original_stat.st_mtime_ns)
        self.assertEqual(self.destination.stat().st_uid, original_stat.st_uid)
        self.assertEqual(self.indexed_path(), str(self.destination))
        con = database.connect()
        try:
            self.assertEqual(con.execute("SELECT media_id FROM movies").fetchone()[0], self.media_id)
            self.assertEqual(con.execute("SELECT media_id FROM schedule_entries").fetchone()[0], self.media_id)
            self.assertEqual(con.execute("SELECT source_value FROM channel_sources").fetchone()[0], str(self.destination))
        finally:
            con.close()
        self.assertEqual(storage.migrate_sd_media()["files"], 0)

    def test_missing_ssd_or_busy_converter_preserves_source(self):
        with patch.object(scanner, "ssd_storage_healthy", return_value=False):
            self.assertTrue(storage.migrate_sd_media()["deferred"])
        with open(Path(config.DATA_DIR) / "transcode.lock", "a+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertTrue(storage.migrate_sd_media()["deferred"])
        self.assertTrue(self.source.exists())
        self.assertFalse(self.destination.exists())

    def test_subtitles_move_with_the_library(self):
        subtitle = self.source.with_suffix('.en.srt')
        subtitle.write_text('1\n00:00:00,000 --> 00:00:01,000\nHello\n')
        contents = subtitle.read_bytes()
        self.assertEqual(storage.migrate_sd_media()['files'], 2)
        self.assertFalse(subtitle.exists())
        self.assertEqual(Path(storage.media_destination(str(subtitle))).read_bytes(), contents)

    def test_category_outside_configured_media_root_is_not_touched(self):
        with patch.object(config, 'MEDIA_ROOT', str(self.root / 'isolated')):
            self.assertEqual(storage.migrate_sd_media()['files'], 0)
        self.assertTrue(self.source.exists())

    def test_source_deletion_failure_restores_library_mapping(self):
        unlink = storage.os.unlink

        def deny_source(path, *args, **kwargs):
            if str(path) == str(self.source):
                raise PermissionError('Cannot remove source')
            return unlink(path, *args, **kwargs)

        with patch.object(storage.os, 'unlink', side_effect=deny_source):
            self.assertEqual(storage.migrate_sd_media()['files'], 0)
        self.assertTrue(self.source.exists())
        self.assertFalse(self.destination.exists())
        self.assertEqual(self.indexed_path(), str(self.source))

    def test_new_upload_and_insufficient_space_preserve_source(self):
        with patch.object(storage, "UPLOAD_SETTLE_SECONDS", 3600):
            self.assertEqual(storage.migrate_sd_media()["files"], 0)
        with patch.object(storage, "SSD_FREE_FLOOR", 10 ** 18):
            self.assertEqual(storage.migrate_sd_media()["files"], 0)
        self.assertTrue(self.source.exists())

    def test_conflicting_destination_is_never_overwritten(self):
        self.destination.parent.mkdir(parents=True)
        self.destination.write_bytes(b"another movie")
        self.assertEqual(storage.migrate_sd_media()["files"], 0)
        self.assertTrue(self.source.exists())
        self.assertEqual(self.destination.read_bytes(), b"another movie")
        self.assertEqual(self.indexed_path(), str(self.source))

    def test_checksum_failure_preserves_source_and_index(self):
        with patch.object(storage, "_checksum", return_value=b"bad checksum"):
            self.assertEqual(storage.migrate_sd_media()["files"], 0)
        self.assertTrue(self.source.exists())
        self.assertFalse(self.destination.exists())
        self.assertFalse(Path(str(self.destination) + ".sd-migration-partial").exists())
        self.assertEqual(self.indexed_path(), str(self.source))

    def test_source_changed_during_copy_is_not_deleted(self):
        copy = storage._copy_verified

        def change_source(source, temporary, before):
            copy(source, temporary, before)
            Path(source).write_bytes(b"new upload")

        with patch.object(storage, "_copy_verified", side_effect=change_source):
            self.assertEqual(storage.migrate_sd_media()["files"], 0)
        self.assertEqual(self.source.read_bytes(), b"new upload")
        self.assertFalse(self.destination.exists())
        self.assertEqual(self.indexed_path(), str(self.source))

    def test_interrupted_move_finishes_without_changing_media_id(self):
        self.destination.parent.mkdir(parents=True)
        shutil.copy2(self.source, self.destination)
        con = database.connect()
        try:
            con.execute("UPDATE media_files SET path=? WHERE id=?", (str(self.destination), self.media_id))
            con.commit()
        finally:
            con.close()
        self.assertEqual(storage.migrate_sd_media()["files"], 1)
        self.assertFalse(self.source.exists())
        self.assertEqual(self.indexed_path(), str(self.destination))

    def test_metadata_layout_does_not_change_for_moved_root_movie(self):
        original = str(Path(config.MOVIES_DIR) / "Example (2000).mp4")
        moved = storage.media_destination(original)
        relative = storage.metadata_relative_path(moved)
        self.assertEqual(scanner.parse_movie(relative), {"title": "Example", "year": 2000})

    def test_converter_refreshes_relocated_source_after_locking(self):
        storage.migrate_sd_media()
        with patch.object(transcode, "_run_one_unlocked") as run:
            self.assertTrue(transcode.run_one(self.media_id, str(self.source), 10))
        self.assertEqual(run.call_args.args[1], str(self.destination))

    def test_movie_folder_channel_keeps_migrated_movies(self):
        con = database.connect()
        try:
            con.execute("UPDATE channel_sources SET source_type='movie_folder', source_value=?",
                        (str(self.source.parent),))
            con.commit()
        finally:
            con.close()
        storage.migrate_sd_media()
        _, movies, _ = scheduler.channel_pool(2)
        self.assertEqual([movie['media_id'] for movie in movies], [self.media_id])

    def test_each_scan_runs_storage_maintenance_before_indexing(self):
        calls = []
        with patch.object(storage, 'maintain', side_effect=lambda: calls.append('maintain')), \
             patch.object(scanner, '_full_scan', side_effect=lambda light: calls.append('scan')):
            scanner.full_scan(light=True)
            scanner.full_scan(light=True)
        self.assertEqual(calls, ['maintain', 'scan', 'maintain', 'scan'])


class BrowserCacheTests(unittest.TestCase):
    def test_cleanup_removes_workspace_left_by_a_crashed_renderer(self):
        with tempfile.TemporaryDirectory() as root, \
             patch.object(storage.tempfile, 'gettempdir', return_value=root):
            stale = Path(root) / 'retro-tv-render-abandoned'
            stale.mkdir()
            os.utime(stale, (0, 0))
            storage.cleanup_browser_cache()
            self.assertFalse(stale.exists())

    def test_cleanup_only_removes_old_headless_profiles(self):
        with tempfile.TemporaryDirectory() as home, patch.object(storage.os.path, "expanduser", return_value=home):
            root = Path(home) / ".cache" / "chromium-headless"
            stale, recent, other = (root / name for name in ["scoped_dirold", "scoped_dirnew", "keep"])
            for path in (stale, recent, other):
                path.mkdir(parents=True)
            os.utime(stale, (0, 0))
            self.assertEqual(storage.cleanup_browser_cache(), 1)
            self.assertFalse(stale.exists())
            self.assertTrue(recent.exists())
            self.assertTrue(other.exists())

    def test_both_renderers_clean_profile_and_cache_after_success_or_timeout(self):
        with tempfile.TemporaryDirectory() as directory:
            for module, renderer in [(livecontent, livecontent._render_card),
                                     (livesports, livesports._render_card_png)]:
                for fails in (False, True):
                    roots = []

                    def run(command, **kwargs):
                        profile = next(x.split("=", 1)[1] for x in command if x.startswith("--user-data-dir="))
                        cache = kwargs["env"]["XDG_CACHE_HOME"]
                        Path(profile).mkdir()
                        Path(cache).mkdir()
                        (Path(cache) / "leftover").write_text("cache")
                        roots.append(str(Path(profile).parent))
                        if fails:
                            raise subprocess.TimeoutExpired(command, 1)

                    with self.subTest(module=module.__name__, fails=fails), \
                         patch.object(module.subprocess, "run", side_effect=run):
                        output = str(Path(directory) / "card.png")
                        if fails:
                            with self.assertRaises(subprocess.TimeoutExpired):
                                renderer("card", output)
                        else:
                            renderer("card", output)
                        self.assertFalse(Path(output + ".tmp.html").exists())
                        self.assertTrue(roots)
                        self.assertFalse(Path(roots[0]).exists())


if __name__ == "__main__":
    unittest.main()
