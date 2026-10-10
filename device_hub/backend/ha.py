"""Home Assistant Core (REST + WebSocket) and Supervisor client.

Inside the app it talks to http://supervisor with SUPERVISOR_TOKEN. In dev mode
it uses HA_URL + HA_TOKEN, and Supervisor calls go through the `supervisor/api`
WebSocket command.
"""

import asyncio
import json
import logging
import os
import socket

import aiohttp

_LOGGER = logging.getLogger(__name__)
SUPERVISOR_FALLBACK_IP = "172.30.32.2"


class HAError(Exception):
    pass


def _supervisor_host():
    try:
        socket.gethostbyname("supervisor")
        return "supervisor"
    except OSError:
        return SUPERVISOR_FALLBACK_IP


class HomeAssistant:
    def __init__(self, session: aiohttp.ClientSession):
        self.session = session
        supervisor_token = os.environ.get("SUPERVISOR_TOKEN")
        if supervisor_token:
            self.mode = "addon"
            self.token = supervisor_token
            host = _supervisor_host()
            self.supervisor_base = f"http://{host}"
            self.rest_base = f"http://{host}/core/api"
            self.ws_url = f"ws://{host}/core/websocket"
        else:
            self.mode = "dev"
            base = os.environ["HA_URL"].rstrip("/")
            self.token = os.environ["HA_TOKEN"]
            self.supervisor_base = None
            self.rest_base = base + "/api"
            self.ws_url = base.replace("http", "ws", 1) + "/api/websocket"
        self._ws = None
        self._next_id = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._subscriptions: dict[int, callable] = {}
        self._connect_lock = asyncio.Lock()
        self._on_connect = []
        self.connected = False

    @property
    def headers(self):
        return {"Authorization": f"Bearer {self.token}"}

    def on_connect(self, callback):
        """Run `callback()` after every (re)connect, e.g. to resubscribe."""
        self._on_connect.append(callback)

    async def run_forever(self):
        while True:
            try:
                await self._ensure()
                while self._ws is not None and not self._ws.closed:
                    await asyncio.sleep(2)
            except Exception as exc:  # noqa: BLE001
                _LOGGER.warning("Home Assistant connection failed: %s", exc)
            self.connected = False
            await asyncio.sleep(5)

    async def _ensure(self):
        async with self._connect_lock:
            if self._ws is not None and not self._ws.closed:
                return
            ws = await self.session.ws_connect(self.ws_url, heartbeat=30, max_msg_size=0)
            await ws.receive_json()
            await ws.send_json({"type": "auth", "access_token": self.token})
            reply = await ws.receive_json()
            if reply.get("type") != "auth_ok":
                await ws.close()
                raise HAError("Home Assistant rejected the access token.")
            self._ws = ws
            self.connected = True
            asyncio.create_task(self._reader(ws))
        for callback in self._on_connect:
            asyncio.create_task(self._safe(callback))

    @staticmethod
    async def _safe(callback):
        try:
            await callback()
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Reconnect hook failed")

    async def _reader(self, ws):
        try:
            async for msg in ws:
                if msg.type != aiohttp.WSMsgType.TEXT:
                    continue
                data = json.loads(msg.data)
                for item in data if isinstance(data, list) else [data]:
                    msg_id = item.get("id")
                    if item.get("type") == "result":
                        future = self._pending.pop(msg_id, None)
                        if future and not future.done():
                            future.set_result(item)
                    elif item.get("type") == "event":
                        callback = self._subscriptions.get(msg_id)
                        if callback:
                            try:
                                callback(item.get("event"))
                            except Exception:  # noqa: BLE001
                                _LOGGER.exception("Event handler failed")
        finally:
            if self._ws is ws:
                self._ws = None
                self.connected = False
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(HAError("Lost the connection to Home Assistant."))
            self._pending.clear()
            self._subscriptions.clear()

    async def _send(self, payload, timeout, callback=None):
        await self._ensure()
        self._next_id += 1
        msg_id = self._next_id
        future = asyncio.get_running_loop().create_future()
        self._pending[msg_id] = future
        if callback:
            self._subscriptions[msg_id] = callback
        await self._ws.send_json({"id": msg_id, **payload})
        try:
            reply = await asyncio.wait_for(future, timeout)
        except asyncio.TimeoutError as exc:
            self._pending.pop(msg_id, None)
            self._subscriptions.pop(msg_id, None)
            raise HAError(f"Home Assistant did not answer {payload.get('type')} in time.") from exc
        if not reply.get("success"):
            self._subscriptions.pop(msg_id, None)
            error = reply.get("error") or {}
            raise HAError(error.get("message") or error.get("code") or "Home Assistant refused the request.")
        return msg_id, reply.get("result")

    async def call(self, type_, timeout=30, **payload):
        _, result = await self._send({"type": type_, **payload}, timeout)
        return result

    async def subscribe(self, type_, callback, timeout=30, **payload):
        msg_id, _ = await self._send({"type": type_, **payload}, timeout, callback)
        return msg_id

    async def call_service(self, domain, service, data=None, timeout=30):
        return await self.call(
            "call_service", timeout=timeout, domain=domain, service=service, service_data=data or {}
        )

    async def rest(self, method, path, payload=None, timeout=60):
        url = self.rest_base + path
        async with self.session.request(
            method,
            url,
            json=payload,
            headers=self.headers,
            timeout=aiohttp.ClientTimeout(total=timeout),
        ) as resp:
            text = await resp.text()
            try:
                data = json.loads(text) if text else None
            except ValueError:
                data = text
            if resp.status >= 400:
                message = data.get("message") if isinstance(data, dict) else None
                raise HAError(message or f"Home Assistant returned {resp.status} for {path}.")
            return data

    async def supervisor(self, method, endpoint, payload=None, timeout=60):
        if self.mode == "dev":
            message = {"type": "supervisor/api", "endpoint": endpoint, "method": method.lower(), "timeout": timeout}
            if payload is not None:
                message["data"] = payload
            _, result = await self._send(message, timeout + 5)
            return result
        async with self.session.request(
            method,
            self.supervisor_base + endpoint,
            json=payload,
            headers=self.headers,
            timeout=aiohttp.ClientTimeout(total=timeout),
        ) as resp:
            data = await resp.json(content_type=None)
            if resp.status >= 400 or (isinstance(data, dict) and data.get("result") == "error"):
                message = data.get("message") if isinstance(data, dict) else None
                raise HAError(message or f"Supervisor returned {resp.status} for {endpoint}.")
            return data.get("data") if isinstance(data, dict) else data

    async def registries(self):
        devices, areas, entries = await asyncio.gather(
            self.call("config/device_registry/list"),
            self.call("config/area_registry/list"),
            self.call("config_entries/get"),
        )
        return {
            "devices": devices or [],
            "areas": {a["area_id"]: a["name"] for a in areas or []},
            "entry_domains": {e["entry_id"]: e["domain"] for e in entries or []},
        }
