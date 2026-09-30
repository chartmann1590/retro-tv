"""Keep disposable browser data bounded and move SD media to the mounted SSD."""
import fcntl
import hashlib
import logging
import os
import shutil
import stat
import tempfile
import time
from contextlib import contextmanager

import config
import database

log = logging.getLogger("retro-tv.storage")
MIGRATION_FOLDER = "SD Card"
SSD_FREE_FLOOR = 4 * 1024 ** 3
UPLOAD_SETTLE_SECONDS = 120
SIDECAR_EXTS = {".srt", ".ass", ".ssa", ".sub", ".idx", ".vtt", ".nfo"}


@contextmanager
def browser_workspace():
    """Put both Chromium's profile and caches in a disposable RAM directory."""
    with tempfile.TemporaryDirectory(prefix="retro-tv-render-") as directory:
        env = dict(os.environ, XDG_CACHE_HOME=os.path.join(directory, "cache"))
        options = ["--user-data-dir=" + os.path.join(directory, "profile"),
                   "--disk-cache-dir=" + os.path.join(directory, "cache")]
        yield options, env


def cleanup_browser_cache(max_age=3600):
    """Remove abandoned headless profiles; leave the desktop browser alone."""
    cache = os.path.join(os.path.expanduser("~"), ".cache", "chromium-headless")
    removed = 0
    locations = [(cache, "scoped_dir"), (tempfile.gettempdir(), "retro-tv-render-")]
    for directory, prefix in locations:
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    if (entry.name.startswith(prefix) and
                            entry.is_dir(follow_symlinks=False) and
                            entry.stat(follow_symlinks=False).st_uid == os.getuid() and
                            time.time() - entry.stat(follow_symlinks=False).st_mtime > max_age):
                        try:
                            shutil.rmtree(entry.path)
                            removed += 1
                        except OSError:
                            log.exception("Could not remove stale browser cache %s", entry.path)
        except FileNotFoundError:
            pass
    if removed:
        log.info("Removed %s abandoned headless browser caches", removed)
    return removed


def media_destination(path):
    """Use a separate namespace so existing SSD media is never overwritten."""
    path = os.path.abspath(path)
    for category in (config.TV_DIR, config.MOVIES_DIR, config.COMMERCIALS_DIR):
        category = os.path.abspath(category)
        if path == category or path.startswith(category + os.sep):
            relative = os.path.relpath(path, category)
            if relative.split(os.sep)[0] != "SSD":
                return os.path.normpath(os.path.join(category, "SSD", MIGRATION_FOLDER, relative))
    return None


def metadata_relative_path(path):
    relative = os.path.relpath(path, config.MEDIA_ROOT)
    parts = relative.split(os.sep)
    if len(parts) > 3 and parts[1:3] == ["SSD", MIGRATION_FOLDER]:
        return os.path.join(parts[0], *parts[3:])
    return relative


def _signature(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def _checksum(path):
    digest = hashlib.sha256()
    with open(path, "rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.digest()


def _copy_verified(source, temporary, before):
    digest = hashlib.sha256()
    with open(source, "rb") as reader, open(temporary, "wb") as writer:
        for block in iter(lambda: reader.read(1024 * 1024), b""):
            writer.write(block)
            digest.update(block)
        writer.flush()
        os.fsync(writer.fileno())
    shutil.copystat(source, temporary)
    copied = os.stat(temporary)
    if (copied.st_size != before.st_size or _checksum(temporary) != digest.digest()
            or _signature(os.stat(source)) != _signature(before)):
        raise OSError("Media changed during copying or SSD checksum did not match")


def _move_file(source, destination):
    """Called with the scanner and converter locks held; preserve all media IDs."""
    before = os.stat(source, follow_symlinks=False)
    if not stat.S_ISREG(before.st_mode):
        return 0
    if time.time() - max(before.st_mtime, before.st_ctime) < UPLOAD_SETTLE_SECONDS:
        return 0
    parent = os.path.dirname(destination)
    os.makedirs(parent, exist_ok=True)
    # Never follow a pre-existing symlink out of the SSD.
    if os.stat(parent).st_dev == before.st_dev or os.path.islink(destination):
        raise OSError("Migration destination is not on a separate filesystem")
    con = database.connect()
    temporary = destination + ".sd-migration-partial"
    installed = False
    relocated = False
    original = None
    try:
        original = con.execute("SELECT id FROM media_files WHERE path=?", (source,)).fetchone()
        target = con.execute("SELECT id FROM media_files WHERE path=?", (destination,)).fetchone()
        if os.path.exists(destination):
            # A crash after installation can leave both copies. Adopt the SSD
            # copy only if all bytes match and it does not belong to another ID.
            if ((target and original and target["id"] != original["id"]) or
                    os.path.getsize(destination) != before.st_size or
                    _checksum(source) != _checksum(destination)):
                raise OSError("Destination already contains different media; preserving source")
        else:
            if shutil.disk_usage(parent).free < before.st_size + SSD_FREE_FLOOR:
                raise OSError("SSD has insufficient space for a verified copy")
            _copy_verified(source, temporary, before)
            # Preserve ownership as well as permissions. This Pi's media is
            # owned by the service user; skip files we cannot preserve.
            copied = os.stat(temporary)
            if (copied.st_uid, copied.st_gid) != (before.st_uid, before.st_gid):
                os.chown(temporary, before.st_uid, before.st_gid)
            os.link(temporary, destination)  # Atomic install; never overwrite.
            installed = True
            os.unlink(temporary)
            with _directory_fd(parent) as directory:
                os.fsync(directory)
        if _signature(os.stat(source)) != _signature(before):
            raise OSError("Media changed before migration completed")
        if original:
            con.execute("""UPDATE media_files SET path=?,
                transcode_path=CASE WHEN transcode_path=? THEN ? ELSE transcode_path END
                WHERE id=?""", (destination, source, destination, original["id"]))
        con.execute("UPDATE channel_sources SET source_value=? WHERE source_type='movie' AND source_value=?",
                    (destination, source))
        con.commit()
        relocated = True
        os.unlink(source)
        log.info("Moved SD media to SSD: %s (%s bytes)", source, before.st_size)
        return before.st_size
    except Exception:
        con.rollback()
        # If deletion fails, restore the original indexed path before scanning.
        if relocated and os.path.exists(source):
            if original:
                con.execute("UPDATE media_files SET path=?, transcode_path=CASE WHEN transcode_path=? THEN ? ELSE transcode_path END WHERE id=?",
                            (source, destination, source, original["id"]))
            con.execute("UPDATE channel_sources SET source_value=? WHERE source_type='movie' AND source_value=?",
                        (source, destination))
            con.commit()
        if installed and os.path.exists(source):
            os.unlink(destination)
        raise
    finally:
        con.close()
        if os.path.exists(temporary):
            os.unlink(temporary)


@contextmanager
def _directory_fd(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        yield descriptor
    finally:
        os.close(descriptor)


def migrate_sd_media():
    """Run before each scan, using its lock and the converter's process lock."""
    import scanner
    if not scanner.ssd_storage_healthy():
        return {"files": 0, "bytes": 0, "deferred": True}
    result = {"files": 0, "bytes": 0, "deferred": False}
    os.makedirs(config.DATA_DIR, exist_ok=True)
    with open(os.path.join(config.DATA_DIR, "transcode.lock"), "a+") as converter:
        try:
            fcntl.flock(converter, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            result["deferred"] = True
            return result
        try:
            for category in (config.TV_DIR, config.MOVIES_DIR, config.COMMERCIALS_DIR):
                if not os.path.abspath(category).startswith(os.path.abspath(config.MEDIA_ROOT) + os.sep):
                    continue
                if not os.path.isdir(category) or not os.path.isdir(os.path.join(category, "SSD")):
                    continue
                source_device = os.stat(category).st_dev
                if source_device == os.stat(os.path.join(category, "SSD")).st_dev:
                    continue
                for root, directories, files in os.walk(category):
                    directories[:] = [name for name in directories if name != "SSD"
                                      and not os.path.islink(os.path.join(root, name))
                                      and os.stat(os.path.join(root, name)).st_dev == source_device]
                    for name in files:
                        if os.path.splitext(name)[1].lower() not in config.VIDEO_EXTS | SIDECAR_EXTS:
                            continue
                        path = os.path.join(root, name)
                        try:
                            moved = _move_file(path, media_destination(path))
                            if moved:
                                result["files"] += 1
                                result["bytes"] += moved
                        except OSError as error:
                            log.warning("SD media migration deferred for %s: %s", path, error)
            if result["files"]:
                log.info("SD migration complete: %s files, %.2f GiB reclaimed",
                         result["files"], result["bytes"] / 1024 ** 3)
        finally:
            fcntl.flock(converter, fcntl.LOCK_UN)
    return result


def maintain():
    cleanup_browser_cache()
    return migrate_sd_media()
