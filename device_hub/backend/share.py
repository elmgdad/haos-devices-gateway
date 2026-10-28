"""Keys that let another app list and control devices Device Hub already knows.

The panel API stays inside Home Assistant. This one listens on the Pi
(port 8765) and answers only for the devices named on the key.
"""

import asyncio
import secrets
import time

from aiohttp import web

from . import __version__, openapi, store
from .activity import say
from .ir import _slug

STORE = "share"
ALL = "*"
WRITABLE = {
    "switch", "light", "fan", "cover", "lock", "number", "select", "input_boolean",
    "input_number", "input_select", "button", "climate", "humidifier", "valve", "siren",
}
TRUTHY = {True, 1, "1", "true", "True", "on", "ON", "open", "unlock"}
RISKY = ("reset", "factory", "limit", "calibrat", "identify", "restart", "reboot")
# Each scope can do everything the ones before it can. Keys made before scopes existed are "control".
SCOPES = ("read", "control", "manage")
SCOPE_ABOUT = {
    "read": "See devices, areas, scenes, activity and live updates.",
    "control": "Also send commands and save, run or delete scenes.",
    "manage": "Also rename devices, move them between areas and remove them.",
}
PANEL = {"id": "panel", "name": "panel", "device_ids": ["*"], "scope": "manage"}


class Shares:
    def __init__(self, hub):
        self.hub = hub
        saved = store.load(STORE) or {}
        self.tokens = saved.get("tokens") or []
        self._catalog = None
        self._catalog_at = 0.0
        self._index = None
        self._index_at = 0.0
        self._lock = asyncio.Lock()
        self._rebuilding = None

    def public(self, base_url):
        base = base_url.rstrip("/") + "/api/v1"
        return {
            "name": "Device Hub API",
            "version": __version__,
            "base_url": base,
            "openapi": base + "/openapi.json",
            "swagger": base + "/swagger",
            "mcp": base_url.rstrip("/") + "/mcp",
            "auth": "Authorization: Bearer <key>",
            "scopes": SCOPE_ABOUT,
            "endpoints": openapi.endpoints(),
            "values": openapi.VALUES,
        }

    def describe_html(self, base_url):
        return openapi.page(base_url, __version__)

    def openapi(self, base_url):
        return openapi.spec(base_url, __version__)

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
                    "scope": _scope(item),
                    "expires_at": item.get("expires_at"),
                    "expired": _expired(item),
                }
                for item in self.tokens
            ],
            "scopes": [{"id": scope, "about": SCOPE_ABOUT[scope]} for scope in SCOPES],
        }

    def create(self, name, device_ids, scope="control", expires_in_days=None):
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
        if scope not in SCOPES:
            raise RuntimeError("Choose what the key may do: read, control or manage.")
        expires_at = None
        if expires_in_days not in (None, "", 0, "0"):
            days = _number(expires_in_days)
            if not days or days < 0:
                raise RuntimeError("The key must last at least a day.")
            expires_at = time.time() + days * 86400
        item = {
            "id": secrets.token_hex(4),
            "name": name[:60],
            "token": "dh_" + secrets.token_urlsafe(24),
            "device_ids": chosen,
            "created_at": time.time(),
            "last_used": None,
            "scope": scope,
            "expires_at": expires_at,
        }
        self.tokens.append(item)
        self._persist()
        self.hub.activity.log("key", f"Created API key {item['name']} ({scope})")
        return {
            "id": item["id"],
            "name": item["name"],
            "token": item["token"],
            "device_ids": item["device_ids"],
            "scope": scope,
            "expires_at": expires_at,
        }

    def reset(self):
        self.tokens = []
        store.remove(STORE)
        self.invalidate()

    def revoke(self, token_id):
        gone = next((item for item in self.tokens if item["id"] == token_id), None)
        if not gone:
            raise RuntimeError("That key is already gone.")
        self.tokens = [item for item in self.tokens if item["id"] != token_id]
        self._persist()
        self.hub.activity.log("key", f"Revoked API key {gone['name']}")
        return {"ok": True}

    def authenticate(self, header):
        raw = (header or "").strip()
        if raw.lower().startswith("bearer "):
            raw = raw[7:].strip()
        if not raw:
            return None
        for item in self.tokens:
            if secrets.compare_digest(item["token"], raw):
                if _expired(item):
                    raise web.HTTPUnauthorized(
                        text='{"error": "This key has expired. Create a new one in Device Hub, Settings, Share."}',
                        content_type="application/json",
                    )
                now = time.time()
                if not item.get("last_used") or now - item["last_used"] > 60:
                    item["last_used"] = now
                    self._persist()
                else:
                    item["last_used"] = now
                return item
        return None

    def require(self, request, need="read", allow_query=False):
        """The key on the request, if it may do `need`. EventSource cannot send headers, so events also take ?key=."""
        header = request.headers.get("Authorization", "")
        if not header and allow_query and request.query.get("key"):
            header = "Bearer " + request.query["key"]
        found = self.authenticate(header)
        if not found:
            raise web.HTTPUnauthorized(
                text='{"error": "Send Authorization: Bearer and a key from Device Hub."}',
                content_type="application/json",
            )
        if SCOPES.index(_scope(found)) < SCOPES.index(need):
            raise web.HTTPForbidden(
                text=f'{{"error": "This key can only {_scope(found)}. This needs a {need} key."}}',
                content_type="application/json",
            )
        return found

    async def devices_for(self, token):
        allowed = set(token["device_ids"])
        if ALL in allowed:
            return await self.catalog()
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
        what = _describe(device, commands)
        try:
            await self._send(device, commands)
        except Exception as exc:
            self.hub.activity.log("command", f"{what}: {exc}", device_id=device["id"], device=device["name"],
                                  ok=False, via=_via(token))
            raise
        self.hub.activity.log("command", what, device_id=device["id"], device=device["name"], via=_via(token))
        await self.refresh_one(device["id"])
        return await self.device_for(token, device_id)

    async def _send(self, device, commands):
        bare = _bare(device["id"])
        remote = self.hub.ir.knows(bare)
        tuya_id = device["id"].removeprefix("tuya:") if device["id"].startswith("tuya:") else None
        for command in commands:
            code = str((command or {}).get("code") or "").strip()
            control = next((item for item in device["controls"] if item["code"] == code or item.get("entity_id") == code), None)
            if not control or not control["writable"]:
                raise RuntimeError(f"{code or 'That control'} is not a control that can be changed on {device['name']}.")
            if remote:
                await self.hub.ir.command(bare, control["code"], command.get("value"))
                continue
            if control.get("via") == "thing":
                if not tuya_id:
                    raise RuntimeError(f"{control['name']} cannot be changed with this key.")
                await self.hub.cloud.issue(tuya_id, {control["code"]: control.get("thing_value") or "01"})
                self.hub.scenes.remember(device["id"], control["code"], command.get("value") in TRUTHY)
                continue
            if control.get("via") == "cloud":
                if not tuya_id:
                    raise RuntimeError(f"{control['name']} cannot be changed with this key.")
                await self.hub.cloud.command(tuya_id, [{"code": control["code"], "value": command.get("value")}])
                continue
            local_code = tuya_id and control["code"] != control.get("entity_id")
            if local_code:
                if not self.hub.local.is_live(tuya_id):
                    raise RuntimeError("The local link to this device is down.")
                await self.hub.local.command(tuya_id, [{"code": control["code"], "value": command.get("value")}])
            else:
                await self._ha_command(control, command.get("value"), command)

    async def catalog(self):
        """Every device with its controls. A catalog under 2 minutes old is returned at once and rebuilt behind."""
        age = time.monotonic() - self._catalog_at
        if self._catalog is not None and age < 8:
            return self._catalog
        if self._catalog is not None and age < 120:
            if self._rebuilding is None or self._rebuilding.done():
                self._rebuilding = asyncio.create_task(self._rebuild())
            return self._catalog
        return await self._rebuild()

    async def _rebuild(self):
        async with self._lock:
            started = time.monotonic()
            if self._catalog is not None and started - self._catalog_at < 8:
                return self._catalog
            rows = (await self.hub.all_devices())["devices"]
            index = await self.ha_index()
            found = {}
            for row in rows:
                if not row["key"].startswith("lan:"):
                    found[row["key"]] = await self._local_controls(row, index)
            # Cloud-only Tuya devices cost a request each, so ask a few at a time instead of one by one.
            gate = asyncio.Semaphore(4)

            async def from_cloud(key):
                async with gate:
                    found[key] = await self._cloud_controls(key)

            await asyncio.gather(*(from_cloud(key) for key, controls in found.items() if not controls and key.startswith("tuya:")))
            items = [item for item in (self._item(row, found.get(row["key"]) or []) for row in rows) if item]
            items.sort(key=lambda item: item["name"].lower())
            self._catalog = items
            self._catalog_at = started
            self.hub.events.map_entities(items)
            return items

    async def refresh_one(self, device_id):
        """Rebuild one device after a command, so the answer shows its new state without rebuilding them all."""
        row = next((r for r in self.hub._rows or [] if r["key"] == device_id), None)
        if row is None or self._catalog is None:
            self.invalidate()
            return
        self._index_at = 0
        index = await self.ha_index()
        if device_id.startswith("tuya:"):
            self.hub.cloud._control_cache.pop(device_id.removeprefix("tuya:"), None)
        controls = await self._local_controls(row, index)
        if not controls and device_id.startswith("tuya:"):
            controls = await self._cloud_controls(device_id)
        item = self._item(row, controls)
        self._catalog = [item if old["id"] == device_id else old for old in self._catalog if item or old["id"] != device_id]

    async def _local_controls(self, row, index):
        garage = await self._garage(row)
        return [garage] if garage else self._controls(row, index)

    async def _cloud_controls(self, key):
        try:
            return await self.hub.cloud.cloud_controls(key.removeprefix("tuya:"))
        except Exception:  # noqa: BLE001
            return []

    def _item(self, row, controls):
        if not controls:
            return None
        for control in controls:
            if any(word in control["name"].lower() for word in RISKY):
                control["primary"] = False
        presence = _as_presence(controls)
        role, detail = _role(self.hub, row, controls)
        if presence and role is None:
            role = "presence"
        return {
            "id": row["key"],
            "name": row["name"],
            "area": row.get("area"),
            "area_id": row.get("area_id"),
            "online": row.get("online"),
            "source": row["sources"][0],
            "role": role,
            "detail": detail,
            "ha_device_id": row.get("ha_device_id"),
            "controls": controls,
        }

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
        for entity in index["by_device"].get(row.get("ha_device_id") or "") or []:
            if entity["entity_id"] in seen:
                continue
            domain = entity["entity_id"].split(".", 1)[0]
            if domain in ("update", "event"):
                continue
            attributes = index["attributes"].get(entity["entity_id"]) or {}
            controls.append({
                "code": entity["entity_id"],
                "name": entity.get("name") or entity.get("original_name")
                or _label(attributes.get("device_class") or domain),
                "type": domain,
                "entity_id": entity["entity_id"],
                "state": index["states"].get(entity["entity_id"]),
                "writable": domain in WRITABLE,
                "primary": entity.get("entity_category") not in ("config", "diagnostic"),
                **_entity_limits(domain, attributes),
            })
        return controls

    async def _garage(self, row):
        if not row["key"].startswith("tuya:"):
            return None
        device_id = row["key"].removeprefix("tuya:")
        device = self.hub.cloud.by_id(device_id) if self.hub.cloud.connected else None
        if not device:
            return None
        text = f"{device.get('name') or ''} {device.get('product_name') or ''}".lower()
        if device.get("category") != "kg" and "rf" not in text:
            return None
        action = await self.hub.cloud.garage_action(device_id)
        if not action:
            return None
        custom = (action.get("custom_name") or "").lower()
        name = action.get("custom_name") or "Garage"
        if not custom or any(word in custom for word in ("grade", "garage", "gate")):
            name = "Garage"
        return {
            "code": action["code"],
            "name": name,
            "type": "Boolean",
            "entity_id": None,
            "state": bool(self.hub.scenes.remembered(row["key"], action["code"])),
            "writable": True,
            "primary": True,
            "via": "thing",
            "thing_value": action.get("thing_value") or "01",
        }

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

    async def _ha_command(self, control, value, command=None):
        domain, service, data = ha_service(control, value, command)
        await self.hub.ha.call_service(domain, service, data)

    def _persist(self):
        store.save(STORE, {"tokens": self.tokens})


def ha_service(control, value, command=None):
    """The Home Assistant service a control and value mean: (domain, service, data with entity_id)."""
    entity_id = control.get("entity_id")
    domain = (entity_id or "").split(".", 1)[0]
    if not entity_id or domain not in WRITABLE:
        raise RuntimeError(f"{control['name']} is only available inside Home Assistant.")
    data = {"entity_id": entity_id}
    brightness = _number((command or {}).get("brightness"))
    if domain == "light" and brightness is not None:
        if brightness <= 0:
            service = "turn_off"
        else:
            service, data = "turn_on", {**data, "brightness_pct": max(1, min(100, round(brightness)))}
    elif domain in ("switch", "light", "fan", "input_boolean", "humidifier", "siren"):
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
    return domain, service, data


def filter_devices(devices, query):
    """Devices narrowed by status (online, offline), area id or name, source and text in the name."""
    status = query.get("status")
    area = (query.get("area") or "").strip().lower()
    source = query.get("source")
    text = (query.get("q") or "").strip().lower()
    shown = []
    for item in devices:
        if status == "online" and item.get("online") is not True:
            continue
        if status == "offline" and item.get("online") is not False:
            continue
        if area and area not in ((item.get("area_id") or "").lower(), (item.get("area") or "").lower()):
            continue
        if source and item.get("source") != source:
            continue
        if text and text not in f"{item['name']} {item.get('area') or ''}".lower():
            continue
        shown.append(item)
    return shown


def _bare(device_id):
    return device_id.split(":", 1)[1] if ":" in device_id else device_id


def _scope(token):
    return token.get("scope") if token.get("scope") in SCOPES else "control"


def _expired(token):
    return bool(token.get("expires_at")) and time.time() > token["expires_at"]


def _via(token):
    return "panel" if token is PANEL else f"API key {token['name']}"


def _describe(device, commands):
    """One line for the activity log, such as: Kitchen plug: Switch on."""
    parts = []
    for command in commands:
        code = str((command or {}).get("code") or "")
        control = next((c for c in device["controls"] if c["code"] == code or c.get("entity_id") == code), None)
        name = control["name"] if control else code
        if (command or {}).get("brightness") is not None:
            parts.append(f"{name} brightness {command['brightness']}%")
        elif control and control.get("type") == "button":
            parts.append(f"{name} pressed")
        else:
            parts.append(f"{name} {say((command or {}).get('value'))}")
    return f"{device['name']}: " + ", ".join(parts)


def _control_label(control):
    code = str(control.get("code") or "").lower()
    return f"{control.get('name') or ''} {code.rsplit('.', 1)[-1]}".lower()


def _is_presence_reading(control):
    if control.get("writable"):
        return False
    label = _control_label(control)
    if any(word in label for word in ("illuminance", "battery", "door", "tamper")):
        return False
    return any(word in label for word in ("presence", "occupan"))


def _is_brightness_reading(control):
    if control.get("writable"):
        return False
    label = _control_label(control)
    unit = str(control.get("unit") or "").lower()
    return "illuminance" in label or unit in {"lx", "lux"}


def _as_presence(controls):
    """Presence sensors keep presence and brightness primary. Relays and settings do not.

    A door-sensor profile used to publish a timer as a battery. The readings that
    matter are whether someone is here and how bright the room is.
    """
    if not any(_is_presence_reading(control) for control in controls):
        return False
    for control in controls:
        if not (_is_presence_reading(control) or _is_brightness_reading(control)):
            control["primary"] = False
    return True


def _role(hub, row, controls):
    codes = {control["code"] for control in controls}
    if "start2" in codes:
        return "garage", "Button 2"
    bare = _bare(row["key"])
    if "temp" in codes and "mode" in codes and hub.ir.knows(bare):
        remote = next((item for item in hub.ir._all() if item["id"] == bare), None)
        blaster = hub.cloud.by_id(remote["blaster_id"]) if remote and remote.get("blaster_id") else None
        return "ac", (blaster or {}).get("name") or ""
    return None, None


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
    if domain == "light":
        modes = set(attributes.get("supported_color_modes") or [])
        if modes - {"onoff"}:
            level = attributes.get("brightness")
            return {"dimmable": True, "brightness": round(level * 100 / 255) if isinstance(level, (int, float)) else 0}
        return {}
    if domain == "climate":
        limits = {key: attributes[key] for key in ("min_temp", "max_temp") if isinstance(attributes.get(key), (int, float))}
        return {
            "options": [str(item) for item in attributes.get("hvac_modes") or []],
            **({"min": limits["min_temp"]} if "min_temp" in limits else {}),
            **({"max": limits["max_temp"]} if "max_temp" in limits else {}),
            "step": attributes.get("target_temp_step") or 0.5,
            "target": attributes.get("temperature"),
            "current": attributes.get("current_temperature"),
            "unit": "°",
        }
    if domain == "cover" and isinstance(attributes.get("current_position"), (int, float)):
        return {"position": attributes["current_position"]}
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
