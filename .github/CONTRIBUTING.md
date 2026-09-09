# Contributing to Retro TV

Thanks for considering a contribution. This is a small personal-media-server
project — issues and pull requests are welcome, but please keep changes
focused and in line with the project's scope (a single-receiver cable-TV
simulator, not a general media server).

## Before you start

For anything beyond a small fix, please open an issue first to discuss the
approach — especially for changes touching the scheduler, playback, or the
shared receiver state (see "Architecture" in `CLAUDE.md`). There is exactly
one receiver process per installation; changes that assume multiple
concurrent instances won't be accepted.

## Development setup

```bash
git clone https://github.com/chartmann1590/retro-tv.git
cd retro-tv
python3 -m venv venv
venv/bin/pip install -r requirements.txt
venv/bin/python app.py
```

You'll need `mpv` and `ffmpeg`/`ffprobe` installed, and a media directory
laid out as described in the README. Point `MEDIA_ROOT` in `config.py` at it.

## Before submitting a change

Run the checks the project uses (there is no separate linter/formatter):

```bash
venv/bin/python -m unittest discover -s tests -v          # tests
venv/bin/python -m py_compile *.py scripts/*.py tests/*.py # syntax check
bash -n scripts/install.sh scripts/kiosk.sh                # shell syntax check
```

Tests isolate SQLite in a temp directory and mock HDMI/mpv calls — never
point tests at a real media directory or `data/retro_tv.db`.

There's no coverage requirement, but if your change touches `/watch`,
`/guide`, `/remote`, or `/admin` and isn't covered by the test suite, verify
it manually in a browser (and on HDMI, if applicable) before opening a PR.

## Pull requests

- Keep PRs focused on one change; avoid unrelated formatting or refactors.
- Describe *why* the change is needed, not just what changed.
- Note which pages/flows you manually verified, if any.
- Match the existing code style (two-space indent and camelCase in
  `static/js/*.js`; standard PEP 8-ish style in Python — no formatter is
  configured, so mirror the surrounding file).

## Reporting bugs

Open a GitHub issue with:
- What you expected vs. what happened
- Steps to reproduce, including relevant channel/schedule state if playback
  or scheduling related
- Relevant log output from `journalctl --user -u retro-tv.service` or
  `logs/retro-tv.log`

## Code of conduct

Be respectful and constructive. This is a hobby project maintained in spare
time — response times may vary.
