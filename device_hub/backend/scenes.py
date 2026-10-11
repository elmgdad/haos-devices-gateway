"""Scenes: one named list of commands that can span Sonoff, Zigbee and Tuya."""

import secrets
import time

from . import store

STORE = "scenes"


class Scenes:
    def __init__(self, hub):
        self.hub = hub
        saved = store.load(STORE) or {}
        self.scenes = saved.get("scenes") or []
        self.values = saved.get("values") or {}

    def remembered(self, device_id, code):
        return (self.values.get(device_id) or {}).get(code)

    def remember(self, device_id, code, value):
        self.values.setdefault(device_id, {})[code] = value
        self._persist()

    def reset(self):
        self.scenes = []
        self.values = {}
        store.remove(STORE)

    async def visible(self, token):
        allowed = await self._allowed(token)
        shown = []
        for scene in self.scenes:
            actions = [action for action in scene["actions"] if action["device_id"] in allowed]
            if actions:
                shown.append({**scene, "actions": actions})
        return shown

    async def create(self, token, name, actions):
        name = (name or "").strip()
        if not name:
            raise RuntimeError("Name this scene.")
        if not actions:
            raise RuntimeError("Add at least one device to this scene.")
        allowed = await self._allowed(token)
        cleaned = []
        for action in actions:
            device_id = str((action or {}).get("device_id") or "").strip()
            code = str((action or {}).get("code") or "").strip()
            device = allowed.get(device_id)
            if not device:
                raise RuntimeError("This key cannot use one of the devices in the scene.")
            control = next((item for item in device["controls"] if item["code"] == code), None)
            if not control or not control["writable"]:
                raise RuntimeError(f"{code or 'That control'} cannot be part of a scene.")
            cleaned.append({
                "device_id": device["id"],
                "device_name": device["name"],
                "source": device.get("source") or "",
                "code": control["code"],
                "name": control["name"],
                "value": (action or {}).get("value"),
            })
        scene = {
            "id": secrets.token_hex(4),
            "name": name[:60],
            "actions": cleaned,
            "created_at": time.time(),
        }
        self.scenes.append(scene)
        self._persist()
        return scene

    def delete(self, scene_id):
        before = len(self.scenes)
        self.scenes = [scene for scene in self.scenes if scene["id"] != scene_id]
        if len(self.scenes) == before:
            raise RuntimeError("That scene is already gone.")
        self._persist()
        return {"ok": True}

    async def run(self, token, scene_id):
        scene = next((item for item in await self.visible(token) if item["id"] == scene_id), None)
        if not scene:
            raise RuntimeError("That scene is not available with this key.")
        results = []
        for action in scene["actions"]:
            try:
                await self.hub.share.command(
                    token, action["device_id"], [{"code": action["code"], "value": action["value"]}]
                )
                results.append({"device_id": action["device_id"], "code": action["code"], "ok": True})
            except Exception as exc:  # noqa: BLE001
                results.append({
                    "device_id": action["device_id"],
                    "code": action["code"],
                    "ok": False,
                    "error": str(exc) or "That device did not accept the command.",
                })
        return {"id": scene["id"], "name": scene["name"], "actions": scene["actions"], "results": results}

    async def _allowed(self, token):
        return {item["id"]: item for item in await self.hub.share.devices_for(token)}

    def _persist(self):
        store.save(STORE, {"scenes": self.scenes, "values": self.values})
