"""Live updates for the panel and for API keys, sent as Server-Sent Events.

Home Assistant's state_changed events are matched to the devices that own the
entity, so a client hears `state` for one control instead of polling the list.
Devices going offline or online and new activity entries are sent too.
"""

import asyncio
import json
import logging
import time

from aiohttp import web

_LOGGER = logging.getLogger(__name__)
HEARTBEAT = 20


class Events:
    def __init__(self, hub):
        self.hub = hub
        self.clients = set()
        self.entities = {}
        self._entities_at = 0.0
        hub.ha.on_connect(self._subscribe)
        hub.activity.listeners.append(lambda entry: self.publish("activity", entry, entry.get("device_id")))

    async def _subscribe(self):
        await self.hub.ha.subscribe("subscribe_events", self._on_ha_event, event_type="state_changed")
        await self.hub.ha.subscribe("subscribe_events", self._on_automation, event_type="automation_triggered")

    def _on_automation(self, event):
        data = (event or {}).get("data") or {}
        source = data.get("source") or ""
        why = f" ({source})" if source and not source.startswith("trigger action") else ""
        self.hub.activity.log("automation", f"{data.get('name') or data.get('entity_id')} ran{why}", via="Home Assistant")

    def _on_ha_event(self, event):
        data = (event or {}).get("data") or {}
        entity_id = data.get("entity_id")
        if not self.clients or entity_id not in self.entities:
            return
        new = data.get("new_state") or {}
        for device_id, code in self.entities[entity_id]:
            self.publish("state", {
                "device_id": device_id,
                "code": code,
                "entity_id": entity_id,
                "state": new.get("state"),
                "attributes": new.get("attributes") or {},
                "at": time.time(),
            }, device_id)

    def map_entities(self, catalog):
        """Remember which device owns each entity. Called whenever the catalog is rebuilt."""
        entities = {}
        for item in catalog:
            for control in item["controls"]:
                if control.get("entity_id"):
                    entities.setdefault(control["entity_id"], []).append((item["id"], control["code"]))
        self.entities = entities
        self._entities_at = time.monotonic()

    def publish(self, kind, data, device_id=None):
        for client in list(self.clients):
            if client.allows(device_id):
                client.push(kind, data)

    async def stream(self, request, allowed=None):
        """Hold the response open and write events. `allowed` is a key's device ids, or None for the panel."""
        if time.monotonic() - self._entities_at > 300:
            try:
                await self.hub.share.catalog()
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Could not map entities to devices")
        response = web.StreamResponse(headers={
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        })
        await response.prepare(request)
        client = _Client(allowed)
        self.clients.add(client)
        try:
            await response.write(b"retry: 5000\n\n")
            client.push("hello", {"at": time.time()})
            while True:
                try:
                    kind, data = await asyncio.wait_for(client.queue.get(), HEARTBEAT)
                except asyncio.TimeoutError:
                    await response.write(b": still here\n\n")
                    continue
                payload = json.dumps(data, ensure_ascii=False, default=str)
                await response.write(f"event: {kind}\ndata: {payload}\n\n".encode())
        except (ConnectionResetError, asyncio.CancelledError):
            pass
        finally:
            self.clients.discard(client)
        return response


class _Client:
    def __init__(self, allowed):
        self.allowed = allowed
        self.queue = asyncio.Queue(maxsize=200)

    def allows(self, device_id):
        """The panel hears everything. A key hears only about devices named on it."""
        if self.allowed is None:
            return True
        if device_id is None:
            return False
        bare = device_id.split(":", 1)[1] if ":" in device_id else device_id
        return "*" in self.allowed or device_id in self.allowed or bare in self.allowed

    def push(self, kind, data):
        try:
            self.queue.put_nowait((kind, data))
        except asyncio.QueueFull:
            pass
