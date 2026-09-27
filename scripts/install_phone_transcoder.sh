#!/usr/bin/env bash
set -euo pipefail

# Pinned Android arm64 FFmpeg CLI build. The phone worker runs it over ADB;
# it does not require installing or changing the Retro TV Android app.
source_url="https://raw.githubusercontent.com/hzw1199/Android-FFmpeg-Prebuilt/90231cc0105aef4f76926b911535f5eb73511b86/ffmpeg-9.0/bin/ffmpeg"
expected_sha256="9085507b0dc32643b4d6d084a7e7d3469ef17907a7ba15c22d3997ed09c932aa"
binary_file="$(mktemp)"
trap 'rm -f "$binary_file"' EXIT

adb_cmd=(adb)
if [[ -n "${RETRO_TV_ADB_SERIAL:-}" ]]; then
  adb_cmd+=(-s "$RETRO_TV_ADB_SERIAL")
fi

curl --fail --location --retry 2 --output "$binary_file" "$source_url"
printf '%s  %s\n' "$expected_sha256" "$binary_file" | sha256sum --check --status
"${adb_cmd[@]}" push "$binary_file" /data/local/tmp/retro-tv-ffmpeg
"${adb_cmd[@]}" shell chmod 755 /data/local/tmp/retro-tv-ffmpeg
"${adb_cmd[@]}" shell /data/local/tmp/retro-tv-ffmpeg -hide_banner -version | head -1
echo "Phone transcoder installed. Retro TV will use it when connected and cool enough."
