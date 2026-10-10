#!/bin/sh
# Build the panel, push main, and rebuild the Device Hub app installed from this repository.
# Commit your changes (including device_hub/www) before running this.
set -eu

HOST="${HA_SSH_HOST:-root@192.168.100.26}"
SLUG="${DEVICE_HUB_SLUG:-3bc3f5fd_device_hub}"
cd "$(dirname "$0")"

(cd device_hub/frontend && npm run build --silent)

git push origin main

ssh "$HOST" "ha store reload >/dev/null && ha apps update ${SLUG} && ha apps info ${SLUG} | grep -E '^(state|version):'"

# The update card keeps the previous installed version for up to 15 minutes.
# Update then fails with "No update available" even though this install finished.
if [ -f .env.dev ]; then
  .venv/bin/python - << 'PY'
import asyncio, sys
from pathlib import Path
import aiohttp
env = {}
for line in Path(".env.dev").read_text().splitlines():
    if "=" in line and not line.startswith("#"):
        key, value = line.split("=", 1)
        env[key.strip()] = value.strip()
base = env["HA_URL"].rstrip("/")
entity = "update.device_hub_update"

async def main():
    async with aiohttp.ClientSession() as session:
        headers = {"Authorization": f"Bearer {env['HA_TOKEN']}"}
        async with session.post(
            base + "/api/services/homeassistant/update_entity",
            headers=headers,
            json={"entity_id": entity},
        ) as response:
            response.raise_for_status()
        for _ in range(8):
            await asyncio.sleep(2)
            async with session.get(base + "/api/states/" + entity, headers=headers) as response:
                state = await response.json()
            installed = state["attributes"].get("installed_version")
            latest = state["attributes"].get("latest_version")
            if state["state"] == "off" and installed == latest:
                print(f"update card {installed}")
                return
        print(f"update card still {state['state']} {installed} -> {latest}", file=sys.stderr)

asyncio.run(main())
PY
fi
