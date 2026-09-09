#!/bin/bash
# Retro TV kiosk helper: run fullscreen TV UI on labwc/Wayland.
# User-level: safe to run at login via labwc autostart.
set -e
for i in $(seq 1 60); do
  curl -sf http://127.0.0.1:5000/api/channels >/dev/null 2>&1 && break
  sleep 2
done
# --disable-accelerated-video-decode: this kiosk page never plays video, but
# chromium's GPU process still opens the Pi's one shared V4L2 H.264 decoder
# (/dev/video10) on startup for capability probing and holds it -- leaving mpv
# unable to acquire it, silently falling back to CPU decode for everything
# (observed: ~90% CPU on a 720p clip that should hwdec, vs the device being
# free and mpv succeeding once chromium is prevented from touching it).
exec chromium --kiosk --noerrdialogs --disable-infobars \
  --autoplay-policy=no-user-gesture-required --start-fullscreen \
  --disable-accelerated-video-decode \
  http://127.0.0.1:5000/
