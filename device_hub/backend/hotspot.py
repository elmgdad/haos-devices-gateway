"""Start and stop a Wi-Fi hotspot on the Home Assistant host's wireless radio.

The host NetworkManager has no dnsmasq, so ipv4.method=shared cannot hand out
addresses. The access point gets 10.42.0.1/24 itself, and this app runs dnsmasq
for the clients.
"""

import asyncio
import logging
import os
import shutil
from pathlib import Path

_LOGGER = logging.getLogger(__name__)

from . import store
from .ha import HAError

HOTSPOT = "device-hub-hotspot"
ADDRESS = "10.42.0.1"
SUBNET = "10.42.0.0/24"
PID_FILE = Path("/tmp/device-hub-dnsmasq.pid")
DNS_FILE = Path("/tmp/device-hub-dnsmasq.dns")
NAT_FILE = Path("/tmp/device-hub-nat.txt")
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
            "upstream": (store.load("hotspot") or {}).get("upstream"),
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
                "connection", "add",
                "type", "wifi",
                "ifname", iface,
                "con-name", HOTSPOT,
                "autoconnect", "no",
                "ssid", ssid,
                "802-11-wireless.mode", "ap",
                "802-11-wireless.band", "bg",
                "802-11-wireless-security.key-mgmt", "wpa-psk",
                "802-11-wireless-security.proto", "rsn",
                "802-11-wireless-security.pairwise", "ccmp",
                "802-11-wireless-security.group", "ccmp",
                "802-11-wireless-security.psk", password,
                "ipv4.method", "manual",
                "ipv4.addresses", f"{ADDRESS}/24",
                "ipv6.method", "disabled",
            )
            await self._nm("connection", "up", HOTSPOT, timeout=60)
            await self._share(iface)
        except RuntimeError:
            await self._stop_dhcp()
            await self._unshare()
            await self._nm("connection", "down", HOTSPOT, check=False)
            await self._nm("connection", "delete", HOTSPOT, check=False)
            if active and active != HOTSPOT:
                await self._nm("connection", "modify", active, "connection.autoconnect", "yes", check=False)
                await self._nm("connection", "up", active, check=False, timeout=60)
            await self._restore_ethernet(store.load("hotspot") or {})
            store.remove("hotspot")
            raise
        await self._nm("connection", "modify", HOTSPOT, "connection.autoconnect", "yes")
        return await self.status()

    async def ensure(self):
        """Keep DHCP running when the access point is already up, including after an app restart."""
        if not self._can_control():
            return
        try:
            info = await self._network()
        except RuntimeError:
            return
        wifi = info["wifi"]
        if not wifi:
            return
        try:
            active = await self._active_connection(wifi["interface"])
        except RuntimeError:
            return
        if wifi.get("mode") == "ap" or active == HOTSPOT:
            try:
                await self._share(wifi["interface"])
            except RuntimeError as exc:
                _LOGGER.warning("Hotspot has no path to the network: %s", exc)

    async def stop(self):
        self._require()
        saved = store.load("hotspot") or {}
        previous = saved.get("previous")
        info = await self._network()
        iface = (info["wifi"] or {}).get("interface") or "wlan0"
        await self._stop_dhcp()
        await self._unshare()
        await self._nm("connection", "down", HOTSPOT, check=False)
        await self._nm("connection", "delete", HOTSPOT, check=False)
        if previous:
            await self._nm("connection", "modify", previous, "connection.autoconnect", "yes")
            await self._nm("connection", "up", previous, timeout=60)
        elif iface:
            await self._nm("device", "connect", iface, timeout=60)
        await self._restore_ethernet(saved)
        store.remove("hotspot")
        return await self.status()

    async def _share(self, iface):
        """Let hotspot clients use the Ethernet connection for the rest of the network."""
        upstream = await self._ensure_upstream()
        if not upstream:
            raise RuntimeError("Plug in Ethernet. The Wi-Fi radio is the hotspot, so internet has to come in on the cable.")
        self._forward()
        await self._nat(iface, upstream["interface"])
        await self._start_dhcp(iface, upstream["nameservers"])
        saved = store.load("hotspot") or {}
        saved["upstream"] = upstream["interface"]
        store.save("hotspot", saved)

    async def _ensure_upstream(self):
        data = await self.ha.supervisor("GET", "/network/info")
        ethernet = next(
            (item for item in data.get("interfaces", []) if item.get("type") == "ethernet" and item.get("connected")),
            None,
        )
        if not ethernet:
            return None
        name = ethernet["interface"]
        ipv4 = ethernet.get("ipv4") or {}
        addresses = ipv4.get("address") or []
        if not addresses and ipv4.get("method") != "auto":
            saved = store.load("hotspot") or {}
            saved.setdefault("ethernet_method", ipv4.get("method") or "disabled")
            saved["ethernet_interface"] = name
            store.save("hotspot", saved)
            await self.ha.supervisor("POST", f"/network/interface/{name}/update", {"ipv4": {"method": "auto"}})
        for _ in range(20):
            if addresses:
                break
            await asyncio.sleep(1)
            data = await self.ha.supervisor("GET", "/network/info")
            ethernet = next((item for item in data.get("interfaces", []) if item.get("interface") == name), None)
            ipv4 = (ethernet or {}).get("ipv4") or {}
            addresses = ipv4.get("address") or []
        if not addresses:
            return None
        nameservers = [item for item in (ipv4.get("nameservers") or []) if item] or ["1.1.1.1", "8.8.8.8"]
        return {"interface": name, "nameservers": nameservers[:3]}

    async def _restore_ethernet(self, saved):
        method = saved.get("ethernet_method")
        name = saved.get("ethernet_interface")
        if method and name and method != "auto":
            try:
                await self.ha.supervisor("POST", f"/network/interface/{name}/update", {"ipv4": {"method": method}})
            except HAError as exc:
                _LOGGER.warning("Could not restore Ethernet: %s", exc)

    def _forward(self):
        path = Path("/proc/sys/net/ipv4/ip_forward")
        try:
            path.write_text("1\n")
        except OSError as exc:
            raise RuntimeError("The Pi will not forward traffic from the hotspot.") from exc

    async def _nat(self, inside, outside):
        if not shutil.which("iptables"):
            raise RuntimeError("iptables is not installed in this app.")
        NAT_FILE.write_text(f"{inside} {outside}\n")
        await self._iptables(["-t", "nat", "-C", "POSTROUTING", "-s", SUBNET, "-o", outside, "-j", "MASQUERADE"], allow_fail=True) or await self._iptables(
            ["-t", "nat", "-A", "POSTROUTING", "-s", SUBNET, "-o", outside, "-j", "MASQUERADE"]
        )
        await self._iptables(["-C", "FORWARD", "-i", inside, "-o", outside, "-j", "ACCEPT"], allow_fail=True) or await self._iptables(
            ["-I", "FORWARD", "-i", inside, "-o", outside, "-j", "ACCEPT"]
        )
        established = ["-i", outside, "-o", inside, "-m", "conntrack", "--ctstate", "RELATED,ESTABLISHED", "-j", "ACCEPT"]
        await self._iptables(["-C", "FORWARD", *established], allow_fail=True) or await self._iptables(["-I", "FORWARD", *established])

    async def _unshare(self):
        if not NAT_FILE.exists() or not shutil.which("iptables"):
            NAT_FILE.unlink(missing_ok=True)
            return
        try:
            inside, outside = NAT_FILE.read_text().split()
        except ValueError:
            NAT_FILE.unlink(missing_ok=True)
            return
        await self._iptables(["-t", "nat", "-D", "POSTROUTING", "-s", SUBNET, "-o", outside, "-j", "MASQUERADE"], allow_fail=True)
        await self._iptables(["-D", "FORWARD", "-i", inside, "-o", outside, "-j", "ACCEPT"], allow_fail=True)
        await self._iptables(
            ["-D", "FORWARD", "-i", outside, "-o", inside, "-m", "conntrack", "--ctstate", "RELATED,ESTABLISHED", "-j", "ACCEPT"],
            allow_fail=True,
        )
        NAT_FILE.unlink(missing_ok=True)

    async def _iptables(self, args, allow_fail=False):
        proc = await asyncio.create_subprocess_exec(
            "iptables", *args, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE
        )
        _, err = await proc.communicate()
        if proc.returncode != 0:
            if allow_fail:
                return False
            detail = err.decode(errors="ignore").strip()
            raise RuntimeError(detail or "The Pi could not share Ethernet with the hotspot.")
        return True

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
        text = _clean_nm_error((err or out).decode(errors="ignore"))
        if check and proc.returncode != 0:
            raise RuntimeError(text or "The Raspberry Pi refused the hotspot change.")
        return out.decode(errors="ignore")

    async def _start_dhcp(self, iface, nameservers=None):
        dns = ",".join(nameservers or ["1.1.1.1", "8.8.8.8"])
        if self._dhcp_running() and DNS_FILE.exists() and DNS_FILE.read_text().strip() == dns:
            return
        await self._stop_dhcp()
        if not shutil.which("dnsmasq"):
            raise RuntimeError("DHCP for the hotspot is not installed in this app.")
        PID_FILE.unlink(missing_ok=True)
        proc = await asyncio.create_subprocess_exec(
            "dnsmasq",
            "--conf-file=/dev/null",
            "--port=0",
            f"--interface={iface}",
            "--bind-interfaces",
            f"--listen-address={ADDRESS}",
            "--dhcp-range=10.42.0.10,10.42.0.100,255.255.255.0,12h",
            f"--dhcp-option=3,{ADDRESS}",
            f"--dhcp-option=6,{dns}",
            "--no-resolv",
            "--no-hosts",
            f"--pid-file={PID_FILE}",
            "--dhcp-leasefile=/tmp/device-hub-dnsmasq.leases",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, err = await proc.communicate()
        if proc.returncode != 0 or not self._dhcp_running():
            detail = (err or out).decode(errors="ignore").strip()
            raise RuntimeError(detail or "The hotspot could not start handing out addresses.")
        DNS_FILE.write_text(dns + "\n")

    async def _stop_dhcp(self):
        if not PID_FILE.exists():
            return
        try:
            pid = int(PID_FILE.read_text().strip())
        except ValueError:
            PID_FILE.unlink(missing_ok=True)
            return
        try:
            os.kill(pid, 15)
        except OSError:
            pass
        PID_FILE.unlink(missing_ok=True)

    def _dhcp_running(self):
        if not PID_FILE.exists():
            return False
        try:
            pid = int(PID_FILE.read_text().strip())
            os.kill(pid, 0)
        except (OSError, ValueError):
            return False
        return True


def _clean_nm_error(text):
    lines = []
    for line in text.splitlines():
        if "versions don't match" in line or line.startswith("Warning:"):
            continue
        if line.strip():
            lines.append(line.strip())
    message = " ".join(lines)
    if "could not be reserved" in message:
        return "The Pi could not give the hotspot an address. It is still on the previous Wi-Fi."
    return message
