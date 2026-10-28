"""Device Hub as an MCP server, so an assistant such as Claude can see and control the home.

Streamable HTTP transport at /mcp: the client POSTs JSON-RPC and gets JSON back.
It uses the same keys as the API. A read key gets the tools that look, a control
key also gets the ones that switch, a manage key also the ones that rename.
"""

import json

from aiohttp import web

from . import __version__
from .share import SCOPES, _scope, filter_devices

PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")

DEVICE_ID = {"type": "string", "description": "A device id from list_devices, such as sonoff:1002ba764f."}
COMMANDS = {
    "type": "array",
    "description": "One or more controls to change.",
    "items": {
        "type": "object",
        "required": ["code"],
        "properties": {
            "code": {"type": "string", "description": "The control code from list_devices or get_device."},
            "value": {"description": "true or false for switches, a number, an option, or open/close/stop for covers."},
            "brightness": {"type": "number", "description": "Dimmable lights only, 0 to 100."},
        },
    },
}

TOOLS = [
    ("list_devices", "read", "List devices with their area, whether they are online, and their main controls and current values.",
     {"status": {"type": "string", "enum": ["online", "offline"]},
      "area": {"type": "string", "description": "An area id or name."},
      "query": {"type": "string", "description": "Text in the device name."}}, []),
    ("get_device", "read", "One device with every control, including settings and diagnostics.",
     {"device_id": DEVICE_ID}, ["device_id"]),
    ("list_areas", "read", "Rooms, with the devices in each and how many are on or offline.", {}, []),
    ("list_scenes", "read", "Saved scenes this key can run.", {}, []),
    ("list_automations", "read", "Home Assistant automations, whether each is on, and when it last ran.", {}, []),
    ("get_activity", "read", "Recent commands, changes and devices going offline or online. Newest first.",
     {"limit": {"type": "integer", "default": 20, "maximum": 100}, "device_id": DEVICE_ID}, []),
    ("control_device", "control", "Change one or more controls of a device, such as turning a switch on or setting a light's brightness.",
     {"device_id": DEVICE_ID, "commands": COMMANDS}, ["device_id", "commands"]),
    ("control_area", "control", "Turn every switch and light in an area on or off.",
     {"area_id": {"type": "string", "description": "An area id from list_areas."},
      "action": {"type": "string", "enum": ["on", "off"]}}, ["area_id", "action"]),
    ("run_scene", "control", "Run a saved scene.", {"scene_id": {"type": "string"}}, ["scene_id"]),
    ("run_automation", "control", "Run an automation's actions now, skipping its conditions.",
     {"automation": {"type": "string", "description": "An entity id such as automation.evening, or an id from list_automations."}},
     ["automation"]),
    ("set_automation_enabled", "control", "Turn an automation on or off.",
     {"automation": {"type": "string"}, "enabled": {"type": "boolean"}}, ["automation", "enabled"]),
    ("rename_device", "manage", "Rename a device in Home Assistant. An empty name goes back to the one the device reports.",
     {"device_id": DEVICE_ID, "name": {"type": "string"}}, ["device_id", "name"]),
    ("set_device_area", "manage", "Move a device to another area, or out of its area with null.",
     {"device_id": DEVICE_ID, "area_id": {"type": ["string", "null"]}}, ["device_id", "area_id"]),
]

INSTRUCTIONS = (
    "Device Hub joins Tuya, Tuya Local, Zigbee2MQTT, ZHA and Sonoff devices in one list. "
    "Call list_devices or list_areas first to learn device ids and control codes, then control_device or control_area. "
    "Values: true or false switch things on or off; numbers set levels; covers take open, close, stop or 0 to 100."
)


class Mcp:
    def __init__(self, hub):
        self.hub = hub

    async def handle(self, request):
        if request.method == "GET":
            return web.Response(status=405, text="Device Hub MCP answers POST only.", headers={"Allow": "POST"})
        token = self.hub.share.require(request)
        try:
            payload = await request.json()
        except ValueError:
            return web.json_response(_error(None, -32700, "That is not JSON."), status=400)
        batch = isinstance(payload, list)
        replies = []
        for message in payload if batch else [payload]:
            reply = await self._one(token, message)
            if reply is not None:
                replies.append(reply)
        if not replies:
            return web.Response(status=202)
        return web.json_response(replies if batch else replies[0])

    async def _one(self, token, message):
        if not isinstance(message, dict):
            return _error(None, -32600, "Each message must be a JSON object.")
        method = message.get("method")
        msg_id = message.get("id")
        if msg_id is None:
            return None  # a notification, such as notifications/initialized
        params = message.get("params") or {}
        if method == "initialize":
            asked = params.get("protocolVersion")
            return _result(msg_id, {
                "protocolVersion": asked if asked in PROTOCOLS else PROTOCOLS[0],
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "device-hub", "title": "Device Hub", "version": __version__},
                "instructions": INSTRUCTIONS,
            })
        if method == "ping":
            return _result(msg_id, {})
        if method == "tools/list":
            return _result(msg_id, {"tools": [_describe(tool) for tool in TOOLS if _may(token, tool[1])]})
        if method == "tools/call":
            name = params.get("name")
            tool = next((t for t in TOOLS if t[0] == name), None)
            if not tool:
                return _error(msg_id, -32602, f"There is no tool {name}.")
            if not _may(token, tool[1]):
                return _result(msg_id, _text(f"This key can only {_scope(token)}. {name} needs a {tool[1]} key.", error=True))
            try:
                data = await self._call(token, name, params.get("arguments") or {})
            except Exception as exc:  # noqa: BLE001
                return _result(msg_id, _text(str(exc) or exc.__class__.__name__, error=True))
            return _result(msg_id, {**_text(json.dumps(data, ensure_ascii=False, default=str)), "structuredContent": {"result": data}})
        return _error(msg_id, -32601, f"Device Hub does not answer {method}.")

    async def _call(self, token, name, args):
        hub = self.hub
        via = f"MCP key {token['name']}"
        if name == "list_devices":
            devices = filter_devices(await hub.share.devices_for(token), {
                "status": args.get("status"), "area": args.get("area"), "q": args.get("query")})
            return [_brief(item) for item in devices]
        if name == "get_device":
            return _full(await hub.share.device_for(token, args.get("device_id", "")))
        if name == "list_areas":
            return (await hub.areas.overview(token))["areas"]
        if name == "list_scenes":
            return await hub.scenes.visible(token)
        if name == "list_automations":
            _needs_everything(token)
            return [{k: item[k] for k in ("entity_id", "name", "enabled", "last_triggered", "ours")} for item in await hub.automations.list()]
        if name == "get_activity":
            ids = {item["id"] for item in await hub.share.devices_for(token)}
            limit = min(int(args.get("limit") or 20), 100)
            return hub.activity.list(limit, device_id=args.get("device_id"), device_ids=ids)
        if name == "control_device":
            return _brief(await hub.share.command(token, args.get("device_id", ""), args.get("commands")))
        if name == "control_area":
            return await hub.areas.command(args.get("area_id", ""), args.get("action"), token)
        if name == "run_scene":
            return await hub.scenes.run(token, args.get("scene_id", ""))
        if name == "run_automation":
            _needs_everything(token)
            return await hub.automations.run(args.get("automation", ""), via=via)
        if name == "set_automation_enabled":
            _needs_everything(token)
            return await hub.automations.set_enabled(args.get("automation", ""), bool(args.get("enabled")), via=via)
        if name == "rename_device":
            device = await hub.share.device_for(token, args.get("device_id", ""))
            await hub.update_device(device["id"], {"name": args.get("name") or ""}, via=via)
            return {"ok": True}
        if name == "set_device_area":
            device = await hub.share.device_for(token, args.get("device_id", ""))
            await hub.update_device(device["id"], {"area_id": args.get("area_id")}, via=via)
            return {"ok": True}
        raise RuntimeError(f"There is no tool {name}.")


def _needs_everything(token):
    if "*" not in token["device_ids"]:
        raise RuntimeError("Automations can touch any device, so they need a key shared with all devices.")


def _may(token, need):
    return SCOPES.index(_scope(token)) >= SCOPES.index(need)


def _describe(tool):
    name, scope, about, properties, required = tool
    return {
        "name": name,
        "title": name.replace("_", " ").capitalize(),
        "description": about,
        "inputSchema": {"type": "object", "properties": properties, "required": required},
        "annotations": {"readOnlyHint": scope == "read", "destructiveHint": False},
    }


def _brief(item):
    """A device with only what an assistant needs to act: main readings and everything it can change."""
    keep = ("code", "name", "type", "state", "writable", "options", "min", "max", "unit", "dimmable", "brightness")
    return {
        "id": item["id"],
        "name": item["name"],
        "area": item.get("area"),
        "area_id": item.get("area_id"),
        "online": item.get("online"),
        "controls": [{k: c[k] for k in keep if k in c} for c in item["controls"] if c["primary"] or c["writable"]],
    }


def _full(item):
    return {**_brief(item), "controls": [{k: v for k, v in c.items() if k not in ("via", "thing_value")} for c in item["controls"]]}


def _text(text, error=False):
    return {"content": [{"type": "text", "text": text}], "isError": error}


def _result(msg_id, result):
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _error(msg_id, code, message):
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}
