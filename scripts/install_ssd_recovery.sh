#!/usr/bin/env bash
# One-time root install of the safe SSD-recovery timer.
set -euo pipefail
if (( EUID != 0 )); then
  echo "Run with sudo: sudo bash scripts/install_ssd_recovery.sh" >&2
  exit 2
fi
src=$(cd "$(dirname "$0")/.." && pwd)
install -o root -g root -m 755 "$src/scripts/recover_media_ssd.sh" /usr/local/sbin/retro-tv-ssd-recover
cat > /etc/systemd/system/retro-tv-ssd-recover.service <<EOF
[Unit]
Description=Recover Retro TV media SSD after a stable USB reconnect
After=local-fs.target

[Service]
Type=oneshot
Environment=RETRO_TV_APP_DIR=$src
ExecStart=/usr/local/sbin/retro-tv-ssd-recover
EOF
cat > /etc/systemd/system/retro-tv-ssd-recover.timer <<'EOF'
[Unit]
Description=Check Retro TV media SSD every two minutes

[Timer]
OnBootSec=2min
OnUnitActiveSec=2min
Persistent=true
Unit=retro-tv-ssd-recover.service

[Install]
WantedBy=timers.target
EOF
systemctl daemon-reload
systemctl enable --now retro-tv-ssd-recover.timer
systemctl start retro-tv-ssd-recover.service
