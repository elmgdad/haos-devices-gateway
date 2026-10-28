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
NOT_SUBSCRIBED = 28841101
SERVICE_OFF = (
    "Tuya's IR service is off for this cloud project, so Tuya drops remote keys without sending them. "
    "On iot.tuya.com open Cloud, your project, Service API, Go to Authorize, and add IR Control Hub Open Service. "
    "Or learn the button: Device Hub then sends it on your network."
)
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
    "mute": "Mute",
    "input": "Input",
}
PRIMARY_KEYS = [
    "Power", "Volume+", "Volume-", "mute", "Channel+", "Channel-", "Up", "Down", "Left", "Right", "OK",
    "Back", "Home", "Menu", "input",
]


def _real_code(value):
    return isinstance(value, str) and len(value) > 24


def _sent_ir(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return False
    return isinstance(value, dict) and value.get("control") == "send_ir" and bool(value.get("key1"))


def _learned(dp, value):
    if value is None:
        return None
    if dp == DP_SEND:
        return {"payload": json.loads(value) if isinstance(value, str) else value}
    return {"code": value}


def _pretty(text):
    text = str(text).replace("_", " ").strip()
    return text[:1].upper() + text[1:] if text else text


def _norm(text):
    return re.sub(r"[\s_]+", "", str(text or "").lower())


KEY_ALIASES = {
    "power": {"power", "poweronoff", "onoff", "switch"},
    "volume+": {"volume+", "volumeup", "vol+"},
    "volume-": {"volume-", "volumedown", "vol-"},
    "channel+": {"channel+", "channelup", "ch+"},
    "channel-": {"channel-", "channeldown", "ch-"},
    "ok": {"ok", "confirm", "enter"},
    "home": {"home", "homepage"},
    "back": {"back", "return"},
}


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
        self._hub_keys = {}
        self.service = None
        self._service_at = 0.0
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
        await asyncio.to_thread(self._check_service)
        for remote in self._cloud_remotes():
            if remote["kind"] == "ac":
                if time.time() - (self.ac.get(remote["id"]) or {}).get("read_at", 0) > AC_POLL:
                    await asyncio.to_thread(self._read_ac, remote["id"])
            elif self.service:
                try:
                    await asyncio.to_thread(self._load_hub_keys, remote)
                except RuntimeError as exc:
                    _LOGGER.warning("Could not read the keys of %s: %s", remote["name"], exc)
            else:
                await asyncio.to_thread(self._cloud_keys, remote["id"])
        self.announce()

    # What exists

    def list(self):
        items = [self._view(remote) for remote in self._all()]
        items.sort(key=lambda item: item["name"].lower())
        return {
            "blasters": self._blaster_views(),
            "remotes": items,
            "cloud_ir": self.service,
            "cloud_ir_help": SERVICE_OFF if self.service is False else None,
        }

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

    def _key_list(self, remote):
        """(code, Tuya's label) for each key: the IR service's full list once it is on, else the standard set."""
        hub = self._hub_keys.get(remote["id"])
        if hub:
            return [(item["key"], item.get("key_name")) for item in hub if item.get("key")]
        return [(code, None) for code in self._keys.get(remote["id"]) or []]

    def _keys_of(self, remote):
        keys = []
        learned = {item["name"] for item in self.buttons.get(remote["id"]) or []}
        if remote["source"] == "cloud" and remote["kind"] == "keys":
            slugs = set()
            for position, (code, label) in enumerate(self._key_list(remote)):
                if _slug(code) in slugs:
                    continue
                slugs.add(_slug(code))
                keys.append({
                    "code": code,
                    "name": KEY_NAMES.get(code) or (code if code.isdigit() else _pretty(label or code)),
                    "primary": code in PRIMARY_KEYS,
                    "local": code in learned,
                    "position": position,
                })
            order = {code: index for index, code in enumerate(PRIMARY_KEYS)}
            keys.sort(key=lambda key: (not key["primary"], order.get(key["code"], 99), key.pop("position")))
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
            raise RuntimeError(
                f"No button press reached the blaster in {LEARN_SECONDS} seconds. "
                "Point the remote at it, or press the key in Smart Life, and try again."
            )
        with self._lock:
            items = [item for item in self.buttons.get(remote_id) or [] if item["name"] != name]
            items.append({"name": name[:40], **code, "learned_at": time.time()})
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
            self._send_learned(remote["blaster_id"], learned)
            return
        if remote["source"] != "cloud":
            raise RuntimeError(f"{code} is not learned on this remote.")
        self._require_service()
        key = self._hub_key(remote, code)
        self._hub_request(
            "POST",
            f"/v2.0/infrareds/{remote['blaster_id']}/remotes/{remote['id']}/raw/command",
            {"category_id": key["category_id"], "key_id": key["key_id"], "key": key["key"]},
        )

    def _set_ac(self, remote, field, value):
        state = self._ac_state(remote["id"])
        if field == "power":
            state["power"] = value in (True, 1, "1", "true", "on", "ON")
        elif field == "mode":
            if value == "off":
                state["power"] = False
            elif value in AC_MODES:
                state.update(power=True, mode=value)
            else:
                raise RuntimeError(f"Mode must be one of {', '.join(AC_MODES)} or off.")
        elif field == "temp":
            try:
                temp = int(round(float(value)))
            except (TypeError, ValueError) as exc:
                raise RuntimeError(f"Temperature must be a number from {AC_MIN} to {AC_MAX}.") from exc
            if not AC_MIN <= temp <= AC_MAX:
                raise RuntimeError(f"Temperature must be from {AC_MIN} to {AC_MAX}.")
            state["temp"] = temp
        elif field == "fan":
            if value not in AC_FANS:
                raise RuntimeError(f"Fan must be one of {', '.join(AC_FANS)}.")
            state["fan"] = value
        self._require_service()
        self._hub_request(
            "POST",
            f"/v2.0/infrareds/{remote['blaster_id']}/air-conditioners/{remote['id']}/scenes/command",
            {
                "power": 1 if state["power"] else 0,
                "mode": AC_MODES.index(state["mode"]),
                "temp": state["temp"],
                "wind": AC_FANS.index(state["fan"]),
            },
        )
        with self._lock:
            self.ac[remote["id"]] = {**state, "at": time.time(), "read_at": (self.ac.get(remote["id"]) or {}).get("read_at", 0)}
            self._persist()
        self._publish_ac(remote["id"])

    def _hub_request(self, method, url, body=None):
        cloud = self.hub.cloud._cloud
        if cloud is None:
            raise RuntimeError("Tuya remotes are sent through a Tuya cloud project. A Smart Life sign-in does not cover them.")
        result = cloud.cloudrequest(url, action=method, post=body)
        if isinstance(result, dict) and result.get("success"):
            self.service, self._service_at = True, time.monotonic()
            return result.get("result")
        if isinstance(result, dict) and result.get("code") == NOT_SUBSCRIBED:
            self.service, self._service_at = False, time.monotonic()
            raise RuntimeError(SERVICE_OFF)
        message = result.get("msg") if isinstance(result, dict) else None
        raise RuntimeError(str(message or "Tuya did not send the IR command."))

    def _require_service(self):
        if self.service is False and time.monotonic() - self._service_at < 60:
            raise RuntimeError(SERVICE_OFF)
        if self.service is not True:
            self._check_service(force=True)
        if self.service is False:
            raise RuntimeError(SERVICE_OFF)

    def _check_service(self, force=False):
        if self.hub.cloud._cloud is None:
            return
        if not force and self.service is not None and time.monotonic() - self._service_at < 600:
            return
        blaster = next((remote["blaster_id"] for remote in self._cloud_remotes() if remote["blaster_id"]), None)
        if not blaster:
            return
        result = self.hub.cloud._cloud.cloudrequest(f"/v2.0/infrareds/{blaster}/remotes")
        if isinstance(result, dict) and result.get("success"):
            self.service = True
        elif isinstance(result, dict) and result.get("code") == NOT_SUBSCRIBED:
            self.service = False
        self._service_at = time.monotonic()

    def _load_hub_keys(self, remote):
        keys = self._hub_keys.get(remote["id"])
        if keys is None:
            result = self._hub_request("GET", f"/v2.0/infrareds/{remote['blaster_id']}/remotes/{remote['id']}/keys") or {}
            keys = [
                {**item, "category_id": item.get("category_id") or result.get("category_id")}
                for item in result.get("key_list") or []
            ]
            self._hub_keys[remote["id"]] = keys
        return keys

    def _hub_key(self, remote, code):
        keys = self._load_hub_keys(remote)
        exact = next((item for item in keys if item.get("key") == code), None)
        if exact:
            return exact
        wanted = KEY_ALIASES.get(_norm(code), {_norm(code)})
        for item in keys:
            if {_norm(item.get("key")), _norm(item.get("key_name"))} & wanted:
                return item
        raise RuntimeError(f"Tuya has no {code} key for {remote['name']}.")

    def _send_learned(self, blaster_id, item):
        if item.get("payload"):
            self._send_payload(blaster_id, json.dumps(item["payload"]))
        else:
            self._send_payload(blaster_id, json.dumps({"control": "send_ir", "head": "", "key1": "1" + item["code"], "type": 0, "delay": 300}))

    def _send_payload(self, blaster_id, payload):
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
        """A code heard from a physical remote (202), or one Smart Life just sent through the blaster (201)."""
        study = json.dumps({"control": "study"})
        leave = json.dumps({"control": "study_exit"})
        link = self.hub.local.links.get(blaster_id)
        if link and link.state == "online":
            link.send({DP_SEND: leave})
            link.send({DP_SEND: study})
            try:
                dp, value = link.wait_for_any({DP_LEARNED: _real_code, DP_SEND: _sent_ir}, LEARN_SECONDS)
                return _learned(dp, value)
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
                    dps = (reply.get("dps") if isinstance(reply, dict) else None) or {}
                    if _real_code(dps.get(DP_LEARNED)):
                        return _learned(DP_LEARNED, dps[DP_LEARNED])
                    if _sent_ir(dps.get(DP_SEND)):
                        return _learned(DP_SEND, dps[DP_SEND])
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

    def reset(self):
        """Remove every remote Device Hub published to Home Assistant and forget learned buttons."""
        mqtt = self.hub.local.mqtt
        topics = set(self._announced)
        for remote in self._all():
            topics.update(topic for topic, _ in _discovery(remote, self._keys_of(remote), False))
            for key in self._keys_of(remote):
                topics.add(f"{DISCOVERY}/button/devicehub_ir_{remote['id']}/{_old_slug(key['code'])}/config")
            mqtt.publish(f"{PREFIX}/ir/{remote['id']}/state", "", retain=True)
        for topic in topics:
            mqtt.publish(topic, "", retain=True)
        with self._lock:
            self.remotes, self.buttons, self.blasters, self.ac = {}, {}, {}, {}
            self._keys, self._hub_keys, self._parents, self._announced = {}, {}, {}, {}
            self._parents_at = self._service_at = 0.0
            self.service = None
            store.remove(STORE)

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
