# Repository Guidelines

## Project Structure & Module Organization

`app.py` defines Flask pages, API routes, and startup jobs. Backend modules live at the repository root: `database.py` manages SQLite, `scanner.py` and `metadata.py` index media, `scheduler.py` builds schedules, and `playback.py`, `streaming.py`, and `remote.py` handle playback and controls. Keep changes in the module responsible for that behavior.

Jinja templates are in `templates/`; browser JavaScript and CSS are in `static/js/` and `static/css/`. `scripts/` contains installation, channel setup, kiosk, and systemd files. `data/`, `logs/`, and `hls_cache/` hold runtime state. `bin/mpv` and `mpv-local/` support local player execution; `venv/` contains installed Python dependencies.

## Build, Test, and Development Commands

Run from the repository root:

- `python3 -m venv venv` — create a Python environment.
- `venv/bin/pip install -r requirements.txt` — install Flask and requests.
- `venv/bin/python app.py` — start the application on port 5000, including scanning, scheduling, and playback restoration. Playback requires mpv and FFmpeg tools.
- `venv/bin/python -m py_compile *.py scripts/*.py` — check Python syntax without importing application modules.
- `bash -n scripts/*.sh` — check shell syntax.

There is no separate frontend build. `bash scripts/install.sh` performs deployment and service configuration; inspect it before running because root execution installs packages and copies the application to `/opt/retro-tv`.

## Coding Style & Naming Conventions

Use four-space Python indentation, `snake_case` functions and variables, and uppercase configuration constants. Follow existing JavaScript's two-space indentation and `camelCase` function names. Match nearby template and CSS formatting. No formatter or linter configuration is present; avoid unrelated reformatting.

## Testing Guidelines

No automated test suite or coverage threshold is configured. Run syntax checks and manually verify affected pages, such as `/watch`, `/guide`, `/remote`, and `/admin`. Check playback changes in both browser and HDMI modes when applicable. Importing `app.py` starts background work; isolate runtime data before experiments. For new automated tests, use `tests/test_<module>.py` and document the runner.

## Commit & Pull Request Guidelines

Git metadata is unavailable in this checkout, so existing commit conventions cannot be verified. Use concise imperative subjects, such as `Fix channel switching offset`. Describe behavior changes, validation performed, and related issues in pull requests; include screenshots for UI changes.

## Configuration & Runtime Data

Review paths, timezone, and host settings in `config.py`. Preserve `/srv/media` ownership and existing SQLite data. Exclude databases, logs, caches, and virtual environments from source changes.
