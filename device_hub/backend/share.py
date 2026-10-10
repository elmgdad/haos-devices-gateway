"""Keys that let another app list and control devices Device Hub already knows.

The panel API stays inside Home Assistant. This one listens on the Pi
(port 8765) and answers only for the devices named on the key.
"""

import html
import re
import secrets
import time

from aiohttp import web

from . import store

STORE = "share"
WRITABLE = {
    "switch", "light", "fan", "cover", "lock", "number", "select", "input_boolean",
    "input_number", "input_select", "button", "climate", "humidifier", "valve", "siren",
}
TRUTHY = {True, 1, "1", "true", "True", "on", "ON", "open", "unlock"}


class Shares:
    def __init__(self, hub):
        self.hub = hub
        saved = store.load(STORE) or {}
        self.tokens = saved.get("tokens") or []
        self._catalog = None
        self._catalog_at = 0.0
        self._index = None
        self._index_at = 0.0

    def public(self, base_url):
        return {
            "name": "Device Hub API",
            "base_url": base_url.rstrip("/") + "/api/v1",
            "auth": "Authorization: Bearer <key>",
            "endpoints": [
                {
                    "method": "GET",
                    "path": "/api/v1/devices",
                    "about": "Devices this key can see, with each control and its current value.",
                },
                {
                    "method": "GET",
                    "path": "/api/v1/devices/{id}",
                    "about": "One device. The id is the value returned in the list, such as tuya:abc or z2m:0x00124b.",
                },
                {
                    "method": "POST",
                    "path": "/api/v1/devices/{id}/commands",
                    "about": "Change one or more controls. code is the control code or the Home Assistant entity id.",
                    "body": {"commands": [{"code": "switch_1", "value": True}]},
                },
            ],
            "values": {
                "switch, light, fan, lock": "true turns it on or unlocks it. false turns it off or locks it.",
                "number": "A number inside the control's range.",
                "select": "One of the options, as text.",
                "cover": "open, close, stop, or a position from 0 to 100.",
                "button": "Any value presses it. IR remote keys such as Power or Volume+ are buttons.",
                "air conditioner": "power true or false, mode cool heat auto fan_only dry, temp 16 to 30, fan auto low medium high.",
                "climate": "A number sets the temperature. Text such as heat or off sets the mode.",
            },
        }

    def describe_html(self, base_url):
        docs = self.public(base_url)
        rows = []
        for item in docs["endpoints"]:
            body = ""
            if item.get("body"):
                body = "<pre>{\"commands\": [{\"code\": \"switch_1\", \"value\": true}]}</pre>"
            rows.append(
                f"<h2><code>{html.escape(item['method'])} {html.escape(item['path'])}</code></h2>"
                f"<p>{html.escape(item['about'])}</p>{body}"
            )
        values = "".join(f"<li><b>{html.escape(k)}</b> {html.escape(v)}</li>" for k, v in docs["values"].items())
        base = html.escape(docs["base_url"])
        return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Device Hub API</title>
<style>
body {{ margin: 0; background: #111; color: #eee; font: 16px/1.5 ui-sans-serif, system-ui, sans-serif; }}
main {{ max-width: 740px; margin: 0 auto; padding: 32px 20px 64px; }}
code, pre {{ font-family: ui-monospace, monospace; }}
pre, code {{ background: #1c1c1c; }}
code {{ padding: 0 4px; }}
pre {{ padding: 12px; overflow: auto; }}
h1 {{ font-size: 28px; letter-spacing: 0.04em; }}
a {{ color: #8ec8ff; }}
</style></head><body><main>
<h1>Device Hub API</h1>
<p>Control devices you shared from the Device Hub panel. Create a key in Device Hub, Settings, Share.</p>
<p>Base URL <code>{base}</code></p>
<p>Send <code>Authorization: Bearer dh_…</code> on every request except this page.</p>
<pre>curl -H "Authorization: Bearer dh_your_key" {base}/devices</pre>
{''.join(rows)}
<h2>Values</h2><ul>{values}</ul>
</main></body></html>"""

    def panel(self, base_url):
        return {
            "base_url": self.public(base_url)["base_url"],
            "docs": self.public(base_url),
            "tokens": [
                {
                    "id": item["id"],
                    "name": item["name"],
                    "token": item["token"],
                    "device_ids": item["device_ids"],
                    "created_at": item["created_at"],
                    "last_used": item.get("last_used"),
                }
                for item in self.tokens
            ],
        }

    def create(self, name, device_ids):
        name = (name or "").strip()
        if not name:
            raise RuntimeError("Name this key so you can tell it apart later.")
        chosen = []
        for device_id in device_ids or []:
            device_id = str(device_id or "").strip()
            if device_id and device_id not in chosen:
                chosen.append(device_id)
        if not chosen:
            raise RuntimeError("Pick at least one device to share.")
        item = {
            "id": secrets.token_hex(4),
            "name": name[:60],
            "token": "dh_" + secrets.token_urlsafe(24),
            "device_ids": chosen,
            "created_at": time.time(),
            "last_used": None,
        }
        self.tokens.append(item)
        self._persist()
        return {"id": item["id"], "name": item["name"], "token": item["token"], "device_ids": item["device_ids"]}

    def revoke(self, token_id):
        before = len(self.tokens)
        self.tokens = [item for item in self.tokens if item["id"] != token_id]
        if len(self.tokens) == before:
            raise RuntimeError("That key is already gone.")
        self._persist()
        return {"ok": True}

    def authenticate(self, header):
        raw = (header or "").strip()
        if raw.lower().startswith("bearer "):
            raw = raw[7:].strip()
        if not raw:
            return None
        for item in self.tokens:
            if secrets.compare_digest(item["token"], raw):
                now = time.time()
                if not item.get("last_used") or now - item["last_used"] > 60:
                    item["last_used"] = now
                    self._persist()
                else:
                    item["last_used"] = now
                return item
        return None

    def require(self, request):
        found = self.authenticate(request.headers.get("Authorization", ""))
        if not found:
            raise web.HTTPUnauthorized(
                text='{"error": "Send Authorization: Bearer and a key from Device Hub."}',
                content_type="application/json",
            )
        return found

    async def devices_for(self, token):
        allowed = set(token["device_ids"])
        return [item for item in await self.catalog() if item["id"] in allowed or _bare(item["id"]) in allowed]

    async def device_for(self, token, device_id):
        found = next((item for item in await self.devices_for(token) if item["id"] == device_id or _bare(item["id"]) == device_id), None)
        if not found:
            raise RuntimeError("This key cannot see that device.")
        return found

    async def command(self, token, device_id, commands):
        device = await self.device_for(token, device_id)
        if not commands:
            raise RuntimeError("Choose a command to send.")
        bare = _bare(device["id"])
        remote = self.hub.ir.knows(bare)
        tuya_id = device["id"].removeprefix("tuya:") if device["id"].startswith("tuya:") else None
        for command in commands:
            code = str((command or {}).get("code") or "").strip()
            control = next((item for item in device["controls"] if item["code"] == code or item.get("entity_id") == code), None)
            if not control or not control["writable"]:
                raise RuntimeError(f"{code or 'That control'} cannot be changed with this key.")
            if remote:
                await self.hub.ir.command(bare, control["code"], command.get("value"))
                continue
            local_code = tuya_id and control["code"] != control.get("entity_id")
            if local_code:
                if not self.hub.local.is_live(tuya_id):
                    raise RuntimeError("The local link to this device is down.")
                await self.hub.local.command(tuya_id, [{"code": control["code"], "value": command.get("value")}])
            else:
                await self._ha_command(control, command.get("value"))
        self.invalidate()
        return await self.device_for(token, device_id)

    async def catalog(self):
        now = time.monotonic()
        if self._catalog is not None and now - self._catalog_at < 8:
            return self._catalog
        rows = (await self.hub.all_devices())["devices"]
        index = await self.ha_index()
        items = []
        for row in rows:
            if row["key"].startswith("lan:"):
                continue
            controls = self._controls(row, index)
            if not controls:
                continue
            items.append({
                "id": row["key"],
                "name": row["name"],
                "area": row.get("area"),
                "area_id": row.get("area_id"),
                "online": row.get("online"),
                "source": row["sources"][0],
                "ha_device_id": row.get("ha_device_id"),
                "controls": controls,
            })
        items.sort(key=lambda item: item["name"].lower())
        self._catalog = items
        self._catalog_at = now
        return items

    def invalidate(self):
        self._catalog_at = 0
        self._index_at = 0

    async def ha_index(self):
        now = time.monotonic()
        if self._index is not None and now - self._index_at < 8:
            return self._index
        entities, states = await _gather_index(self.hub)
        by_device = {}
        by_unique = {}
        for entity in entities or []:
            if entity.get("disabled_by"):
                continue
            by_unique[entity.get("unique_id") or ""] = entity
            by_device.setdefault(entity.get("device_id"), []).append(entity)
        state_of = {item["entity_id"]: item.get("state") for item in states or [] if isinstance(item, dict)}
        attributes = {item["entity_id"]: item.get("attributes") or {} for item in states or [] if isinstance(item, dict)}
        self._index = {"by_device": by_device, "by_unique": by_unique, "states": state_of, "attributes": attributes}
        self._index_at = now
        return self._index

    def _controls(self, row, index):
        bare = _bare(row["key"])
        if row["key"].startswith(("tuya:", "ir:")) and self.hub.ir.knows(bare):
            return self._ir_controls(bare, index)
        controls = []
        seen = set()
        tuya_id = row["key"].removeprefix("tuya:") if row["key"].startswith("tuya:") else None
        profile = self.hub.local.profiles.get(tuya_id) if tuya_id else None
        link = self.hub.local.links.get(tuya_id) if tuya_id else None
        if profile:
            for point in profile["dps"]:
                entity = index["by_unique"].get(f"devicehub_tuya_{tuya_id}_{point['code']}")
                entity_id = entity.get("entity_id") if entity else None
                live = link.dps.get(point["dp"]) if link and link.state == "online" and point["dp"] in link.dps else None
                state = live if live is not None else _from_ha(index["states"].get(entity_id), point["type"])
                controls.append({
                    "code": point["code"],
                    "name": _label(point["code"]),
                    "type": point["type"],
                    "entity_id": entity_id,
                    "state": state,
                    "writable": bool(point["writable"]),
                    "primary": not entity or entity.get("entity_category") not in ("config", "diagnostic"),
                    **_point_limits(point),
                })
                if entity_id:
                    seen.add(entity_id)
        for entity in index["by_device"].get(row.get("ha_device_id")) or []:
            if entity["entity_id"] in seen:
                continue
            domain = entity["entity_id"].split(".", 1)[0]
            if domain in ("update", "event"):
                continue
            controls.append({
                "code": entity["entity_id"],
                "name": entity.get("name") or entity.get("original_name") or entity["entity_id"],
                "type": domain,
                "entity_id": entity["entity_id"],
                "state": index["states"].get(entity["entity_id"]),
                "writable": domain in WRITABLE,
                "primary": entity.get("entity_category") not in ("config", "diagnostic"),
                **_entity_limits(domain, index["attributes"].get(entity["entity_id"]) or {}),
            })
        return controls

    def _ir_controls(self, remote_id, index):
        view = self.hub.ir.list_one(remote_id)

        def entity_of(suffix):
            entity = index["by_unique"].get(f"devicehub_ir_{remote_id}_{suffix}")
            return entity.get("entity_id") if entity else None

        controls = []
        if view["ac"]:
            ac, climate = view["ac"], entity_of("ac")
            controls += [
                {"code": "power", "name": "Power", "type": "Boolean", "entity_id": climate, "state": ac["power"],
                 "writable": True, "primary": True},
                {"code": "mode", "name": "Mode", "type": "Enum", "entity_id": climate, "state": ac["mode"],
                 "writable": True, "primary": True, "options": ac["modes"]},
                {"code": "temp", "name": "Temperature", "type": "Integer", "entity_id": climate, "state": ac["temp"],
                 "writable": True, "primary": True, "min": ac["min"], "max": ac["max"], "step": 1, "unit": "°C"},
                {"code": "fan", "name": "Fan", "type": "Enum", "entity_id": climate, "state": ac["fan"],
                 "writable": True, "primary": True, "options": ac["fans"]},
            ]
        for key in view["keys"]:
            controls.append({
                "code": key["code"],
                "name": key["name"],
                "type": "button",
                "entity_id": entity_of(_slug(key["code"])),
                "state": None,
                "writable": True,
                "primary": key["primary"],
            })
        return controls

    async def _ha_command(self, control, value):
        entity_id = control.get("entity_id")
        domain = (entity_id or "").split(".", 1)[0]
        if not entity_id or domain not in WRITABLE:
            raise RuntimeError(f"{control['name']} is only available inside Home Assistant.")
        data = {"entity_id": entity_id}
        if domain in ("switch", "light", "fan", "input_boolean", "humidifier", "siren"):
            service = "turn_on" if value in TRUTHY else "turn_off"
        elif domain == "valve":
            if isinstance(value, (int, float)) or str(value).replace(".", "", 1).isdigit():
                service, data = "set_valve_position", {**data, "position": int(float(value))}
            else:
                service = "open_valve" if value in TRUTHY else "close_valve"
        elif domain == "lock":
            service = "unlock" if value in TRUTHY else "lock"
        elif domain in ("number", "input_number"):
            service, data = "set_value", {**data, "value": float(value)}
        elif domain in ("select", "input_select"):
            service, data = "select_option", {**data, "option": str(value)}
        elif domain == "button":
            service = "press"
        elif domain == "cover":
            if isinstance(value, (int, float)) or str(value).isdigit():
                service, data = "set_cover_position", {**data, "position": int(float(value))}
            elif str(value).lower() == "stop":
                service = "stop_cover"
            elif value in TRUTHY or str(value).lower() == "open":
                service = "open_cover"
            else:
                service = "close_cover"
        elif domain == "climate":
            if isinstance(value, (int, float)) or _number(value) is not None:
                service, data = "set_temperature", {**data, "temperature": _number(value)}
            else:
                service, data = "set_hvac_mode", {**data, "hvac_mode": str(value)}
        else:
            raise RuntimeError(f"{control['name']} cannot be changed through the Device Hub API.")
        await self.hub.ha.call_service(domain, service, data)

    def _persist(self):
        store.save(STORE, {"tokens": self.tokens})


def _bare(device_id):
    return device_id.split(":", 1)[1] if ":" in device_id else device_id


def _slug(text):
    return re.sub(r"[^a-z0-9]+", "_", str(text).lower()).strip("_") or "key"


def _point_limits(point):
    values = point.get("values") or {}
    if point["type"] == "Enum":
        return {"options": [str(item) for item in values.get("range") or []]}
    if point["type"] == "Integer":
        limits = {key: values[key] for key in ("min", "max", "step") if isinstance(values.get(key), (int, float))}
        if values.get("scale"):
            limits["scale"] = values["scale"]
        if values.get("unit"):
            limits["unit"] = values["unit"]
        return limits
    return {}


def _entity_limits(domain, attributes):
    if domain in ("select", "input_select"):
        return {"options": [str(item) for item in attributes.get("options") or []]}
    if domain in ("number", "input_number"):
        limits = {key: attributes[key] for key in ("min", "max", "step") if isinstance(attributes.get(key), (int, float))}
        if attributes.get("unit_of_measurement"):
            limits["unit"] = attributes["unit_of_measurement"]
        return limits
    if domain == "sensor" and attributes.get("unit_of_measurement"):
        return {"unit": attributes["unit_of_measurement"]}
    return {}


def _label(code):
    return code.replace("_", " ").strip().capitalize() or code


def _number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _from_ha(state, kind):
    if state in (None, "unavailable", "unknown"):
        return None
    if kind == "Boolean":
        return state == "on"
    if kind == "Integer":
        return _number(state)
    return state


async def _gather_index(hub):
    import asyncio

    async def entities():
        try:
            return await hub.ha.call("config/entity_registry/list")
        except Exception:
            return []

    async def states():
        try:
            data = await hub.ha.rest("GET", "/states")
            return data if isinstance(data, list) else []
        except Exception:
            return []

    return await asyncio.gather(entities(), states())
