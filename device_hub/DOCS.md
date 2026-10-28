# Device Hub

Device Hub puts Tuya, Tuya Local, Zigbee2MQTT, ZHA, Sonoff (SonoffLAN) and the devices on your network on one screen inside Home Assistant. From there you can control every device, sort them into areas, build automations, and hand other apps a key to the same controls through an HTTP API or MCP.

- [Install](#install)
- [Configuration](#configuration)
- [What you need](#what-you-need)
- [The panel](#the-panel)
- [Removing devices](#removing-devices)
- [Automations](#automations)
- [API](#api)
- [MCP for assistants](#mcp-for-assistants)
- [What Device Hub stores](#what-device-hub-stores)
- [Troubleshooting](#troubleshooting)
- [Developing](#developing)

## Install

1. In Home Assistant, open **Settings → Add-ons → Add-on Store**.
2. Open **⋮ → Repositories** and add `https://github.com/elmgdad/haos-devices-gateway`.
3. Find **Device Hub** in the store, install it, and press **Start**.
4. Open **Device Hub** from the sidebar. Only administrators see it.

Updates appear in the same place. If a new version does not show up, use **⋮ → Check for updates** in the Add-on Store.

## Configuration

All options are optional. You can also sign in to Tuya from the panel's Tuya tab instead.

| Option | What it is |
| --- | --- |
| `tuya_access_id` | Access ID (Client ID) of your cloud project on [iot.tuya.com](https://iot.tuya.com), under Cloud, Development. |
| `tuya_access_secret` | Access Secret of the same project. |
| `tuya_region` | The project's data center: `eu` Central Europe, `eu-w` Western Europe, `us` Western America, `us-e` Eastern America, `in` India, `sg` Singapore, `cn` China. |

The Tuya project needs your Smart Life account linked under **Devices → Link App Account**, or it sees no devices.

Device Hub uses the host network and listens on port **8765** for the API and MCP. The panel itself is reached through Home Assistant's sidebar.

## What you need

Device Hub reads what is already in Home Assistant, so each system is only shown once it is set up there. The **Setup** tab checks all of this and links to the fix.

| | Needed for |
| --- | --- |
| **MQTT** (the Mosquitto broker add-on and the MQTT integration) | Required. Tuya devices that Device Hub controls on your network, and learned IR remotes, appear in Home Assistant through MQTT. |
| **Tuya cloud project** | Required for Tuya. Device list, local keys, IR remotes. |
| **Tuya Local** (HACS) | Optional. Local control of Tuya devices through Home Assistant. |
| **SonoffLAN** (HACS) | Optional. Sonoff and eWeLink devices. |
| **Zigbee2MQTT** or **ZHA** | Optional. Zigbee devices. Use one or the other, not both. |

## The panel

The top bar shows every system with a count and a status light. The icons on its right open **Setup**, **Activity** and **Settings**.

**All devices** lists every device from every system.
- Filter by system, by status (online or offline), or by **Needs attention**, and search by name, area or address.
- Click a device's name, or the sliders icon, to open its drawer:
  - **Control**: switches, dimmers, numbers, modes, covers, climate and IR keys. It updates live.
  - **Details**: rename, area, hardware, firmware and entities.
  - **Activity**: that device's own history.
- Tick several devices to move them to an area, turn them on or off, or remove them. With the status set to Offline, one button removes every offline device.

**Areas** shows each room with its devices.
- Turn any device on or off, or the whole room with **All on** / **All off**.
- Add, rename and delete areas. Deleting an area keeps its devices; they're left without an area.
- Devices without an area are listed separately.

**Automations** lists every Home Assistant automation.
- Turn any of them on or off, run them, or open them in Home Assistant.
- **New automation** builds one. See [Automations](#automations).

**Tuya cloud** and **Tuya Local**:
- Devices with their local keys and network addresses.
- Add them to Tuya Local, one at a time or in bulk, and fix entries whose key or IP has changed.
- Control devices through the cloud, and learn or use IR remotes.
- **Local control** keeps a device's local key, address and data points in Device Hub. Its switches and sensors then appear in Home Assistant through MQTT and work without the cloud.

**Zigbee2MQTT** and **ZHA**: open pairing, rename, reconfigure and remove Zigbee devices.

**Sonoff**: SonoffLAN devices, whether each is connected locally or through the cloud, signal strength, switches, and area.

**Network** scans your network for smart devices, so Device Hub learns their IP addresses. It can also start a Wi-Fi hotspot on the Home Assistant host, to pair devices that need one.

**Setup** is a checklist of what is connected and what is missing, each item with a button that leads to the fix.

**Activity** logs:
- commands from the panel, the API and MCP;
- renames, area changes and removals;
- API keys being created or revoked;
- automations running;
- devices going offline or coming back. Device Hub checks every minute.

**Settings**:
- your preferences and the Tuya cloud project;
- **Share**, where you create API keys and find the API docs, Swagger UI and MCP details;
- **Reset Device Hub**, which forgets everything Device Hub saved.

## Removing devices

Removing a device does what that device's own system does when you remove one:

| System | What removing does |
| --- | --- |
| Tuya cloud | Tuya unbinds the device from your Smart Life account. Reset it and pair it again to use it. Tuya refuses devices that the cloud project is not allowed to change; remove those in the Smart Life app. |
| Tuya Local | Its Tuya Local entry and entities are removed from Home Assistant. |
| Sonoff | It is removed from Home Assistant. SonoffLAN adds it back on its next start while it is still in your eWeLink account, so delete it in the eWeLink app to remove it for good. |
| ZHA, Zigbee2MQTT | The device is asked to leave the Zigbee network. |
| IR remote | Device Hub forgets the learned remote. |

Devices seen only by the network scan cannot be removed.

## Automations

Device Hub writes real Home Assistant automations, so they keep running when Device Hub is stopped.

- **When:** at a time, at sunrise or sunset (with an offset in minutes), or when a device control changes: it turns on or off, becomes a value, or goes above or below a number. A device trigger can also wait until the change has lasted a number of seconds.
- **Days:** only on the weekdays you pick, or every day.
- **Then:** set a device control (on or off, a level, an option, brightness for dimmable lights), turn everything in an area on or off, or wait.

Device Hub edits and deletes only the automations it made; their ids start with `devicehub_`. Every other automation can be turned on or off, run, or opened in Home Assistant.

A few controls work only through Device Hub itself, such as IR air conditioner settings and Tuya cloud-only controls, so they can't be used in an automation. The builder only offers the controls that can.

## API

The API answers on your network at `http://<home-assistant-ip>:8765/api/v1`. **Settings → Share** shows the exact address.

| Page | Where |
| --- | --- |
| Docs, with examples | `/api/v1` in a browser |
| Swagger UI, to try requests | `/api/v1/swagger` (loads its script from a CDN, so the browser needs internet) |
| OpenAPI 3.1 document | `/api/v1/openapi.json` |

### Keys

Create keys in **Settings → Share**, and send them on every request:

```
Authorization: Bearer dh_your_key
```

Each key has:
- **the devices it may see**: the ones you pick, or all devices, including ones added later;
- **what it may do**:

  | Scope | Can |
  | --- | --- |
  | read | See devices, areas, scenes, automations, activity and live updates. |
  | control | Also send commands, turn areas on or off, and run, enable or disable automations and scenes. |
  | manage | Also rename and remove devices, move them between areas, edit areas, and create, edit or delete automations. |

- **an optional expiry**, from one day to a year.

Keys made before scopes existed are control keys. Area changes and every automation endpoint need a key shared with all devices, because they can affect any device. A key can never see local keys or your Tuya secret.

### Endpoints

| Method | Path | Scope | What it does |
| --- | --- | --- | --- |
| GET | `/devices` | read | Devices with their controls. Filter with `?status=online\|offline`, `area`, `source`, `q`. |
| GET | `/devices/{id}` | read | One device. |
| POST | `/devices/{id}/commands` | control | Change controls: `{"commands": [{"code": "switch_1", "value": true}]}`. |
| PATCH | `/devices/{id}` | manage | Rename or move: `{"name": "…", "area_id": "kitchen"}`. |
| DELETE | `/devices/{id}` | manage | Remove the device from its system. |
| GET | `/areas` | read | Areas with device counts and how many are on or offline. |
| GET | `/areas/{id}` | read | One area with its devices. |
| POST | `/areas/{id}/commands` | control | `{"action": "on"}` or `"off"` for everything in it. |
| POST, PATCH, DELETE | `/areas`, `/areas/{id}` | manage | Create, rename, delete areas. |
| GET | `/automations`, `/automations/{id}` | read | Automations, with the spec of ones Device Hub made. |
| POST | `/automations/{id}/run` | control | Run it now. |
| POST | `/automations/{id}/enabled` | control | `{"enabled": false}` |
| POST, PUT, DELETE | `/automations`, `/automations/{id}` | manage | Create, replace or delete Device Hub automations. |
| GET | `/scenes` | read | Saved scenes. |
| POST | `/scenes`, `/scenes/{id}/run` | control | Save a scene that mixes systems, run it. |
| DELETE | `/scenes/{id}` | control | Delete a scene. |
| GET | `/activity` | read | Recent activity for this key's devices. |
| GET | `/events` | read | Live updates as Server-Sent Events. |

### Values

- Switches, lights, fans and locks take `true` or `false`.
- Dimmable lights also take `"brightness": 0-100` in the command.
- Numbers take a value in their range, and selects take one of their options.
- Covers take `open`, `close`, `stop` or a position from 0 to 100.
- Buttons and IR keys are pressed by any value.
- Climate takes a number for the temperature, or a mode such as `heat`.

```bash
curl -H "Authorization: Bearer dh_your_key" http://192.168.1.10:8765/api/v1/devices?status=offline

curl -X POST -H "Authorization: Bearer dh_your_key" -H "Content-Type: application/json" \
  -d '{"commands": [{"code": "switch.kitchen_plug", "value": false}]}' \
  http://192.168.1.10:8765/api/v1/devices/sonoff:1002ba764f/commands
```

### Live updates

`GET /api/v1/events` keeps the connection open and sends:
- `state` when a control changes;
- `device` when a device goes offline or comes back;
- `activity` for new log entries.

Browsers cannot send headers with `EventSource`, so this endpoint also accepts `?key=`:

```js
const events = new EventSource("http://192.168.1.10:8765/api/v1/events?key=dh_your_key")
events.addEventListener("state", (e) => console.log(JSON.parse(e.data)))
```

Tuya devices that only work through the cloud do not send `state` events. Read them from `/devices` instead.

## MCP for assistants

Device Hub is also an MCP server, so Claude or another assistant can look at and control your home. It speaks Streamable HTTP at `http://<home-assistant-ip>:8765/mcp` and takes the same keys as the API.

```bash
claude mcp add --transport http device-hub http://192.168.1.10:8765/mcp \
  --header "Authorization: Bearer dh_your_key"
```

| Key | Tools |
| --- | --- |
| read | `list_devices`, `get_device`, `list_areas`, `list_scenes`, `list_automations`, `get_activity` |
| control | also `control_device`, `control_area`, `run_scene`, `run_automation`, `set_automation_enabled` |
| manage | also `rename_device`, `set_device_area` |

Start with a read key to try it. Give an assistant a control key only if you want it to switch things.

## What Device Hub stores

Everything lives in the add-on's `/data` folder, which Home Assistant includes in backups:

| File | Holds |
| --- | --- |
| `tuya.json` | The Tuya cloud sign-in, if you signed in from the panel. |
| `local_control.json` | Devices saved for local control: local key, address, data points. |
| `ir.json` | Learned IR remotes and buttons. |
| `share.json` | API keys. |
| `scenes.json` | Scenes saved through the API. |
| `automations.json` | The specs of automations Device Hub made, so they can be edited. |
| `activity.json` | The last 500 activity entries. |
| `scan.json`, `settings.json`, `hotspot.json` | The last network scan, your preferences, and the hotspot state. |

**Settings → Reset Device Hub** forgets all of this. It also removes the entities Device Hub added to Home Assistant. Tuya Local, Zigbee2MQTT and ZHA keep their own devices.

## Troubleshooting

- **The API returns 401.** The key is missing, unknown or expired. **403** means it lacks the scope, or the endpoint needs a key shared with all devices. The `error` field says which.
- **A Tuya device says "permission deny".** The cloud project is not allowed to read or change it, usually because it belongs to a home that is not linked to the project. Link that account under **Devices → Link App Account** on iot.tuya.com, or manage the device in Smart Life.
- **Removed Sonoff devices come back.** SonoffLAN reloads every device in your eWeLink account when it starts. Delete them in the eWeLink app.
- **Swagger UI stays blank.** It loads from a CDN. `/api/v1` and `/api/v1/openapi.json` work without internet.
- **Tuya API quota.** While the panel is open, Device Hub refreshes the Tuya device list every two minutes. The once-a-minute offline check reuses the last list, so it doesn't spend Tuya calls.

## Developing

The backend is Python (aiohttp) in `backend/`. The panel is React and TypeScript in `frontend/`, built into `www/`.

To run the backend on your computer against a real Home Assistant, create `ha-apps/.env.dev`:

```
HA_URL=http://192.168.1.10:8123
HA_TOKEN=<a long-lived access token>
TUYA_CLIENT_ID=<optional>
TUYA_SECRET=<optional>
TUYA_REGION=eu
```

Then start it from `device_hub/`. It runs in dev mode on `http://127.0.0.1:8765` and keeps its data in `ha-apps/.data`. In dev mode it leaves MQTT publishing to the add-on running on Home Assistant.

```bash
../.venv/bin/python -m backend.main
```

To work on the panel, run `npm run dev` in `frontend/`, which proxies `/api` to the backend. `npm run build` writes the built panel into `www/`; commit it with your change.

To release, raise `version` in `config.yaml` and `__version__` in `backend/__init__.py` together, add a `CHANGELOG.md` entry, and push. Home Assistant offers the update after **Check for updates**.
