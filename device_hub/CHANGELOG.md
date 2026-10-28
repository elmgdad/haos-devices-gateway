# 0.1.29

Device Hub has a logo: a hub joined to one dot for each system it brings together. It is the add-on icon and logo in Home Assistant, the panel's tab icon, and sits next to the title. The add-on has a README and a Documentation tab covering setup, every tab, removing devices, automations, the API, MCP, what is stored and troubleshooting.

# 0.1.28

Areas: every room with its devices, a switch per device, All on and All off, and adding, renaming or deleting areas. Automations: every Home Assistant automation with an on/off switch, last run and Run, and a builder for new ones (at a time, at sunrise or sunset, or when a device changes; on chosen days; set devices, switch an area, wait). Device Hub edits and deletes only the automations it made.

The API covers areas (detail, on/off, create, rename, delete) and automations (list, create, edit, delete, run, turn on or off). Device Hub is also an MCP server at /mcp for Claude and other assistants, using the same keys. Swagger UI is at /api/v1/swagger, linked from the docs page and Settings, Share.

# 0.1.27

Full control from one place. Every device in All Devices opens a drawer with its controls (switches, dimmers, numbers, modes, covers, climate, IR keys), its details (rename, area, hardware, entities) and its activity, and updates live. Pick several devices to move them to an area, turn them on or off, or remove them. Setup lists what is connected and how to fix what is missing. Activity logs commands, renames, removals, API keys and devices going offline or coming back.

The API has read, control and manage keys that can expire, rename and remove devices, areas, activity, live updates at /api/v1/events, and an OpenAPI document at /api/v1/openapi.json. Keys made before keep the control access they had. Device lists build in about a second instead of 15.

# 0.1.26

All Devices, Tuya and Sonoff filter by status: online or offline. Each device has a remove button, and with Offline picked, one button removes every offline device. Tuya devices are unbound from the Smart Life account. Sonoff devices are removed from Home Assistant; SonoffLAN adds them back on its next start while they are still in eWeLink.

# 0.1.25

All Devices loads when Home Assistant has a HomeKit Bridge. Its device identifier has three parts, which stopped the list with "too many values to unpack".

# 0.1.24

The share API saves scenes that mix Sonoff, Zigbee and Tuya, then runs them together. Smart RF Copier button 2 is the garage on/off. The air conditioner on the Smart Control Panel Mini is a remote. Cloud-only Tuya devices, including the presence sensors, are listed too.

# 0.1.23

Sonoff is a source. Devices from the SonoffLAN integration show up with their local address, signal and switches, and can be placed in an area and shared.

# 0.1.22

Settings has Reset Device Hub. It forgets saved devices, learned buttons, share keys and the Tuya sign-in, and removes the entities Device Hub added to Home Assistant.

# 0.1.21

With Tuya's IR service on, each TV remote shows every key Tuya has for it, including mute and input.

# 0.1.20

Tuya TV and AC remotes are sent through Tuya's IR service. When that service is off for the cloud project, Device Hub says so instead of reporting success. Learning a button also accepts a key pressed in the Smart Life app.

# 0.1.19

Reset, limit and calibration controls are no longer listed with a shared device's main controls.

# 0.1.18

Shared controls without their own name are labeled by what they are, such as Cover or Door.

# 0.1.17

A shared device that is not in Home Assistant no longer picks up unrelated entities.

# 0.1.16

A share key can cover all devices, including ones added later. Shared controls list their options, ranges and units.

# 0.1.15

Volume up and down, and channel up and down, are separate buttons in Home Assistant.

# 0.1.14

IR remotes. TV and AC remotes added on a control panel get buttons and AC controls in Device Hub, Home Assistant and the API. Any remote can be learned on a panel or Smart IR and is then sent on your network without the Tuya cloud.

# 0.1.13

A key from Settings can list and control the devices you choose. The same page documents the API. Each device can be placed in a Home Assistant area, and saved devices show the entity ids automations use.

# 0.1.12

Asking for controls on a device that is not in the Tuya project says so. A device Tuya lists but will not share still explains the Smart Life account.

# 0.1.11

Release notes for the Home Assistant update dialog.

# 0.1.10

A device saved for local control stays in the list while its network link pauses so Tuya Local can connect.

# 0.1.9

Adding a device to Tuya Local closes a setup that was already open for it, and pauses Device Hub's own link for the test.

# 0.1.8

Saving a Tuya device keeps its local key and controls, and publishes them to Home Assistant over the local network.
