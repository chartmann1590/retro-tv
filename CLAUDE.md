# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A Raspberry Pi cable-TV simulator for a personal media library. A Flask backend builds continuous multi-day channel "schedules" from files in `/srv/media`, and a persistent HDMI mpv player joins whatever is scheduled "live" on the current channel. A phone acts as the remote; browsers can also watch via `/watch`. There is exactly one receiver process per installation — the scheduler, player, and native TV guide overlay all share in-process state (`playback._current`, mpv IPC socket, schedule lock), so running two instances against the same data directory will corrupt playback state.

## Commands

```bash
python3 -m venv venv
venv/bin/pip install -r requirements.txt
venv/bin/python app.py                                   # start on :5000 (scanning, scheduling, playback restore run at startup)

venv/bin/python -m unittest discover -s tests -v         # run tests
venv/bin/python -m unittest tests.test_receiver.ReceiverTests.test_name  # run one test

venv/bin/python -m py_compile *.py scripts/*.py tests/*.py   # syntax check (no formatter/linter configured)
bash -n scripts/install.sh scripts/kiosk.sh                   # shell syntax check
```

No frontend build step — templates and static JS/CSS are served as-is.

Service management on the deployed Pi:
```bash
systemctl --user status retro-tv.service
systemctl --user restart retro-tv.service
journalctl --user -u retro-tv.service -n 50
```

`bash scripts/install.sh` deploys to `/opt/retro-tv` and installs system packages; it runs as root, so inspect before running.

### Testing notes
- Tests isolate SQLite by monkeypatching `config.DB_PATH`/`config.DATA_DIR` to a tempdir before calling `database.init_db()` (see `tests/test_receiver.py`), and mock HDMI/mpv calls. Never point tests at the real `data/retro_tv.db`.
- Importing `app.py` starts background threads (scanning, scheduling, playback restore) as a side effect — isolate runtime data before experimenting in a REPL.
- There is no coverage requirement; manually verify affected pages (`/watch`, `/guide`, `/remote`, `/admin`) when tests don't cover a change, and check playback in both browser and HDMI modes.

## Architecture

**Everything is "live."** There's no per-user playback state — `schedule_entries` rows have absolute `start_ts`/`end_ts`, and both the HDMI player and any browser tab compute `offset = now - entry.start_ts` to join mid-program. Changing channel or reloading `/watch` always seeks to the current live position, never to zero.

Module responsibilities, in the order data flows:

- **`scanner.py`** — walks `/srv/media/{TVShows,Movies,Commercials}`, parses show/season/episode from paths and filenames (regex heuristics, not a metadata source), runs `ffprobe`, and upserts `media_files`/`episodes`/`movies`/`commercials`. `full_scan(light=True)` runs every 15 min in the background loop; `light=False` (triggered from `/api/scan`) does a fuller pass and also calls `metadata.enrich_episodes()`.
- **`metadata.py`** — best-effort TVMaze lookups (rate-limited to ~1 req/0.6s) to backfill episode descriptions/artwork. Purely additive and offline-safe; failures just leave filename-derived data in place.
- **`scheduler.py`** — the core scheduling engine. `generate_day(channel, day)` deterministically builds one day of a channel's lineup (seeded RNG per channel+day so regeneration without new sources reproduces the same order), interleaving episodes/movies per channel config and inserting commercial breaks via `pick_commercials`, which maintains a persistent fair-rotation cursor (`commercial_rotation` table) across days — long commercial compilations are sliced into ~90s segments and resumed later rather than replayed from the start. **`generate_day` never rewrites already-aired entries** — it resumes from `MAX(end_ts)` for the day. All scheduler mutations that touch generation go through `@serialized` (a module-level `RLock`) since concurrent generation for the same channel/day would double-book. `ensure_schedules()` keeps `SCHEDULE_DAYS_AHEAD` (3) days generated per channel and runs hourly plus at startup.
- **`streaming.py` / `playback.py`** — two independent consumers of `scheduler.now_playing()`/`resolve_live()`, one for HDMI (mpv, persistent process reused across channel changes to avoid rebuilding audio/video devices) and one for browsers (direct file serving with HTTP Range, on-demand remux to MP4 via ffmpeg when codecs are browser-incompatible, or HLS for cases direct play can't handle). Browser streaming only copies video and transcodes audio when needed — it never transcodes video, so some source formats require HDMI instead of browser viewing. `playback.monitor_loop` polls every second to detect schedule changes or a dead/idle mpv and re-tunes; retries back off exponentially on repeated failure.
- **`tvguide.py`** — renders the on-TV guide as an mpv OSD overlay (ASS subtitle markup sent over the same IPC socket) rather than a second video layer, so the guide works without a browser on the HDMI output.
- **`remote.py`** — stores keycode-to-action mappings; physical 2.4GHz remotes present as USB HID keyboards, so actual key capture happens in the browser (`static/js/remote.js`) or via evdev, not here.
- **`app.py`** — Flask routes and the single background thread (`bg_loop`) that owns startup (`ensure_schedules` → `playback.restore_last`) and periodic maintenance (light rescan, hourly schedule extension + HLS cleanup, daily DB backup). Runs under Waitress (`serve(...)`), not Flask's dev server.
- **`database.py`** — raw `sqlite3` (WAL mode, no ORM). Every function opens and closes its own connection; there's no shared connection pool or session object.

### Data model
`channels` (+ `channel_sources`, which define what a channel draws from: a show, a season, all TV, all movies, a movie file/folder, or commercials) → `schedule_entries` (kind: `episode`/`movie`/`commercial_break`/`slate`, keyed by `channel_number` + `day` in `America/New_York`) reference `media_files` (physical files, one row per file, referenced by `episodes`/`movies`/`commercials` sub-tables). A channel with no sources defaults to using everything. A channel with no eligible media at all gets a single `slate` ("Off Air") entry for the day instead of an empty schedule.

### Frontend
Server-rendered Jinja templates (`templates/`) with vanilla JS per page (`static/js/{admin,guide,player,remote,tv,common}.js`, two-space indent, camelCase) — no bundler or framework. Pages poll JSON endpoints (`/api/now/<ch>`, `/api/guide`, `/api/hdmi`) rather than using websockets.

## Hardware/runtime constraints worth knowing
- Target is a Raspberry Pi 4 (4GB) driving 1080p HDMI; mpv uses hardware decoding and bounded demuxer buffers deliberately — don't remove `--profile=fast`/buffer limits without reason.
- Audio device selection (`playback.audio_device()`) auto-discovers the HDMI sink by name via `pw-dump`/ALSA ELD, falling back to `auto`; `RETRO_TV_AUDIO_DEVICE` overrides it. No numeric PipeWire node ID is ever persisted (those aren't stable across reboots).
- `MEDIA_ROOT` (`/srv/media`) ownership must be preserved — don't change permissions there as a side effect of a fix.
- Config (paths, timezone, port, media roots) lives in `config.py`; there are no environment-specific config files beyond the `RETRO_TV_AUDIO_DEVICE` env var override.
