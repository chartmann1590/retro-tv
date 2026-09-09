#!/bin/bash
# Retro TV reproducible installer.
# - As regular user (charles): sets up venv, user systemd services, kiosk autostart.
# - With sudo/root: installs system packages (mpv), migrates to /opt/retro-tv,
#   installs system systemd units, preserves Samba.
set -e
SRC="$(cd "$(dirname "$0")/.." && pwd)"
DEST="/opt/retro-tv"
USER_NAME="${SUDO_USER:-$(whoami)}"
USER_HOME="$(eval echo ~$USER_NAME)"

echo "=== Retro TV install ==="
echo "src=$SRC user=$USER_NAME"

echo "[1/8] Debian packages (needs root; skipped if not root)..."
if [ "$(id -u)" -eq 0 ]; then
  apt-get update
  apt-get install -y mpv ffmpeg python3-venv python3-pip curl chromium evtest
else
  echo "  not root: skipping apt. mpv user-local build at $SRC/mpv-local will be used."
  echo "  to install system mpv later: sudo apt-get install -y mpv ffmpeg"
fi

echo "[2/8] Directories..."
if [ "$(id -u)" -eq 0 ]; then
  mkdir -p "$DEST"
  # copy project (preserve Samba-owned /srv/media untouched)
  rsync -a --exclude venv --exclude mpv-local --exclude __pycache__ --exclude hls_cache "$SRC/" "$DEST/" || cp -a "$SRC/." "$DEST/"
  chown -R "$USER_NAME:$USER_NAME" "$DEST"
  mkdir -p "$DEST/data" "$DEST/logs" "$DEST/hls_cache"
  chmod 755 "$DEST"
  APP="$DEST"
else
  APP="$SRC"
  mkdir -p "$APP/data" "$APP/logs" "$APP/hls_cache"
fi
echo "  app=$APP"

echo "[3/8] Python venv..."
if [ "$(id -u)" -eq 0 ]; then
  sudo -u "$USER_NAME" python3 -m venv "$APP/venv"
  sudo -u "$USER_NAME" "$APP/venv/bin/pip" install -r "$APP/requirements.txt"
else
  python3 -m venv "$APP/venv" || true
  "$APP/venv/bin/pip" install -r "$APP/requirements.txt"
fi

echo "[4/8] Permissions (NEVER touch /srv/media ownership)..."
if [ "$(id -u)" -eq 0 ]; then
  chown -R "$USER_NAME:$USER_NAME" "$APP"
  echo "  /srv/media left untouched:"
  ls -ld /srv/media || true
fi

echo "[5/8] systemd units..."
if [ "$(id -u)" -eq 0 ]; then
  cat > /etc/systemd/system/retro-tv.service <<EOF
[Unit]
Description=Retro TV backend
After=network-online.target smbd.service nmbd.service
Wants=network-online.target
[Service]
Type=simple
User=$USER_NAME
WorkingDirectory=$APP
ExecStart=$APP/venv/bin/python $APP/app.py
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1
[Install]
WantedBy=multi-user.target
EOF
  systemctl daemon-reload
  systemctl enable --now retro-tv.service || true
  echo "  system unit installed: retro-tv.service"
else
  mkdir -p "$USER_HOME/.config/systemd/user"
  sed "s|%h|$USER_HOME|g" "$APP/scripts/retro-tv.service" > "$USER_HOME/.config/systemd/user/retro-tv.service"
  sed "s|%h|$USER_HOME|g" "$APP/scripts/retro-tv-kiosk.service" > "$USER_HOME/.config/systemd/user/retro-tv-kiosk.service" || true
  systemctl --user daemon-reload || true
  systemctl --user enable --now retro-tv.service || true
  echo "  user units installed: retro-tv.service (+ retro-tv-kiosk.service available)"
fi

echo "[6/8] Samba check (must remain working)..."
testparm -s 2>/dev/null | grep -A6 "\[Media\]" || echo "  WARNING: [Media] share not found in testparm"
systemctl is-active smbd nmbd 2>/dev/null || systemctl --user is-active smbd 2>/dev/null || true
smbclient -U "$USER_NAME" -L localhost 2>&1 | head -5 || true

echo "[7/8] Kiosk autostart (labwc)..."
AUTOSTART="$USER_HOME/.config/labwc/autostart"
mkdir -p "$(dirname "$AUTOSTART")"
touch "$AUTOSTART"
if ! grep -q "retro-tv" "$AUTOSTART"; then
  echo "systemctl --user start retro-tv-kiosk.service &  # retro-tv kiosk" >> "$AUTOSTART"
fi
echo "  autostart=$AUTOSTART"

echo "[8/8] Database init + first scan..."
if [ "$(id -u)" -eq 0 ]; then
  sudo -u "$USER_NAME" "$APP/venv/bin/python" -c "import sys; sys.path.insert(0,'$APP'); import database,scanner,scheduler; database.init_db(); print(scanner.full_scan()); print(scheduler.ensure_schedules())" || true
else
  "$APP/venv/bin/python" -c "import database,scanner,scheduler; database.init_db(); print(scanner.full_scan()); print(scheduler.ensure_schedules())" || true
fi

echo "Done. TV: http://10.0.0.30:5000/ Watch: http://10.0.0.30:5000/watch Guide: http://10.0.0.30:5000/guide Admin: http://10.0.0.30:5000/admin"
echo "Status: systemctl --user status retro-tv.service  |  Logs: journalctl --user -u retro-tv.service -f"
