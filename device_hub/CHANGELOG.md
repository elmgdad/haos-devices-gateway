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
