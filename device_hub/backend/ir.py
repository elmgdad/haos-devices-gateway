"""Infrared remotes: Tuya remotes on a blaster, and buttons learned from a physical remote.

Tuya remotes (TV, AC...) are sent through the Tuya cloud's standard commands.
Learned buttons are replayed on the blaster over the LAN with its local key
(data point 201), so they keep working without the cloud.
"""

import asyncio
import json
import logging
import re
import secrets
import threading
import time

import tinytuya

from . import store
from .tuya_bridge import BRIDGE_TOPIC, DISCOVERY, ORIGIN, PREFIX

_LOGGER = logging.getLogger(__name__)

STORE = "ir"
BLASTER_CATEGORIES = {"wnykq", "dgnzk"}
DP_SEND = "201"
DP_LEARNED = "202"
LEARN_SECONDS = 20
AC_POLL = 600
AC_MODES = ["cool", "heat", "auto", "fan_only", "dry"]
AC_FANS = ["auto", "low", "medium", "high"]
AC_MIN, AC_MAX = 16, 30
KEY_NAMES = {
    "Power": "Power",
    "Volume+": "Volume up",
    "Volume-": "Volume down",
    "Channel+": "Channel up",
    "Channel-": "Channel down",
    "Up": "Up",
    "Down": "Down",
    "Left": "Left",
    "Right": "Right",
    "OK": "OK",
    "Back": "Back",
    "Home": "Home",
    "Menu": "Menu",
    "-/--": "Digits",
}
PRIMARY_KEYS = ["Power", "Volume+", "Volume-", "Channel+", "Channel-", "Up", "Down", "Left", "Right", "OK", "Back", "Home", "Menu"]


def _real_code(value):
    return isinstance(value, str) and len(value) > 24


def _slug(text):
    text = str(text).lower().replace("+", " plus ").replace("-", " minus ")
    return re.sub(r"[^a-z0-9]+", "_", text).strip("_") or "key"


def _old_slug(text):
    return re.sub(r"[^a-z0-9]+", "_", str(text).lower()).strip("_") or "key"


class IrRemotes:
    def __init__(self, hub):
        self.hub = hub
        saved = store.load(STORE) or {}
        self.remotes = saved.get("remotes") or {}
        self.buttons = saved.get("buttons") or {}
        self.blasters = saved.get("blasters") or {}
        self.ac = saved.get("ac") or {}
        self._parents = {}
        self._parents_at = 0.0
        self._keys = {}
        self._learning = set()
        self._send_locks = {}
        self._announced = {}
        self._lock = threading.RLock()
        self._task = None

    # Lifecycle

    def attach(self):
        self.hub.local.extensions.append(self)

    async def start(self):
        self._task = asyncio.create_task(self._forever())

    def stop(self):
        if self._task:
            self._task.cancel()

    async def _forever(self):
        await asyncio.sleep(5)
        while True:
            try:
                await self.refresh()
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Refreshing IR remotes failed")
            await asyncio.sleep(60)

    async def refresh(self):
        await asyncio.to_thread(self._load_parents)
        for remote in self._cloud_remotes():
            if remote["kind"] == "ac":
                if time.time() - (self.ac.get(remote["id"]) or {}).get("read_at", 0) > AC_POLL:
                    await asyncio.to_thread(self._read_ac, remote["id"])
            else:
                await asyncio.to_thread(self._cloud_keys, remote["id"])
        self.announce()

    # What exists

    def list(self):
        items = [self._view(remote) for remote in self._all()]
        items.sort(key=lambda item: item["name"].lower())
        return {"blasters": self._blaster_views(), "remotes": items}

    def list_one(self, remote_id):
        return self._view(self.get(remote_id))

    def knows(self, remote_id):
        return any(remote["id"] == remote_id for remote in self._all())

    def get(self, remote_id):
        remote = next((item for item in self._all() if item["id"] == remote_id), None)
        if not remote:
            raise RuntimeError("That remote is gone.")
        return remote

    def _all(self):
        return self._cloud_remotes() + [
            {**remote, "source": "learned", "kind": "keys"} for remote in self.remotes.values()
        ]

    def _cloud_remotes(self):
        if not self.hub.cloud.connected:
            return []
        remotes = []
        for device in self.hub.cloud.devices:
            category = device.get("category") or ""
            if not category.startswith("infrared_"):
                continue
            remotes.append({
                "id": device["id"],
                "name": device["name"],
                "blaster_id": device.get("gateway_id") or self._parents.get(device["id"]) or "",
                "source": "cloud",
                "kind": "ac" if category == "infrared_ac" else "keys",
                "category": category,
            })
        return remotes

    def _blaster_ids(self):
        ids = {d["id"] for d in self.hub.cloud.devices if d.get("category") in BLASTER_CATEGORIES} if self.hub.cloud.connected else set()
        ids.update(self.blasters)
        return ids

    def _blaster_views(self):
        views = []
        for blaster_id in self._blaster_ids():
            device = self.hub.cloud.by_id(blaster_id) or {}
            where = self._where(blaster_id)
            views.append({
                "id": blaster_id,
                "name": device.get("name") or self.blasters.get(blaster_id, {}).get("name") or blaster_id,
                "ip": where.get("ip"),
                "linked": self.hub.local.is_live(blaster_id),
                "learning": blaster_id in self._learning,
            })
        views.sort(key=lambda item: item["name"].lower())
        return views

    def _view(self, remote):
        blaster = self.hub.cloud.by_id(remote["blaster_id"]) or {}
        learned = self.buttons.get(remote["id"]) or []
        view = {
            "id": remote["id"],
            "name": remote["name"],
            "kind": remote["kind"],
            "source": remote["source"],
            "blaster_id": remote["blaster_id"],
            "blaster_name": blaster.get("name") or self.blasters.get(remote["blaster_id"], {}).get("name") or "",
            "keys": self._keys_of(remote),
            "learned": [{"name": item["name"]} for item in learned],
            "ac": None,
        }
        if remote["kind"] == "ac":
            view["ac"] = {**self._ac_state(remote["id"]), "modes": AC_MODES, "fans": AC_FANS, "min": AC_MIN, "max": AC_MAX}
        return view

    def _keys_of(self, remote):
        keys = []
        learned = {item["name"] for item in self.buttons.get(remote["id"]) or []}
        if remote["source"] == "cloud" and remote["kind"] == "keys":
            for code in self._keys.get(remote["id"]) or []:
                keys.append({
                    "code": code,
                    "name": KEY_NAMES.get(code, code),
                    "primary": code in PRIMARY_KEYS,
                    "local": code in learned,
                })
            order = {code: index for index, code in enumerate(PRIMARY_KEYS)}
            keys.sort(key=lambda key: (not key["primary"], order.get(key["code"], 99), key["code"]))
        cloud_codes = {key["code"] for key in keys}
        for item in self.buttons.get(remote["id"]) or []:
            if item["name"] not in cloud_codes:
                keys.append({"code": item["name"], "name": item["name"], "primary": True, "local": True})
        return keys

    # Remote changes

    def create(self, name, blaster_id):
        name = (name or "").strip()
        if not name:
            raise RuntimeError("Name the remote, for example Bedroom TV.")
        if blaster_id not in self._blaster_ids():
            raise RuntimeError("Pick the IR blaster this remote points at.")
        remote_id = "ir_" + secrets.token_hex(4)
        with self._lock:
            self.remotes[remote_id] = {"id": remote_id, "name": name[:60], "blaster_id": blaster_id}
            self._persist()
        self.announce()
        return self._view(self.get(remote_id))

    def delete(self, remote_id):
        with self._lock:
            removed = self.remotes.pop(remote_id, None)
            buttons = self.buttons.pop(remote_id, None)
            self._persist()
        if not removed and buttons is None:
            raise RuntimeError("There is nothing learned for this remote.")
        self.announce()
        return {"ok": True}

    def forget_button(self, remote_id, name):
        with self._lock:
            items = self.buttons.get(remote_id) or []
            kept = [item for item in items if item["name"] != name]
            if len(kept) == len(items):
                raise RuntimeError("That button is not learned.")
            self.buttons[remote_id] = kept
            self._persist()
        self.announce()
        return self._view(self.get(remote_id))

    async def learn(self, remote_id, name):
        remote = self.get(remote_id)
        name = (name or "").strip()
        if not name:
            raise RuntimeError("Name the button, for example Power.")
        blaster_id = remote["blaster_id"]
        if not blaster_id:
            raise RuntimeError("Device Hub does not know which blaster this remote uses.")
        if blaster_id in self._learning:
            raise RuntimeError("That blaster is already listening for a button.")
        self._learning.add(blaster_id)
        try:
            code = await asyncio.to_thread(self._learn, blaster_id)
        finally:
            self._learning.discard(blaster_id)
        if not code:
            raise RuntimeError(f"No button press reached the blaster in {LEARN_SECONDS} seconds. Point the remote at it and try again.")
        with self._lock:
            items = [item for item in self.buttons.get(remote_id) or [] if item["name"] != name]
            items.append({"name": name[:40], "code": code, "learned_at": time.time()})
            self.buttons[remote_id] = items
            self._persist()
        self.announce()
        return self._view(self.get(remote_id))

    # Sending

    async def command(self, remote_id, code, value=None):
        remote = self.get(remote_id)
        if remote["kind"] == "ac" and code in ("power", "mode", "temp", "fan"):
            await asyncio.to_thread(self._set_ac, remote, code, value)
        else:
            await asyncio.to_thread(self._press, remote, code)
        return self._view(self.get(remote_id))

    def _press(self, remote, code):
        learned = next((item for item in self.buttons.get(remote["id"]) or [] if item["name"] == code), None)
        if learned:
            self._send_code(remote["blaster_id"], learned["code"])
            return
        if remote["source"] != "cloud":
            raise RuntimeError(f"{code} is not learned on this remote.")
        if code not in (self._keys.get(remote["id"]) or self._cloud_keys(remote["id"])):
            raise RuntimeError(f"{code} is not a key on {remote['name']}.")
        self._cloud_send(remote["id"], [{"code": code, "value": code}])

    def _set_ac(self, remote, field, value):
        state = self._ac_state(remote["id"])
        commands = []
        if field == "power":
            on = value in (True, 1, "1", "true", "on", "ON")
            commands.append({"code": "PowerOn" if on else "PowerOff", "value": "PowerOn" if on else "PowerOff"})
            state["power"] = on
        elif field == "mode":
            if value == "off":
                commands.append({"code": "PowerOff", "value": "PowerOff"})
                state["power"] = False
            else:
                if value not in AC_MODES:
                    raise RuntimeError(f"Mode must be one of {', '.join(AC_MODES)} or off.")
                if not state.get("power"):
                    commands.append({"code": "PowerOn", "value": "PowerOn"})
                commands.append({"code": "M", "value": AC_MODES.index(value)})
                state.update(power=True, mode=value)
        elif field == "temp":
            try:
                temp = int(round(float(value)))
            except (TypeError, ValueError) as exc:
                raise RuntimeError(f"Temperature must be a number from {AC_MIN} to {AC_MAX}.") from exc
            if not AC_MIN <= temp <= AC_MAX:
                raise RuntimeError(f"Temperature must be from {AC_MIN} to {AC_MAX}.")
            commands.append({"code": "T", "value": temp})
            state["temp"] = temp
        elif field == "fan":
            if value not in AC_FANS:
                raise RuntimeError(f"Fan must be one of {', '.join(AC_FANS)}.")
            commands.append({"code": "F", "value": AC_FANS.index(value)})
            state["fan"] = value
        for command in commands:
            self._cloud_send(remote["id"], [command])
        with self._lock:
            self.ac[remote["id"]] = {**state, "at": time.time(), "read_at": (self.ac.get(remote["id"]) or {}).get("read_at", 0)}
            self._persist()
        self._publish_ac(remote["id"])

    def _cloud_send(self, device_id, commands):
        cloud = self.hub.cloud._cloud
        if cloud is None:
            raise RuntimeError("Sign in to the Tuya cloud project first. Tuya remotes are sent through it.")
        result = cloud.cloudrequest(f"/v1.0/iot-03/devices/{device_id}/commands", action="POST", post={"commands": commands})
        if not isinstance(result, dict) or not result.get("success"):
            message = result.get("msg") if isinstance(result, dict) else None
            raise RuntimeError(str(message or "Tuya did not accept the command."))

    def _send_code(self, blaster_id, code):
        payload = json.dumps({"control": "send_ir", "head": "", "key1": "1" + code, "type": 0, "delay": 300})
        link = self.hub.local.links.get(blaster_id)
        if link and link.state == "online":
            link.send({DP_SEND: payload})
            return
        with self._send_lock(blaster_id):
            device = self._open(blaster_id)
            try:
                device.set_value(int(DP_SEND), payload, nowait=True)
                time.sleep(0.3)
            finally:
                device.close()

    def _learn(self, blaster_id):
        study = json.dumps({"control": "study"})
        leave = json.dumps({"control": "study_exit"})
        link = self.hub.local.links.get(blaster_id)
        if link and link.state == "online":
            link.send({DP_SEND: leave})
            link.send({DP_SEND: study})
            try:
                return link.wait_for_dp(DP_LEARNED, _real_code, LEARN_SECONDS)
            finally:
                link.send({DP_SEND: leave})
        with self._send_lock(blaster_id):
            device = self._open(blaster_id)
            try:
                device.set_value(int(DP_SEND), leave, nowait=True)
                device.set_value(int(DP_SEND), study, nowait=True)
                device.set_socketTimeout(1)
                deadline = time.monotonic() + LEARN_SECONDS
                while time.monotonic() < deadline:
                    reply = device.receive()
                    dps = reply.get("dps") if isinstance(reply, dict) else None
                    value = (dps or {}).get(DP_LEARNED)
                    if _real_code(value):
                        return value
                return None
            finally:
                try:
                    device.set_value(int(DP_SEND), leave, nowait=True)
                finally:
                    device.close()

    def _send_lock(self, blaster_id):
        with self._lock:
            return self._send_locks.setdefault(blaster_id, threading.Lock())

    def _where(self, blaster_id):
        saved = self.blasters.get(blaster_id) or {}
        seen = self.hub.scanner.tuya_ip(blaster_id) or {}
        profile = self.hub.local.profiles.get(blaster_id) or {}
        device = self.hub.cloud.by_id(blaster_id) or {}
        return {
            "ip": seen.get("ip") or profile.get("ip") or saved.get("ip"),
            "version": str(seen.get("version") or profile.get("version") or saved.get("version") or "3.5"),
            "local_key": device.get("local_key") or profile.get("local_key") or saved.get("local_key"),
            "name": device.get("name") or saved.get("name"),
        }

    def _open(self, blaster_id):
        where = self._where(blaster_id)
        if not where["ip"]:
            raise RuntimeError("The blaster has no LAN address yet. Run Find on network on the Tuya tab.")
        if not where["local_key"]:
            raise RuntimeError("Device Hub has no local key for this blaster.")
        with self._lock:
            if self.blasters.get(blaster_id) != where:
                self.blasters[blaster_id] = where
                self._persist()
        return tinytuya.Device(
            blaster_id, where["ip"], where["local_key"], version=float(where["version"]),
            persist=True, connection_timeout=5, connection_retry_limit=1,
        )

    # Tuya cloud reads

    def _load_parents(self):
        if time.monotonic() - self._parents_at < 600 or self.hub.cloud._cloud is None:
            return
        parents = {}
        for blaster_id in self._blaster_ids():
            result = self.hub.cloud._cloud.cloudrequest(f"/v1.0/devices/{blaster_id}/sub-devices")
            for child in (result or {}).get("result") or [] if isinstance(result, dict) else []:
                parents[child.get("id")] = blaster_id
        self._parents = parents
        self._parents_at = time.monotonic()

    def _cloud_keys(self, remote_id):
        if remote_id in self._keys:
            return self._keys[remote_id]
        cloud = self.hub.cloud._cloud
        if cloud is None:
            return []
        result = cloud.cloudrequest(f"/v1.0/iot-03/devices/{remote_id}/functions")
        functions = ((result or {}).get("result") or {}).get("functions") or [] if isinstance(result, dict) else []
        keys = []
        for item in functions:
            if item.get("type") == "STRING" and item.get("code") and item["code"] not in keys:
                keys.append(item["code"])
        self._keys[remote_id] = keys
        return keys

    def _read_ac(self, remote_id):
        cloud = self.hub.cloud._cloud
        if cloud is None:
            return
        result = cloud.cloudrequest(f"/v1.0/devices/{remote_id}/status")
        items = (result or {}).get("result") if isinstance(result, dict) else None
        if not isinstance(items, list):
            return
        raw = {item.get("code"): item.get("value") for item in items}
        state = dict(self._ac_state(remote_id))
        if "power" in raw:
            state["power"] = str(raw["power"]) in ("1", "true", "True")
        if str(raw.get("mode", "")).isdigit() and int(raw["mode"]) < len(AC_MODES):
            state["mode"] = AC_MODES[int(raw["mode"])]
        if str(raw.get("temp", "")).isdigit():
            state["temp"] = int(raw["temp"])
        if str(raw.get("wind", "")).isdigit() and int(raw["wind"]) < len(AC_FANS):
            state["fan"] = AC_FANS[int(raw["wind"])]
        with self._lock:
            self.ac[remote_id] = {**state, "at": time.time(), "read_at": time.time()}
            self._persist()
        self._publish_ac(remote_id)

    def _ac_state(self, remote_id):
        state = self.ac.get(remote_id) or {}
        return {
            "power": bool(state.get("power")),
            "mode": state.get("mode") or "cool",
            "temp": state.get("temp") or 24,
            "fan": state.get("fan") or "auto",
        }

    # Home Assistant over MQTT

    def on_mqtt_connect(self):
        self.hub.local.mqtt.subscribe(f"{PREFIX}/ir/+/press")
        self.hub.local.mqtt.subscribe(f"{PREFIX}/ir/+/ac/+/set")
        self._announced = {}
        self.announce()

    def on_mqtt_message(self, topic, payload):
        parts = topic.split("/")
        if len(parts) < 4 or parts[0] != PREFIX or parts[1] != "ir":
            return False
        remote_id = parts[2]
        if parts[3] == "press":
            code, value = payload.strip(), None
        elif len(parts) == 6 and parts[3] == "ac" and parts[5] == "set":
            code, value = parts[4], payload.strip()
        else:
            return True
        threading.Thread(target=self._from_mqtt, args=(remote_id, code, value), daemon=True).start()
        return True

    def _from_mqtt(self, remote_id, code, value):
        try:
            remote = self.get(remote_id)
            if remote["kind"] == "ac" and code in ("power", "mode", "temp", "fan"):
                self._set_ac(remote, code, value)
            else:
                self._press(remote, code)
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("IR command %s on %s failed: %s", code, remote_id, exc)

    def announce(self):
        mqtt = self.hub.local.mqtt
        if not mqtt.connected:
            return
        fresh = {}
        saved = set(self.hub.local.profiles)
        for remote in self._all():
            for topic, config in _discovery(remote, self._keys_of(remote), remote.get("blaster_id") in saved):
                fresh[topic] = json.dumps(config)
        stale = set(self._announced) - set(fresh)
        if not self._announced:
            for remote in self._all():
                for key in self._keys_of(remote):
                    if _old_slug(key["code"]) != _slug(key["code"]):
                        stale.add(f"{DISCOVERY}/button/devicehub_ir_{remote['id']}/{_old_slug(key['code'])}/config")
            stale -= set(fresh)
        for topic in stale:
            mqtt.publish(topic, "", retain=True)
        for topic, payload in fresh.items():
            if self._announced.get(topic) != payload:
                mqtt.publish(topic, payload, retain=True)
        self._announced = fresh
        for remote in self._all():
            if remote["kind"] == "ac":
                self._publish_ac(remote["id"])

    def _publish_ac(self, remote_id):
        state = self._ac_state(remote_id)
        payload = {
            "mode": state["mode"] if state["power"] else "off",
            "temp": state["temp"],
            "fan": state["fan"],
            "power": "ON" if state["power"] else "OFF",
        }
        self.hub.local.mqtt.publish(f"{PREFIX}/ir/{remote_id}/state", json.dumps(payload), retain=True)

    def _persist(self):
        store.save(STORE, {"remotes": self.remotes, "buttons": self.buttons, "blasters": self.blasters, "ac": self.ac})


def _discovery(remote, keys, blaster_published):
    remote_id = remote["id"]
    device = {
        "identifiers": [f"devicehub_ir_{remote_id}"],
        "name": remote["name"],
        "manufacturer": "Tuya" if remote["source"] == "cloud" else "Learned remote",
        "model": "IR air conditioner" if remote["kind"] == "ac" else "IR remote",
    }
    if blaster_published:
        device["via_device"] = f"devicehub_tuya_{remote['blaster_id']}"
    base = {
        "availability": [{"topic": BRIDGE_TOPIC}],
        "device": device,
        "origin": ORIGIN,
    }
    entries = []
    if remote["kind"] == "ac":
        state = f"{PREFIX}/ir/{remote_id}/state"
        entries.append((f"{DISCOVERY}/climate/devicehub_ir_{remote_id}/ac/config", {
            **base,
            "name": None,
            "unique_id": f"devicehub_ir_{remote_id}_ac",
            "modes": ["off", *AC_MODES],
            "mode_command_topic": f"{PREFIX}/ir/{remote_id}/ac/mode/set",
            "mode_state_topic": state,
            "mode_state_template": "{{ value_json.mode }}",
            "temperature_command_topic": f"{PREFIX}/ir/{remote_id}/ac/temp/set",
            "temperature_state_topic": state,
            "temperature_state_template": "{{ value_json.temp }}",
            "fan_modes": AC_FANS,
            "fan_mode_command_topic": f"{PREFIX}/ir/{remote_id}/ac/fan/set",
            "fan_mode_state_topic": state,
            "fan_mode_state_template": "{{ value_json.fan }}",
            "min_temp": AC_MIN,
            "max_temp": AC_MAX,
            "temp_step": 1,
            "precision": 1.0,
            "temperature_unit": "C",
        }))
    for key in keys:
        config = {
            **base,
            "name": key["name"],
            "unique_id": f"devicehub_ir_{remote_id}_{_slug(key['code'])}",
            "command_topic": f"{PREFIX}/ir/{remote_id}/press",
            "payload_press": key["code"],
        }
        if not key["primary"]:
            config["entity_category"] = "config"
        entries.append((f"{DISCOVERY}/button/devicehub_ir_{remote_id}/{_slug(key['code'])}/config", config))
    return entries
