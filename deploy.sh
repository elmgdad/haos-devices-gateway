#!/bin/sh
# Build the panel, push main, and rebuild the Device Hub app installed from this repository.
# Commit your changes (including device_hub/www) before running this.
set -eu

HOST="${HA_SSH_HOST:-root@192.168.100.26}"
SLUG="${DEVICE_HUB_SLUG:-3bc3f5fd_device_hub}"
cd "$(dirname "$0")"

(cd device_hub/frontend && npm run build --silent)

git push origin main

ssh "$HOST" "ha store reload >/dev/null && ha apps rebuild ${SLUG} && ha apps info ${SLUG} | grep -E '^(state|version):'"
