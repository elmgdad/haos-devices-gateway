"""Device Hub web server: Ingress panel + JSON API."""

import asyncio
import logging
import os
import time
from pathlib import Path

import aiohttp
from aiohttp import web

from . import openapi, store
from .activity import Activity
from .areas import Areas
from .automations import Automations
from .events import Events
from .ha import HAError, HomeAssistant
from .hotspot import Hotspot
from .ir import IrRemotes
from .mcp import Mcp
from .scanner import Scanner
from .scenes import Scenes
from .share import PANEL, Shares, filter_devices
from .sonoff import Sonoff
from .tuya_bridge import LocalBridge
from .tuya_cloud import TuyaCloud
from .tuya_local import TuyaLocal
from .z2m import Z2M
from .zha import Zha

_LOGGER = logging.getLogger("device_hub")
ROOT = Path(__file__).resolve().parent.parent
WWW = ROOT / "www"
PORT = 8765
INGRESS_PEERS = {"172.30.32.2", "127.0.0.1"}
CORS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "Authorization, Content-Type",
    "Access-Control-Allow-Methods": "GET, POST, PATCH, DELETE, OPTIONS",
}


def load_dev_env():
    path = ROOT.parent / ".env.dev"
    if store.ADDON or not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())


@web.middleware
async def guard(request, handler):
    shared = request.path in ("/api/v1", "/mcp") or request.path.startswith("/api/v1/")
    if store.ADDON and not shared and request.remote not in INGRESS_PEERS:
        return web.json_response({"error": "Open Device Hub from the Home Assistant sidebar."}, status=403)
    if shared and request.method == "OPTIONS":
        return web.Response(status=204, headers=CORS)
    try:
        response = await handler(request)
    except web.HTTPException as exc:
        if shared:
            exc.headers.update(CORS)
        raise
    except (HAError, RuntimeError, ValueError, KeyError) as exc:
        response = web.json_response({"error": str(exc) or exc.__class__.__name__}, status=400)
    except Exception as exc:  # noqa: BLE001
        _LOGGER.exception("Request failed: %s", request.path)
        response = web.json_response({"error": f"Unexpected error: {exc}"}, status=500)
    if shared and not response.prepared:
        response.headers.update(CORS)
    return response


class Hub:
    def __init__(self, session):
        self.ha = HomeAssistant(session)
        self.cloud = TuyaCloud()
        self.tuya_local = TuyaLocal(self.ha)
        self.scanner = Scanner(self.ha, self.cloud, self.tuya_local)
        self.local = LocalBridge(self.ha, self.cloud, self.scanner)
        self.tuya_local.bridge = self.local
        self.ir = IrRemotes(self)
        self.ir.attach()
        self.activity = Activity()
        self.scenes = Scenes(self)
        self.share = Shares(self)
        self.hotspot = Hotspot(self.ha)
        self.zha = Zha(self.ha)
        self.z2m = Z2M(self.ha)
        self.sonoff = Sonoff(self.ha)
        self.areas = Areas(self)
        self.automations = Automations(self)
        self.mcp = Mcp(self)
        self.events = Events(self)
        self._rows = None

    async def tuya_view(self, refresh=False, registries=None, quiet=False):
        """quiet uses the Tuya list already fetched, so a background check never spends cloud calls."""
        if not self.cloud.connected:
            devices = []
        elif quiet and self.cloud.devices:
            devices = self.cloud.devices
        else:
            devices = await self.cloud.list(refresh=refresh)
        registries = registries or await self.ha.registries()
        status = await self.tuya_local.status()
        entries = await self.tuya_local.entries(details=True, registries=registries) if status["installed"] else []
        matched = set()

        def find_entry(device, lan_ip):
            for entry in entries:
                if device["child"]:
                    if entry["device_cid"] == device["node_id"]:
                        return entry
                elif entry["device_id"] == device["id"] and not entry["device_cid"]:
                    return entry
            if lan_ip and not device["child"]:
                return next((e for e in entries if e["host"] == lan_ip and not e["device_cid"]), None)
            return None

        mqtt_devices = {}
        ha_by_id = {ha_device["id"]: ha_device for ha_device in registries["devices"]}
        for ha_device in registries["devices"]:
            for domain, identifier, *_ in ha_device.get("identifiers") or []:
                if domain == "mqtt" and identifier.startswith("devicehub_tuya_"):
                    mqtt_devices[identifier.removeprefix("devicehub_tuya_")] = ha_device["id"]

        rows = []
        for device in devices:
            seen = self.scanner.tuya_ip(device["id"]) or {}
            if seen.get("ip"):
                self.local.note_address(device["id"], seen["ip"], seen.get("version"))
            local = self.local.link_info(device["id"])
            if local:
                local["ha_device_id"] = mqtt_devices.get(device["id"])
                placed = ha_by_id.get(local["ha_device_id"]) if local.get("ha_device_id") else None
                if placed and placed.get("area_id"):
                    local["area_id"] = placed["area_id"]
                    local["area"] = registries["areas"].get(placed["area_id"]) or local.get("area")
            device = {**device, "child": bool(device["node_id"]) and not seen.get("ip")}
            entry = find_entry(device, seen.get("ip"))
            drift = []
            if entry:
                matched.add(entry["entry_id"])
                if entry.get("local_key") and device["local_key"] and entry["local_key"] != device["local_key"]:
                    drift.append("key")
                if seen.get("ip") and entry.get("host") and entry["host"].lower() != "auto" and entry["host"] != seen["ip"]:
                    drift.append("ip")
            gateway = self.cloud.by_id(device["gateway_id"]) if device["gateway_id"] else None
            rows.append({
                **device,
                "lan_ip": seen.get("ip"),
                "protocol": seen.get("version"),
                "seen_at": seen.get("at"),
                "gateway_name": gateway["name"] if gateway else None,
                "tuya_local": {
                    "entry_id": entry["entry_id"],
                    "title": entry["title"],
                    "state": entry["state"],
                    "host": entry["host"],
                    "protocol_version": entry["protocol_version"],
                    "area": entry["area"],
                    "ha_device_id": entry["ha_device_id"],
                    "broken": entry["broken"],
                } if entry else None,
                "local": local,
                "drift": drift,
            })
        extra = [e for e in entries if e["entry_id"] not in matched]
        return {
            "config": self.cloud.config(),
            "tuya_local": status,
            "devices": rows,
            "unmatched_entries": [{k: v for k, v in e.items() if k != "local_key"} for e in extra],
            "gateways": self._gateways(devices, entries),
        }

    def _gateways(self, devices, entries):
        found = {}
        for device in devices:
            if not device["node_id"]:
                seen = self.scanner.tuya_ip(device["id"]) or {}
                found[device["id"]] = {"id": device["id"], "name": device["name"], "host": seen.get("ip")}
        for entry in entries:
            if entry["device_id"] and not entry["device_cid"]:
                current = found.setdefault(entry["device_id"], {"id": entry["device_id"], "name": entry["title"], "host": None})
                current["host"] = current["host"] or entry["host"]
                current["in_tuya_local"] = True
        return sorted(found.values(), key=lambda g: (not g.get("in_tuya_local"), g["name"].lower()))

    async def resolve_gateway(self, gateway_id):
        device = self.cloud.by_id(gateway_id) or {}
        seen = self.scanner.tuya_ip(gateway_id) or {}
        result = {"host": seen.get("ip"), "local_key": device.get("local_key"), "protocol": seen.get("version")}
        for entry in await self.tuya_local.entries(details=True):
            if entry["device_id"] == gateway_id and not entry["device_cid"]:
                result["host"] = result["host"] or entry["host"]
                result["local_key"] = result["local_key"] or entry["local_key"]
                result["protocol"] = result["protocol"] or (str(entry["protocol_version"]) if entry["protocol_version"] else None)
        return result

    async def save_local(self, device_ids):
        view = await self.tuya_view()
        rows = {d["id"]: d for d in view["devices"]}
        everything = not device_ids
        if everything:
            device_ids = [d["id"] for d in view["devices"]]
        blocked = {}
        for device_id in device_ids:
            entry = (rows.get(device_id) or {}).get("tuya_local")
            if entry and entry["state"] == "loaded":
                blocked[device_id] = (
                    "Tuya Local already controls it. Remove it from Tuya Local first so the device keeps one local connection."
                )
        return await self.local.save(device_ids, blocked, refresh_live=not everything)

    async def add_params(self, body):
        device_id = (body.get("device_id") or "").strip()
        device = self.cloud.by_id(device_id)
        host = (body.get("host") or "").strip()
        local_key = (body.get("local_key") or "").strip()
        protocol = (body.get("protocol_version") or "").strip()
        device_cid = (body.get("device_cid") or "").strip()
        seen = self.scanner.tuya_ip(device_id) or {}
        if device and device["node_id"] and not seen.get("ip"):
            gateway_id = (body.get("gateway_id") or device["gateway_id"] or "").strip()
            if not gateway_id:
                raise RuntimeError("Pick the gateway this device is paired with.")
            gateway = await self.resolve_gateway(gateway_id)
            device_cid = device_cid or device["node_id"] or device["uuid"]
            host = host or gateway["host"] or ""
            local_key = local_key or device["local_key"] or gateway["local_key"] or ""
            protocol = protocol or gateway["protocol"] or "auto"
            device_id = gateway_id
            if not host:
                raise RuntimeError("The gateway has no LAN IP yet. Run a network scan or type its IP.")
        else:
            host = host or seen.get("ip") or ""
            local_key = local_key or (device or {}).get("local_key") or ""
            protocol = protocol or seen.get("version") or "auto"
            if not host:
                raise RuntimeError("No LAN IP for this device yet. Run a network scan or type the IP.")
        if not local_key:
            raise RuntimeError("Enter the local key for this device.")
        name = (body.get("name") or (device or {}).get("name") or "").strip()
        return {"device_id": device_id, "host": host, "local_key": local_key, "protocol_version": protocol,
                "device_cid": device_cid or None, "name": name}

    async def all_devices(self, quiet=False):
        registries = await self.ha.registries()
        rows = []
        lan_claimed = set()
        try:
            tuya = await self.tuya_view(registries=registries, quiet=quiet)
        except Exception as exc:  # noqa: BLE001
            tuya = {"devices": [], "unmatched_entries": [], "error": str(exc)}
        for device in tuya["devices"]:
            entry = device["tuya_local"]
            local = device["local"]
            if device["lan_ip"]:
                lan_claimed.add(device["lan_ip"])
            if entry and entry.get("host"):
                lan_claimed.add(entry["host"])
            if local:
                status = "Local control in Home Assistant" if local["state"] == "online" else f"Local link {local['state']}"
            elif entry and entry["broken"]:
                status = "Tuya Local entry is broken"
            elif device["drift"]:
                status = "Needs attention: " + " and ".join(device["drift"]) + " changed"
            elif entry:
                status = "In Tuya Local" if entry["state"] == "loaded" else f"Tuya Local: {entry['state'].replace('_', ' ')}"
            else:
                status = "Cloud only"
            rows.append({
                "key": f"tuya:{device['id']}",
                "name": device["name"],
                "sources": ["tuya", "tuya_local"] if entry else ["tuya"],
                "kind": device["category_name"] or device["product_name"] or device["category"],
                "area": entry["area"] if entry else None,
                "online": local["state"] == "online" if local else device["online"],
                "status": status,
                "warn": bool(local and local["state"] == "offline") or bool(device["drift"])
                or bool(entry and entry["state"] != "loaded"),
                "address": device["lan_ip"] or (f"via {device['gateway_name']}" if device["gateway_name"] else ""),
                "ha_device_id": (local or {}).get("ha_device_id") or (entry["ha_device_id"] if entry else None),
                "tab": "tuya",
            })
        for entry in tuya["unmatched_entries"]:
            if entry.get("host"):
                lan_claimed.add(entry["host"])
            rows.append({
                "key": f"tuya_local:{entry['entry_id']}",
                "name": entry["title"],
                "sources": ["tuya_local"],
                "kind": "Tuya Local entry",
                "area": entry["area"],
                "online": entry["state"] == "loaded",
                "status": "Loaded" if entry["state"] == "loaded" else f"Not loaded ({entry['state']})",
                "warn": entry["state"] != "loaded",
                "address": entry["host"] or "",
                "ha_device_id": entry["ha_device_id"],
                "tab": "tuya",
            })
        try:
            zha = await self.zha.overview(registries)
        except HAError:
            zha = {"devices": []}
        for device in zha["devices"]:
            rows.append({
                "key": f"zha:{device['ieee']}",
                "name": device["name"],
                "sources": ["zha"],
                "kind": " ".join(p for p in (device["manufacturer"], device["model"]) if p) or device["device_type"],
                "area": device["area"],
                "online": device["available"],
                "status": f"LQI {device['lqi']}" if device["lqi"] is not None else device["device_type"],
                "warn": device["available"] is False,
                "address": device["ieee"],
                "ha_device_id": device["ha_device_id"],
                "tab": "zha",
            })
        try:
            z2m = await self.z2m.overview(registries)
        except HAError:
            z2m = {"devices": []}
        for device in z2m["devices"]:
            if device["type"] == "Coordinator":
                continue
            online = None if device["availability"] is None else device["availability"] == "online"
            rows.append({
                "key": f"z2m:{device['ieee']}",
                "name": device["name"],
                "sources": ["z2m"],
                "kind": " ".join(p for p in (device["vendor"], device["model"]) if p) or device["type"],
                "area": device["area"],
                "online": online,
                "status": f"LQI {device['linkquality']}" if device["linkquality"] is not None else (device["type"] or ""),
                "warn": online is False or device["interview_completed"] is False,
                "address": device["ieee"],
                "ha_device_id": device["ha_device_id"],
                "tab": "z2m",
            })
        try:
            sonoff = await self.sonoff.overview(registries)
        except HAError:
            sonoff = {"devices": []}
        for device in sonoff["devices"]:
            if device["ip"]:
                lan_claimed.add(device["ip"])
            on = sum(1 for item in device["switches"] if item["state"] == "on")
            mode = "Local" if device["local"] else "Cloud"
            rows.append({
                "key": f"sonoff:{device['id']}",
                "name": device["name"],
                "sources": ["sonoff"],
                "kind": device["model"],
                "area": device["area"],
                "online": device["online"],
                "status": f"{mode} · {on} of {len(device['switches'])} on" if device["online"] is not False else "Offline",
                "warn": device["online"] is False,
                "address": device["ip"] or "",
                "ha_device_id": device["ha_device_id"],
                "tab": "sonoff",
            })
        for host in self.scanner.results:
            if not host.get("smart") or host["ip"] in lan_claimed or host.get("is_self"):
                continue
            rows.append({
                "key": f"lan:{host['ip']}",
                "name": host.get("hostname") or (host["services"][0]["name"] if host.get("services") else "") or host["kind"],
                "sources": ["lan"],
                "kind": host["kind"],
                "area": None,
                "online": True,
                "status": f"In HA as {host['ha']['name']}" if host.get("ha") else "Not in Home Assistant",
                "warn": False,
                "address": host["ip"],
                "ha_device_id": (host.get("ha") or {}).get("device_id"),
                "tab": "network",
            })
        ir_placed = _ir_devices(registries)
        blasters = {item["id"]: item["name"] for item in self.ir.list()["blasters"]}
        for row in rows:
            remote_id = row["key"].removeprefix("tuya:")
            if row["key"].startswith("tuya:") and self.ir.knows(remote_id):
                row["ha_device_id"] = row.get("ha_device_id") or (ir_placed.get(remote_id) or {}).get("id")
                blaster = blasters.get(self.ir.get(remote_id)["blaster_id"])
                row["status"] = f"IR remote on {blaster}" if blaster else "IR remote"
        for remote in self.ir.remotes.values():
            rows.append({
                "key": f"ir:{remote['id']}",
                "name": remote["name"],
                "sources": ["tuya"],
                "kind": "Learned IR remote",
                "area": None,
                "online": None,
                "status": f"Learned on {blasters.get(remote['blaster_id'], 'a blaster')}",
                "warn": False,
                "address": "",
                "ha_device_id": (ir_placed.get(remote["id"]) or {}).get("id"),
                "tab": "tuya",
            })
        by_id = {device["id"]: device for device in registries["devices"]}
        for row in rows:
            placed = by_id.get(row.get("ha_device_id") or "")
            area_id = placed.get("area_id") if placed else None
            if not area_id and row["key"].startswith("tuya:"):
                profile = self.local.profiles.get(row["key"].removeprefix("tuya:")) or {}
                area_id = profile.get("area_id")
                if area_id and not row.get("area"):
                    row["area"] = profile.get("area")
            row["area_id"] = area_id
            if area_id and registries["areas"].get(area_id):
                row["area"] = registries["areas"][area_id]
        self._rows = rows
        return {"devices": rows, "areas": registries["areas"]}

    async def _row(self, key):
        for rows in (self._rows, None):
            rows = rows if rows is not None else (await self.all_devices())["devices"]
            found = next((row for row in rows if row["key"] == key), None)
            if found:
                return found
        raise RuntimeError("Device Hub does not know that device. Refresh and try again.")

    def _name(self, key):
        return next((row["name"] for row in self._rows or [] if row["key"] == key), key)

    async def remove_device(self, key, via="panel"):
        """Remove a device from the system it belongs to. One place, so every removal is logged."""
        prefix, _, ident = key.partition(":")
        name = self._name(key)
        try:
            if prefix == "tuya":
                await self.cloud.remove(ident)
                if ident in self.local.profiles:
                    await self.local.forget(ident)
            elif prefix == "tuya_local":
                await self.tuya_local.remove(ident)
            elif prefix == "zha":
                await self.zha.remove(ident)
            elif prefix == "z2m":
                await self.z2m.remove(ident)
            elif prefix == "sonoff":
                devices = await self.ha.call("config/device_registry/list")
                device = next(
                    (d for d in devices or []
                     if any(domain == "sonoff" and str(value) == ident for domain, value, *_ in d.get("identifiers") or [])),
                    None,
                )
                if not device:
                    raise RuntimeError("That Sonoff device is not in Home Assistant.")
                await self.sonoff.remove(device["id"])
            elif prefix == "ir":
                self.ir.delete(ident)
            else:
                raise RuntimeError("Device Hub cannot remove a device it only saw on the network.")
        except Exception as exc:
            self.activity.log("remove", f"Could not remove {name}: {exc}", device_id=key, device=name, ok=False, via=via)
            raise
        self._rows = [row for row in self._rows or [] if row["key"] != key]
        self.share.invalidate()
        self.activity.log("remove", f"Removed {name}", device_id=key, device=name, via=via)
        return {"ok": True}

    async def update_device(self, key, changes, via="panel"):
        row = await self._row(key)
        if not row.get("ha_device_id"):
            raise RuntimeError("This device is not in Home Assistant yet, so it cannot be renamed or given an area.")
        payload = {}
        if "name" in changes:
            payload["name_by_user"] = str(changes.get("name") or "").strip() or None
        if "area_id" in changes:
            payload["area_id"] = changes.get("area_id") or None
        if not payload:
            raise RuntimeError("Send a name or an area_id.")
        updated = await self.ha.call("config/device_registry/update", device_id=row["ha_device_id"], **payload)
        area = None
        if "area_id" in payload:
            await self.remember_area(row["ha_device_id"], payload["area_id"])
            areas = await self.ha.call("config/area_registry/list")
            area = next((a["name"] for a in areas or [] if a["area_id"] == payload["area_id"]), payload["area_id"])
        self._log_change(key, row["name"], payload, updated, area, via)
        self._rows = None
        self.share.invalidate()
        return {"ok": True}

    def _log_change(self, key, before, payload, updated, area, via):
        updated = updated or {}
        parts = []
        if "name_by_user" in payload:
            after = updated.get("name_by_user") or updated.get("name") or before
            parts.append(f"renamed to {after}" if payload["name_by_user"] else f"name reset to {after}")
        if "area_id" in payload:
            parts.append(f"moved to {area}" if payload["area_id"] else "taken out of its area")
        self.activity.log("change", f"{before} " + " and ".join(parts), device_id=key, device=before, via=via)

    async def device_detail(self, key):
        row = await self._row(key)
        registries = await self.ha.registries()
        ha_device = next((d for d in registries["devices"] if d["id"] == row.get("ha_device_id")), None)
        index = await self.share.ha_index()
        entities = []
        for entity in index["by_device"].get(row.get("ha_device_id") or "") or []:
            attributes = index["attributes"].get(entity["entity_id"]) or {}
            entities.append({
                "entity_id": entity["entity_id"],
                "name": entity.get("name") or entity.get("original_name") or attributes.get("friendly_name") or entity["entity_id"],
                "domain": entity["entity_id"].split(".", 1)[0],
                "state": index["states"].get(entity["entity_id"]),
                "unit": attributes.get("unit_of_measurement"),
                "category": entity.get("entity_category"),
            })
        entities.sort(key=lambda item: (item["category"] is not None, item["entity_id"]))
        ha = None
        if ha_device:
            via = next((d for d in registries["devices"] if d["id"] == ha_device.get("via_device_id")), None)
            ha = {
                "id": ha_device["id"],
                "name": ha_device.get("name"),
                "name_by_user": ha_device.get("name_by_user"),
                "manufacturer": ha_device.get("manufacturer"),
                "model": ha_device.get("model"),
                "model_id": ha_device.get("model_id"),
                "sw_version": ha_device.get("sw_version"),
                "hw_version": ha_device.get("hw_version"),
                "serial_number": ha_device.get("serial_number"),
                "configuration_url": ha_device.get("configuration_url"),
                "via": (via or {}).get("name_by_user") or (via or {}).get("name"),
                "integrations": sorted({registries["entry_domains"].get(e, e) for e in ha_device.get("config_entries") or []}),
                "disabled": bool(ha_device.get("disabled_by")),
            }
        return {
            "device": row,
            "ha": ha,
            "entities": entities,
            "activity": self.activity.list(30, device_id=key),
            "removable": key.partition(":")[0] in REMOVABLE,
        }

    async def bulk(self, keys, action, area_id=None):
        """Do one thing to many devices. One failing does not stop the rest."""
        if action not in ("area", "on", "off", "remove"):
            raise RuntimeError("Choose area, on, off or remove.")
        if not keys:
            raise RuntimeError("Pick at least one device.")
        catalog = {item["id"]: item for item in await self.share.catalog()} if action in ("on", "off") else {}
        results = []
        for key in keys:
            try:
                if action == "area":
                    await self.update_device(key, {"area_id": area_id})
                elif action == "remove":
                    await self.remove_device(key)
                else:
                    item = catalog.get(key)
                    switches = [c for c in (item or {}).get("controls", [])
                                if c["writable"] and c["primary"] and c["type"] in ON_OFF]
                    if not switches:
                        raise RuntimeError("It has no on or off control.")
                    await self.share.command(PANEL, key, [{"code": c["code"], "value": action == "on"} for c in switches])
                results.append({"key": key, "name": self._name(key), "ok": True})
            except Exception as exc:  # noqa: BLE001
                results.append({"key": key, "name": self._name(key), "ok": False, "error": str(exc) or exc.__class__.__name__})
        return {"results": results}

    async def setup(self):
        """A checklist of what is connected, what is missing, and how to fix it."""
        status = await self.status()
        mqtt = self.local.mqtt.info()
        try:
            rows = (await self.all_devices(quiet=True))["devices"]
        except Exception:  # noqa: BLE001
            rows = self._rows or []
        tuya, local, sonoff, z2m, zha, lan = (status[k] for k in ("tuya", "tuya_local", "sonoff", "z2m", "zha", "lan"))
        no_area = [r for r in rows if r.get("ha_device_id") and not r.get("area_id")]
        offline = [r for r in rows if r.get("online") is False]
        items = [
            _check("ha", "Home Assistant", True,
                   ("ok", "Connected.") if status["ha_connected"]
                   else ("warn", "Device Hub cannot reach Home Assistant. It retries every few seconds."), None),
            _check("mqtt", "MQTT broker", True,
                   ("ok", f"Connected to {mqtt.get('host') or 'the broker'}.") if mqtt.get("state") == "connected"
                   else ("optional", "Dev mode: the Device Hub app on Home Assistant publishes these entities, not this copy.")
                   if self.ha.mode == "dev"
                   else ("todo", "Install the Mosquitto broker app and the MQTT integration. Device Hub uses MQTT to put "
                                 "Tuya devices it controls locally and learned IR remotes into Home Assistant."
                                 + (f" Last error: {mqtt['error']}" if mqtt.get("error") else "")),
                   {"label": "Open MQTT", "ha": "/config/integrations/integration/mqtt"}),
            _check("tuya", "Tuya cloud project", True,
                   ("warn", f"Sign-in problem: {tuya['error']}") if tuya.get("error")
                   else ("todo", "Sign in with the Access ID and Secret of your cloud project on iot.tuya.com.") if not tuya["connected"]
                   else ("warn", "Signed in, but no devices. Link your Smart Life account to the project under Devices, Link App Account.")
                   if not tuya["count"] else ("ok", f"{tuya['count']} devices with their local keys."),
                   {"label": "Open Tuya", "tab": "tuya"} if not tuya.get("count") else None),
            _check("tuya_local", "Tuya Local", False,
                   ("optional", "Install Tuya Local from HACS to control Tuya devices on your network, without the cloud.")
                   if not local["installed"]
                   else ("warn", f"{local['problems']} entries are not loaded.") if local.get("problems")
                   else ("ok", f"{local['count']} entries.") if local.get("count")
                   else ("ok", "Installed. Add devices to it from the Tuya tab."),
                   {"label": "Get Tuya Local", "url": local.get("hacs_url") or "https://github.com/make-all/tuya-local"}
                   if not local["installed"] else {"label": "Open Tuya Local", "tab": "tuya_local"} if local.get("problems") else None),
            _check("sonoff", "Sonoff (SonoffLAN)", False,
                   ("optional", "Install SonoffLAN from HACS to add eWeLink and Sonoff devices.") if not sonoff["installed"]
                   else ("warn", f"{sonoff['offline']} of {sonoff['count']} devices are offline.") if sonoff.get("offline")
                   else ("ok", f"{sonoff['count']} devices, {sonoff['local']} on your network."),
                   {"label": "Get SonoffLAN", "url": "https://github.com/AlexxIT/SonoffLAN"} if not sonoff["installed"]
                   else {"label": "Open Sonoff", "tab": "sonoff"} if sonoff.get("offline") else None),
            _check("z2m", "Zigbee2MQTT", False,
                   ("optional", "For Zigbee devices on a USB stick. Use Zigbee2MQTT or ZHA, not both.") if not z2m["installed"]
                   else ("warn", "Installed but not running.") if z2m.get("state") != "started"
                   else ("warn", f"Running, bridge {z2m.get('bridge') or 'unknown'}.") if z2m.get("bridge") != "online"
                   else ("ok", f"{z2m['count']} devices."),
                   {"label": "Open add-on store", "ha": "/hassio/store"} if not z2m["installed"]
                   else {"label": "Open Zigbee2MQTT", "tab": "z2m"} if z2m.get("state") != "started" or z2m.get("bridge") != "online" else None),
            _check("zha", "ZHA", False,
                   ("ok", f"{zha['count']} devices.") if zha["loaded"]
                   else ("warn", "Set up, but not running.") if zha["entries"]
                   else ("optional", "Home Assistant's own Zigbee integration. Use it or Zigbee2MQTT, not both."),
                   {"label": "Add ZHA", "ha": "/config/integrations/dashboard/add?domain=zha"} if not zha["entries"]
                   else {"label": "Open ZHA", "tab": "zha"} if not zha["loaded"] else None),
            _check("scan", "Network scan", False,
                   ("ok", f"{lan['count']} smart devices among {lan['hosts']} on the network.") if lan.get("finished_at")
                   else ("todo", "Scan the network once so Device Hub knows each device's IP. Tuya Local needs it."),
                   {"label": "Open Network", "tab": "network"} if not lan.get("finished_at") else None),
            _check("areas", "Areas", False,
                   ("warn", f"{len(no_area)} devices in Home Assistant have no area. Apps group devices by area.") if no_area
                   else ("ok", "Every device in Home Assistant has an area."),
                   {"label": "Show devices", "tab": "all"} if no_area else None),
            _check("offline", "Offline devices", False,
                   ("warn", f"{len(offline)} devices are offline: "
                            + ", ".join(r["name"] for r in offline[:6]) + ("…" if len(offline) > 6 else "")
                            + ". Check power and Wi-Fi, or remove ones you no longer have.") if offline
                   else ("ok", "Every device reports in."),
                   {"label": "Show offline", "tab": "all", "status": "offline"} if offline else None),
            _check("api", "API keys", False,
                   ("ok", f"{len(self.share.tokens)} keys.") if self.share.tokens
                   else ("optional", "Create a key to control devices from your phone app or another system."),
                   {"label": "Open Share", "tab": "settings"} if not self.share.tokens else None),
        ]
        return {"items": items}

    async def watch(self):
        """Note devices going offline or coming back, for the activity log and live updates."""
        seen = None
        while True:
            await asyncio.sleep(60 if seen is not None else 20)
            try:
                rows = (await self.all_devices(quiet=True))["devices"]
            except Exception:  # noqa: BLE001
                continue
            now = {row["key"]: (row["online"], row["name"]) for row in rows if row.get("online") is not None}
            if seen is not None:
                for key, (online, name) in now.items():
                    before = seen.get(key)
                    if before is None or before[0] == online:
                        continue
                    text = f"{name} came back online" if online else f"{name} went offline"
                    self.activity.log("online" if online else "offline", text, device_id=key, device=name, via="watcher")
                    self.events.publish("device", {"device_id": key, "name": name, "online": online, "at": time.time()}, key)
            seen = now

    async def reset(self):
        """Forget everything Device Hub saved and remove what it published to Home Assistant.

        Tuya Local, Zigbee2MQTT and ZHA keep their own devices. Options from the app
        configuration page are kept; Tuya signs in again with them on the next start.
        """
        for device_id in list(self.local.profiles):
            await self.local.forget(device_id)
        self.local.last_results = []
        self.ir.reset()
        self.share.reset()
        self.scenes.reset()
        self.cloud.logout()
        for name in ("settings", "scan"):
            store.remove(name)
        self.scanner.results = []
        self.scanner.finished_at = None
        self.z2m.set_base(await self.z2m.discovered_topic() or "zigbee2mqtt")
        return {"ok": True}

    async def remember_area(self, ha_device_id, area_id):
        devices = await self.ha.call("config/device_registry/list")
        device = next((item for item in devices or [] if item["id"] == ha_device_id), None)
        if not device:
            return
        areas = await self.ha.call("config/area_registry/list")
        name = next((item["name"] for item in areas or [] if item["area_id"] == area_id), None)
        for domain, identifier, *_ in device.get("identifiers") or []:
            if domain == "mqtt" and str(identifier).startswith("devicehub_tuya_"):
                self.local.set_area(str(identifier).removeprefix("devicehub_tuya_"), area_id, name)
        self.share.invalidate()

    async def api_base(self):
        try:
            info = await self.ha.supervisor("GET", "/network/info")
        except HAError:
            info = {}
        candidates = []
        for iface in (info or {}).get("interfaces") or []:
            ipv4 = iface.get("ipv4") or {}
            for item in ipv4.get("address") or []:
                raw = item.get("address") if isinstance(item, dict) else str(item)
                ip = raw.split("/")[0]
                if not ip or ip.startswith("127.") or ip.startswith("169.254.") or ip.startswith("172.30."):
                    continue
                candidates.append((not iface.get("primary"), ip))
        candidates.sort()
        host = candidates[0][1] if candidates else "homeassistant.local"
        return f"http://{host}:8765"

    async def status(self):
        async def safe(coro, fallback):
            try:
                return await coro
            except Exception as exc:  # noqa: BLE001
                return {**fallback, "error": str(exc)}

        tl_status = await safe(self.tuya_local.status(), {"installed": False})
        entries = await safe(self.ha.call("config_entries/get"), {})
        entries = entries if isinstance(entries, list) else []
        tl_entries = [e for e in entries if e["domain"] == "tuya_local"]
        zha_entries = [e for e in entries if e["domain"] == "zha"]
        zha_count = 0
        if any(e["state"] == "loaded" for e in zha_entries):
            devices = await safe(self.ha.call("zha/devices", timeout=60), {})
            zha_count = len(devices) if isinstance(devices, list) else 0
        addon = await safe(self.z2m.addon(), {"installed": False})
        sonoff = await safe(self.sonoff.overview(), {"installed": False, "devices": []})
        try:
            areas = len(await self.ha.call("config/area_registry/list") or [])
            automations = await self.automations.list()
        except Exception:  # noqa: BLE001
            areas, automations = None, []
        return {
            "areas": {"count": areas},
            "automations": {
                "count": len(automations),
                "on": sum(1 for a in automations if a["enabled"]),
                "ours": sum(1 for a in automations if a["ours"]),
            },
            "sonoff": {
                "installed": sonoff.get("installed", False),
                "count": len(sonoff.get("devices") or []),
                "local": sum(1 for d in sonoff.get("devices") or [] if d["local"] and d["online"] is not False),
                "offline": sum(1 for d in sonoff.get("devices") or [] if d["online"] is False),
            },
            "mode": self.ha.mode,
            "ha_connected": self.ha.connected,
            "tuya": {"connected": self.cloud.connected, "count": len(self.cloud.devices), "error": self.cloud.error},
            "tuya_local": {
                **tl_status,
                "count": len(tl_entries),
                "problems": sum(1 for e in tl_entries if e["state"] != "loaded"),
            },
            "zha": {
                "entries": len(zha_entries),
                "loaded": any(e["state"] == "loaded" for e in zha_entries),
                "count": zha_count,
            },
            "z2m": {
                "installed": addon.get("installed", False),
                "state": addon.get("state"),
                "bridge": self.z2m.bridge_state,
                "count": sum(1 for d in self.z2m.devices if d.get("type") != "Coordinator"),
                "permit_join": (self.z2m.info or {}).get("permit_join"),
            },
            "lan": {
                "count": sum(1 for h in self.scanner.results if h.get("smart")),
                "hosts": len(self.scanner.results),
                "finished_at": self.scanner.finished_at,
                "running": self.scanner.running,
            },
        }


async def _settings_view(hub: Hub):
    current = store.settings()
    discovered = await hub.z2m.discovered_topic()
    if discovered:
        current["z2m_base_topic"] = discovered
    current["z2m_topic_discovered"] = bool(discovered)
    return current


REMOVABLE = {"tuya", "tuya_local", "zha", "z2m", "sonoff", "ir"}
ON_OFF = {"Boolean", "switch", "light", "fan", "input_boolean"}


def _check(check_id, title, required, outcome, action):
    state, detail = outcome
    return {"id": check_id, "title": title, "required": required, "state": state, "detail": detail, "action": action}


def _ir_devices(registries):
    found = {}
    for device in registries["devices"]:
        for domain, identifier, *_ in device.get("identifiers") or []:
            if domain == "mqtt" and str(identifier).startswith("devicehub_ir_"):
                found[str(identifier).removeprefix("devicehub_ir_")] = device
    return found


PUBLIC_ACTIVITY = {"command", "scene", "remove", "change", "offline", "online", "automation"}


def _all_devices(token):
    if "*" not in token["device_ids"]:
        raise web.HTTPForbidden(
            text='{"error": "Areas and automations can touch any device, so this needs a key shared with all devices."}',
            content_type="application/json",
        )


def _public_automation(item):
    return {k: item.get(k) for k in ("entity_id", "id", "name", "enabled", "last_triggered", "mode", "running", "ours", "spec")}


def _public_device(item):
    return {
        "id": item["id"],
        "name": item["name"],
        "area": item.get("area"),
        "area_id": item.get("area_id"),
        "online": item.get("online"),
        "source": item.get("source"),
        "role": item.get("role"),
        "detail": item.get("detail"),
        "controls": [
            {
                key: control[key]
                for key in ("code", "name", "type", "entity_id", "state", "writable", "primary",
                            "options", "min", "max", "step", "scale", "unit",
                            "dimmable", "brightness", "target", "current", "position")
                if key in control
            }
            for control in item["controls"]
        ],
    }


async def _share_choices(hub: Hub):
    return [
        {"id": item["id"], "name": item["name"], "area": item.get("area"), "source": item.get("source"), "online": item.get("online")}
        for item in await hub.share.catalog()
    ]


def routes(hub: Hub):
    r = web.RouteTableDef()

    async def body(request):
        if request.can_read_body:
            return await request.json()
        return {}

    @r.get("/api/status")
    async def status(_request):
        return web.json_response(await hub.status())

    @r.get("/api/settings")
    async def get_settings(_request):
        return web.json_response(await _settings_view(hub))

    @r.post("/api/settings")
    async def set_settings(request):
        data = await body(request)
        current = store.settings()
        discovered = await hub.z2m.discovered_topic()
        if "default_cidr" in data:
            current["default_cidr"] = str(data["default_cidr"] or "").strip()
        if not discovered and "z2m_base_topic" in data:
            current["z2m_base_topic"] = str(data["z2m_base_topic"] or "").strip()
        store.save("settings", current)
        hub.z2m.set_base(discovered or current["z2m_base_topic"] or "zigbee2mqtt")
        return web.json_response(await _settings_view(hub))

    @r.post("/api/reset")
    async def reset(_request):
        return web.json_response(await hub.reset())

    @r.get("/api/areas")
    async def areas(_request):
        items = await hub.ha.call("config/area_registry/list")
        return web.json_response([{"id": a["area_id"], "name": a["name"]} for a in items or []])

    @r.post("/api/areas")
    async def create_area(request):
        data = await body(request)
        return web.json_response(await hub.areas.create(data.get("name")))

    @r.get("/api/areas/overview")
    async def areas_overview(_request):
        return web.json_response(await hub.areas.overview())

    @r.post("/api/areas/{area_id}/command")
    async def area_command(request):
        data = await body(request)
        return web.json_response(await hub.areas.command(request.match_info["area_id"], data.get("action")))

    @r.post("/api/areas/{area_id}")
    async def area_rename(request):
        data = await body(request)
        return web.json_response(await hub.areas.rename(request.match_info["area_id"], data.get("name")))

    @r.delete("/api/areas/{area_id}")
    async def area_delete(request):
        return web.json_response(await hub.areas.delete(request.match_info["area_id"]))

    @r.get("/api/automations")
    async def automations(_request):
        return web.json_response({"automations": await hub.automations.list()})

    @r.get("/api/automations/targets")
    async def automation_targets(_request):
        return web.json_response({"devices": await hub.automations.targets()})

    @r.post("/api/automations")
    async def automation_create(request):
        return web.json_response(await hub.automations.save(await body(request)))

    @r.post("/api/automations/{ref}/run")
    async def automation_run(request):
        return web.json_response(await hub.automations.run(request.match_info["ref"]))

    @r.post("/api/automations/{ref}/enabled")
    async def automation_enabled(request):
        data = await body(request)
        return web.json_response(await hub.automations.set_enabled(request.match_info["ref"], bool(data.get("enabled"))))

    @r.post("/api/automations/{ref}")
    async def automation_save(request):
        return web.json_response(await hub.automations.save(await body(request), request.match_info["ref"]))

    @r.delete("/api/automations/{ref}")
    async def automation_delete(request):
        return web.json_response(await hub.automations.delete(request.match_info["ref"]))

    @r.post("/api/ha/devices/{device_id}")
    async def update_device(request):
        data = await body(request)
        payload = {}
        if "name_by_user" in data:
            payload["name_by_user"] = (data["name_by_user"] or "").strip() or None
        if "area_id" in data:
            payload["area_id"] = data["area_id"] or None
        device_id = request.match_info["device_id"]
        key = next((row["key"] for row in hub._rows or [] if row.get("ha_device_id") == device_id), None)
        if key and payload:
            await hub.update_device(key, {k.replace("name_by_user", "name"): v for k, v in payload.items()})
            return web.json_response({"ok": True})
        await hub.ha.call("config/device_registry/update", device_id=device_id, **payload)
        if "area_id" in data:
            await hub.remember_area(device_id, data.get("area_id") or None)
        return web.json_response({"ok": True})

    @r.get("/api/control/{key}")
    async def control_get(request):
        key = request.match_info["key"]
        return web.json_response(next((item for item in await hub.share.catalog() if item["id"] == key), None))

    @r.post("/api/control/{key}")
    async def control_send(request):
        data = await body(request)
        return web.json_response(await hub.share.command(PANEL, request.match_info["key"], data.get("commands")))

    @r.post("/api/devices/bulk")
    async def devices_bulk(request):
        data = await body(request)
        keys = [str(k) for k in data.get("keys") or [] if k]
        return web.json_response(await hub.bulk(keys, data.get("action"), data.get("area_id")))

    @r.get("/api/devices/{key}/detail")
    async def device_detail(request):
        return web.json_response(await hub.device_detail(request.match_info["key"]))

    @r.post("/api/devices/{key}")
    async def device_update(request):
        data = await body(request)
        return web.json_response(await hub.update_device(request.match_info["key"], data))

    @r.delete("/api/devices/{key}")
    async def device_remove(request):
        return web.json_response(await hub.remove_device(request.match_info["key"]))

    @r.get("/api/activity")
    async def activity(request):
        limit = min(int(request.query.get("limit") or 200), 500)
        return web.json_response({"activity": hub.activity.list(limit, kind=request.query.get("kind") or None)})

    @r.delete("/api/activity")
    async def activity_clear(_request):
        hub.activity.clear()
        return web.json_response({"ok": True})

    @r.get("/api/setup")
    async def setup(_request):
        return web.json_response(await hub.setup())

    @r.get("/api/events")
    async def events(request):
        return await hub.events.stream(request)

    @r.get("/api/devices/all")
    async def all_devices(_request):
        return web.json_response(await hub.all_devices())

    @r.get("/api/tuya")
    async def tuya(request):
        return web.json_response(await hub.tuya_view(refresh=request.query.get("refresh") == "1"))

    @r.delete("/api/tuya/devices/{device_id}")
    async def tuya_remove(request):
        return web.json_response(await hub.remove_device(f"tuya:{request.match_info['device_id']}"))

    @r.post("/api/tuya/login")
    async def tuya_login(request):
        data = await body(request)
        await hub.cloud.login(data.get("access_id"), data.get("secret"), data.get("region"))
        return web.json_response(hub.cloud.config())

    @r.post("/api/tuya/logout")
    async def tuya_logout(_request):
        hub.cloud.logout()
        return web.json_response(hub.cloud.config())

    @r.get("/api/tuya/devices/{device_id}/control")
    async def tuya_control(request):
        device_id = request.match_info["device_id"]
        if hub.ir.knows(device_id):
            raise RuntimeError("This is an IR remote. Use its buttons in the Remotes panel.")
        return web.json_response(hub.local.control(device_id) or await hub.cloud.control(device_id))

    @r.post("/api/tuya/devices/{device_id}/commands")
    async def tuya_command(request):
        device_id = request.match_info["device_id"]
        data = await body(request)
        if hub.local.is_live(device_id):
            return web.json_response(await hub.local.command(device_id, data.get("commands")))
        return web.json_response(await hub.cloud.command(device_id, data.get("commands")))

    @r.get("/api/local")
    async def local_overview(_request):
        overview = hub.local.overview()
        catalog = {item["id"]: item for item in await hub.share.catalog()}
        for device in overview["devices"]:
            match = catalog.get(f"tuya:{device['id']}")
            if not match:
                device["controls"] = []
                continue
            device["ha_device_id"] = match.get("ha_device_id")
            device["area"] = match.get("area") or device.get("area")
            device["area_id"] = match.get("area_id") or device.get("area_id")
            device["controls"] = match["controls"]
        return web.json_response(overview)

    @r.get("/api/ir")
    async def ir_list(_request):
        view = hub.ir.list()
        registries = await hub.ha.registries()
        placed = _ir_devices(registries)
        for remote in view["remotes"]:
            device = placed.get(remote["id"]) or {}
            remote["ha_device_id"] = device.get("id")
            remote["area_id"] = device.get("area_id")
            remote["area"] = registries["areas"].get(device.get("area_id"))
        return web.json_response(view)

    @r.post("/api/ir/remotes")
    async def ir_create(request):
        data = await body(request)
        return web.json_response(hub.ir.create(data.get("name"), data.get("blaster_id")))

    @r.delete("/api/ir/remotes/{remote_id}")
    async def ir_delete(request):
        return web.json_response(hub.ir.delete(request.match_info["remote_id"]))

    @r.post("/api/ir/remotes/{remote_id}/learn")
    async def ir_learn(request):
        data = await body(request)
        return web.json_response(await hub.ir.learn(request.match_info["remote_id"], data.get("name")))

    @r.delete("/api/ir/remotes/{remote_id}/buttons/{name}")
    async def ir_forget_button(request):
        return web.json_response(hub.ir.forget_button(request.match_info["remote_id"], request.match_info["name"]))

    @r.post("/api/ir/remotes/{remote_id}/command")
    async def ir_command(request):
        data = await body(request)
        code = str(data.get("code") or "").strip()
        if not code:
            raise RuntimeError("Choose a key to send.")
        result = await hub.ir.command(request.match_info["remote_id"], code, data.get("value"))
        hub.share.invalidate()
        return web.json_response(result)

    @r.get("/api/share")
    async def share_panel(_request):
        return web.json_response({**hub.share.panel(await hub.api_base()), "devices": await _share_choices(hub)})

    @r.post("/api/share")
    async def share_create(request):
        data = await body(request)
        created = hub.share.create(
            data.get("name"), data.get("device_ids"), data.get("scope") or "control", data.get("expires_in_days")
        )
        return web.json_response(created)

    @r.delete("/api/share/{token_id}")
    async def share_revoke(request):
        return web.json_response(hub.share.revoke(request.match_info["token_id"]))

    @r.get("/api/v1")
    async def share_docs(request):
        docs = hub.share.public(await hub.api_base())
        accept = request.headers.get("Accept", "")
        if "text/html" in accept and not accept.strip().startswith("application/json"):
            return web.Response(text=hub.share.describe_html(await hub.api_base()), content_type="text/html")
        return web.json_response(docs)

    @r.get("/api/v1/openapi.json")
    async def share_openapi(_request):
        return web.json_response(hub.share.openapi(await hub.api_base()), headers=CORS)

    @r.get("/api/v1/devices")
    async def share_devices(request):
        token = hub.share.require(request)
        devices = filter_devices(await hub.share.devices_for(token), request.query)
        return web.json_response({"devices": [_public_device(item) for item in devices]})

    @r.get("/api/v1/devices/{device_id}")
    async def share_device(request):
        token = hub.share.require(request)
        return web.json_response(_public_device(await hub.share.device_for(token, request.match_info["device_id"])))

    @r.post("/api/v1/devices/{device_id}/commands")
    async def share_command(request):
        token = hub.share.require(request, "control")
        data = await body(request)
        device = await hub.share.command(token, request.match_info["device_id"], data.get("commands"))
        return web.json_response(_public_device(device))

    @r.patch("/api/v1/devices/{device_id}")
    async def share_update(request):
        token = hub.share.require(request, "manage")
        device = await hub.share.device_for(token, request.match_info["device_id"])
        data = await body(request)
        await hub.update_device(device["id"], {k: data[k] for k in ("name", "area_id") if k in data},
                                via=f"API key {token['name']}")
        return web.json_response(_public_device(await hub.share.device_for(token, device["id"])))

    @r.delete("/api/v1/devices/{device_id}")
    async def share_remove(request):
        token = hub.share.require(request, "manage")
        device = await hub.share.device_for(token, request.match_info["device_id"])
        return web.json_response(await hub.remove_device(device["id"], via=f"API key {token['name']}"))

    @r.get("/api/v1/areas")
    async def share_areas(request):
        token = hub.share.require(request)
        overview = await hub.areas.overview(token)
        return web.json_response({"areas": [
            {**{k: area[k] for k in ("id", "name", "online", "offline", "on", "switchable")}, "devices": len(area["devices"])}
            for area in overview["areas"]
        ]})

    @r.post("/api/v1/areas")
    async def share_area_create(request):
        token = hub.share.require(request, "manage")
        _all_devices(token)
        return web.json_response(await hub.areas.create((await body(request)).get("name")))

    @r.get("/api/v1/areas/{area_id}")
    async def share_area(request):
        token = hub.share.require(request)
        area = await hub.areas.one(request.match_info["area_id"], token)
        ids = {d["id"] for d in area["devices"]}
        devices = [_public_device(item) for item in await hub.share.devices_for(token) if item["id"] in ids]
        return web.json_response({**area, "devices": devices})

    @r.patch("/api/v1/areas/{area_id}")
    async def share_area_rename(request):
        token = hub.share.require(request, "manage")
        _all_devices(token)
        return web.json_response(await hub.areas.rename(request.match_info["area_id"], (await body(request)).get("name")))

    @r.delete("/api/v1/areas/{area_id}")
    async def share_area_delete(request):
        token = hub.share.require(request, "manage")
        _all_devices(token)
        return web.json_response(await hub.areas.delete(request.match_info["area_id"]))

    @r.post("/api/v1/areas/{area_id}/commands")
    async def share_area_command(request):
        token = hub.share.require(request, "control")
        data = await body(request)
        return web.json_response(await hub.areas.command(request.match_info["area_id"], data.get("action"), token))

    @r.get("/api/v1/automations")
    async def share_automations(request):
        token = hub.share.require(request)
        _all_devices(token)
        return web.json_response({"automations": [_public_automation(item) for item in await hub.automations.list()]})

    @r.post("/api/v1/automations")
    async def share_automation_create(request):
        token = hub.share.require(request, "manage")
        _all_devices(token)
        made = await hub.automations.save(await body(request), via=f"API key {token['name']}")
        return web.json_response(_public_automation(made))

    @r.get("/api/v1/automations/{ref}")
    async def share_automation(request):
        token = hub.share.require(request)
        _all_devices(token)
        return web.json_response(_public_automation(await hub.automations.find(request.match_info["ref"])))

    @r.put("/api/v1/automations/{ref}")
    async def share_automation_save(request):
        token = hub.share.require(request, "manage")
        _all_devices(token)
        made = await hub.automations.save(await body(request), request.match_info["ref"], via=f"API key {token['name']}")
        return web.json_response(_public_automation(made))

    @r.delete("/api/v1/automations/{ref}")
    async def share_automation_delete(request):
        token = hub.share.require(request, "manage")
        _all_devices(token)
        return web.json_response(await hub.automations.delete(request.match_info["ref"], via=f"API key {token['name']}"))

    @r.post("/api/v1/automations/{ref}/run")
    async def share_automation_run(request):
        token = hub.share.require(request, "control")
        _all_devices(token)
        return web.json_response(await hub.automations.run(request.match_info["ref"], via=f"API key {token['name']}"))

    @r.post("/api/v1/automations/{ref}/enabled")
    async def share_automation_enabled(request):
        token = hub.share.require(request, "control")
        _all_devices(token)
        data = await body(request)
        return web.json_response(await hub.automations.set_enabled(
            request.match_info["ref"], bool(data.get("enabled")), via=f"API key {token['name']}"))

    @r.get("/api/v1/swagger")
    async def share_swagger(_request):
        return web.Response(text=openapi.swagger(), content_type="text/html")

    @r.post("/mcp")
    async def mcp(request):
        return await hub.mcp.handle(request)

    @r.get("/mcp")
    async def mcp_get(request):
        return await hub.mcp.handle(request)

    @r.get("/api/v1/activity")
    async def share_activity(request):
        token = hub.share.require(request)
        ids = {item["id"] for item in await hub.share.devices_for(token)}
        wanted = request.query.get("device_id")
        limit = min(int(request.query.get("limit") or 100), 500)
        entries = hub.activity.list(limit, device_id=wanted, device_ids=ids)
        return web.json_response({"activity": [entry for entry in entries if entry["kind"] in PUBLIC_ACTIVITY]})

    @r.get("/api/v1/events")
    async def share_events(request):
        token = hub.share.require(request, allow_query=True)
        return await hub.events.stream(request, allowed=set(token["device_ids"]))

    @r.get("/api/v1/scenes")
    async def scenes_list(request):
        token = hub.share.require(request)
        return web.json_response({"scenes": await hub.scenes.visible(token)})

    @r.post("/api/v1/scenes")
    async def scenes_create(request):
        token = hub.share.require(request, "control")
        data = await body(request)
        return web.json_response(await hub.scenes.create(token, data.get("name"), data.get("actions")))

    @r.delete("/api/v1/scenes/{scene_id}")
    async def scenes_delete(request):
        hub.share.require(request, "control")
        return web.json_response(hub.scenes.delete(request.match_info["scene_id"]))

    @r.post("/api/v1/scenes/{scene_id}/run")
    async def scenes_run(request):
        token = hub.share.require(request, "control")
        scene_id = request.match_info["scene_id"]
        result = await hub.scenes.run(token, scene_id)
        name = next((s["name"] for s in hub.scenes.scenes if s["id"] == scene_id), scene_id)
        failed = sum(1 for item in result.get("results") or [] if not item.get("ok"))
        hub.activity.log("scene", f"Ran scene {name}" + (f", {failed} actions failed" if failed else ""),
                         ok=not failed, via=f"API key {token['name']}")
        return web.json_response(result)

    @r.post("/api/local/save")
    async def local_save(request):
        data = await body(request)
        ids = [str(i) for i in data.get("ids") or [] if i]
        return web.json_response(await hub.save_local(ids))

    @r.delete("/api/local/{device_id}")
    async def local_forget(request):
        return web.json_response(await hub.local.forget(request.match_info["device_id"]))

    @r.post("/api/tuya_local/add/start")
    async def tl_start(request):
        params = await hub.add_params(await body(request))
        name = params.pop("name")
        result = await hub.tuya_local.start_add(**params)
        return web.json_response({**result, "name": name, "host": params["host"]})

    @r.post("/api/tuya_local/add/finish")
    async def tl_finish(request):
        data = await body(request)
        return web.json_response(await hub.tuya_local.finish_add(data["flow_id"], data["type"], data.get("name")))

    @r.post("/api/tuya_local/add/cancel")
    async def tl_cancel(request):
        data = await body(request)
        await hub.tuya_local.cancel(data["flow_id"])
        return web.json_response({"ok": True})

    @r.post("/api/tuya_local/entries/{entry_id}/sync")
    async def tl_sync(request):
        data = await body(request)
        return web.json_response(await hub.tuya_local.update(request.match_info["entry_id"], data))

    @r.post("/api/tuya_local/entries/{entry_id}/reload")
    async def tl_reload(request):
        await hub.tuya_local.reload(request.match_info["entry_id"])
        return web.json_response({"ok": True})

    @r.delete("/api/tuya_local/entries/{entry_id}")
    async def tl_remove(request):
        await hub.tuya_local.remove(request.match_info["entry_id"])
        return web.json_response({"ok": True})

    @r.get("/api/scan")
    async def scan_status(_request):
        return web.json_response(hub.scanner.status())

    @r.get("/api/scan/networks")
    async def scan_networks(_request):
        return web.json_response({"networks": hub.scanner.networks(), "default": store.settings()["default_cidr"]})

    @r.post("/api/scan")
    async def scan_start(request):
        data = await body(request)
        hub.scanner.start(data.get("cidr") or "", sweep=data.get("sweep", True), mdns=data.get("mdns", True))
        return web.json_response(hub.scanner.status())

    @r.get("/api/scan/wifi")
    async def scan_wifi(_request):
        return web.json_response(await hub.scanner.wifi())

    @r.get("/api/hotspot")
    async def hotspot_status(_request):
        return web.json_response(await hub.hotspot.status())

    @r.post("/api/hotspot")
    async def hotspot_start(request):
        data = await body(request)
        return web.json_response(await hub.hotspot.start(data.get("ssid"), data.get("password")))

    @r.delete("/api/hotspot")
    async def hotspot_stop(_request):
        return web.json_response(await hub.hotspot.stop())

    @r.get("/api/zha")
    async def zha(_request):
        return web.json_response(await hub.zha.overview(await hub.ha.registries()))

    @r.post("/api/zha/permit")
    async def zha_permit(request):
        data = await body(request)
        await hub.zha.permit(data.get("duration", 60))
        return web.json_response({"ok": True})

    @r.post("/api/zha/devices/{ieee}/reconfigure")
    async def zha_reconfigure(request):
        await hub.zha.reconfigure(request.match_info["ieee"])
        return web.json_response({"ok": True})

    @r.delete("/api/zha/devices/{ieee}")
    async def zha_remove(request):
        await hub.zha.remove(request.match_info["ieee"])
        return web.json_response({"ok": True})

    @r.get("/api/sonoff")
    async def sonoff(_request):
        return web.json_response(await hub.sonoff.overview(await hub.ha.registries()))

    @r.delete("/api/sonoff/devices/{ha_device_id}")
    async def sonoff_remove(request):
        ha_device_id = request.match_info["ha_device_id"]
        key = next((row["key"] for row in hub._rows or [] if row.get("ha_device_id") == ha_device_id
                    and row["key"].startswith("sonoff:")), None)
        if key:
            return web.json_response(await hub.remove_device(key))
        await hub.sonoff.remove(ha_device_id)
        hub.share.invalidate()
        return web.json_response({"ok": True})

    @r.post("/api/sonoff/switch")
    async def sonoff_switch(request):
        data = await body(request)
        result = await hub.sonoff.set_switch(str(data.get("entity_id") or ""), bool(data.get("on")))
        hub.share.invalidate()
        return web.json_response(result)

    @r.get("/api/z2m")
    async def z2m(_request):
        return web.json_response(await hub.z2m.overview(await hub.ha.registries()))

    @r.post("/api/z2m/addon/{action}")
    async def z2m_addon(request):
        return web.json_response(await hub.z2m.addon_action(request.match_info["action"]))

    @r.post("/api/z2m/permit")
    async def z2m_permit(request):
        data = await body(request)
        await hub.z2m.permit(data.get("seconds", 254))
        return web.json_response({"ok": True})

    @r.post("/api/z2m/devices/{ieee}/rename")
    async def z2m_rename(request):
        data = await body(request)
        if not (data.get("name") or "").strip():
            raise RuntimeError("Enter a name.")
        await hub.z2m.rename(request.match_info["ieee"], data["name"])
        return web.json_response({"ok": True})

    @r.post("/api/z2m/devices/{ieee}/configure")
    async def z2m_configure(request):
        await hub.z2m.configure(request.match_info["ieee"])
        return web.json_response({"ok": True})

    @r.delete("/api/z2m/devices/{ieee}")
    async def z2m_remove(request):
        await hub.z2m.remove(request.match_info["ieee"], force=request.query.get("force") == "1")
        return web.json_response({"ok": True})

    @r.get("/")
    async def index(_request):
        page = WWW / "index.html"
        if not page.exists():
            return web.Response(text="The panel has not been built yet. Run npm run build in frontend/.", status=503)
        return web.FileResponse(page, headers={"Cache-Control": "no-cache"})

    return r


async def create_app():
    load_dev_env()
    session = aiohttp.ClientSession()
    hub = Hub(session)
    app = web.Application(middlewares=[guard], client_max_size=4 * 1024 * 1024)
    app.add_routes(routes(hub))
    if (WWW / "assets").exists():
        app.router.add_static("/assets", WWW / "assets", append_version=False)
    for icon in ("favicon.svg",):
        if (WWW / icon).exists():
            app.router.add_get(f"/{icon}", lambda _r, p=WWW / icon: web.FileResponse(p))

    async def on_startup(_app):
        app["ha_task"] = asyncio.create_task(hub.ha.run_forever())
        app["tuya_task"] = asyncio.create_task(hub.cloud.restore())
        app["hotspot_task"] = asyncio.create_task(hub.hotspot.ensure())
        app["watch_task"] = asyncio.create_task(hub.watch())
        await hub.local.start()
        await hub.ir.start()

    async def on_cleanup(_app):
        hub.ir.stop()
        await asyncio.to_thread(hub.local.stop)
        app["ha_task"].cancel()
        app["watch_task"].cancel()
        await session.close()

    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    return app


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    host = "0.0.0.0" if store.ADDON else "127.0.0.1"
    web.run_app(create_app(), host=host, port=PORT, access_log=None)


if __name__ == "__main__":
    main()
