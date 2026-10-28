"""Home Assistant automations: listed and switched from Device Hub, and simple ones built here.

Device Hub writes real Home Assistant automations, so they keep running when
Device Hub is stopped. It edits and deletes only the ones it made (their ids
start with devicehub_). Every other automation can be turned on or off and run.

A Device Hub automation is a small spec, kept in /data/automations.json so it
can be edited again:

    {"name": "Evening", "trigger": {"type": "sun", "event": "sunset", "offset_minutes": -15},
     "days": ["fri", "sat"],
     "actions": [{"device_id": "sonoff:1002ba764f", "code": "switch.kitchen_plug", "value": true},
                 {"delay_seconds": 30},
                 {"area_id": "living_room", "action": "off"}]}
"""

import asyncio
import re
import secrets

from . import store
from .share import WRITABLE, _bare, ha_service

PREFIX = "devicehub_"
STORE = "automations"
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
ON_OFF = {"Boolean", "switch", "light", "fan", "input_boolean", "binary_sensor", "lock", "cover", "siren", "humidifier"}


class Automations:
    def __init__(self, hub):
        self.hub = hub
        self.specs = (store.load(STORE) or {}).get("specs") or {}

    async def list(self):
        states = await self.hub.ha.rest("GET", "/states") or []
        items = []
        for state in states:
            entity_id = state.get("entity_id", "")
            if not entity_id.startswith("automation."):
                continue
            attributes = state.get("attributes") or {}
            config_id = attributes.get("id")
            ours = bool(config_id) and str(config_id).startswith(PREFIX)
            items.append({
                "entity_id": entity_id,
                "id": config_id,
                "name": attributes.get("friendly_name") or entity_id,
                "enabled": state.get("state") == "on",
                "last_triggered": attributes.get("last_triggered"),
                "mode": attributes.get("mode"),
                "running": attributes.get("current") or 0,
                "ours": ours,
                "spec": self.specs.get(config_id) if ours else None,
            })
        items.sort(key=lambda item: (not item["ours"], item["name"].lower()))
        return items

    async def find(self, ref):
        """An automation by entity id (automation.kitchen) or by its config id."""
        for item in await self.list():
            if ref in (item["entity_id"], item["id"]):
                return item
        raise RuntimeError("There is no automation with that id.")

    async def run(self, ref, via="panel"):
        item = await self.find(ref)
        await self.hub.ha.call_service("automation", "trigger", {"entity_id": item["entity_id"], "skip_condition": True})
        self.hub.activity.log("automation", f"Ran {item['name']}", via=via)
        return {"ok": True}

    async def set_enabled(self, ref, enabled, via="panel"):
        item = await self.find(ref)
        await self.hub.ha.call_service("automation", "turn_on" if enabled else "turn_off", {"entity_id": item["entity_id"]})
        self.hub.activity.log("automation", f"Turned {item['name']} {'on' if enabled else 'off'}", via=via)
        return {"ok": True}

    async def save(self, spec, ref=None, via="panel"):
        """Create an automation, or replace one Device Hub made. Returns it once Home Assistant lists it."""
        if ref:
            existing = await self.find(ref)
            if not existing["ours"]:
                raise RuntimeError("Device Hub only edits automations it made. Change this one in Home Assistant.")
            config_id = existing["id"]
        else:
            config_id = PREFIX + secrets.token_hex(5)
        config, clean = await self.build(spec)
        await self.hub.ha.rest("POST", f"/config/automation/config/{config_id}", config)
        self.specs[config_id] = clean
        self._persist()
        self.hub.activity.log("automation", f"{'Updated' if ref else 'Created'} automation {clean['name']}", via=via)
        for _ in range(20):
            try:
                return await self.find(config_id)
            except RuntimeError:
                await asyncio.sleep(0.5)
        return {"id": config_id, "name": clean["name"], "ours": True, "spec": clean}

    async def delete(self, ref, via="panel"):
        item = await self.find(ref)
        if not item["ours"]:
            raise RuntimeError("Device Hub only deletes automations it made. Delete this one in Home Assistant.")
        await self.hub.ha.rest("DELETE", f"/config/automation/config/{item['id']}")
        self.specs.pop(item["id"], None)
        self._persist()
        self.hub.activity.log("automation", f"Deleted automation {item['name']}", via=via)
        return {"ok": True}

    async def targets(self):
        """Devices and the controls an automation can watch or change. Only controls with a Home Assistant entity qualify."""
        shown = []
        for item in await self.hub.share.catalog():
            controls = []
            for control in item["controls"]:
                if not control.get("entity_id"):
                    continue
                controls.append({
                    **{k: control[k] for k in ("code", "name", "type", "entity_id", "state", "options", "min", "max", "unit", "dimmable")
                       if k in control},
                    "action": _can_act(self.hub, item, control),
                    "trigger": True,
                })
            if controls:
                shown.append({"id": item["id"], "name": item["name"], "area": item.get("area"), "controls": controls})
        return shown

    async def build(self, spec):
        """The Home Assistant config for a spec, and the spec as saved. Raises with a plain reason when it is incomplete."""
        spec = spec or {}
        name = str(spec.get("name") or "").strip()
        if not name:
            raise RuntimeError("Name the automation.")
        devices = {item["id"]: item for item in await self.hub.share.catalog()}
        trigger = spec.get("trigger") or {}
        triggers = [self._trigger(trigger, devices)]
        days = [d for d in spec.get("days") or [] if d in DAYS]
        conditions = [{"condition": "time", "weekday": days}] if days and len(days) < 7 else []
        actions = [self._action(action, devices) for action in spec.get("actions") or []]
        if not any("delay" not in a for a in actions):
            raise RuntimeError("Add at least one thing for the automation to do.")
        config = {
            "alias": name,
            "description": "Made in Device Hub. Edit it there so the two stay the same.",
            "mode": "single",
            "triggers": triggers,
            "conditions": conditions,
            "actions": actions,
        }
        clean = {"name": name, "trigger": trigger, "days": days, "actions": spec.get("actions")}
        return config, clean

    def _trigger(self, trigger, devices):
        kind = trigger.get("type")
        if kind == "time":
            at = str(trigger.get("at") or "")
            if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", at):
                raise RuntimeError("Give the time as HH:MM, such as 18:30.")
            return {"trigger": "time", "at": at + ":00"}
        if kind == "sun":
            event = trigger.get("event")
            if event not in ("sunrise", "sunset"):
                raise RuntimeError("Choose sunrise or sunset.")
            minutes = int(trigger.get("offset_minutes") or 0)
            sign = "-" if minutes < 0 else ""
            minutes = abs(minutes)
            return {"trigger": "sun", "event": event, "offset": f"{sign}{minutes // 60:02d}:{minutes % 60:02d}:00"}
        if kind == "device":
            control = _control(devices, trigger.get("device_id"), trigger.get("code"))
            entity_id = control.get("entity_id")
            if not entity_id:
                raise RuntimeError(f"{control['name']} is not in Home Assistant, so it cannot start an automation.")
            stay = {"for": {"seconds": int(trigger["for_seconds"])}} if trigger.get("for_seconds") else {}
            if trigger.get("above") is not None or trigger.get("below") is not None:
                limits = {k: float(trigger[k]) for k in ("above", "below") if trigger.get(k) is not None}
                return {"trigger": "numeric_state", "entity_id": entity_id, **limits, **stay}
            if "to" not in trigger:
                raise RuntimeError("Say what the control should change to, or a number to go above or below.")
            return {"trigger": "state", "entity_id": entity_id, "to": _state_text(control, trigger["to"]), **stay}
        raise RuntimeError("Choose a trigger: a time, sunrise or sunset, or a device changing.")

    def _action(self, action, devices):
        if action.get("delay_seconds") is not None:
            seconds = int(action["delay_seconds"])
            if seconds <= 0:
                raise RuntimeError("A wait must be at least one second.")
            return {"delay": {"seconds": seconds}}
        if action.get("area_id"):
            if action.get("action") not in ("on", "off"):
                raise RuntimeError("An area can be turned on or off.")
            return {"action": f"homeassistant.turn_{action['action']}", "target": {"area_id": action["area_id"]}}
        device = devices.get(action.get("device_id"))
        control = _control(devices, action.get("device_id"), action.get("code"))
        if not _can_act(self.hub, device, control):
            raise RuntimeError(f"{control['name']} only works through Device Hub, so a Home Assistant automation cannot change it.")
        domain, service, data = ha_service(control, action.get("value"), action)
        entity_id = data.pop("entity_id")
        return {"action": f"{domain}.{service}", "target": {"entity_id": entity_id}, **({"data": data} if data else {})}

    def _persist(self):
        store.save(STORE, {"specs": self.specs})


def _control(devices, device_id, code):
    device = devices.get(device_id)
    if not device:
        raise RuntimeError("Pick a device Device Hub knows.")
    control = next((c for c in device["controls"] if c["code"] == code or c.get("entity_id") == code), None)
    if not control:
        raise RuntimeError(f"{device['name']} has no control {code}.")
    return control


def _can_act(hub, device, control):
    """IR air conditioner settings go through Device Hub itself. Everything else with a writable entity can be in HA."""
    entity_id = control.get("entity_id") or ""
    if not control.get("writable") or entity_id.split(".", 1)[0] not in WRITABLE:
        return False
    return not (device and hub.ir.knows(_bare(device["id"])) and control.get("type") != "button")


def _state_text(control, value):
    if control.get("type") == "lock" and value in (True, False, "on", "off"):
        return "unlocked" if value in (True, "on") else "locked"
    if control.get("type") in ON_OFF or isinstance(value, bool):
        if value in (True, "on", "true", "open"):
            return "on" if control.get("type") != "cover" else "open"
        if value in (False, "off", "false", "closed"):
            return "off" if control.get("type") != "cover" else "closed"
    return str(value)
