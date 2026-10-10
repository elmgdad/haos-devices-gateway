"""LAN discovery: Tuya UDP broadcasts, port sweep + ARP, mDNS, Wi-Fi access points."""

import asyncio
import html
import ipaddress
import json
import logging
import os
import re
import shutil
import socket
import sys
import time

import aiohttp
import ifaddr
import tinytuya
from zeroconf import IPVersion, ServiceStateChange
from zeroconf.asyncio import AsyncServiceBrowser, AsyncServiceInfo, AsyncZeroconf

from . import store
from .ha import HAError

_LOGGER = logging.getLogger(__name__)

MAX_ADDRESSES = 1024
TUYA_PORTS = (6666, 6667, 7000)
PORTS = {
    6668: "Tuya",
    80: "HTTP",
    443: "HTTPS",
    554: "RTSP",
    8081: "Sonoff DIY",
    1883: "MQTT",
    8008: "Cast",
    6053: "ESPHome",
    9999: "Kasa",
    55443: "Yeelight",
    1400: "Sonos",
    8123: "Home Assistant",
    5540: "Matter",
    37777: "Dahua",
    8080: "HTTP alt",
}
MDNS_TYPES = {
    "_esphomelib._tcp.local.": "ESPHome",
    "_shelly._tcp.local.": "Shelly",
    "_hap._tcp.local.": "HomeKit",
    "_googlecast._tcp.local.": "Google Cast",
    "_hue._tcp.local.": "Philips Hue",
    "_ewelink._tcp.local.": "Sonoff (eWeLink)",
    "_miio._udp.local.": "Xiaomi",
    "_matter._tcp.local.": "Matter",
    "_matterc._udp.local.": "Matter (pairing)",
    "_wled._tcp.local.": "WLED",
    "_sonos._tcp.local.": "Sonos",
    "_airplay._tcp.local.": "AirPlay",
    "_home-assistant._tcp.local.": "Home Assistant",
    "_mqtt._tcp.local.": "MQTT broker",
    "_androidtvremote2._tcp.local.": "Android TV",
    "_ipp._tcp.local.": "Printer",
    "_axis-video._tcp.local.": "Camera",
}
SKIP_IFACES = re.compile(r"^(lo|docker|hassio|veth|br-|utun|awdl|llw|bridge|anpi|ap\d)", re.I)
PAIRING_SSID = re.compile(
    r"^(SmartLife|SL-|Smart ?Life|TY_|Tuya|ESP[_-]|ESP32|ITEAD-|Sonoff|eWeLink|shelly|tasmota|"
    r"A1-|Broadlink|BroadLinkProv|yeelink|Yeelight|xiaomi|miio|lumi|wiz_|WiZ|Govee|Meross|Kasa|"
    r"TP-LINK_Smart|ezviz|EZVIZ|Matter|HomeKit|WLED-AP|ESPHome|SmartPlug|SmartSwitch|SmartBulb)",
    re.I,
)
VENDOR_KINDS = [
    ("tuya", "Tuya"),
    ("espressif", "ESP module"),
    ("shelly", "Shelly"),
    ("allterco", "Shelly"),
    ("itead", "Sonoff"),
    ("sonoff", "Sonoff"),
    ("lumi united", "Aqara"),
    ("xiaomi", "Xiaomi"),
    ("broadlink", "Broadlink"),
    ("hikvision", "Camera"),
    ("dahua", "Camera"),
    ("ezviz", "Camera"),
    ("reolink", "Camera"),
    ("signify", "Philips Hue"),
    ("philips lighting", "Philips Hue"),
    ("sonos", "Sonos"),
    ("beken", "Wi-Fi module"),
    ("raspberry", "Raspberry Pi"),
    ("google", "Google"),
    ("amazon", "Amazon"),
    ("apple", "Apple"),
    ("samsung", "Samsung"),
    ("lg electronics", "LG"),
    ("tp-link", "TP-Link"),
    ("ubiquiti", "Ubiquiti"),
]
SMART_KINDS = {
    "Tuya", "Tuya (port 6668)", "ESPHome", "Tasmota", "Shelly", "Sonoff", "Sonoff DIY", "Sonoff (eWeLink)",
    "HomeKit", "Matter", "Matter (pairing)", "Philips Hue", "Yeelight", "TP-Link Kasa", "WLED", "Xiaomi",
    "Aqara", "Broadlink", "ESP module", "Wi-Fi module", "Camera", "Google Cast", "Sonos", "Android TV",
}
OUI_FILES = [
    "/usr/share/arp-scan/ieee-oui.txt",
    "/opt/homebrew/share/arp-scan/ieee-oui.txt",
    "/usr/local/share/arp-scan/ieee-oui.txt",
]
_oui = None


def _load_oui():
    global _oui
    if _oui is not None:
        return _oui
    _oui = {}
    for path in OUI_FILES:
        if os.path.exists(path):
            with open(path, encoding="utf-8", errors="ignore") as handle:
                for line in handle:
                    if line.startswith("#") or "\t" not in line:
                        continue
                    prefix, vendor = line.rstrip("\n").split("\t", 1)
                    _oui[prefix.upper()] = vendor.strip()
            break
    return _oui


def is_private_mac(mac):
    return len(mac) >= 2 and mac[1].lower() in "26ae"


def vendor_for(mac):
    if not mac:
        return ""
    if is_private_mac(mac):
        return "Private address"
    digits = mac.replace(":", "").upper()
    table = _load_oui()
    for size in (9, 7, 6):
        vendor = table.get(digits[:size])
        if vendor:
            return vendor
    return ""


def normalize_mac(mac):
    parts = re.split(r"[:-]", mac.strip().lower())
    if len(parts) != 6:
        return ""
    return ":".join(part.zfill(2) for part in parts)


def _read_arp_table():
    table = {}
    if sys.platform.startswith("linux") and os.path.exists("/proc/net/arp"):
        with open("/proc/net/arp") as handle:
            next(handle, None)
            for line in handle:
                cols = line.split()
                if len(cols) >= 4 and cols[2] != "0x0" and cols[3] != "00:00:00:00:00:00":
                    table[cols[0]] = normalize_mac(cols[3])
        return table
    if shutil.which("arp"):
        import subprocess

        out = subprocess.run(["arp", "-an"], capture_output=True, text=True, timeout=10).stdout
        for match in re.finditer(r"\(([\d.]+)\) at ([0-9a-fA-F:]+) ", out):
            mac = normalize_mac(match.group(2))
            if mac and mac != "ff:ff:ff:ff:ff:ff":
                table[match.group(1)] = mac
    return table


def _discovery_request(local_ip):
    """Packet that asks protocol 3.5 devices to announce themselves on UDP 7000."""
    if not tinytuya.AESCipher.CRYPTOLIB_HAS_GCM:
        return None
    body = json.dumps({"from": "app", "ip": local_ip}).encode()
    msg = tinytuya.TuyaMessage(0, tinytuya.REQ_DEVINFO, None, body, 0, True, tinytuya.PREFIX_6699_VALUE, True)
    return tinytuya.pack_message(msg, hmac_key=tinytuya.udpkey)


class _TuyaListener(asyncio.DatagramProtocol):
    def __init__(self, found):
        self.found = found

    def datagram_received(self, data, addr):
        try:
            info = json.loads(tinytuya.decrypt_udp(data))
        except Exception:  # noqa: BLE001
            return
        dev_id = info.get("gwId") or info.get("id")
        if not dev_id or info.get("from") == "app":
            return
        ip = info.get("ip") or addr[0]
        self.found[ip] = {
            "id": dev_id,
            "ip": ip,
            "version": str(info.get("version") or ""),
            "product_key": info.get("productKey") or "",
        }


def classify(host):
    services = {s["type"] for s in host.get("services", [])}
    title = (host.get("title") or "").lower()
    ports = set(host.get("ports", []))
    vendor = (host.get("vendor") or "").lower()
    if host.get("tuya"):
        return "Tuya"
    if 8123 in ports:
        return "Home Assistant"
    for service in ("ESPHome", "Shelly", "WLED", "Philips Hue", "Sonoff (eWeLink)", "Matter (pairing)", "Matter",
                    "HomeKit", "Google Cast", "Sonos", "Xiaomi", "Android TV", "Home Assistant", "Camera",
                    "Printer", "AirPlay", "MQTT broker"):
        if service in services:
            return service
    if "tasmota" in title:
        return "Tasmota"
    if "esphome" in title:
        return "ESPHome"
    if "shelly" in title:
        return "Shelly"
    port_kinds = [
        (6053, "ESPHome"), (8081, "Sonoff DIY"), (6668, "Tuya (port 6668)"), (55443, "Yeelight"),
        (9999, "TP-Link Kasa"), (1400, "Sonos"), (8008, "Google Cast"), (8123, "Home Assistant"),
        (37777, "Camera"), (554, "Camera"), (5540, "Matter"), (1883, "MQTT broker"),
    ]
    for port, kind in port_kinds:
        if port in ports:
            return kind
    for needle, kind in VENDOR_KINDS:
        if needle in vendor:
            return kind
    if vendor == "private address":
        return "Phone or tablet"
    return "Unknown"


class Scanner:
    def __init__(self, ha, cloud, tuya_local):
        self.ha = ha
        self.cloud = cloud
        self.tuya_local = tuya_local
        last = store.load("scan") or {}
        self.results = last.get("results", [])
        self.cidr = last.get("cidr")
        self.finished_at = last.get("finished_at")
        self.tuya_seen = last.get("tuya_seen", {})
        self.running = False
        self.phase = ""
        self.progress = 0
        self.started_at = None
        self.error = None
        self.notes = []
        self._task = None

    def status(self):
        return {
            "running": self.running,
            "phase": self.phase,
            "progress": self.progress,
            "cidr": self.cidr,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "error": self.error,
            "notes": self.notes,
            "results": self.results,
        }

    def tuya_ip(self, device_id):
        return self.tuya_seen.get(device_id)

    def networks(self):
        seen = {}
        for adapter in ifaddr.get_adapters():
            if SKIP_IFACES.match(adapter.nice_name or ""):
                continue
            for ip in adapter.ips:
                if not isinstance(ip.ip, str):
                    continue
                iface = ipaddress.ip_interface(f"{ip.ip}/{ip.network_prefix}")
                if iface.ip.is_loopback or iface.ip.is_link_local or not iface.ip.is_private:
                    continue
                if iface.ip in ipaddress.ip_network("172.30.32.0/23") or iface.ip in ipaddress.ip_network("172.17.0.0/16"):
                    continue
                net = iface.network
                if net.num_addresses > MAX_ADDRESSES:
                    net = ipaddress.ip_network(f"{iface.ip}/24", strict=False)
                key = str(net)
                if key not in seen:
                    seen[key] = {
                        "cidr": key,
                        "interface": adapter.nice_name,
                        "address": str(iface.ip),
                        "hosts": max(net.num_addresses - 2, 1),
                    }
        return sorted(seen.values(), key=lambda n: (not n["interface"].startswith(("wlan", "end", "eth", "en")), n["cidr"]))

    def _local_for(self, net):
        for item in self.networks():
            if ipaddress.ip_address(item["address"]) in net:
                return item["address"], item["interface"]
        return "", ""

    async def wifi(self):
        info = await self.ha.supervisor("GET", "/network/info")
        wlans = [i for i in info.get("interfaces", []) if i.get("type") == "wireless"]
        if not wlans:
            return {"available": False, "interface": None, "connected_ssid": None, "access_points": []}
        wlan = wlans[0]
        name = wlan["interface"]
        connected = (wlan.get("wifi") or {}).get("ssid") if wlan.get("connected") else None
        data = await self.ha.supervisor("GET", f"/network/interface/{name}/accesspoints", timeout=90)
        points = []
        for ap in (data or {}).get("accesspoints", []):
            ssid = ap.get("ssid") or ""
            freq = ap.get("frequency") or 0
            points.append({
                "ssid": ssid,
                "mac": (ap.get("mac") or "").lower(),
                "signal": ap.get("signal"),
                "frequency": freq,
                "band": "5 GHz" if freq >= 4900 else "2.4 GHz",
                "pairing": bool(ssid and PAIRING_SSID.match(ssid)),
                "connected": bool(connected and ssid == connected),
                "vendor": vendor_for((ap.get("mac") or "").lower()),
            })
        points.sort(key=lambda p: (not p["pairing"], not p["connected"], -(p["signal"] or 0)))
        return {"available": True, "interface": name, "connected_ssid": connected, "access_points": points}

    def start(self, cidr, sweep=True, mdns=True):
        if self.running:
            raise RuntimeError("A scan is already running.")
        try:
            net = ipaddress.ip_network(cidr, strict=False)
        except ValueError as exc:
            raise RuntimeError(f"{cidr} is not a valid network. Use a form like 192.168.100.0/24.") from exc
        if net.version != 4 or net.num_addresses > MAX_ADDRESSES:
            raise RuntimeError("Pick an IPv4 network of /22 or smaller (up to 1,024 addresses).")
        self.running = True
        self.started_at = time.time()
        self.error = None
        self.notes = []
        self.progress = 0
        self.phase = "Starting"
        self._task = asyncio.create_task(self._run(net, sweep, mdns))

    async def _run(self, net, sweep, mdns):
        try:
            local_ip, iface = self._local_for(net)
            if not local_ip:
                self.notes.append("This host has no address in that network, so broadcasts and ARP will not reach it.")
            tuya_found, mdns_found, open_ports, alive = {}, {}, {}, set()
            jobs = [self._tuya_listen(net, local_ip, tuya_found, 18 if sweep else 12)]
            if sweep:
                jobs.append(self._sweep(net, open_ports, alive))
                if mdns:
                    jobs.append(self._mdns(mdns_found, 8))
            await asyncio.gather(*jobs)
            await self._merge_ha_discovery(tuya_found)
            now = time.time()
            for ip, info in tuya_found.items():
                self.tuya_seen[info["id"]] = {**info, "at": now}

            registries = None
            tl_entries = []
            try:
                registries = await self.ha.registries()
                tl_entries = await self.tuya_local.entries(details=True, registries=registries)
            except HAError as exc:
                self.notes.append(f"Could not read Home Assistant devices: {exc}")

            if sweep:
                self.phase = "Reading MAC addresses"
                arp = await self._arp(net, iface)
                ips = set(arp) | set(open_ports) | alive | set(tuya_found)
                ips |= {ip for ip in mdns_found if _in(ip, net)}
                ips = {ip for ip in ips if _in(ip, net)}
                self.phase = "Looking up names"
                names, titles = await asyncio.gather(
                    self._reverse_dns(ips),
                    self._http_titles([ip for ip in ips if 80 in open_ports.get(ip, ())]),
                )
                hosts = []
                for ip in ips:
                    mac_info = arp.get(ip, {})
                    services = mdns_found.get(ip, [])
                    mac = mac_info.get("mac") or next((normalize_mac(s["mac"]) for s in services if s.get("mac")), "")
                    hosts.append({
                        "ip": ip,
                        "mac": mac,
                        "vendor": mac_info.get("vendor") or vendor_for(mac),
                        "hostname": names.get(ip) or next((s["host"] for s in services if s.get("host")), ""),
                        "ports": sorted(open_ports.get(ip, ())),
                        "services": services,
                        "title": (titles.get(ip) or {}).get("title", ""),
                        "is_self": ip == local_ip,
                    })
                self.results = self._annotate(hosts, tuya_found, registries, tl_entries)
                self.cidr = str(net)
            else:
                self._merge_tuya_only(tuya_found, registries, tl_entries)
            self.finished_at = time.time()
            store.save("scan", {
                "results": self.results,
                "cidr": self.cidr,
                "finished_at": self.finished_at,
                "tuya_seen": self.tuya_seen,
            })
        except Exception as exc:  # noqa: BLE001
            _LOGGER.exception("Scan failed")
            self.error = str(exc)
        finally:
            self.running = False
            self.phase = ""
            self.progress = 100

    def _annotate(self, hosts, tuya_found, registries, tl_entries):
        by_mac = {}
        if registries:
            for device in registries["devices"]:
                for kind, value in device.get("connections") or []:
                    if kind == "mac":
                        by_mac[normalize_mac(value)] = device
        tl_by_host = {e["host"]: e for e in tl_entries if e.get("host")}
        tl_by_id = {e["device_id"]: e for e in tl_entries if e.get("device_id")}
        for host in hosts:
            tuya = tuya_found.get(host["ip"])
            if tuya:
                cloud = self.cloud.by_id(tuya["id"])
                host["tuya"] = {
                    **tuya,
                    "cloud_name": cloud["name"] if cloud else None,
                    "in_cloud": bool(cloud),
                }
            else:
                host["tuya"] = None
            entry = tl_by_host.get(host["ip"]) or (tl_by_id.get(tuya["id"]) if tuya else None)
            host["tuya_local"] = (
                {"entry_id": entry["entry_id"], "title": entry["title"], "state": entry["state"]} if entry else None
            )
            device = by_mac.get(host["mac"]) if host["mac"] else None
            host["ha"] = (
                {
                    "device_id": device["id"],
                    "name": device.get("name_by_user") or device.get("name"),
                    "integration": next(
                        (registries["entry_domains"].get(e) for e in device.get("config_entries") or []), None
                    ),
                }
                if device
                else None
            )
            if not host["ha"] and entry and entry.get("ha_device_id"):
                host["ha"] = {"device_id": entry["ha_device_id"], "name": entry["title"], "integration": "tuya_local"}
            host["private_mac"] = is_private_mac(host["mac"]) if host["mac"] else False
            host["kind"] = classify(host)
            host["smart"] = host["kind"] in SMART_KINDS
        hosts.sort(key=lambda h: tuple(int(p) for p in h["ip"].split(".")))
        return hosts

    def _merge_tuya_only(self, tuya_found, registries, tl_entries):
        by_ip = {h["ip"]: h for h in self.results}
        for ip, info in tuya_found.items():
            for host in self.results:
                if host.get("tuya") and host["tuya"]["id"] == info["id"] and host["ip"] != ip:
                    host["tuya"] = None
            if ip not in by_ip:
                by_ip[ip] = {"ip": ip, "mac": "", "vendor": "", "hostname": "", "ports": [], "services": [],
                             "title": "", "is_self": False}
        hosts = list(by_ip.values())
        self.results = self._annotate(hosts, {**{h["ip"]: h["tuya"] for h in hosts if h.get("tuya")}, **tuya_found},
                                      registries, tl_entries)

    async def _merge_ha_discovery(self, tuya_found):
        """Tuya Local raises discovery flows for unconfigured devices; use them as a second source."""
        try:
            flows = await self.ha.call("config_entries/flow/progress")
        except HAError:
            return
        known_ids = {info["id"] for info in tuya_found.values()}
        for flow in flows or []:
            if flow.get("handler") != "tuya_local":
                continue
            context = flow.get("context") or {}
            dev_id = context.get("unique_id")
            host = (context.get("title_placeholders") or {}).get("name")
            if dev_id and host and dev_id not in known_ids and re.match(r"^\d+\.\d+\.\d+\.\d+$", host):
                tuya_found[host] = {"id": dev_id, "ip": host, "version": "", "product_key": ""}

    async def _tuya_listen(self, net, local_ip, found, seconds):
        loop = asyncio.get_running_loop()
        transports = []
        busy = []
        for port in TUYA_PORTS:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            if hasattr(socket, "SO_REUSEPORT"):
                try:
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
                except OSError:
                    pass
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            try:
                sock.bind(("", port))
            except OSError:
                busy.append(str(port))
                sock.close()
                continue
            sock.setblocking(False)
            transport, _ = await loop.create_datagram_endpoint(lambda: _TuyaListener(found), sock=sock)
            transports.append(transport)
        if busy:
            self.notes.append(f"UDP ports {', '.join(busy)} are busy, so some Tuya broadcasts may be missed.")
        payload = _discovery_request(local_ip) if local_ip else None
        sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sender.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        try:
            sender.bind((local_ip, 0))
        except OSError:
            pass
        sender.setblocking(False)
        try:
            end = time.time() + seconds
            while time.time() < end:
                if payload:
                    try:
                        sender.sendto(payload, (str(net.broadcast_address), 7000))
                    except OSError:
                        pass
                if not self.phase.startswith(("Probing", "Reading", "Looking")):
                    self.phase = f"Listening for Tuya devices ({len(found)} found)"
                await asyncio.sleep(min(3, max(0.1, end - time.time())))
        finally:
            sender.close()
            for transport in transports:
                transport.close()

    async def _sweep(self, net, open_ports, alive):
        hosts = [str(h) for h in net.hosts()]
        total = len(hosts) * len(PORTS)
        done = 0
        semaphore = asyncio.Semaphore(256)

        async def probe(ip, port):
            nonlocal done
            async with semaphore:
                try:
                    _, writer = await asyncio.wait_for(asyncio.open_connection(ip, port), 0.8)
                    open_ports.setdefault(ip, set()).add(port)
                    alive.add(ip)
                    writer.close()
                except ConnectionRefusedError:
                    alive.add(ip)
                except (OSError, asyncio.TimeoutError):
                    pass
                finally:
                    done += 1
                    self.progress = int(done * 100 / total)
                    self.phase = f"Probing {len(hosts)} addresses ({len(alive)} answering)"

        await asyncio.gather(*(probe(ip, port) for port in PORTS for ip in hosts))

    async def _arp(self, net, iface):
        result = {}
        if shutil.which("arp-scan") and hasattr(os, "geteuid") and os.geteuid() == 0:
            args = ["arp-scan", "--plain", "--retry=2"]
            if iface:
                args.append(f"--interface={iface}")
            args.append(str(net))
            try:
                proc = await asyncio.create_subprocess_exec(
                    *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
                )
                out, _ = await asyncio.wait_for(proc.communicate(), 90)
                for line in out.decode(errors="ignore").splitlines():
                    cols = line.split("\t")
                    if len(cols) >= 2 and re.match(r"^\d+\.\d+\.\d+\.\d+$", cols[0]):
                        vendor = cols[2].strip() if len(cols) > 2 else ""
                        if vendor.startswith("(Unknown"):
                            vendor = ""
                        result[cols[0]] = {"mac": normalize_mac(cols[1]), "vendor": vendor}
            except Exception as exc:  # noqa: BLE001
                self.notes.append(f"arp-scan failed ({exc}); used the system ARP table instead.")
        table = await asyncio.to_thread(_read_arp_table)
        for ip, mac in table.items():
            if _in(ip, net) and ip not in result and mac:
                result[ip] = {"mac": mac, "vendor": ""}
        for info in result.values():
            if not info["vendor"]:
                info["vendor"] = vendor_for(info["mac"])
        return result

    async def _mdns(self, found, seconds):
        aiozc = AsyncZeroconf(ip_version=IPVersion.V4Only)
        names = set()

        def on_change(zeroconf, service_type, name, state_change):
            if state_change is ServiceStateChange.Added:
                names.add((service_type, name))

        browser = AsyncServiceBrowser(aiozc.zeroconf, list(MDNS_TYPES), handlers=[on_change])
        try:
            await asyncio.sleep(seconds)
        finally:
            await browser.async_cancel()

        async def resolve(service_type, name):
            info = AsyncServiceInfo(service_type, name)
            if not await info.async_request(aiozc.zeroconf, 2500):
                return
            props = {}
            for key, value in (info.properties or {}).items():
                if key:
                    props[key.decode(errors="ignore")] = value.decode(errors="ignore") if isinstance(value, bytes) else value
            label = props.get("fn") or props.get("friendly_name") or name[: -len(service_type) - 1]
            for address in info.parsed_addresses(IPVersion.V4Only):
                found.setdefault(address, []).append({
                    "type": MDNS_TYPES[service_type],
                    "name": label,
                    "host": (info.server or "").rstrip(".").removesuffix(".local"),
                    "mac": props.get("mac") or "",
                })

        try:
            await asyncio.gather(*(resolve(t, n) for t, n in names))
        finally:
            await aiozc.async_close()

    async def _reverse_dns(self, ips):
        names = {}
        semaphore = asyncio.Semaphore(8)

        async def one(ip):
            async with semaphore:
                try:
                    host = await asyncio.wait_for(asyncio.to_thread(socket.gethostbyaddr, ip), 1.5)
                    if host[0] and host[0] != ip:
                        names[ip] = host[0]
                except Exception:  # noqa: BLE001
                    pass

        await asyncio.gather(*(one(ip) for ip in ips))
        return names

    async def _http_titles(self, ips):
        titles = {}
        if not ips:
            return titles
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=3)) as session:

            async def one(ip):
                try:
                    async with session.get(f"http://{ip}/", allow_redirects=True, ssl=False) as resp:
                        body = (await resp.content.read(65536)).decode(errors="ignore")
                        match = re.search(r"<title[^>]*>(.*?)</title>", body, re.I | re.S)
                        titles[ip] = {"title": html.unescape(match.group(1)).strip()[:80] if match else ""}
                except Exception:  # noqa: BLE001
                    pass

            await asyncio.gather(*(one(ip) for ip in ips))
        return titles


def _in(ip, net):
    try:
        return ipaddress.ip_address(ip) in net
    except ValueError:
        return False
