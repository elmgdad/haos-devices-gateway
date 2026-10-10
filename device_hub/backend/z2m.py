"""Zigbee2MQTT: app lifecycle through Supervisor, bridge traffic through HA's MQTT WebSocket API."""

import asyncio
import json
import logging
import time
import uuid

from . import store
from .ha import HAError, HomeAssistant

_LOGGER = logging.getLogger(__name__)
TELEMETRY_KEYS = ("linkquality", "last_seen", "battery")


def _decode(payload):
    if isinstance(payload, (dict, list)):
        return payload
    try:
        return json.loads(payload)
    except (TypeError, ValueError):
        return payload


class Z2M:
    def __init__(self, ha: HomeAssistant):
        self.ha = ha
        self.base = store.settings()["z2m_base_topic"]
        self.bridge_state = None
        self.info = {}
        self.devices = []
        self.availability = {}
        self.telemetry = {}
        self._pending: dict[str, asyncio.Future] = {}
        self._subscribed_base = None
        self.subscribe_error = None
        ha.on_connect(self.subscribe)

    def set_base(self, base):
        base = (base or "zigbee2mqtt").strip().strip("/")
        if base != self.base:
            self.base = base
            self.bridge_state, self.info, self.devices = None, {}, []
            self.availability, self.telemetry = {}, {}
            asyncio.create_task(self.subscribe())

    async def subscribe(self):
        base = self.base
        topics = [
            f"{base}/bridge/state",
            f"{base}/bridge/info",
            f"{base}/bridge/devices",
            f"{base}/bridge/response/#",
            f"{base}/+/availability",
            f"{base}/+",
        ]
        try:
            for topic in topics:
                await self.ha.subscribe("mqtt/subscribe", self._on_message, topic=topic)
            self._subscribed_base = base
            self.subscribe_error = None
        except HAError as exc:
            self.subscribe_error = str(exc)
            _LOGGER.warning("Could not subscribe to Zigbee2MQTT topics: %s", exc)

    def _on_message(self, event):
        topic = event.get("topic") or ""
        prefix = self.base + "/"
        if not topic.startswith(prefix):
            return
        rest = topic[len(prefix):]
        payload = _decode(event.get("payload"))
        if rest == "bridge/state":
            self.bridge_state = payload.get("state") if isinstance(payload, dict) else payload
        elif rest == "bridge/info":
            if isinstance(payload, dict):
                self.info = payload
        elif rest == "bridge/devices":
            if isinstance(payload, list):
                self.devices = payload
        elif rest.startswith("bridge/response/"):
            if isinstance(payload, dict):
                future = self._pending.pop(str(payload.get("transaction")), None)
                if future and not future.done():
                    future.set_result(payload)
        elif rest.endswith("/availability"):
            name = rest[: -len("/availability")]
            state = payload.get("state") if isinstance(payload, dict) else payload
            self.availability[name] = state
        elif "/" not in rest and rest != "bridge" and isinstance(payload, dict):
            current = self.telemetry.setdefault(rest, {})
            for key in TELEMETRY_KEYS:
                if key in payload:
                    current[key] = payload[key]
            current["seen_at"] = time.time()

    async def addon(self):
        try:
            data = await self.ha.supervisor("GET", "/addons")
        except HAError as exc:
            return {"installed": False, "error": str(exc)}
        candidates = [a for a in data.get("addons", []) if a.get("slug", "").endswith(("zigbee2mqtt", "zigbee2mqtt_edge"))]
        if not candidates:
            return {"installed": False}
        candidates.sort(key=lambda a: a.get("state") != "started")
        slug = candidates[0]["slug"]
        info = await self.ha.supervisor("GET", f"/addons/{slug}/info")
        return {
            "installed": True,
            "slug": slug,
            "name": info.get("name"),
            "state": info.get("state"),
            "version": info.get("version"),
            "update_available": info.get("update_available"),
            "panel_path": f"/{slug}" if info.get("ingress_panel") else None,
            "serial_port": ((info.get("options") or {}).get("serial") or {}).get("port"),
        }

    async def addon_action(self, action):
        if action not in ("start", "stop", "restart"):
            raise HAError("Unknown action.")
        addon = await self.addon()
        if not addon.get("installed"):
            raise HAError("Zigbee2MQTT is not installed.")
        await self.ha.supervisor("POST", f"/addons/{addon['slug']}/{action}", timeout=300)
        return {"ok": True}

    async def request(self, path, payload, timeout=30):
        transaction = uuid.uuid4().hex[:12]
        future = asyncio.get_running_loop().create_future()
        self._pending[transaction] = future
        body = {**payload, "transaction": transaction}
        await self.ha.call_service(
            "mqtt", "publish", {"topic": f"{self.base}/bridge/request/{path}", "payload": json.dumps(body)}
        )
        try:
            reply = await asyncio.wait_for(future, timeout)
        except asyncio.TimeoutError as exc:
            self._pending.pop(transaction, None)
            raise HAError("Zigbee2MQTT did not answer. Check that it is running.") from exc
        if reply.get("status") != "ok":
            raise HAError(reply.get("error") or "Zigbee2MQTT refused the request.")
        return reply.get("data")

    async def permit(self, seconds):
        return await self.request("permit_join", {"time": max(0, min(int(seconds), 254))})

    async def rename(self, ieee, name):
        current = self._friendly(ieee)
        return await self.request("device/rename", {"from": current, "to": name.strip(), "homeassistant_rename": True})

    async def remove(self, ieee, force=False):
        return await self.request("device/remove", {"id": ieee, "force": bool(force)}, timeout=60)

    async def configure(self, ieee):
        return await self.request("device/configure", {"id": ieee}, timeout=60)

    def _friendly(self, ieee):
        for device in self.devices:
            if device.get("ieee_address") == ieee:
                return device.get("friendly_name") or ieee
        return ieee

    async def overview(self, registries=None):
        addon = await self.addon()
        by_identifier = {}
        areas = (registries or {}).get("areas") or {}
        for device in (registries or {}).get("devices") or []:
            for domain, value in device.get("identifiers") or []:
                if domain == "mqtt" and value.startswith("zigbee2mqtt_"):
                    by_identifier[value.removeprefix("zigbee2mqtt_")] = device
        devices = []
        for device in self.devices:
            ieee = device.get("ieee_address")
            name = device.get("friendly_name") or ieee
            definition = device.get("definition") or {}
            telemetry = self.telemetry.get(name, {})
            ha_device = by_identifier.get(ieee)
            devices.append({
                "ieee": ieee,
                "name": name,
                "type": device.get("type"),
                "network_address": device.get("network_address"),
                "model": definition.get("model") or device.get("model_id") or "",
                "vendor": definition.get("vendor") or device.get("manufacturer") or "",
                "description": definition.get("description") or "",
                "power_source": device.get("power_source") or "",
                "supported": device.get("supported"),
                "interview_completed": device.get("interview_completed"),
                "disabled": device.get("disabled"),
                "availability": self.availability.get(name),
                "linkquality": telemetry.get("linkquality"),
                "last_seen": telemetry.get("last_seen"),
                "battery": telemetry.get("battery"),
                "ha_device_id": ha_device["id"] if ha_device else None,
                "area_id": ha_device.get("area_id") if ha_device else None,
                "area": areas.get(ha_device.get("area_id")) if ha_device else None,
            })
        devices.sort(key=lambda d: (d["type"] != "Coordinator", (d["name"] or "").lower()))
        info = self.info or {}
        return {
            "addon": addon,
            "base_topic": self.base,
            "bridge_state": self.bridge_state,
            "subscribed": self._subscribed_base == self.base,
            "subscribe_error": self.subscribe_error,
            "version": info.get("version"),
            "coordinator": (info.get("coordinator") or {}).get("type"),
            "permit_join": info.get("permit_join"),
            "permit_join_end": info.get("permit_join_end") or info.get("permit_join_timeout"),
            "channel": ((info.get("network") or {}).get("channel")),
            "devices": devices,
        }
