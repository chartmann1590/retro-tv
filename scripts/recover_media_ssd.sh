#!/usr/bin/env bash
# Repair a stale media-SSD mount after the USB disk reconnects.
set -euo pipefail

SSD_UUID=08b782c4-b69e-4f5d-b3b5-a9d8941b93a7
SSD_MOUNT=/mnt/media-ssd
APP_DIR=${RETRO_TV_APP_DIR:-/home/charles/retro-tv}
LOCK=/run/retro-tv-ssd-recover.lock

healthy() {
  local options
  mountpoint -q "$SSD_MOUNT" || return 1
  options=$(findmnt -rn -o OPTIONS --target "$SSD_MOUNT" 2>/dev/null) || return 1
  [[ ",$options," != *,shutdown,* ]] || return 1
  [[ -d "$SSD_MOUNT/transcoded" ]] || return 1
  ls -U "$SSD_MOUNT/transcoded" >/dev/null 2>&1
}

if [[ ${1:-} == --check ]]; then
  healthy
  exit $?
fi
if (( EUID != 0 )); then
  echo "Run this recovery script as root." >&2
  exit 2
fi
exec 9>"$LOCK"
flock -n 9 || exit 0
healthy && exit 0

device=$(readlink -f "/dev/disk/by-uuid/$SSD_UUID" 2>/dev/null || true)
if [[ ! -b "$device" ]]; then
  echo "Media SSD is not connected; waiting for its USB device." >&2
  exit 0
fi
if journalctl -k --since '2 minutes ago' --no-pager 2>/dev/null | grep -Eq 'over-current|USB disconnect'; then
  echo "USB connection is still unstable; waiting two quiet minutes before filesystem recovery." >&2
  exit 0
fi

app_owner=$(stat -c %U "$APP_DIR")
app_uid=$(id -u "$app_owner")
user_service() {
  runuser -u "$app_owner" -- env XDG_RUNTIME_DIR="/run/user/$app_uid" \
    DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$app_uid/bus" systemctl --user "$@"
}
app_was_active=0
if user_service is-active --quiet retro-tv.service; then
  app_was_active=1
  user_service stop retro-tv.service
fi
restart_app() {
  if (( app_was_active )); then
    user_service start retro-tv.service || true
  fi
}
trap restart_app EXIT

for mountpoint in /srv/media/Commercials/SSD /srv/media/Movies/SSD /srv/media/TVShows/SSD "$SSD_MOUNT"; do
  if mountpoint -q "$mountpoint"; then
    umount "$mountpoint"
  fi
done

set +e
e2fsck -p "$device"
fsck_result=$?
set -e
if (( fsck_result != 0 && fsck_result != 1 )); then
  echo "SSD check needs manual attention (e2fsck exit $fsck_result); leaving it unmounted." >&2
  exit 1
fi

mount "$SSD_MOUNT"
for mountpoint in /srv/media/TVShows/SSD /srv/media/Movies/SSD /srv/media/Commercials/SSD; do
  mount "$mountpoint"
done
if ! healthy; then
  echo "SSD remounted but did not pass the directory check." >&2
  exit 1
fi
echo "Media SSD recovered; Retro TV can resume its saved phone job."
