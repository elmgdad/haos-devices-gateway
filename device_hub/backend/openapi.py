"""The Device Hub API, described once.

OPERATIONS feeds the OpenAPI document at /api/v1/openapi.json, the docs page at
/api/v1, and the endpoint list the panel shows under Settings, Share.
"""

import html
import json

DEVICE_ID = {"name": "id", "in": "path", "required": True, "schema": {"type": "string"},
             "description": "A device id from the list, such as tuya:bf12ab or sonoff:1002ba764f."}
SCENE_ID = {"name": "id", "in": "path", "required": True, "schema": {"type": "string"}}
AREA_ID = {"name": "id", "in": "path", "required": True, "schema": {"type": "string"}, "description": "An area id from /areas."}
AUTOMATION_ID = {"name": "id", "in": "path", "required": True, "schema": {"type": "string"},
                 "description": "An entity id such as automation.evening, or the id from the list."}
ALL_KEY = " Needs a key shared with all devices."
SPEC_EXAMPLE = {
    "name": "Kitchen plug at sunset",
    "trigger": {"type": "sun", "event": "sunset", "offset_minutes": -15},
    "days": ["thu", "fri"],
    "actions": [{"device_id": "sonoff:1002ba764f", "code": "switch.kitchen_plug", "value": True},
                {"delay_seconds": 3600},
                {"area_id": "kitchen", "action": "off"}],
}

OPERATIONS = [
    {
        "method": "GET", "path": "/devices", "scope": "read", "id": "listDevices",
        "about": "Devices this key can see, with each control and its current value.",
        "params": [
            {"name": "status", "in": "query", "schema": {"type": "string", "enum": ["online", "offline"]},
             "description": "Only devices that are online, or only offline ones."},
            {"name": "area", "in": "query", "schema": {"type": "string"},
             "description": "An area id or name from /areas."},
            {"name": "source", "in": "query",
             "schema": {"type": "string", "enum": ["tuya", "tuya_local", "z2m", "zha", "sonoff"]}},
            {"name": "q", "in": "query", "schema": {"type": "string"}, "description": "Text in the name or area."},
        ],
        "response": {"type": "object", "properties": {"devices": {"type": "array", "items": {"$ref": "#/components/schemas/Device"}}}},
    },
    {
        "method": "GET", "path": "/devices/{id}", "scope": "read", "id": "getDevice",
        "about": "One device. The id is the value returned in the list.",
        "params": [DEVICE_ID],
        "response": {"$ref": "#/components/schemas/Device"},
    },
    {
        "method": "POST", "path": "/devices/{id}/commands", "scope": "control", "id": "sendCommands",
        "about": "Change one or more controls. code is the control code or the Home Assistant entity id. "
                 "Lights that are dimmable also take brightness from 0 to 100.",
        "params": [DEVICE_ID],
        "body": {"commands": [{"code": "switch_1", "value": True}]},
        "body_schema": {"type": "object", "required": ["commands"], "properties": {"commands": {
            "type": "array", "items": {"$ref": "#/components/schemas/Command"}}}},
        "response": {"$ref": "#/components/schemas/Device"},
    },
    {
        "method": "PATCH", "path": "/devices/{id}", "scope": "manage", "id": "updateDevice",
        "about": "Rename a device or move it to another area. It must be in Home Assistant.",
        "params": [DEVICE_ID],
        "body": {"name": "Kitchen lights", "area_id": "kitchen"},
        "body_schema": {"type": "object", "properties": {
            "name": {"type": "string", "description": "Empty goes back to the name the device reports."},
            "area_id": {"type": ["string", "null"], "description": "An id from /areas, or null for no area."}}},
        "response": {"$ref": "#/components/schemas/Device"},
    },
    {
        "method": "DELETE", "path": "/devices/{id}", "scope": "manage", "id": "removeDevice",
        "about": "Remove a device from the system it belongs to. Tuya unbinds it from the Smart Life account, "
                 "Sonoff removes it from Home Assistant, ZHA and Zigbee2MQTT ask it to leave the network.",
        "params": [DEVICE_ID],
        "response": {"$ref": "#/components/schemas/Ok"},
    },
    {
        "method": "GET", "path": "/areas", "scope": "read", "id": "listAreas",
        "about": "Home Assistant areas, with how many of this key's devices are in each.",
        "response": {"type": "object", "properties": {"areas": {"type": "array", "items": {"$ref": "#/components/schemas/Area"}}}},
    },
    {
        "method": "POST", "path": "/areas", "scope": "manage", "id": "createArea",
        "about": "Add an area in Home Assistant." + ALL_KEY,
        "body": {"name": "Guest room"},
        "body_schema": {"type": "object", "required": ["name"], "properties": {"name": {"type": "string"}}},
        "response": {"type": "object", "properties": {"id": {"type": "string"}, "name": {"type": "string"}}},
    },
    {
        "method": "GET", "path": "/areas/{id}", "scope": "read", "id": "getArea",
        "about": "One area with every device in it, each with its controls.",
        "params": [AREA_ID],
        "response": {"$ref": "#/components/schemas/AreaDetail"},
    },
    {
        "method": "PATCH", "path": "/areas/{id}", "scope": "manage", "id": "renameArea",
        "about": "Rename an area." + ALL_KEY,
        "params": [AREA_ID],
        "body": {"name": "Living room"},
        "body_schema": {"type": "object", "required": ["name"], "properties": {"name": {"type": "string"}}},
        "response": {"type": "object", "properties": {"id": {"type": "string"}, "name": {"type": "string"}}},
    },
    {
        "method": "DELETE", "path": "/areas/{id}", "scope": "manage", "id": "deleteArea",
        "about": "Delete an area. Its devices stay in Home Assistant without an area." + ALL_KEY,
        "params": [AREA_ID],
        "response": {"$ref": "#/components/schemas/Ok"},
    },
    {
        "method": "POST", "path": "/areas/{id}/commands", "scope": "control", "id": "controlArea",
        "about": "Turn every switch and light this key can see in the area on or off.",
        "params": [AREA_ID],
        "body": {"action": "off"},
        "body_schema": {"type": "object", "required": ["action"], "properties": {"action": {"type": "string", "enum": ["on", "off"]}}},
        "response": {"type": "object", "properties": {"area": {"type": "string"}, "results": {"type": "array", "items": {
            "type": "object", "properties": {"device_id": {"type": "string"}, "name": {"type": "string"},
                                             "ok": {"type": "boolean"}, "error": {"type": "string"}}}}}},
    },
    {
        "method": "GET", "path": "/automations", "scope": "read", "id": "listAutomations",
        "about": "Every Home Assistant automation: whether it is on, when it last ran, and the spec of ones Device Hub made." + ALL_KEY,
        "response": {"type": "object", "properties": {"automations": {"type": "array", "items": {"$ref": "#/components/schemas/Automation"}}}},
    },
    {
        "method": "POST", "path": "/automations", "scope": "manage", "id": "createAutomation",
        "about": "Make a Home Assistant automation from a spec: one trigger (time, sunrise or sunset, a device changing), "
                 "optional weekdays, and actions (set a device control, turn an area on or off, wait)." + ALL_KEY,
        "body": SPEC_EXAMPLE,
        "body_schema": {"$ref": "#/components/schemas/AutomationSpec"},
        "response": {"$ref": "#/components/schemas/Automation"},
    },
    {
        "method": "GET", "path": "/automations/{id}", "scope": "read", "id": "getAutomation",
        "about": "One automation." + ALL_KEY,
        "params": [AUTOMATION_ID],
        "response": {"$ref": "#/components/schemas/Automation"},
    },
    {
        "method": "PUT", "path": "/automations/{id}", "scope": "manage", "id": "updateAutomation",
        "about": "Replace the spec of an automation Device Hub made. Others are changed in Home Assistant." + ALL_KEY,
        "params": [AUTOMATION_ID],
        "body": SPEC_EXAMPLE,
        "body_schema": {"$ref": "#/components/schemas/AutomationSpec"},
        "response": {"$ref": "#/components/schemas/Automation"},
    },
    {
        "method": "DELETE", "path": "/automations/{id}", "scope": "manage", "id": "deleteAutomation",
        "about": "Delete an automation Device Hub made." + ALL_KEY,
        "params": [AUTOMATION_ID],
        "response": {"$ref": "#/components/schemas/Ok"},
    },
    {
        "method": "POST", "path": "/automations/{id}/run", "scope": "control", "id": "runAutomation",
        "about": "Run an automation's actions now, skipping its conditions." + ALL_KEY,
        "params": [AUTOMATION_ID],
        "response": {"$ref": "#/components/schemas/Ok"},
    },
    {
        "method": "POST", "path": "/automations/{id}/enabled", "scope": "control", "id": "enableAutomation",
        "about": "Turn an automation on or off." + ALL_KEY,
        "params": [AUTOMATION_ID],
        "body": {"enabled": False},
        "body_schema": {"type": "object", "required": ["enabled"], "properties": {"enabled": {"type": "boolean"}}},
        "response": {"$ref": "#/components/schemas/Ok"},
    },
    {
        "method": "GET", "path": "/scenes", "scope": "read", "id": "listScenes",
        "about": "Scenes saved for this key. A scene can mix Sonoff, Zigbee and Tuya.",
        "response": {"type": "object", "properties": {"scenes": {"type": "array", "items": {"$ref": "#/components/schemas/Scene"}}}},
    },
    {
        "method": "POST", "path": "/scenes", "scope": "control", "id": "createScene",
        "about": "Save a scene. Each action is a device id, a control code, and the value to send.",
        "body": {"name": "Leaving", "actions": [{"device_id": "sonoff:abc", "code": "switch.kitchen", "value": False}]},
        "body_schema": {"type": "object", "required": ["name", "actions"], "properties": {
            "name": {"type": "string"},
            "actions": {"type": "array", "items": {"$ref": "#/components/schemas/SceneAction"}}}},
        "response": {"$ref": "#/components/schemas/Scene"},
    },
    {
        "method": "POST", "path": "/scenes/{id}/run", "scope": "control", "id": "runScene",
        "about": "Send every action in the scene. One device failing does not skip the others.",
        "params": [SCENE_ID],
        "response": {"type": "object", "properties": {"results": {"type": "array", "items": {
            "type": "object", "properties": {"device_id": {"type": "string"}, "code": {"type": "string"},
                                             "ok": {"type": "boolean"}, "error": {"type": "string"}}}}}},
    },
    {
        "method": "DELETE", "path": "/scenes/{id}", "scope": "control", "id": "deleteScene",
        "about": "Remove a scene.",
        "params": [SCENE_ID],
        "response": {"$ref": "#/components/schemas/Ok"},
    },
    {
        "method": "GET", "path": "/activity", "scope": "read", "id": "listActivity",
        "about": "Recent commands, changes and devices going offline or online, for this key's devices. Newest first.",
        "params": [
            {"name": "limit", "in": "query", "schema": {"type": "integer", "default": 100, "maximum": 500}},
            {"name": "device_id", "in": "query", "schema": {"type": "string"}},
        ],
        "response": {"type": "object", "properties": {"activity": {"type": "array", "items": {"$ref": "#/components/schemas/Activity"}}}},
    },
    {
        "method": "GET", "path": "/events", "scope": "read", "id": "streamEvents",
        "about": "Live updates as Server-Sent Events: state (a control changed), device (went online or offline) "
                 "and activity. Browsers cannot send headers with EventSource, so this also takes ?key=.",
        "params": [{"name": "key", "in": "query", "schema": {"type": "string"},
                    "description": "The key, for clients that cannot send the Authorization header."}],
        "stream": True,
    },
]

VALUES = {
    "switch, light, fan, lock": "true turns it on or unlocks it. false turns it off or locks it.",
    "dimmable light": "Add brightness from 0 to 100 to the command. 0 turns it off.",
    "number": "A number inside the control's range.",
    "select": "One of the options, as text.",
    "cover": "open, close, stop, or a position from 0 to 100.",
    "button": "Any value presses it. IR remote keys such as Power or Volume+ are buttons.",
    "air conditioner": "power true or false, mode cool heat auto fan_only dry, temp 16 to 30, fan auto low medium high.",
    "climate": "A number sets the temperature. Text such as heat or off sets the mode.",
    "garage": "The Smart RF Copier control named Garage is button 2. true is On and false is Off. Each change presses the button.",
}

SCHEMAS = {
    "Control": {"type": "object", "required": ["code", "name", "type", "writable"], "properties": {
        "code": {"type": "string", "description": "Send this as code in a command."},
        "name": {"type": "string"},
        "type": {"type": "string", "description": "Boolean, Integer, Enum, String, button, or a Home Assistant domain such as switch or light."},
        "entity_id": {"type": ["string", "null"]},
        "state": {"description": "The current value. null while unknown."},
        "writable": {"type": "boolean"},
        "primary": {"type": "boolean", "description": "False for settings and diagnostics a dashboard usually hides."},
        "options": {"type": "array", "items": {"type": "string"}},
        "min": {"type": "number"}, "max": {"type": "number"}, "step": {"type": "number"},
        "scale": {"type": "integer"}, "unit": {"type": "string"},
        "dimmable": {"type": "boolean"}, "brightness": {"type": "number", "description": "0 to 100."},
        "target": {"type": ["number", "null"], "description": "Climate target temperature."},
        "current": {"type": ["number", "null"], "description": "Climate measured temperature."},
        "position": {"type": "number", "description": "Cover position, 0 to 100."},
    }},
    "Device": {"type": "object", "required": ["id", "name", "controls"], "properties": {
        "id": {"type": "string"},
        "name": {"type": "string"},
        "area": {"type": ["string", "null"]},
        "area_id": {"type": ["string", "null"]},
        "online": {"type": ["boolean", "null"]},
        "source": {"type": "string", "enum": ["tuya", "tuya_local", "z2m", "zha", "sonoff"]},
        "role": {"type": ["string", "null"], "description": "garage, ac or presence when Device Hub recognises one."},
        "detail": {"type": ["string", "null"]},
        "controls": {"type": "array", "items": {"$ref": "#/components/schemas/Control"}},
    }},
    "Command": {"type": "object", "required": ["code"], "properties": {
        "code": {"type": "string"},
        "value": {"description": "See Values in the docs page."},
        "brightness": {"type": "number", "description": "Dimmable lights only, 0 to 100."},
    }},
    "Area": {"type": "object", "properties": {
        "id": {"type": "string"}, "name": {"type": "string"}, "devices": {"type": "integer"},
        "online": {"type": "integer"}, "offline": {"type": "integer"},
        "on": {"type": "integer", "description": "Devices with a switch that is on."},
        "switchable": {"type": "integer", "description": "Devices that can be turned on or off."}}},
    "AreaDetail": {"type": "object", "properties": {
        "id": {"type": "string"}, "name": {"type": "string"}, "online": {"type": "integer"}, "offline": {"type": "integer"},
        "on": {"type": "integer"}, "switchable": {"type": "integer"},
        "devices": {"type": "array", "items": {"$ref": "#/components/schemas/Device"}}}},
    "Automation": {"type": "object", "properties": {
        "entity_id": {"type": "string"}, "id": {"type": ["string", "null"]}, "name": {"type": "string"},
        "enabled": {"type": "boolean"}, "last_triggered": {"type": ["string", "null"]},
        "mode": {"type": ["string", "null"]}, "running": {"type": "integer"},
        "ours": {"type": "boolean", "description": "Made in Device Hub, so it can be edited and deleted here."},
        "spec": {"oneOf": [{"$ref": "#/components/schemas/AutomationSpec"}, {"type": "null"}]}}},
    "AutomationSpec": {"type": "object", "required": ["name", "trigger", "actions"], "properties": {
        "name": {"type": "string"},
        "trigger": {"type": "object", "description": "time: at HH:MM. sun: event sunrise or sunset, offset_minutes. "
                    "device: device_id, code, and to (a value) or above/below (a number), optional for_seconds.",
                    "properties": {"type": {"type": "string", "enum": ["time", "sun", "device"]},
                                   "at": {"type": "string"}, "event": {"type": "string", "enum": ["sunrise", "sunset"]},
                                   "offset_minutes": {"type": "integer"}, "device_id": {"type": "string"},
                                   "code": {"type": "string"}, "to": {}, "above": {"type": "number"},
                                   "below": {"type": "number"}, "for_seconds": {"type": "integer"}}},
        "days": {"type": "array", "items": {"type": "string", "enum": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]},
                 "description": "Only on these days. Empty means every day."},
        "actions": {"type": "array", "items": {"type": "object", "description": "A device command (device_id, code, value, "
                    "brightness), an area (area_id, action on or off), or a wait (delay_seconds)."}}}},
    "SceneAction": {"type": "object", "required": ["device_id", "code"], "properties": {
        "device_id": {"type": "string"}, "code": {"type": "string"}, "value": {}}},
    "Scene": {"type": "object", "properties": {
        "id": {"type": "string"}, "name": {"type": "string"},
        "actions": {"type": "array", "items": {"$ref": "#/components/schemas/SceneAction"}}}},
    "Activity": {"type": "object", "properties": {
        "id": {"type": "string"},
        "at": {"type": "number", "description": "Unix time in seconds."},
        "kind": {"type": "string", "enum": ["command", "scene", "remove", "change", "offline", "online"]},
        "text": {"type": "string"},
        "device_id": {"type": ["string", "null"]},
        "device": {"type": ["string", "null"]},
        "ok": {"type": "boolean"},
        "via": {"type": "string", "description": "panel, watcher, or API key and its name."},
    }},
    "Ok": {"type": "object", "properties": {"ok": {"type": "boolean"}}},
    "Error": {"type": "object", "properties": {"error": {"type": "string"}}},
}


TAGS = {"devices": "Devices", "areas": "Areas", "automations": "Automations", "scenes": "Scenes",
        "activity": "Activity and live updates", "events": "Activity and live updates"}


def endpoints():
    """The short list the panel shows, and GET /api/v1 returns as JSON."""
    return [
        {
            "method": op["method"],
            "path": "/api/v1" + op["path"],
            "about": op["about"],
            "scope": op["scope"],
            **({"body": op["body"]} if op.get("body") else {}),
        }
        for op in OPERATIONS
    ]


def spec(base_url, version):
    paths = {}
    for op in OPERATIONS:
        responses = {
            "401": {"description": "No key, an unknown key, or an expired key.", "content": _json({"$ref": "#/components/schemas/Error"})},
            "403": {"description": f"The key cannot {op['scope']}.", "content": _json({"$ref": "#/components/schemas/Error"})},
            "400": {"description": "The request could not be done. error says why.", "content": _json({"$ref": "#/components/schemas/Error"})},
        }
        if op.get("stream"):
            responses["200"] = {"description": "An event stream that stays open.", "content": {"text/event-stream": {"schema": {"type": "string"}}}}
        else:
            responses["200"] = {"description": "OK", "content": _json(op["response"])}
        entry = {
            "operationId": op["id"],
            "tags": [TAGS[op["path"].split("/")[1]]],
            "summary": op["about"],
            "description": f"Needs a {op['scope']} key.",
            "parameters": op.get("params", []),
            "responses": responses,
        }
        if op.get("body_schema"):
            entry["requestBody"] = {"required": True, "content": {"application/json": {"schema": op["body_schema"], "example": op["body"]}}}
        paths.setdefault(op["path"], {})[op["method"].lower()] = entry
    return {
        "openapi": "3.1.0",
        "info": {
            "title": "Device Hub API",
            "version": version,
            "description": "List and control the devices Device Hub knows: Tuya, Tuya Local, Zigbee2MQTT, ZHA and Sonoff. "
                           "Create keys in Device Hub, Settings, Share. A read key sees, a control key also sends "
                           "commands and runs scenes, a manage key also renames, moves and removes devices.",
        },
        "servers": [
            {"url": "/api/v1", "description": "The Device Hub serving this document"},
            {"url": base_url.rstrip("/") + "/api/v1", "description": "Device Hub on your network"},
        ],
        "security": [{"key": []}],
        "paths": paths,
        "components": {
            "securitySchemes": {"key": {"type": "http", "scheme": "bearer", "description": "A key that starts with dh_."}},
            "schemas": SCHEMAS,
        },
    }


def page(base_url, version):
    """The docs page a browser gets at /api/v1."""
    base = html.escape(base_url.rstrip("/") + "/api/v1")
    mcp = html.escape(base_url.rstrip("/") + "/mcp")
    sections = []
    for op in OPERATIONS:
        path = html.escape(op["path"])
        example = f'curl -H "Authorization: Bearer dh_your_key" {base}{path.replace("{id}", "ID")}'
        if op["method"] != "GET":
            example = f'curl -X {op["method"]} -H "Authorization: Bearer dh_your_key" '
            if op.get("body"):
                example += f"-H 'Content-Type: application/json' -d '{html.escape(json.dumps(op['body']))}' "
            example += f'{base}{path.replace("{id}", "ID")}'
        if op.get("stream"):
            example = f'curl -N "{base}/events?key=dh_your_key"'
        params = "".join(
            f"<li><code>{html.escape(p['name'])}</code> {html.escape(p.get('description', ''))}</li>"
            for p in op.get("params", []) if p["in"] == "query"
        )
        sections.append(
            f'<section><h2><span class="m {op["method"].lower()}">{op["method"]}</span> <code>{path}</code>'
            f'<span class="s">{op["scope"]}</span></h2><p>{html.escape(op["about"])}</p>'
            f'{"<ul>" + params + "</ul>" if params else ""}<pre>{example}</pre></section>'
        )
    values = "".join(f"<li><b>{html.escape(k)}</b> {html.escape(v)}</li>" for k, v in VALUES.items())
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Device Hub API</title>
<style>
body {{ margin: 0; background: #111; color: #eee; font: 16px/1.55 ui-sans-serif, system-ui, sans-serif; }}
main {{ max-width: 820px; margin: 0 auto; padding: 32px 20px 64px; }}
code, pre {{ font-family: ui-monospace, monospace; font-size: 14px; }}
code {{ background: #1c1c1c; padding: 0 4px; border-radius: 3px; }}
pre {{ background: #1c1c1c; padding: 12px; overflow: auto; border-radius: 6px; white-space: pre-wrap; word-break: break-all; }}
h1 {{ font-size: 28px; letter-spacing: 0.04em; margin-bottom: 4px; }}
h2 {{ font-size: 17px; display: flex; flex-wrap: wrap; align-items: center; gap: 8px; margin: 0 0 4px; }}
section {{ border-top: 1px solid #2a2a2a; padding: 18px 0 6px; }}
.m {{ font: 600 12px ui-monospace, monospace; padding: 2px 6px; border-radius: 4px; background: #2a2a2a; }}
.get {{ color: #7fd3a0; }} .post {{ color: #8ec8ff; }} .patch {{ color: #f0c674; }} .delete {{ color: #ff8f8f; }}
.s {{ margin-left: auto; font-size: 12px; color: #aaa; border: 1px solid #333; border-radius: 99px; padding: 1px 8px; }}
.muted {{ color: #aaa; }}
a {{ color: #8ec8ff; }}
table {{ border-collapse: collapse; }} td {{ padding: 4px 12px 4px 0; vertical-align: top; }}
</style></head><body><main>
<h1>Device Hub API</h1>
<p class="muted">Version {html.escape(version)}. Machine-readable: <a href="{base}/openapi.json">openapi.json</a>.</p>
<p>Base URL <code>{base}</code>. Create a key in Device Hub, Settings, Share, and send
<code>Authorization: Bearer dh_…</code> on every request except this page and openapi.json.</p>
<table>
<tr><td><b>read</b></td><td>See devices, areas, scenes, activity and live updates.</td></tr>
<tr><td><b>control</b></td><td>Also send commands and save, run or delete scenes.</td></tr>
<tr><td><b>manage</b></td><td>Also rename devices, move them between areas and remove them.</td></tr>
</table>
<p><a href="{base}/swagger">Try it in Swagger UI</a> · <a href="#mcp">Use it from Claude (MCP)</a></p>
<p class="muted">Errors come back as <code>{{"error": "…"}}</code> with 400, 401 (no key, unknown or expired key) or 403 (the key cannot do that).</p>
{''.join(sections)}
<section><h2>Live updates</h2>
<p>Each event has a type and JSON data:</p>
<pre>event: state
data: {{"device_id": "sonoff:1002ba764f", "code": "switch.kitchen_plug", "entity_id": "switch.kitchen_plug", "state": "on", "attributes": {{}}, "at": 1793212000}}

event: device
data: {{"device_id": "tuya:bf53…", "name": "Living dimmer", "online": false, "at": 1793212000}}</pre>
<pre>const events = new EventSource("{base}/events?key=dh_your_key")
events.addEventListener("state", (e) =&gt; console.log(JSON.parse(e.data)))</pre>
<p class="muted">Tuya devices that only work through the cloud do not send state events. Read them with /devices.</p>
</section>
<section id="mcp"><h2>MCP for assistants</h2>
<p>Device Hub is also an MCP server, so Claude or another assistant can list and control devices, areas, scenes and
automations. It takes the same keys: a read key gets the tools that look, a control key the ones that switch, a manage
key the ones that rename and move.</p>
<pre>claude mcp add --transport http device-hub {mcp} --header "Authorization: Bearer dh_your_key"</pre>
<p class="muted">Other clients: Streamable HTTP at <code>{mcp}</code> with the same Authorization header.</p>
</section>
<section><h2>Values</h2><ul>{values}</ul></section>
</main></body></html>"""


def _json(schema):
    return {"application/json": {"schema": schema}}


SWAGGER_UI = "5.17.14"


def swagger():
    """Swagger UI for openapi.json. Its script and style come from a CDN, so the browser needs internet."""
    spec = "openapi.json"  # next to this page, so it loads wherever the page was opened from
    cdn = f"https://unpkg.com/swagger-ui-dist@{SWAGGER_UI}"
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Device Hub API · Swagger</title>
<link rel="stylesheet" href="{cdn}/swagger-ui.css">
<style>body {{ margin: 0; }} .topbar {{ display: none; }}</style>
</head><body>
<div id="ui"><p style="font: 15px system-ui; padding: 24px">Loading Swagger UI. It needs internet to load.
The spec itself is at <a href="{spec}">openapi.json</a>.</p></div>
<script src="{cdn}/swagger-ui-bundle.js" crossorigin></script>
<script>
window.ui = SwaggerUIBundle({{ url: "{spec}", dom_id: "#ui", deepLinking: true, persistAuthorization: true,
  tryItOutEnabled: true, displayRequestDuration: true }})
</script>
</body></html>"""
