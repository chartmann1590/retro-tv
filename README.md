# RETRO-TV

A Raspberry Pi cable receiver for your own media collection. HDMI plays the scheduled program at its current live position; changing channels joins whatever is airing. The phone remote controls the television over the local network.

## Use the receiver

- **Remote:** open `http://10.0.0.30:5000/remote` on a phone on the same network. The receiver homepage also shows its current network address.
- **Guide:** press **GUIDE** on the remote to display the cable guide on the TV and phone. Select programs, use the arrow controls, and press **TUNE TV**. **Close TV guide** returns to the picture.
- **Watch:** `/watch` plays on the device viewing the page. Guide and Watch fill the viewport. **FULLSCREEN** removes browser chrome when supported; browsers may require a tap. Watch controls disappear after three seconds and return when touched or moved over.
- **Live TV:** rejoins the live position after pausing. **LAST** returns to the previous TV channel.
- **Setup:** `/admin` manages channels, media scanning, metadata, and schedules.

## Run and maintain

```bash
python3 -m venv venv
venv/bin/pip install -r requirements.txt
venv/bin/python app.py
```

The application runs one Waitress process with six request threads. Run only one instance: the scheduler, player, and native guide share process state. mpv, FFmpeg/ffprobe, and a desktop audio session are required for this installation.

The installed user service starts automatically:

```bash
systemctl --user status retro-tv.service
systemctl --user restart retro-tv.service
journalctl --user -u retro-tv.service -n 50
```

`config.py` sets media paths, timezone, and port. Media remains in `/srv/media/{TVShows,Movies,Commercials}`; preserve its ownership. Runtime databases are in `data/`; logs and browser remuxes are in `logs/` and `hls_cache/`.

## Hardware and audio

This installation is a Raspberry Pi 4 with 4 GB RAM and a 1080p HDMI television. mpv uses hardware decoding when available, a fast rendering profile, bounded buffers, and a persistent player for channel changes. The receiver and remote do not decode preview video. Browser streaming copies video and only converts incompatible audio; it does not attempt expensive video transcoding. Some formats therefore need HDMI playback instead of browser viewing.

The player discovers the HDMI sink, sends stereo PCM at 48 kHz, and preserves mute/volume across channels. To override discovery, set `RETRO_TV_AUDIO_DEVICE` in the service environment using a name from `bin/mpv --audio-device=help`. No numeric PipeWire node ID is stored.

Check temperatures with `vcgencmd measure_temp` and `vcgencmd get_throttled`. Avoid leaving an additional Watch tab playing on the Pi while HDMI is playing. Cooling matters for sustained playback; this machine measured about 83°C during the initial inspection.

## Validation

```bash
venv/bin/python -m unittest discover -s tests -v
venv/bin/python -m py_compile *.py scripts/*.py tests/*.py
bash -n scripts/install.sh scripts/kiosk.sh
```

Tests isolate SQLite data and mock HDMI commands. They cover complete schedules, stable program IDs, live timing, persistent player reuse, HDMI selection, HTTP byte ranges, page rendering, and the native guide overlay. Browser checks were also performed at desktop, phone, and landscape sizes.

Source files and a consistent SQLite backup from before these changes are stored outside the application at `/home/charles/retro-tv-backups/20260908-163415/`.
