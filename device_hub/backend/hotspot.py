"""Start and stop a Wi-Fi hotspot on the Home Assistant host's wireless radio."""

import asyncio
import os
import shutil

from . import store
from .ha import HAError

HOTSPOT = "device-hub-hotspot"
DBUS_SOCKETS = (
    "/run/dbus/system_bus_socket",
    "/var/run/dbus/system_bus_socket",
    "/run/host/dbus/system_bus_socket",
)


class Hotspot:
    def __init__(self, ha):
        self.ha = ha

    async def status(self):
        info = await self._network()
        wifi = info["wifi"]
        previous = (store.load("hotspot") or {}).get("previous")
        running = bool(wifi and wifi.get("mode") == "ap")
        address = None
        if self._can_control() and wifi:
            try:
                address = await self._address(wifi["interface"])
                active = await self._active_connection(wifi["interface"])
                running = running or active == HOTSPOT
            except RuntimeError:
                pass
        return {
            "available": self._can_control(),
            "reason": None if self._can_control() else "The hotspot runs on the Raspberry Pi app.",
            "interface": wifi["interface"] if wifi else None,
            "running": running,
            "ssid": wifi.get("ssid") if wifi else None,
            "address": address,
            "signal": wifi.get("signal") if wifi else None,
            "ethernet_ipv4": info["ethernet_ipv4"],
            "previous": previous,
        }

    async def start(self, ssid, password):
        self._require()
        ssid = (ssid or "").strip()
        password = password or ""
        if not ssid or len(ssid) > 32:
            raise RuntimeError("The hotspot name must be 1 to 32 characters.")
        if len(password) < 8 or len(password) > 63:
            raise RuntimeError("The hotspot password must be 8 to 63 characters.")
        info = await self._network()
        wifi = info["wifi"]
        if not wifi:
            raise RuntimeError("This Raspberry Pi has no Wi-Fi radio.")
        iface = wifi["interface"]
        active = await self._active_connection(iface)
        if active and active != HOTSPOT:
            store.save("hotspot", {"previous": active, "ssid": wifi.get("ssid") or ""})
            await self._nm("connection", "modify", active, "connection.autoconnect", "no")
        await self._nm("connection", "delete", HOTSPOT, check=False)
        try:
            await self._nm(
                "device", "wifi", "hotspot",
                "ifname", iface,
                "con-name", HOTSPOT,
                "ssid", ssid,
                "band", "bg",
                "password", password,
                timeout=60,
            )
        except RuntimeError:
            if active and active != HOTSPOT:
                await self._nm("connection", "modify", active, "connection.autoconnect", "yes", check=False)
                await self._nm("connection", "up", active, check=False, timeout=60)
            store.remove("hotspot")
            raise
        await self._nm("connection", "modify", HOTSPOT, "connection.autoconnect", "yes")
        return await self.status()

    async def stop(self):
        self._require()
        saved = store.load("hotspot") or {}
        previous = saved.get("previous")
        info = await self._network()
        iface = (info["wifi"] or {}).get("interface") or "wlan0"
        await self._nm("connection", "down", HOTSPOT, check=False)
        await self._nm("connection", "delete", HOTSPOT, check=False)
        if previous:
            await self._nm("connection", "modify", previous, "connection.autoconnect", "yes")
            await self._nm("connection", "up", previous, timeout=60)
        elif iface:
            await self._nm("device", "connect", iface, timeout=60)
        store.remove("hotspot")
        return await self.status()

    def _can_control(self):
        return store.ADDON and bool(shutil.which("nmcli")) and self._dbus() is not None

    def _require(self):
        if not store.ADDON:
            raise RuntimeError("Start the hotspot from Device Hub on the Raspberry Pi.")
        if not shutil.which("nmcli"):
            raise RuntimeError("nmcli is not installed in this app.")
        if self._dbus() is None:
            raise RuntimeError("This app cannot reach the Raspberry Pi network service.")

    def _dbus(self):
        current = os.environ.get("DBUS_SYSTEM_BUS_ADDRESS")
        if current and current.startswith("unix:path="):
            path = current.removeprefix("unix:path=")
            if os.path.exists(path):
                return current
        for path in DBUS_SOCKETS:
            if os.path.exists(path):
                return f"unix:path={path}"
        return None

    async def _network(self):
        try:
            data = await self.ha.supervisor("GET", "/network/info")
        except HAError as exc:
            raise RuntimeError(str(exc)) from exc
        wifi = None
        ethernet_ipv4 = False
        for interface in data.get("interfaces", []):
            addresses = (interface.get("ipv4") or {}).get("address") or []
            if interface.get("type") == "ethernet" and addresses:
                ethernet_ipv4 = True
            if interface.get("type") == "wireless" and wifi is None:
                details = interface.get("wifi") or {}
                wifi = {
                    "interface": interface.get("interface"),
                    "mode": details.get("mode"),
                    "ssid": details.get("ssid"),
                    "signal": details.get("signal"),
                    "connected": interface.get("connected"),
                }
        return {"wifi": wifi, "ethernet_ipv4": ethernet_ipv4}

    async def _active_connection(self, iface):
        out = await self._nm("-t", "-f", "NAME,TYPE,DEVICE", "connection", "show", "--active")
        for line in out.splitlines():
            name, _, device = (line.split(":", 2) + ["", ""])[:3]
            if device == iface:
                return name
        return None

    async def _address(self, iface):
        out = await self._nm("-g", "IP4.ADDRESS", "device", "show", iface, check=False)
        line = next((item.strip() for item in out.splitlines() if item.strip()), "")
        return line.split("/", 1)[0] or None

    async def _nm(self, *args, timeout=30, check=True):
        env = os.environ.copy()
        env["DBUS_SYSTEM_BUS_ADDRESS"] = self._dbus()
        proc = await asyncio.create_subprocess_exec(
            "nmcli", *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=env
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout)
        except asyncio.TimeoutError as exc:
            proc.kill()
            raise RuntimeError("The network service did not answer.") from exc
        text = (err or out).decode(errors="ignore").strip()
        if check and proc.returncode != 0:
            raise RuntimeError(text or "The Raspberry Pi refused the hotspot change.")
        return out.decode(errors="ignore")
