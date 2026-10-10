#!/bin/sh
# Build the panel, copy Device Hub to /local_apps on the HA host, and rebuild the app.
set -eu

HOST="${HA_SSH_HOST:-root@192.168.100.26}"
cd "$(dirname "$0")"

(cd device_hub/frontend && npm run build --silent)

COPYFILE_DISABLE=1 tar \
  --exclude='device_hub/frontend' \
  --exclude='device_hub/.data' \
  --exclude='__pycache__' \
  --exclude='.DS_Store' \
  -czf - device_hub | ssh "$HOST" 'rm -rf /local_apps/device_hub && tar -C /local_apps -xzf -'

ssh "$HOST" 'ha store reload >/dev/null && ha apps rebuild local_device_hub && ha apps info local_device_hub | grep -E "^(state|version):"'
