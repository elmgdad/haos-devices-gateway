"""Drive the Tuya Local (make-all/tuya-local) config and options flows."""

import asyncio
import re
import time

from .ha import HAError, HomeAssistant

DOMAIN = "tuya_local"
IP_PATTERN = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")
HACS_URL = "/hacs/repository/256928684"
DETAILS_SECONDS = 60
OPTION_FIELDS = ("local_key", "host", "protocol_version", "poll_only")

ABORT_MESSAGES = {
    "already_configured": "This device is already in Tuya Local.",
    "already_in_progress": "Tuya Local was already setting this device up. Close that setup in Home Assistant and try again.",
    "not_supported": "Tuya Local has no device file for this product yet.",
}
ERROR_MESSAGES = {
    "connection": "Could not talk to the device. Check the IP address, local key and protocol version.",
    "does_not_need_hub": "This device connects directly, not through a gateway.",
}


def _schema_defaults(schema):
    return {field["name"]: field.get("default") for field in schema or [] if "name" in field}


def _select_options(field):
    selector = (field.get("selector") or {}).get("select") or {}
    options = selector.get("options") or field.get("options") or []
    result = []
    for option in options:
        if isinstance(option, dict):
            result.append({"value": option.get("value"), "label": option.get("label") or option.get("value")})
        elif isinstance(option, (list, tuple)) and option:
            result.append({"value": option[0], "label": option[-1]})
        else:
            result.append({"value": option, "label": str(option)})
    return result


def _form_error(step):
    errors = step.get("errors") or {}
    code = errors.get("base") or next(iter(errors.values()), None)
    return ERROR_MESSAGES.get(code, code or "Tuya Local rejected the settings.")


class TuyaLocal:
    def __init__(self, ha: HomeAssistant):
        self.ha = ha
        self.bridge = None
        self._pending: dict[str, str] = {}
        self._details: dict[str, dict] = {}
        self._details_lock = asyncio.Semaphore(3)

    async def status(self):
        try:
            manifest = await self.ha.call("manifest/get", integration=DOMAIN)
            return {"installed": True, "version": manifest.get("version"), "hacs_url": HACS_URL}
        except HAError:
            return {"installed": False, "version": None, "hacs_url": HACS_URL}

    async def entries(self, details=True, registries=None):
        entries = await self.ha.call("config_entries/get", domain=DOMAIN)
        registries = registries or await self.ha.registries()
        by_entry = {}
        for device in registries["devices"]:
            for identifier in device.get("identifiers") or []:
                if identifier[0] != DOMAIN:
                    continue
                for entry_id in device.get("config_entries") or []:
                    by_entry[entry_id] = device, identifier[1]
        if details:
            await asyncio.gather(*(self._load_details(e["entry_id"]) for e in entries))
        result = []
        for entry in entries:
            device, ident = by_entry.get(entry["entry_id"], (None, ""))
            device_id, _, device_cid = (ident or "").partition("/")
            info = self._details.get(entry["entry_id"], {})
            title = entry.get("title") or ""
            host = info.get("host") or (title if IP_PATTERN.match(title) else None)
            result.append({
                "entry_id": entry["entry_id"],
                "title": entry.get("title") or "",
                "state": entry.get("state"),
                "reason": entry.get("reason"),
                "disabled_by": entry.get("disabled_by"),
                "device_id": device_id,
                "device_cid": device_cid,
                "ha_device_id": device["id"] if device else None,
                "area_id": device.get("area_id") if device else None,
                "area": registries["areas"].get(device.get("area_id")) if device else None,
                "host": host,
                "local_key": info.get("local_key"),
                "protocol_version": info.get("protocol_version"),
                "poll_only": info.get("poll_only"),
                "broken": bool(info.get("broken")),
            })
        return result

    async def _load_details(self, entry_id, force=False):
        cached = self._details.get(entry_id)
        if cached and not force and time.time() - cached["at"] < DETAILS_SECONDS:
            return cached
        async with self._details_lock:
            try:
                flow = await self.ha.rest("POST", "/config/config_entries/options/flow", {"handler": entry_id})
            except HAError:
                info = {"broken": True, "at": time.time()}
                self._details[entry_id] = info
                return info
            try:
                defaults = _schema_defaults(flow.get("data_schema"))
            finally:
                if flow.get("flow_id"):
                    try:
                        await self.ha.rest("DELETE", f"/config/config_entries/options/flow/{flow['flow_id']}")
                    except HAError:
                        pass
        info = {key: defaults.get(key) for key in OPTION_FIELDS}
        info["at"] = time.time()
        self._details[entry_id] = info
        return info

    async def update(self, entry_id, changes):
        flow = await self.ha.rest("POST", "/config/config_entries/options/flow", {"handler": entry_id})
        flow_id = flow["flow_id"]
        data = {key: value for key, value in _schema_defaults(flow.get("data_schema")).items() if key in OPTION_FIELDS}
        for key in OPTION_FIELDS:
            if changes.get(key) not in (None, ""):
                data[key] = changes[key]
        data["protocol_version"] = str(data.get("protocol_version") or "auto")
        try:
            step = await self.ha.rest("POST", f"/config/config_entries/options/flow/{flow_id}", data, timeout=240)
        except HAError:
            await self._drop_options_flow(flow_id)
            raise
        if step.get("type") != "create_entry":
            await self._drop_options_flow(flow_id)
            raise HAError(_form_error(step))
        self._details.pop(entry_id, None)
        return {"ok": True}

    async def _drop_options_flow(self, flow_id):
        try:
            await self.ha.rest("DELETE", f"/config/config_entries/options/flow/{flow_id}")
        except HAError:
            pass

    async def start_add(self, device_id, host, local_key, protocol_version="auto", device_cid=None):
        if not device_id or not host or not local_key:
            raise HAError("Device ID, IP address and local key are all required.")
        unique_id = f"{device_id}/{device_cid}" if device_cid else device_id
        await self._clear_flows(unique_id)
        if self.bridge:
            await self.bridge.hold(device_id)
        flow_id = None
        try:
            payload = {
                "device_id": device_id,
                "host": host,
                "local_key": local_key,
                "protocol_version": str(protocol_version or "auto"),
                "poll_only": False,
            }
            if device_cid:
                payload["device_cid"] = device_cid
            step = None
            for attempt in (1, 2):
                flow = await self.ha.rest(
                    "POST", "/config/config_entries/flow", {"handler": DOMAIN, "show_advanced_options": False}
                )
                flow_id = flow["flow_id"]
                stop = asyncio.Event()
                sweep = asyncio.create_task(self._sweep_flows(unique_id, stop, {flow_id}))
                try:
                    step = flow
                    if step.get("step_id") == "user":
                        step = await self.ha.rest("POST", f"/config/config_entries/flow/{flow_id}", {"setup_mode": "manual"})
                    if step.get("type") != "abort" and step.get("step_id") == "local":
                        step = await self.ha.rest("POST", f"/config/config_entries/flow/{flow_id}", payload, timeout=300)
                finally:
                    stop.set()
                    await sweep
                if step.get("type") == "abort" and step.get("reason") == "already_in_progress" and attempt == 1:
                    await self._delete_flow(flow_id)
                    flow_id = None
                    await self._clear_flows(unique_id)
                    continue
                break
            if step.get("type") == "abort":
                raise HAError(ABORT_MESSAGES.get(step.get("reason"), step.get("reason")))
            if step.get("type") == "form" and step.get("step_id") == "local":
                raise HAError(_form_error(step))
            if step.get("step_id") not in ("select_type", "select_type_auto_detected"):
                raise HAError(f"Tuya Local showed an unexpected step: {step.get('step_id')}.")
            self._pending[flow_id] = {"id": device_id, "replace": not device_cid}
            field = (step.get("data_schema") or [{}])[0]
            return {
                "flow_id": flow_id,
                "options": _select_options(field),
                "default": field.get("default"),
                "detected_protocol": (step.get("description_placeholders") or {}).get("detected_protocol"),
            }
        except Exception:
            if flow_id:
                self._pending.pop(flow_id, None)
                await self._delete_flow(flow_id)
            if self.bridge:
                await self.bridge.release(device_id)
            raise

    async def _sweep_flows(self, unique_id, stop, keep):
        """Drop discovery setups Tuya Local opens while the connection test runs."""
        while not stop.is_set():
            try:
                await self._clear_flows(unique_id, keep)
            except Exception:  # noqa: BLE001
                pass
            try:
                await asyncio.wait_for(stop.wait(), 0.4)
            except asyncio.TimeoutError:
                pass

    async def _clear_flows(self, unique_id, keep=()):
        try:
            flows = await self.ha.call("config_entries/flow/progress")
        except HAError:
            return
        prefix = unique_id.split("/", 1)[0]
        for flow in flows or []:
            if flow.get("handler") != DOMAIN or flow.get("flow_id") in keep:
                continue
            uid = str((flow.get("context") or {}).get("unique_id") or "")
            if uid == unique_id or uid == prefix or uid.startswith(f"{prefix}/"):
                await self._delete_flow(flow["flow_id"])

    async def _delete_flow(self, flow_id):
        try:
            await self.ha.rest("DELETE", f"/config/config_entries/flow/{flow_id}")
        except HAError:
            pass

    async def finish_add(self, flow_id, type_, name):
        try:
            step = await self.ha.rest("POST", f"/config/config_entries/flow/{flow_id}", {"type": type_})
            if step.get("type") == "form" and step.get("step_id") == "choose_entities":
                default_name = _schema_defaults(step.get("data_schema")).get("name")
                step = await self.ha.rest(
                    "POST", f"/config/config_entries/flow/{flow_id}", {"name": (name or "").strip() or default_name}
                )
            if step.get("type") == "abort":
                raise HAError(ABORT_MESSAGES.get(step.get("reason"), step.get("reason")))
            if step.get("type") != "create_entry":
                raise HAError(_form_error(step))
        except Exception:
            await self._delete_flow(flow_id)
            await self._resume(flow_id)
            raise
        await self._resume(flow_id, added=True)
        result = step.get("result") or {}
        return {"entry_id": result.get("entry_id"), "title": result.get("title") or step.get("title")}

    async def cancel(self, flow_id):
        await self._delete_flow(flow_id)
        await self._resume(flow_id)

    async def _resume(self, flow_id, added=False):
        pending = self._pending.pop(flow_id, None)
        if not pending or not self.bridge:
            return
        if added and pending["replace"]:
            await self.bridge.forget(pending["id"])
        else:
            await self.bridge.release(pending["id"])

    async def remove(self, entry_id):
        await self.ha.rest("DELETE", f"/config/config_entries/entry/{entry_id}")
        self._details.pop(entry_id, None)

    async def reload(self, entry_id):
        await self.ha.rest("POST", f"/config/config_entries/entry/{entry_id}/reload")
        self._details.pop(entry_id, None)
