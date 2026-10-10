"""Device Hub web server: Ingress panel + JSON API."""

import asyncio
import logging
import os
from pathlib import Path

import aiohttp
from aiohttp import web

from . import store
from .ha import HAError, HomeAssistant
from .hotspot import Hotspot
from .ir import IrRemotes
from .scanner import Scanner
from .share import Shares
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
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
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
    shared = request.path == "/api/v1" or request.path.startswith("/api/v1/")
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
    if shared:
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
        self.share = Shares(self)
        self.hotspot = Hotspot(self.ha)
        self.zha = Zha(self.ha)
        self.z2m = Z2M(self.ha)

    async def tuya_view(self, refresh=False, registries=None):
        devices = await self.cloud.list(refresh=refresh) if self.cloud.connected else []
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
            for domain, identifier in ha_device.get("identifiers") or []:
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

    async def all_devices(self):
        registries = await self.ha.registries()
        rows = []
        lan_claimed = set()
        try:
            tuya = await self.tuya_view(registries=registries)
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
        return {"devices": rows, "areas": registries["areas"]}

    async def remember_area(self, ha_device_id, area_id):
        devices = await self.ha.call("config/device_registry/list")
        device = next((item for item in devices or [] if item["id"] == ha_device_id), None)
        if not device:
            return
        areas = await self.ha.call("config/area_registry/list")
        name = next((item["name"] for item in areas or [] if item["area_id"] == area_id), None)
        for domain, identifier in device.get("identifiers") or []:
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
        return {
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


def _ir_devices(registries):
    found = {}
    for device in registries["devices"]:
        for domain, identifier in device.get("identifiers") or []:
            if domain == "mqtt" and str(identifier).startswith("devicehub_ir_"):
                found[str(identifier).removeprefix("devicehub_ir_")] = device
    return found


def _public_device(item):
    return {
        "id": item["id"],
        "name": item["name"],
        "area": item.get("area"),
        "online": item.get("online"),
        "source": item.get("source"),
        "controls": [
            {
                key: control[key]
                for key in ("code", "name", "type", "entity_id", "state", "writable", "primary",
                            "options", "min", "max", "step", "scale", "unit")
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

    @r.get("/api/areas")
    async def areas(_request):
        items = await hub.ha.call("config/area_registry/list")
        return web.json_response([{"id": a["area_id"], "name": a["name"]} for a in items or []])

    @r.post("/api/areas")
    async def create_area(request):
        data = await body(request)
        name = str(data.get("name") or "").strip()
        if not name:
            raise RuntimeError("Name the area.")
        created = await hub.ha.call("config/area_registry/create", name=name)
        return web.json_response({"id": created["area_id"], "name": created["name"]})

    @r.post("/api/ha/devices/{device_id}")
    async def update_device(request):
        data = await body(request)
        payload = {}
        if "name_by_user" in data:
            payload["name_by_user"] = (data["name_by_user"] or "").strip() or None
        if "area_id" in data:
            payload["area_id"] = data["area_id"] or None
        device_id = request.match_info["device_id"]
        await hub.ha.call("config/device_registry/update", device_id=device_id, **payload)
        if "area_id" in data:
            await hub.remember_area(device_id, data.get("area_id") or None)
        return web.json_response({"ok": True})

    @r.get("/api/devices/all")
    async def all_devices(_request):
        return web.json_response(await hub.all_devices())

    @r.get("/api/tuya")
    async def tuya(request):
        return web.json_response(await hub.tuya_view(refresh=request.query.get("refresh") == "1"))

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
        created = hub.share.create(data.get("name"), data.get("device_ids"))
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

    @r.get("/api/v1/devices")
    async def share_devices(request):
        token = hub.share.require(request)
        devices = await hub.share.devices_for(token)
        return web.json_response({"devices": [_public_device(item) for item in devices]})

    @r.get("/api/v1/devices/{device_id}")
    async def share_device(request):
        token = hub.share.require(request)
        return web.json_response(_public_device(await hub.share.device_for(token, request.match_info["device_id"])))

    @r.post("/api/v1/devices/{device_id}/commands")
    async def share_command(request):
        token = hub.share.require(request)
        data = await body(request)
        device = await hub.share.command(token, request.match_info["device_id"], data.get("commands"))
        return web.json_response(_public_device(device))

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
        await hub.local.start()
        await hub.ir.start()

    async def on_cleanup(_app):
        hub.ir.stop()
        await asyncio.to_thread(hub.local.stop)
        app["ha_task"].cancel()
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
