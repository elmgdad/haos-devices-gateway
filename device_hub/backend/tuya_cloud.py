"""Tuya IoT Platform cloud project: login, device list with local keys."""

import asyncio
import os
import socket
import time

import requests
import tinytuya
import urllib3.util.connection

from . import store

# tinytuya calls requests without a timeout, and on hosts with a global but unroutable
# IPv6 address the AAAA records of openapi.tuya*.com hang forever.
urllib3.util.connection.allowed_gai_family = lambda: socket.AF_INET
_session_request = requests.sessions.Session.request


def _request_with_timeout(self, method, url, **kwargs):
    kwargs.setdefault("timeout", 20)
    return _session_request(self, method, url, **kwargs)


requests.sessions.Session.request = _request_with_timeout

REGIONS = [
    {"id": "eu", "name": "Central Europe"},
    {"id": "eu-w", "name": "Western Europe"},
    {"id": "us", "name": "Western America"},
    {"id": "us-e", "name": "Eastern America"},
    {"id": "in", "name": "India"},
    {"id": "sg", "name": "Singapore"},
    {"id": "cn", "name": "China"},
]
REGION_IDS = {r["id"] for r in REGIONS}
CACHE_SECONDS = 120


class TuyaCloud:
    def __init__(self):
        self._cloud = None
        self._lock = asyncio.Lock()
        self.devices = []
        self.fetched_at = 0
        self.error = None
        self._creds = _initial_creds()

    @property
    def connected(self):
        return self._cloud is not None and bool(self._cloud.token)

    def config(self):
        return {
            "access_id": self._creds.get("access_id", ""),
            "region": self._creds.get("region", "eu"),
            "has_secret": bool(self._creds.get("secret")),
            "connected": self.connected,
            "device_count": len(self.devices),
            "fetched_at": self.fetched_at,
            "error": self.error,
            "regions": REGIONS,
            "source": self._creds.get("source") or "",
        }

    async def restore(self):
        if self._creds.get("access_id") and self._creds.get("secret"):
            try:
                await self.login(self._creds["access_id"], self._creds["secret"], self._creds.get("region", "eu"))
            except Exception as exc:  # noqa: BLE001
                self.error = str(exc)

    async def login(self, access_id, secret, region):
        access_id = (access_id or "").strip()
        secret = (secret or "").strip() or self._creds.get("secret", "")
        region = region if region in REGION_IDS else "eu"
        if not access_id or not secret:
            raise RuntimeError("Enter the Access ID and Access Secret from your Tuya IoT project.")
        cloud = await asyncio.to_thread(_open_cloud, access_id, secret, region)
        self._cloud = cloud
        self._creds = {"access_id": access_id, "secret": secret, "region": region, "source": "panel"}
        store.save("tuya", self._creds)
        self.error = None
        await self.list(refresh=True)

    def logout(self):
        self._cloud = None
        self._creds = {}
        self.devices = []
        self.fetched_at = 0
        store.remove("tuya")

    async def list(self, refresh=False):
        if not self.connected:
            return []
        async with self._lock:
            if refresh or time.time() - self.fetched_at > CACHE_SECONDS:
                try:
                    self.devices = await asyncio.to_thread(_fetch_devices, self._cloud)
                    self.fetched_at = time.time()
                    self.error = None
                except Exception as exc:
                    self.error = str(exc)
                    if not self.devices:
                        raise
        return self.devices

    def by_id(self, device_id):
        return next((d for d in self.devices if d["id"] == device_id), None)


def _initial_creds():
    saved = store.load("tuya") or {}
    options = store.addon_options()
    access_id = str(options.get("tuya_access_id") or "").strip()
    secret = str(options.get("tuya_access_secret") or "").strip()
    region = options.get("tuya_region") if options.get("tuya_region") in REGION_IDS else "eu"
    if access_id and not secret and saved.get("access_id") == access_id:
        secret = saved.get("secret") or ""
    if access_id and secret:
        return {"access_id": access_id, "secret": secret, "region": region, "source": "app"}
    if not saved and os.environ.get("TUYA_CLIENT_ID") and not store.ADDON:
        return {
            "access_id": os.environ["TUYA_CLIENT_ID"],
            "secret": os.environ.get("TUYA_SECRET", ""),
            "region": os.environ.get("TUYA_REGION", "eu"),
            "source": "env",
        }
    return saved


def _open_cloud(access_id, secret, region):
    cloud = tinytuya.Cloud(apiRegion=region, apiKey=access_id, apiSecret=secret)
    if not cloud.token:
        error = cloud.error if isinstance(cloud.error, dict) else {}
        raise RuntimeError(
            str(error.get("Payload") or "Tuya rejected the login. Check the Access ID, Secret and data center.")
        )
    return cloud


def _fetch_devices(cloud):
    filtered = cloud.getdevices()
    if isinstance(filtered, dict):
        raise RuntimeError(str(filtered.get("Payload") or filtered.get("msg") or "Tuya did not return the device list."))
    raw = {d.get("id"): d for d in (getattr(cloud, "getdevices_raw", {}) or {}).get("result", [])}
    devices = []
    for item in filtered:
        dev_id = item.get("id")
        if not dev_id:
            continue
        full = raw.get(dev_id, {})
        devices.append({
            "id": dev_id,
            "name": (item.get("name") or full.get("name") or "Unnamed device").strip(),
            "category": item.get("category") or full.get("category") or "",
            "category_name": full.get("category_name") or "",
            "product_name": item.get("product_name") or full.get("product_name") or "",
            "product_id": item.get("product_id") or full.get("product_id") or "",
            "model": item.get("model") or full.get("model") or "",
            "online": full.get("online"),
            "sub": bool(item.get("sub") or full.get("sub")),
            "node_id": item.get("node_id") or full.get("node_id") or "",
            "gateway_id": full.get("gateway_id") or item.get("gateway_id") or item.get("parent") or "",
            "uuid": item.get("uuid") or full.get("uuid") or "",
            "mac": (item.get("mac") or "").lower(),
            "local_key": item.get("key") or full.get("local_key") or "",
            "icon": item.get("icon") or "",
        })
    devices.sort(key=lambda d: d["name"].lower())
    return devices
