"""Local Tuya control: saved device profiles, one LAN link per device, MQTT discovery for Home Assistant.

Saving reads the device's data points from the Tuya cloud once. After that every
status update and command goes over the LAN with the local key.
"""

import asyncio
import json
import logging
import os
import queue
import select
import socket
import threading
import time

import paho.mqtt.client as mqtt
import tinytuya

from . import store
from .ha import HAError

_LOGGER = logging.getLogger(__name__)

STORE = "local_control"
PREFIX = "devicehub"
DISCOVERY = "homeassistant"
BRIDGE_TOPIC = f"{PREFIX}/bridge"
HEARTBEAT = 10
REFRESH = 30
SUPPORTED = {"Boolean", "Enum", "Integer", "Bitmap", "String"}
READ_ONLY = {"Bitmap", "String"}
NETWORK_ERRORS = {"901", "902", "905"}
ORIGIN = {"name": "Device Hub", "support_url": "https://github.com/elmgdad/haos-devices-gateway"}
LABELS = {
    "battery_percentage": "Battery",
    "countdown": "Countdown",
    "far_detection": "Far detection",
    "illuminance_value": "Illuminance",
    "light_mode": "Indicator light",
    "liquid_level_percent": "Liquid level",
    "liquid_state": "Liquid state",
    "max_set": "Upper alarm level",
    "mini_set": "Lower alarm level",
    "presence_state": "Presence",
    "relay_status": "Power-on behavior",
}
UNITS = {"lux": "lx", "℃": "°C", "℉": "°F"}

_FIND_LOCK = threading.Lock()


class _Gone(Exception):
    """The LAN connection to a device is unusable."""


class LocalBridge:
    def __init__(self, ha, cloud, scanner):
        self.ha = ha
        self.cloud = cloud
        self.scanner = scanner
        self.profiles = {}
        self.links = {}
        self._held = set()
        self.last_results = []
        self.mqtt = _Mqtt(self._on_mqtt_connect, self._on_mqtt_message)
        self._lock = threading.RLock()
        self._mqtt_task = None

    async def start(self):
        saved = store.load(STORE) or {}
        for profile in (saved.get("devices") or {}).values():
            if profile.get("id") and profile.get("local_key") and profile.get("dps"):
                self.profiles[profile["id"]] = profile
                self._start_link(profile)
        self._mqtt_task = asyncio.create_task(self._mqtt_forever())

    def stop(self):
        if self._mqtt_task:
            self._mqtt_task.cancel()
        for link in list(self.links.values()):
            link.stop()
        self.mqtt.stop()

    async def _mqtt_forever(self):
        while True:
            try:
                if not self.mqtt.started:
                    await asyncio.to_thread(self.mqtt.start, await self._mqtt_settings())
                elif self.mqtt.failing_for() > 120:
                    await asyncio.to_thread(self.mqtt.stop)
                    continue
            except Exception as exc:  # noqa: BLE001
                self.mqtt.error = str(exc) or exc.__class__.__name__
            await asyncio.sleep(30)

    async def _mqtt_settings(self):
        if self.ha.mode == "addon":
            try:
                data = await self.ha.supervisor("GET", "/services/mqtt")
            except HAError as exc:
                raise RuntimeError(f"No MQTT broker from Supervisor: {exc}. Install and start the Mosquitto broker app.") from exc
            if not isinstance(data, dict) or not data.get("host"):
                raise RuntimeError("Install and start the Mosquitto broker app, then Device Hub connects on its own.")
            host = await asyncio.to_thread(_reachable_host, data["host"])
            return {
                "host": host,
                "port": int(data.get("port") or 1883),
                "username": data.get("username"),
                "password": data.get("password"),
            }
        host = os.environ.get("MQTT_HOST")
        if not host:
            raise RuntimeError("Home Assistant entities are published by the Device Hub app on the Pi.")
        return {
            "host": host,
            "port": int(os.environ.get("MQTT_PORT") or 1883),
            "username": os.environ.get("MQTT_USER"),
            "password": os.environ.get("MQTT_PASSWORD"),
        }

    # Saved devices

    def overview(self):
        devices = [link.info() for link in self.links.values()]
        shown = {item["id"] for item in devices}
        for device_id in self._held:
            profile = self.profiles.get(device_id)
            if not profile or device_id in shown:
                continue
            devices.append({
                "id": profile["id"],
                "name": profile["name"],
                "ip": profile["ip"],
                "version": profile["version"],
                "state": "offline",
                "error": "Paused while Tuya Local connects.",
                "last_seen": None,
                "entities": len(profile["dps"]),
                "saved_at": profile.get("saved_at"),
                "area_id": profile.get("area_id"),
                "area": profile.get("area"),
            })
        devices.sort(key=lambda item: item["name"].lower())
        return {"mqtt": self.mqtt.info(), "devices": devices, "last_results": self.last_results}

    def link_info(self, device_id):
        link = self.links.get(device_id)
        return link.info() if link else None

    def is_live(self, device_id):
        link = self.links.get(device_id)
        return bool(link and link.state == "online")

    async def save(self, device_ids, blocked, refresh_live=True):
        if not self.cloud.connected:
            raise RuntimeError("Sign in to the Tuya cloud project first. Saving reads each device's data points from Tuya once.")
        results = []
        for device_id in device_ids:
            device = self.cloud.by_id(device_id)
            name = device["name"] if device else device_id
            if not refresh_live and self.is_live(device_id):
                results.append({"id": device_id, "name": name, "ok": True, "reason": "Already saved and live.",
                                "entities": len(self.profiles[device_id]["dps"])})
                continue
            try:
                if not device:
                    raise RuntimeError("Not in the Tuya cloud project.")
                if blocked.get(device_id):
                    raise RuntimeError(blocked[device_id])
                profile = await self._build(device)
            except Exception as exc:  # noqa: BLE001
                results.append({"id": device_id, "name": name, "ok": False, "reason": str(exc) or exc.__class__.__name__})
                continue
            self._keep(profile)
            results.append({"id": device_id, "name": name, "ok": True, "reason": None, "entities": len(profile["dps"])})
        self.last_results = results
        return {**self.overview(), "results": results}

    async def _build(self, device):
        device_id = device["id"]
        previous = self.profiles.get(device_id) or {}
        seen = self.scanner.tuya_ip(device_id) or {}
        ip = seen.get("ip") or previous.get("ip")
        if device.get("node_id") and not ip:
            raise RuntimeError("Paired through a gateway. Local control covers devices with their own LAN address.")
        if not ip:
            raise RuntimeError("No LAN address yet. Run Find on network first.")
        if not device.get("local_key"):
            raise RuntimeError("Tuya has no local key for this device.")
        spec = await self.cloud.spec(device_id)
        running = self.links.pop(device_id, None)
        if running:
            await asyncio.to_thread(running.stop)
        try:
            version, dps = await asyncio.to_thread(
                _probe, device_id, ip, device["local_key"], str(seen.get("version") or previous.get("version") or "")
            )
        except Exception:
            if running:
                self._start_link(previous)
            raise
        points = _points(spec, dps)
        if not points:
            if running:
                self._start_link(previous)
            raise RuntimeError("None of its data points answered on the LAN.")
        return {
            "id": device_id,
            "name": device["name"],
            "ip": ip,
            "version": version,
            "local_key": device["local_key"],
            "category": device.get("category") or "",
            "product_name": device.get("product_name") or "",
            "product_id": device.get("product_id") or "",
            "model": device.get("model") or "",
            "dps": points,
            "area_id": previous.get("area_id"),
            "area": previous.get("area"),
            "saved_at": time.time(),
        }

    def _keep(self, profile):
        with self._lock:
            previous = self.profiles.get(profile["id"])
            self.profiles[profile["id"]] = profile
            self._persist()
        if previous:
            fresh = {topic for topic, _ in _discovery(profile)}
            for topic, _ in _discovery(previous):
                if topic not in fresh:
                    self.mqtt.publish(topic, "", retain=True)
        self._announce(profile)
        self._start_link(profile)

    async def hold(self, device_id):
        """Close the LAN link so Tuya Local can open the one connection the device allows."""
        self._held.add(device_id)
        link = self.links.pop(device_id, None)
        if link:
            await asyncio.to_thread(link.stop)

    async def release(self, device_id):
        self._held.discard(device_id)
        if device_id not in self.links and device_id in self.profiles:
            self._start_link(self.profiles[device_id])

    async def forget(self, device_id):
        self._held.discard(device_id)
        link = self.links.pop(device_id, None)
        if link:
            await asyncio.to_thread(link.stop)
        with self._lock:
            profile = self.profiles.pop(device_id, None)
            self._persist()
        if profile:
            for topic, _ in _discovery(profile):
                self.mqtt.publish(topic, "", retain=True)
            self.mqtt.publish(_state_topic(device_id), "", retain=True)
            self.mqtt.publish(_availability_topic(device_id), "", retain=True)
        self.last_results = [r for r in self.last_results if r["id"] != device_id]
        return self.overview()

    def set_area(self, device_id, area_id, area_name):
        profile = self.profiles.get(device_id)
        if not profile:
            return False
        with self._lock:
            profile["area_id"] = area_id or None
            profile["area"] = area_name or None
            self._persist()
        self._announce(profile)
        return True

    def note_address(self, device_id, ip, version):
        profile = self.profiles.get(device_id)
        if not profile or not ip or profile.get("ip") == ip:
            return
        with self._lock:
            profile["ip"] = ip
            if version:
                profile["version"] = str(version)
            self._persist()
        link = self.links.get(device_id)
        if link:
            link.kick()

    def _persist(self):
        store.save(STORE, {"devices": self.profiles})

    def _start_link(self, profile):
        if profile["id"] in self._held or profile["id"] in self.links:
            return
        link = _Link(self, profile)
        self.links[profile["id"]] = link
        link.start()

    # Control from the Device Hub panel, in raw Tuya units like the cloud API

    def control(self, device_id):
        link = self.links.get(device_id)
        if not link or link.state != "online":
            return None
        profile = link.profile
        return {
            "id": device_id,
            "name": profile["name"],
            "online": True,
            "via": "local",
            "status": [{"code": p["code"], "value": link.dps.get(p["dp"])} for p in profile["dps"] if p["dp"] in link.dps],
            "functions": [
                {"code": p["code"], "type": p["type"], "values": json.dumps(p["values"]), "desc": _label(p["code"])}
                for p in profile["dps"]
                if p["writable"]
            ],
            "blocked": None,
        }

    async def command(self, device_id, commands):
        link = self.links.get(device_id)
        if not link or link.state != "online":
            raise RuntimeError("The local link to this device is down.")
        by_code = {p["code"]: p for p in link.profile["dps"]}
        values = {}
        for command in commands or []:
            code = str((command or {}).get("code") or "").strip()
            point = by_code.get(code)
            if not point or not point["writable"]:
                raise RuntimeError(f"{code or 'That data point'} cannot be changed locally.")
            values[point["dp"]] = _raw_value(point, command.get("value"))
        if not values:
            raise RuntimeError("Choose a command to send.")
        link.send(values)
        await asyncio.to_thread(link.wait_for, values, 4)
        return self.control(device_id) or {"id": device_id, "online": False, "status": [], "functions": [], "via": "local"}

    # MQTT

    def _on_mqtt_connect(self):
        self.mqtt.publish(BRIDGE_TOPIC, "online", retain=True)
        self.mqtt.subscribe(f"{PREFIX}/tuya/+/set/+")
        self.mqtt.subscribe(f"{DISCOVERY}/status")
        self._announce_all()

    def _on_mqtt_message(self, topic, payload):
        if topic == f"{DISCOVERY}/status":
            if payload.strip().lower() == "online":
                self._announce_all()
            return
        parts = topic.split("/")
        if len(parts) != 5 or parts[1] != "tuya" or parts[3] != "set":
            return
        link = self.links.get(parts[2])
        point = next((p for p in (link.profile["dps"] if link else []) if p["code"] == parts[4]), None)
        if not point or not point["writable"]:
            return
        try:
            value = _mqtt_value(point, payload)
        except (TypeError, ValueError) as exc:
            _LOGGER.warning("Ignored %s for %s: %s", parts[4], link.profile["name"], exc)
            return
        link.send({point["dp"]: value})

    def _announce_all(self):
        for profile in list(self.profiles.values()):
            self._announce(profile)

    def _announce(self, profile):
        if not self.mqtt.connected:
            return
        for topic, config in _discovery(profile):
            self.mqtt.publish(topic, json.dumps(config), retain=True)
        link = self.links.get(profile["id"])
        if link:
            self.link_changed(link)
            if link.dps:
                self.publish_state(link)

    def link_changed(self, link):
        self.mqtt.publish(_availability_topic(link.profile["id"]), "online" if link.state == "online" else "offline", retain=True)

    def publish_state(self, link):
        state = {}
        for point in link.profile["dps"]:
            if point["dp"] in link.dps:
                state[point["code"]] = _scaled(point, link.dps[point["dp"]])
        self.mqtt.publish(_state_topic(link.profile["id"]), json.dumps(state), retain=True)

    def relocated(self, link, ip, version):
        with self._lock:
            link.profile["ip"] = ip
            if version:
                link.profile["version"] = str(version)
            self._persist()


class _Link:
    """One persistent LAN connection, owned by its own thread."""

    def __init__(self, bridge, profile):
        self.bridge = bridge
        self.profile = profile
        self.dps = {}
        self.state = "connecting"
        self.error = None
        self.last_seen = None
        self._commands = queue.SimpleQueue()
        self._wake_r, self._wake_w = socket.socketpair()
        self._wake_r.setblocking(False)
        self._stop = threading.Event()
        self._retry = threading.Event()
        self._changed = threading.Condition()
        self._thread = threading.Thread(target=self._run, name=f"tuya-{profile['id'][-6:]}", daemon=True)

    def info(self):
        return {
            "id": self.profile["id"],
            "name": self.profile["name"],
            "ip": self.profile["ip"],
            "version": self.profile["version"],
            "state": self.state,
            "error": self.error,
            "last_seen": self.last_seen,
            "entities": len(self.profile["dps"]),
            "saved_at": self.profile.get("saved_at"),
            "area_id": self.profile.get("area_id"),
            "area": self.profile.get("area"),
        }

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._retry.set()
        self._wake()
        self._thread.join(timeout=8)
        for sock in (self._wake_r, self._wake_w):
            try:
                sock.close()
            except OSError:
                pass

    def kick(self):
        self._retry.set()

    def send(self, values):
        self._commands.put(values)
        self._wake()

    def wait_for(self, values, timeout):
        deadline = time.monotonic() + timeout
        with self._changed:
            while any(self.dps.get(dp) != value for dp, value in values.items()):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._changed.wait(remaining)
        return True

    def _wake(self):
        try:
            self._wake_w.send(b"\0")
        except OSError:
            pass

    def _run(self):
        failures = 0
        while not self._stop.is_set():
            device = None
            try:
                device = self._open()
                failures = 0
                self._serve(device)
            except _Gone as exc:
                self._offline(str(exc))
            except Exception as exc:  # noqa: BLE001
                _LOGGER.exception("Local link to %s failed", self.profile["name"])
                self._offline(str(exc) or exc.__class__.__name__)
            finally:
                if device is not None:
                    try:
                        device.close()
                    except Exception:  # noqa: BLE001
                        pass
            if self._stop.is_set():
                break
            failures += 1
            if failures % 3 == 0:
                self._relocate()
            self._retry.wait(min(60, 5 * failures))
            self._retry.clear()

    def _open(self):
        profile = self.profile
        device = tinytuya.Device(
            profile["id"], profile["ip"], profile["local_key"], version=float(profile["version"]),
            persist=True, connection_timeout=5, connection_retry_limit=1, connection_retry_delay=1,
        )
        result = device.status()
        dps = _dps_of(result)
        if not dps:
            device.close()
            raise _Gone(_error_text(result) if isinstance(result, dict) else "The device did not report its status.")
        self._merge(dps)
        return device

    def _serve(self, device):
        last_beat = last_refresh = time.monotonic()
        while not self._stop.is_set():
            sock = device.socket
            if sock is None:
                raise _Gone("The device closed the connection.")
            readable, _, _ = select.select([sock, self._wake_r], [], [], 1.0)
            if self._wake_r in readable:
                try:
                    while self._wake_r.recv(256):
                        pass
                except (BlockingIOError, OSError):
                    pass
            self._flush(device)
            if sock in readable and device.socket is sock:
                self._take(device.receive())
            now = time.monotonic()
            if now - last_refresh >= REFRESH:
                self._take(device.status())
                last_refresh = last_beat = now
            elif now - last_beat >= HEARTBEAT:
                device.heartbeat(nowait=True)
                last_beat = now

    def _flush(self, device):
        while True:
            try:
                values = self._commands.get_nowait()
            except queue.Empty:
                return
            result = device.set_multiple_values(values, nowait=True)
            if isinstance(result, dict) and result.get("Err"):
                raise _Gone(_error_text(result))
            if device.socket is None:
                raise _Gone("The device closed the connection.")

    def _take(self, result):
        if result is None:
            return
        dps = _dps_of(result)
        if dps is None:
            raise _Gone(_error_text(result))
        if dps:
            self._merge(dps)
        else:
            self.last_seen = time.time()

    def _merge(self, dps):
        changed = False
        with self._changed:
            for dp, value in dps.items():
                dp = str(dp)
                if dp not in self.dps or self.dps[dp] != value:
                    self.dps[dp] = value
                    changed = True
            self.last_seen = time.time()
            self._changed.notify_all()
        if self.state != "online":
            self.state, self.error = "online", None
            _LOGGER.info("Local link to %s is up at %s", self.profile["name"], self.profile["ip"])
            self.bridge.link_changed(self)
            changed = True
        if changed:
            self.bridge.publish_state(self)

    def _offline(self, error):
        was = self.state
        self.state, self.error = "offline", error
        if was != "offline":
            _LOGGER.warning("Local link to %s is down: %s", self.profile["name"], error)
            self.bridge.link_changed(self)

    def _relocate(self):
        with _FIND_LOCK:
            try:
                found = tinytuya.find_device(self.profile["id"])
            except Exception:  # noqa: BLE001
                return
        ip = (found or {}).get("ip")
        if ip and ip != self.profile["ip"]:
            _LOGGER.info("%s moved to %s", self.profile["name"], ip)
            self.bridge.relocated(self, ip, found.get("version"))


class _Mqtt:
    def __init__(self, on_connect, on_message):
        self._on_connect = on_connect
        self._on_message = on_message
        self.client = None
        self.started = False
        self.connected = False
        self.error = None
        self.host = None
        self._down_since = None

    def info(self):
        state = "connected" if self.connected else "connecting" if self.started else "off"
        return {"state": state, "error": None if self.connected else self.error, "host": self.host}

    def failing_for(self):
        if self.connected or self._down_since is None:
            return 0
        return time.monotonic() - self._down_since

    def start(self, settings):
        client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=f"device-hub-{socket.gethostname()}",
            protocol=mqtt.MQTTv311,
        )
        if settings.get("username"):
            client.username_pw_set(settings["username"], settings.get("password"))
        client.will_set(BRIDGE_TOPIC, "offline", qos=1, retain=True)
        client.reconnect_delay_set(1, 30)
        client.on_connect = self._connected
        client.on_disconnect = self._disconnected
        client.on_connect_fail = self._connect_failed
        client.on_message = self._message
        self.host = f"{settings['host']}:{settings['port']}"
        self._down_since = time.monotonic()
        client.connect_async(settings["host"], settings["port"], keepalive=30)
        client.loop_start()
        self.client = client
        self.started = True

    def stop(self):
        client, self.client = self.client, None
        self.started = False
        if client is None:
            return
        if self.connected:
            client.publish(BRIDGE_TOPIC, "offline", qos=1, retain=True).wait_for_publish(2)
        self.connected = False
        client.disconnect()
        client.loop_stop()

    def publish(self, topic, payload, retain=False):
        client = self.client
        if client is not None and self.connected:
            client.publish(topic, payload, qos=1, retain=retain)

    def subscribe(self, topic):
        if self.client is not None:
            self.client.subscribe(topic, qos=1)

    def _connected(self, _client, _userdata, _flags, reason_code, _properties):
        if reason_code.is_failure:
            self.connected = False
            self.error = f"The MQTT broker refused Device Hub: {reason_code}"
            return
        self.connected, self.error, self._down_since = True, None, None
        _LOGGER.info("Connected to MQTT at %s", self.host)
        try:
            self._on_connect()
        except Exception:  # noqa: BLE001
            _LOGGER.exception("MQTT announce failed")

    def _disconnected(self, _client, _userdata, _flags, reason_code, _properties):
        if self.connected:
            _LOGGER.warning("MQTT disconnected: %s", reason_code)
        self.connected = False
        self._down_since = self._down_since or time.monotonic()
        if reason_code and reason_code.is_failure:
            self.error = f"MQTT disconnected: {reason_code}"

    def _connect_failed(self, _client, _userdata):
        self.connected = False
        self.error = f"Could not reach the MQTT broker at {self.host}."

    def _message(self, _client, _userdata, message):
        try:
            self._on_message(message.topic, message.payload.decode("utf-8", "replace"))
        except Exception:  # noqa: BLE001
            _LOGGER.exception("MQTT command failed on %s", message.topic)


def _reachable_host(host):
    try:
        socket.gethostbyname(host)
        return host
    except OSError:
        return "127.0.0.1"


def _probe(device_id, ip, key, version):
    versions = [version] if version else []
    versions += [v for v in ("3.5", "3.4", "3.3") if v not in versions]
    last = None
    for candidate in versions:
        device = tinytuya.Device(
            device_id, ip, key, version=float(candidate),
            connection_timeout=5, connection_retry_limit=1, connection_retry_delay=1,
        )
        try:
            result = device.status()
        finally:
            device.close()
        dps = _dps_of(result)
        if dps:
            return candidate, dps
        last = result
        if isinstance(result, dict) and str(result.get("Err")) in NETWORK_ERRORS:
            break
    raise RuntimeError(f"No answer on {ip}: {_error_text(last)}")


def _points(spec, dps):
    items = {}
    for item in spec.get("status") or []:
        if item.get("dp_id") is not None and item.get("code"):
            items[str(item["dp_id"])] = {**item, "writable": False}
    for item in spec.get("functions") or []:
        if item.get("dp_id") is not None and item.get("code"):
            items[str(item["dp_id"])] = {**items.get(str(item["dp_id"]), {}), **item, "writable": True}
    points = []
    for dp, item in sorted(items.items(), key=lambda pair: int(pair[0])):
        kind = item.get("type")
        if dp not in {str(k) for k in dps} or kind not in SUPPORTED:
            continue
        if kind in READ_ONLY and item["writable"]:
            continue
        try:
            values = json.loads(item.get("values") or "{}")
        except ValueError:
            values = {}
        points.append({
            "dp": dp,
            "code": item["code"],
            "type": kind,
            "values": values if isinstance(values, dict) else {},
            "writable": item["writable"],
        })
    return points


def _dps_of(result):
    if not isinstance(result, dict) or result.get("Err") or result.get("Error"):
        return None
    dps = result.get("dps")
    if not isinstance(dps, dict):
        data = result.get("data")
        dps = data.get("dps") if isinstance(data, dict) else None
    return dps if isinstance(dps, dict) else {}


def _error_text(result):
    if isinstance(result, dict):
        return str(result.get("Error") or result.get("Payload") or "No answer from the device.")
    return "No answer from the device."


def _scale(point):
    return int(point["values"].get("scale") or 0) if point["type"] == "Integer" else 0


def _number(value):
    return int(value) if float(value).is_integer() else value


def _scaled(point, raw):
    scale = _scale(point)
    if scale and isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return round(raw / 10**scale, scale)
    return raw


def _clamp(point, raw):
    low, high = point["values"].get("min"), point["values"].get("max")
    if low is not None:
        raw = max(int(low), raw)
    if high is not None:
        raw = min(int(high), raw)
    return raw


def _raw_value(point, value):
    kind = point["type"]
    if kind == "Boolean":
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in ("true", "on", "1"):
            return True
        if text in ("false", "off", "0"):
            return False
        raise RuntimeError(f"{point['code']} needs on or off.")
    if kind == "Integer":
        return _clamp(point, int(round(float(value))))
    if kind == "Enum":
        text = str(value)
        options = [str(o) for o in point["values"].get("range") or []]
        if options and text not in options:
            raise RuntimeError(f"{point['code']} accepts {', '.join(options)}.")
        return text
    raise RuntimeError(f"{point['code']} cannot be changed locally.")


def _mqtt_value(point, payload):
    kind = point["type"]
    if kind == "Boolean":
        text = payload.strip().upper()
        if text not in ("ON", "OFF"):
            raise ValueError(f"expected ON or OFF, got {payload!r}")
        return text == "ON"
    if kind == "Integer":
        return _clamp(point, int(round(float(payload) * 10 ** _scale(point))))
    if kind == "Enum":
        options = [str(o) for o in point["values"].get("range") or []]
        if options and payload not in options:
            raise ValueError(f"{payload!r} is not one of {options}")
        return payload
    raise ValueError("read-only")


def _label(code):
    base, _, suffix = code.rpartition("_")
    if suffix.isdigit() and base in LABELS:
        return f"{LABELS[base]} {suffix}"
    if code in LABELS:
        return LABELS[code]
    text = code.replace("_", " ").strip()
    return text[:1].upper() + text[1:]


def _unit(values):
    unit = str(values.get("unit") or "").strip()
    return UNITS.get(unit, unit)


def _device_class(code, unit):
    if unit == "%" and "battery" in code:
        return "battery", "measurement"
    if unit == "%" and "humidity" in code:
        return "humidity", "measurement"
    if unit in ("°C", "°F"):
        return "temperature", "measurement"
    if unit == "lx":
        return "illuminance", "measurement"
    if unit == "V":
        return "voltage", "measurement"
    if unit in ("A", "mA"):
        return "current", "measurement"
    if unit == "W":
        return "power", "measurement"
    if unit == "kWh":
        return "energy", "total_increasing"
    return None, "measurement"


def _state_topic(device_id):
    return f"{PREFIX}/tuya/{device_id}/state"


def _availability_topic(device_id):
    return f"{PREFIX}/tuya/{device_id}/availability"


def _discovery(profile):
    device_id = profile["id"]
    device = {
        "identifiers": [f"devicehub_tuya_{device_id}"],
        "name": profile["name"],
        "manufacturer": "Tuya",
        "model": profile.get("model") or profile.get("product_name") or profile.get("category") or "Tuya device",
    }
    if profile.get("product_id"):
        device["model_id"] = profile["product_id"]
    if profile.get("area"):
        device["suggested_area"] = profile["area"]
    entries = []
    for point in profile["dps"]:
        code, kind, values, writable = point["code"], point["type"], point["values"], point["writable"]
        value = f"value_json['{code}']"
        config = {
            "name": _label(code),
            "unique_id": f"devicehub_tuya_{device_id}_{code}",
            "state_topic": _state_topic(device_id),
            "availability": [{"topic": BRIDGE_TOPIC}, {"topic": _availability_topic(device_id)}],
            "availability_mode": "all",
            "device": device,
            "origin": ORIGIN,
        }
        command = f"{PREFIX}/tuya/{device_id}/set/{code}"
        if kind == "Boolean":
            component = "switch" if writable else "binary_sensor"
            config.update(value_template="{{ 'ON' if " + value + " else 'OFF' }}", payload_on="ON", payload_off="OFF")
            if writable:
                config["command_topic"] = command
                if "switch" not in code:
                    config["entity_category"] = "config"
        elif kind == "Enum":
            options = [str(o) for o in values.get("range") or []]
            config["value_template"] = "{{ " + value + " }}"
            if writable:
                component = "select"
                config.update(options=options, command_topic=command, entity_category="config")
            else:
                component = "sensor"
                config.update(device_class="enum", options=options)
        elif kind == "Integer":
            factor = 10 ** _scale(point)
            unit = _unit(values)
            config["value_template"] = "{{ " + value + " }}"
            if unit:
                config["unit_of_measurement"] = unit
            if writable:
                component = "number"
                config.update(
                    command_topic=command,
                    min=_number(float(values.get("min", 0)) / factor),
                    max=_number(float(values.get("max", 100)) / factor),
                    step=_number(float(values.get("step") or 1) / factor),
                    entity_category="config",
                )
            else:
                component = "sensor"
                device_class, state_class = _device_class(code, unit)
                config["state_class"] = state_class
                if device_class:
                    config["device_class"] = device_class
                if _scale(point):
                    config["suggested_display_precision"] = _scale(point)
        else:
            component = "sensor"
            config.update(value_template="{{ " + value + " }}", entity_category="diagnostic")
        entries.append((f"{DISCOVERY}/{component}/devicehub_{device_id}/{code}/config", config))
    return entries
