#!/bin/bash
# Retro TV kiosk helper: run fullscreen TV UI on labwc/Wayland.
# User-level: safe to run at login via labwc autostart.
set -e
for i in $(seq 1 60); do
  curl -sf http://127.0.0.1:5000/api/channels >/dev/null 2>&1 && break
  sleep 2
done
exec chromium --kiosk --noerrdialogs --disable-infobars \
  --autoplay-policy=no-user-gesture-required --start-fullscreen \
  http://127.0.0.1:5000/
