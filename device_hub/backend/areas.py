"""Rooms: the Home Assistant areas, with the devices Device Hub controls in each.

Turning an area on or off sends the same command a person would send each
device: every primary on/off control of every device in it.
"""

from .share import PANEL

ON_OFF = {"Boolean", "switch", "light", "fan", "input_boolean"}
ON_STATES = {True, "on", "open"}


class Areas:
    def __init__(self, hub):
        self.hub = hub

    async def overview(self, token=PANEL):
        devices = await self.hub.share.devices_for(token)
        registry = await self.hub.ha.call("config/area_registry/list") or []
        areas = [_area(a["area_id"], a["name"], a.get("icon"), devices) for a in registry]
        loose = [item for item in devices if not item.get("area_id")]
        return {"areas": areas, "unassigned": [_summary(item) for item in loose]}

    async def one(self, area_id, token=PANEL):
        for area in (await self.overview(token))["areas"]:
            if area["id"] == area_id:
                return area
        raise RuntimeError("There is no area with that id.")

    async def command(self, area_id, action, token=PANEL):
        """Turn every switch in the area on or off. One device failing does not stop the rest."""
        if action not in ("on", "off"):
            raise RuntimeError("Choose on or off.")
        area = await self.one(area_id, token)
        devices = {item["id"]: item for item in await self.hub.share.devices_for(token)}
        results = []
        for summary in area["devices"]:
            item = devices.get(summary["id"])
            switches = _switches(item)
            if not switches:
                continue
            try:
                await self.hub.share.command(token, item["id"], [{"code": c["code"], "value": action == "on"} for c in switches])
                results.append({"device_id": item["id"], "name": item["name"], "ok": True})
            except Exception as exc:  # noqa: BLE001
                results.append({"device_id": item["id"], "name": item["name"], "ok": False, "error": str(exc)})
        if not results:
            raise RuntimeError(f"Nothing in {area['name']} can be turned {action}.")
        failed = sum(1 for r in results if not r["ok"])
        self.hub.activity.log(
            "command",
            f"{area['name']}: everything turned {action}" + (f", {failed} failed" if failed else ""),
            ok=not failed,
            via="panel" if token is PANEL else f"API key {token['name']}",
        )
        return {"area": area["name"], "results": results}

    async def create(self, name):
        name = (name or "").strip()
        if not name:
            raise RuntimeError("Name the area.")
        created = await self.hub.ha.call("config/area_registry/create", name=name)
        self.hub.activity.log("change", f"Added area {created['name']}")
        return {"id": created["area_id"], "name": created["name"]}

    async def rename(self, area_id, name):
        name = (name or "").strip()
        if not name:
            raise RuntimeError("Name the area.")
        before = await self._name(area_id)
        updated = await self.hub.ha.call("config/area_registry/update", area_id=area_id, name=name)
        self.hub.share.invalidate()
        self.hub.activity.log("change", f"Renamed area {before} to {updated['name']}")
        return {"id": updated["area_id"], "name": updated["name"]}

    async def delete(self, area_id):
        """Home Assistant keeps the devices. They are left without an area."""
        before = await self._name(area_id)
        await self.hub.ha.call("config/area_registry/delete", area_id=area_id)
        self.hub.share.invalidate()
        self.hub.activity.log("change", f"Deleted area {before}")
        return {"ok": True}

    async def _name(self, area_id):
        registry = await self.hub.ha.call("config/area_registry/list") or []
        found = next((a["name"] for a in registry if a["area_id"] == area_id), None)
        if not found:
            raise RuntimeError("There is no area with that id.")
        return found


def _switches(item):
    return [c for c in (item or {}).get("controls", []) if c["writable"] and c["primary"] and c["type"] in ON_OFF]


def _summary(item):
    switches = _switches(item)
    return {
        "id": item["id"],
        "name": item["name"],
        "online": item.get("online"),
        "source": item.get("source"),
        "switchable": bool(switches),
        "on": any(c.get("state") in ON_STATES for c in switches),
    }


def _area(area_id, name, icon, devices):
    inside = [_summary(item) for item in devices if item.get("area_id") == area_id]
    return {
        "id": area_id,
        "name": name,
        "icon": icon,
        "devices": inside,
        "online": sum(1 for d in inside if d["online"] is True),
        "offline": sum(1 for d in inside if d["online"] is False),
        "on": sum(1 for d in inside if d["on"]),
        "switchable": sum(1 for d in inside if d["switchable"]),
    }
