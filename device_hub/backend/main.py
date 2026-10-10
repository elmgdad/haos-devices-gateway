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
from .scanner import Scanner
from .tuya_cloud import TuyaCloud
from .tuya_local import TuyaLocal
from .z2m import Z2M
from .zha import Zha

_LOGGER = logging.getLogger("device_hub")
ROOT = Path(__file__).resolve().parent.parent
WWW = ROOT / "www"
PORT = 8765
INGRESS_PEERS = {"172.30.32.2", "127.0.0.1"}


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
    if store.ADDON and request.remote not in INGRESS_PEERS:
        return web.json_response({"error": "Open Device Hub from the Home Assistant sidebar."}, status=403)
    try:
        return await handler(request)
    except web.HTTPException:
        raise
    except (HAError, RuntimeError, ValueError, KeyError) as exc:
        return web.json_response({"error": str(exc) or exc.__class__.__name__}, status=400)
    except Exception as exc:  # noqa: BLE001
        _LOGGER.exception("Request failed: %s", request.path)
        return web.json_response({"error": f"Unexpected error: {exc}"}, status=500)


class Hub:
    def __init__(self, session):
        self.ha = HomeAssistant(session)
        self.cloud = TuyaCloud()
        self.tuya_local = TuyaLocal(self.ha)
        self.scanner = Scanner(self.ha, self.cloud, self.tuya_local)
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

        rows = []
        for device in devices:
            seen = self.scanner.tuya_ip(device["id"]) or {}
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
            if device["lan_ip"]:
                lan_claimed.add(device["lan_ip"])
            if entry and entry.get("host"):
                lan_claimed.add(entry["host"])
            rows.append({
                "key": f"tuya:{device['id']}",
                "name": device["name"],
                "sources": ["tuya", "tuya_local"] if entry else ["tuya"],
                "kind": device["category_name"] or device["product_name"] or device["category"],
                "area": entry["area"] if entry else None,
                "online": device["online"],
                "status": "Tuya Local entry is broken" if entry and entry["broken"]
                else ("Needs attention: " + " and ".join(device["drift"]) + " changed") if device["drift"]
                else ("In Tuya Local" if entry and entry["state"] == "loaded"
                      else f"Tuya Local: {entry['state'].replace('_', ' ')}" if entry else "Cloud only"),
                "warn": bool(device["drift"]) or bool(entry and entry["state"] != "loaded"),
                "address": device["lan_ip"] or (f"via {device['gateway_name']}" if device["gateway_name"] else ""),
                "ha_device_id": entry["ha_device_id"] if entry else None,
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
        return {"devices": rows, "areas": registries["areas"]}

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
        return web.json_response(store.settings())

    @r.post("/api/settings")
    async def set_settings(request):
        data = await body(request)
        current = store.settings()
        for key in ("z2m_base_topic", "default_cidr"):
            if key in data:
                current[key] = str(data[key] or "").strip()
        store.save("settings", current)
        hub.z2m.set_base(current["z2m_base_topic"] or "zigbee2mqtt")
        return web.json_response(current)

    @r.get("/api/areas")
    async def areas(_request):
        items = await hub.ha.call("config/area_registry/list")
        return web.json_response([{"id": a["area_id"], "name": a["name"]} for a in items or []])

    @r.post("/api/ha/devices/{device_id}")
    async def update_device(request):
        data = await body(request)
        payload = {}
        if "name_by_user" in data:
            payload["name_by_user"] = (data["name_by_user"] or "").strip() or None
        if "area_id" in data:
            payload["area_id"] = data["area_id"] or None
        await hub.ha.call("config/device_registry/update", device_id=request.match_info["device_id"], **payload)
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

    async def on_cleanup(_app):
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
