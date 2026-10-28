"""Sonoff devices from the SonoffLAN integration (domain `sonoff`) in Home Assistant.

Device Hub reads them from Home Assistant and switches them through Home
Assistant, so SonoffLAN keeps its own local connection to each device.
"""

import time

from .ha import HAError

DOMAIN = "sonoff"
SWITCH_DOMAINS = ("switch", "light", "fan", "cover")


class Sonoff:
    def __init__(self, ha):
        self.ha = ha
        self._diagnostics = {}
        self._diagnostics_at = 0.0

    async def overview(self, registries=None):
        entries = await self.ha.call("config_entries/get", domain=DOMAIN)
        if not entries:
            return {"installed": False, "entries": [], "devices": [], "cloud_error": None}
        registries = registries or await self.ha.registries()
        entities = await self.ha.call("config/entity_registry/list")
        states = {item["entity_id"]: item for item in await self.ha.rest("GET", "/states") or []}
        entry_ids = {entry["entry_id"] for entry in entries}
        info = await self._read_diagnostics([entry["entry_id"] for entry in entries if entry["state"] == "loaded"])
        devices = []
        for device in registries["devices"]:
            if not set(device.get("config_entries") or []) & entry_ids:
                continue
            device_id = next((value for domain, value, *_ in device.get("identifiers") or [] if domain == DOMAIN), None)
            if not device_id:
                continue
            found = info["devices"].get(device_id) or {}
            params = found.get("params") or {}
            host = (found.get("host") or "").split(":")[0] or None
            switches = []
            for entity in entities:
                if entity.get("device_id") != device["id"] or entity.get("disabled_by"):
                    continue
                domain = entity["entity_id"].split(".", 1)[0]
                if domain not in SWITCH_DOMAINS or entity.get("entity_category"):
                    continue
                state = states.get(entity["entity_id"]) or {}
                switches.append({
                    "entity_id": entity["entity_id"],
                    "name": _channel_name(entity, state),
                    "domain": domain,
                    "state": state.get("state"),
                })
            switches.sort(key=lambda item: item["entity_id"])
            available = [item for item in switches if item["state"] not in (None, "unavailable")]
            online = found.get("online") if "online" in found else (bool(available) if switches else None)
            devices.append({
                "id": device_id,
                "ha_device_id": device["id"],
                "name": device.get("name_by_user") or device.get("name") or device_id,
                "model": " ".join(part for part in (device.get("manufacturer"), device.get("model")) if part) or "Sonoff",
                "firmware": device.get("sw_version") or params.get("fwVersion"),
                "mac": next((value for kind, value in device.get("connections") or [] if kind == "mac"), None),
                "area_id": device.get("area_id"),
                "area": registries["areas"].get(device.get("area_id")),
                "online": online,
                "local": bool(found.get("local")),
                "ip": host,
                "rssi": params.get("rssi"),
                "switches": switches,
            })
        devices.sort(key=lambda item: item["name"].lower())
        return {
            "installed": True,
            "entries": [{"entry_id": entry["entry_id"], "title": entry["title"], "state": entry["state"]} for entry in entries],
            "devices": devices,
            "cloud_error": info["cloud_error"],
        }

    async def set_switch(self, entity_id, on):
        overview = await self.overview()
        known = {item["entity_id"]: item for device in overview["devices"] for item in device["switches"]}
        switch = known.get(entity_id)
        if not switch:
            raise RuntimeError("That is not a Sonoff switch in Home Assistant.")
        service = "turn_on" if on else "turn_off"
        if switch["domain"] == "cover":
            service = "open_cover" if on else "close_cover"
        await self.ha.call_service(switch["domain"], service, {"entity_id": entity_id})
        return await self.overview()

    async def _read_diagnostics(self, entry_ids):
        """SonoffLAN's own view: online, local or cloud, and the device's LAN address."""
        if time.monotonic() - self._diagnostics_at < 15:
            return self._diagnostics
        result = {"devices": {}, "cloud_error": None}
        for entry_id in entry_ids:
            try:
                data = ((await self.ha.rest("GET", f"/diagnostics/config_entry/{entry_id}", timeout=20)) or {}).get("data") or {}
            except HAError:
                continue
            result["devices"].update(data.get("devices") or {})
            for error in data.get("errors") or []:
                if "cloud" in str(error.get("name", "")).lower():
                    result["cloud_error"] = "SonoffLAN cannot reach the eWeLink cloud. Devices keep working on your network."
        self._diagnostics, self._diagnostics_at = result, time.monotonic()
        return result


def _channel_name(entity, state):
    name = entity.get("name") or entity.get("original_name")
    if name:
        return name
    suffix = entity["entity_id"].rsplit("_", 1)[-1]
    if suffix.isdigit():
        return f"Switch {suffix}"
    friendly = (state.get("attributes") or {}).get("friendly_name")
    return friendly or entity["entity_id"]
