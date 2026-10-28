"""Signing in to Tuya with the Smart Life app: a user code and a QR code, no cloud project.

This is the login Home Assistant's own Tuya integration uses. The person reads
their user code in Smart Life (Me, Settings, Account and Security, User Code),
Device Hub shows a QR code for it, they scan it in Smart Life and confirm, and
Tuya hands back tokens that keep working. The SDK refreshes them; every new
pair is saved in /data/tuya_account.json.

The login covers the device list with local keys, status, specifications and
commands. Cloud-project features (IR remotes, the thing model, removing devices)
still need a project.
"""

import json
import logging
import time

import segno
from tuya_sharing.customerapi import CustomerApi, CustomerTokenInfo, SharingTokenListener
from tuya_sharing.exceptions import ApiRequestException
from tuya_sharing.user import LoginControl

from . import store

_LOGGER = logging.getLogger(__name__)

# Home Assistant's client id and schema, which the Smart Life app trusts for this login.
CLIENT_ID = "HA_3y9q4ak7g4ephrvke"
SCHEMA = "haauthorize"
STORE = "tuya_account"
QR_SECONDS = 120


class Account(SharingTokenListener):
    def __init__(self):
        self.info = store.load(STORE) or {}
        self.api = None
        self._pending = {}
        self._specs = {}
        if self.info.get("token_info"):
            self._open()

    @property
    def connected(self):
        return self.api is not None

    def public(self):
        return {
            "connected": self.connected,
            "username": self.info.get("username") or "",
            "user_code": self.info.get("user_code") or "",
            "since": self.info.get("since"),
        }

    def start(self, user_code):
        """Ask Tuya for a QR code for this user code. Returns the token to poll and the code as SVG."""
        user_code = (user_code or "").strip()
        if not user_code:
            raise RuntimeError("Enter the user code from Smart Life: Me, Settings, Account and Security, User Code.")
        reply = LoginControl().qr_code(CLIENT_ID, SCHEMA, user_code)
        if not reply.get("success"):
            raise RuntimeError(reply.get("msg") or "Tuya did not accept that user code. Check it in Smart Life and try again.")
        token = reply["result"]["qrcode"]
        content = f"tuyaSmart--qrLogin?token={token}"
        self._pending[token] = {"user_code": user_code, "at": time.monotonic()}
        # The standard 4-module quiet zone, and no fixed size, so the panel can scale it into its box.
        svg = segno.make(content, error="m").svg_inline(border=4, omitsize=True, dark="#111", light="#fff")
        return {"token": token, "qr": content, "svg": svg, "expires_in": QR_SECONDS}

    def finish(self, token):
        """Check whether the QR code was scanned and confirmed. Returns the account once it was, else None."""
        pending = self._pending.get(token)
        if not pending:
            raise RuntimeError("That QR code is gone. Start again.")
        if time.monotonic() - pending["at"] > QR_SECONDS:
            self._pending.pop(token, None)
            raise RuntimeError("The QR code expired. Start again.")
        ok, result = LoginControl().login_result(token, CLIENT_ID, pending["user_code"])
        if not ok:
            return None
        self._pending.pop(token, None)
        self.info = {
            "user_code": pending["user_code"],
            "terminal_id": result["terminal_id"],
            "endpoint": result["endpoint"],
            "username": result.get("username") or "",
            "since": time.time(),
            "token_info": {
                "t": result["t"],
                "uid": result["uid"],
                "expire_time": result["expire_time"],
                "access_token": result["access_token"],
                "refresh_token": result["refresh_token"],
            },
        }
        store.save(STORE, self.info)
        self._open()
        return self.public()

    def logout(self):
        if self.api is not None:
            try:
                self.api.refresh_access_token_if_need()
                self.api.post("/v1.0/m/token/terminal/expire", None, {
                    "accessToken": self.api.token_info.access_token,
                    "terminalId": self.info.get("terminal_id"),
                })
            except Exception:  # noqa: BLE001
                _LOGGER.info("Tuya did not confirm the sign-out; the saved tokens are forgotten anyway")
        self.api = None
        self.info = {}
        self._specs = {}
        store.remove(STORE)

    def update_token(self, token_info):
        """The SDK calls this after refreshing the tokens. Save them so a restart keeps the login."""
        self.info["token_info"] = token_info
        store.save(STORE, self.info)

    def fetch_devices(self):
        """Every device in every home of the account, in the shape tuya_cloud uses."""
        homes = self._get("/v1.0/m/life/users/homes")
        if not homes.get("success"):
            raise RuntimeError(homes.get("msg") or "Tuya did not return your homes.")
        devices = []
        for home in homes.get("result") or []:
            reply = self._get("/v1.0/m/life/ha/home/devices", {"homeId": str(home["ownerId"])})
            for item in reply.get("result") or [] if reply.get("success") else []:
                devices.append({
                    "id": item.get("id"),
                    "name": (item.get("name") or "Unnamed device").strip(),
                    "category": item.get("category") or "",
                    "category_name": item.get("category_name") or "",
                    "product_name": item.get("product_name") or "",
                    "product_id": item.get("product_id") or "",
                    "model": item.get("model") or "",
                    "online": item.get("online"),
                    "sub": bool(item.get("sub")),
                    "node_id": item.get("node_id") or "",
                    "gateway_id": item.get("gateway_id") or item.get("parent_id") or "",
                    "uuid": item.get("uuid") or "",
                    "mac": (item.get("mac") or "").lower(),
                    "local_key": item.get("local_key") or "",
                    "icon": item.get("icon") or "",
                    "home": home.get("name") or "",
                })
        devices = [d for d in devices if d["id"]]
        devices.sort(key=lambda d: d["name"].lower())
        return devices

    def read_control(self, device_id):
        """(status, functions, blocked), like tuya_cloud._read_control."""
        reply = self._get("/v1.0/m/life/ha/devices/detail", {"devIds": device_id})
        items = reply.get("result") or [] if reply.get("success") else []
        status = [{"code": s.get("code"), "value": s.get("value")} for s in (items[0].get("status") if items else []) or [] if s.get("code")]
        spec = self._spec(device_id)
        functions = [{"code": f["code"], "type": f.get("type") or "", "values": values_text(f.get("values")), "desc": f.get("desc") or f.get("name") or ""}
                     for f in spec.get("functions") or [] if f.get("code")]
        return status, functions, None

    def read_spec(self, device_id):
        """Data points with their ids, like tuya_cloud._read_spec, so the local bridge can talk to the device."""
        spec = self._spec(device_id)
        dp_ids = self._dp_ids(device_id)
        shaped = {}
        for part in ("functions", "status"):
            shaped[part] = [
                {"code": item["code"], "dp_id": item.get("dp_id") or dp_ids.get(item["code"]),
                 "type": item.get("type") or "", "values": values_text(item.get("values"))}
                for item in spec.get(part) or [] if item.get("code")
            ]
            shaped[part] = [item for item in shaped[part] if item["dp_id"] is not None]
        if not shaped["functions"] and not shaped["status"]:
            raise RuntimeError("Tuya publishes no data points for this device.")
        return shaped

    def send(self, device_id, commands):
        reply = self._post(f"/v1.1/m/thing/{device_id}/commands", {"commands": commands})
        if not reply.get("success"):
            raise RuntimeError(reply.get("msg") or "Tuya refused the command.")

    def _spec(self, device_id):
        cached = self._specs.get(device_id)
        if cached and time.monotonic() - cached[0] < 3600:
            return cached[1]
        reply = self._get(f"/v1.1/m/life/{device_id}/specifications")
        if not reply.get("success"):
            raise RuntimeError(reply.get("msg") or "Tuya did not return this device's data points.")
        result = reply.get("result") or {}
        self._specs[device_id] = (time.monotonic(), result)
        return result

    def _dp_ids(self, device_id):
        reply = self._get(f"/v1.0/m/life/devices/{device_id}/status")
        if not reply.get("success"):
            return {}
        relations = (reply.get("result") or {}).get("dpStatusRelationDTOS") or []
        return {item["statusCode"]: item["dpId"] for item in relations if item.get("statusCode") and item.get("dpId") is not None}

    def _open(self):
        info = self.info
        self.api = CustomerApi(CustomerTokenInfo(info["token_info"]), CLIENT_ID, info["user_code"], info["endpoint"], self)

    def _require(self):
        if self.api is None:
            raise RuntimeError("Sign in with the Smart Life app first.")
        return self.api

    def _get(self, path, params=None):
        return self._call(lambda api: api.get(path, params))

    def _post(self, path, body):
        return self._call(lambda api: api.post(path, None, body))

    def _call(self, request):
        """The SDK returns None or raises its own error; callers get a dict or a RuntimeError with Tuya's reason."""
        try:
            reply = request(self._require())
        except ApiRequestException as exc:
            message = getattr(exc, "error_message", None) or str(exc)
            if "token" in message.lower() and "invalid" in message.lower():
                raise RuntimeError("The Smart Life sign-in has expired. Sign in again with a new QR code.") from exc
            raise RuntimeError(message) from exc
        return reply or {}


def values_text(values):
    """Spec values arrive as JSON text or as a dict; callers want text."""
    return values if isinstance(values, str) else json.dumps(values or {})
