"""ZHA through the Home Assistant WebSocket API."""

from .ha import HAError, HomeAssistant


class Zha:
    def __init__(self, ha: HomeAssistant):
        self.ha = ha

    async def overview(self, registries=None):
        entries = await self.ha.call("config_entries/get", domain="zha")
        loaded = any(e.get("state") == "loaded" for e in entries)
        devices = []
        error = None
        if loaded:
            try:
                raw = await self.ha.call("zha/devices", timeout=60)
            except HAError as exc:
                raw, error = [], str(exc)
            areas = (registries or {}).get("areas") or {}
            for device in raw or []:
                devices.append({
                    "ieee": device.get("ieee"),
                    "nwk": device.get("nwk"),
                    "name": device.get("user_given_name") or device.get("name") or device.get("ieee"),
                    "manufacturer": device.get("manufacturer") or "",
                    "model": device.get("model") or "",
                    "quirk": device.get("quirk_class") if device.get("quirk_applied") else "",
                    "device_type": device.get("device_type") or "",
                    "power_source": device.get("power_source") or "",
                    "lqi": device.get("lqi"),
                    "rssi": device.get("rssi"),
                    "last_seen": device.get("last_seen"),
                    "available": device.get("available"),
                    "ha_device_id": device.get("device_reg_id"),
                    "area_id": device.get("area_id"),
                    "area": areas.get(device.get("area_id")),
                })
            devices.sort(key=lambda d: (d["device_type"] != "Coordinator", (d["name"] or "").lower()))
        return {
            "entries": [
                {
                    "entry_id": e["entry_id"],
                    "title": e.get("title") or "ZHA",
                    "state": e.get("state"),
                    "reason": e.get("reason"),
                    "disabled_by": e.get("disabled_by"),
                }
                for e in entries
            ],
            "loaded": loaded,
            "devices": devices,
            "error": error,
        }

    async def permit(self, duration=60, ieee=None):
        payload = {"duration": max(0, min(int(duration), 254))}
        if ieee:
            payload["ieee"] = ieee
        await self.ha.subscribe("zha/devices/permit", lambda _event: None, **payload)

    async def reconfigure(self, ieee):
        await self.ha.subscribe("zha/devices/reconfigure", lambda _event: None, ieee=ieee)

    async def remove(self, ieee):
        await self.ha.call_service("zha", "remove", {"ieee": ieee}, timeout=60)
