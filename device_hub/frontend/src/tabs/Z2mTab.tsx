import { ExternalLink, Pencil, Play, RotateCw, Square, Trash2, Wrench } from "lucide-react"
import { useEffect, useMemo, useState } from "react"
import { api } from "../api"
import { errorText, openInHa, sourceColor, timeAgo, usePoll } from "../lib"
import type { Area, Z2mDevice } from "../types"
import { Badge, Button, Empty, Input, Led, Notice, Panel, SearchInput, Select, Signal, Td, Th, useFeedback } from "../ui"

export function useCountdown(endMs: number | null) {
  const [now, setNow] = useState(Date.now())
  useEffect(() => {
    if (!endMs) return
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [endMs])
  if (!endMs) return null
  const left = Math.max(0, Math.round((endMs - now) / 1000))
  return `${Math.floor(left / 60)}:${String(left % 60).padStart(2, "0")}`
}

export function AreaSelect({
  areas,
  value,
  deviceId,
  onSaved,
}: {
  areas: Area[]
  value: string | null
  deviceId: string | null
  onSaved: () => void
}) {
  const feedback = useFeedback()
  const [busy, setBusy] = useState(false)
  if (!deviceId) return <span className="text-xs text-muted">not in HA</span>
  return (
    <Select
      value={value ?? ""}
      disabled={busy}
      className="h-8 min-w-36 text-xs"
      onChange={async (e) => {
        setBusy(true)
        try {
          await api.updateHaDevice(deviceId, { area_id: e.target.value || null })
          onSaved()
        } catch (exc) {
          feedback.fail(errorText(exc))
        } finally {
          setBusy(false)
        }
      }}
    >
      <option value="">No area</option>
      {areas.map((area) => (
        <option key={area.id} value={area.id}>
          {area.name}
        </option>
      ))}
    </Select>
  )
}

export function RenameCell({ name, onSave }: { name: string; onSave: (next: string) => Promise<void> }) {
  const [editing, setEditing] = useState(false)
  const [value, setValue] = useState(name)
  const [busy, setBusy] = useState(false)
  if (!editing) {
    return (
      <span className="group inline-flex items-center gap-1.5">
        <span className="font-medium">{name}</span>
        <button
          className="rounded p-0.5 text-muted opacity-60 group-hover:opacity-100 hover:text-ink"
          onClick={() => {
            setValue(name)
            setEditing(true)
          }}
          aria-label={`Rename ${name}`}
          title="Rename"
        >
          <Pencil className="size-3.5" />
        </button>
      </span>
    )
  }
  const save = async () => {
    setBusy(true)
    try {
      await onSave(value)
      setEditing(false)
    } finally {
      setBusy(false)
    }
  }
  return (
    <form
      className="flex items-center gap-1.5"
      onSubmit={(e) => {
        e.preventDefault()
        void save()
      }}
    >
      <Input value={value} onChange={(e) => setValue(e.target.value)} className="h-8 w-44" autoFocus />
      <Button type="submit" variant="primary" busy={busy} className="h-8">
        Save
      </Button>
      <Button type="button" variant="ghost" className="h-8" onClick={() => setEditing(false)}>
        Cancel
      </Button>
    </form>
  )
}

export default function Z2mTab({ onChanged, zhaLoaded }: { onChanged: () => void; zhaLoaded: boolean }) {
  const feedback = useFeedback()
  const view = usePoll(api.z2m, 8000)
  const areas = usePoll(api.areas)
  const [busy, setBusy] = useState<string | null>(null)
  const [query, setQuery] = useState("")
  const data = view.data
  const countdown = useCountdown(data?.permit_join ? data.permit_join_end : null)

  const devices = useMemo(() => {
    const q = query.trim().toLowerCase()
    return (data?.devices ?? []).filter(
      (d) => !q || [d.name, d.model, d.vendor, d.description, d.ieee].some((v) => (v || "").toLowerCase().includes(q)),
    )
  }, [data, query])

  const run = async (id: string, action: () => Promise<unknown>, done: string) => {
    setBusy(id)
    try {
      await action()
      feedback.ok(done)
      await view.reload()
      onChanged()
    } catch (exc) {
      feedback.fail(errorText(exc))
    } finally {
      setBusy(null)
    }
  }

  const remove = async (device: Z2mDevice) => {
    const ok = await feedback.confirm({
      title: `Remove ${device.name}?`,
      body: "Zigbee2MQTT asks the device to leave the network and deletes it. Battery devices may need a button press to wake up.",
      action: "Remove device",
      danger: true,
    })
    if (!ok) return
    setBusy(`remove:${device.ieee}`)
    try {
      await api.z2mRemove(device.ieee, false)
      feedback.ok(`Removed ${device.name}.`)
    } catch (exc) {
      const force = await feedback.confirm({
        title: "The device did not answer",
        body: `${errorText(exc)} Force removal deletes it from Zigbee2MQTT without asking the device.`,
        action: "Force remove",
        danger: true,
      })
      if (force) {
        try {
          await api.z2mRemove(device.ieee, true)
          feedback.ok(`Removed ${device.name}.`)
        } catch (exc2) {
          feedback.fail(errorText(exc2))
        }
      }
    } finally {
      setBusy(null)
      await view.reload()
      onChanged()
    }
  }

  if (view.loading && !data) return <Panel><Empty title="Loading Zigbee2MQTT" /></Panel>
  if (!data) return <Notice tone="bad">Could not load Zigbee2MQTT: {view.error}</Notice>

  const addon = data.addon
  if (!addon.installed) {
    return (
      <Panel>
        <Empty title="Zigbee2MQTT is not installed">
          Install the Zigbee2MQTT app from its repository in Settings, Apps. Device Hub finds it automatically.
        </Empty>
      </Panel>
    )
  }
  const running = addon.state === "started"
  const coordinator = data.devices.find((d) => d.type === "Coordinator")

  return (
    <div className="space-y-4">
      {zhaLoaded && running && (
        <Notice tone="bad">
          ZHA and Zigbee2MQTT are both running. Make sure each one uses its own coordinator; two stacks on one dongle
          corrupt the network.
        </Notice>
      )}
      {data.subscribe_error && (
        <Notice tone="bad">Could not subscribe to MQTT topics: {data.subscribe_error}. Check the MQTT integration.</Notice>
      )}

      <Panel
        title={<span style={{ color: sourceColor("z2m") }}>Zigbee2MQTT</span>}
        aside={
          <>
            {addon.panel_path && running && (
              <Button icon={<ExternalLink className="size-4" />} onClick={() => openInHa(addon.panel_path!)}>
                Open Zigbee2MQTT
              </Button>
            )}
            {running ? (
              <>
                <Button
                  icon={<RotateCw className="size-4" />}
                  busy={busy === "restart"}
                  onClick={() => run("restart", () => api.z2mAddon("restart"), "Zigbee2MQTT restarted.")}
                >
                  Restart
                </Button>
                <Button
                  variant="danger"
                  icon={<Square className="size-4" />}
                  busy={busy === "stop"}
                  onClick={async () => {
                    const ok = await feedback.confirm({
                      title: "Stop Zigbee2MQTT?",
                      body: "All Zigbee2MQTT devices go unavailable in Home Assistant until you start it again.",
                      action: "Stop",
                      danger: true,
                    })
                    if (ok) await run("stop", () => api.z2mAddon("stop"), "Zigbee2MQTT stopped.")
                  }}
                >
                  Stop
                </Button>
              </>
            ) : (
              <Button
                variant="primary"
                icon={<Play className="size-4" />}
                busy={busy === "start"}
                onClick={() => run("start", () => api.z2mAddon("start"), "Zigbee2MQTT started.")}
              >
                Start
              </Button>
            )}
          </>
        }
      >
        <dl className="grid grid-cols-2 gap-x-6 gap-y-3 px-4 py-4 text-sm sm:grid-cols-3 lg:grid-cols-6">
          <div>
            <dt className="label text-[11px] text-muted">App</dt>
            <dd className="mt-0.5 flex items-center gap-1.5">
              <Led state={running ? "ok" : "off"} />
              {addon.state} <span className="text-muted">{addon.version}</span>
            </dd>
          </div>
          <div>
            <dt className="label text-[11px] text-muted">Bridge</dt>
            <dd className="mt-0.5 flex items-center gap-1.5">
              <Led state={data.bridge_state === "online" ? "ok" : running ? "warn" : "off"} />
              {data.bridge_state ?? "no data"}
            </dd>
          </div>
          <div>
            <dt className="label text-[11px] text-muted">Coordinator</dt>
            <dd className="mt-0.5">{data.coordinator ?? "–"}</dd>
          </div>
          <div>
            <dt className="label text-[11px] text-muted">Channel</dt>
            <dd className="mt-0.5 font-mono">{data.channel ?? "–"}</dd>
          </div>
          <div>
            <dt className="label text-[11px] text-muted">Base topic</dt>
            <dd className="mt-0.5 font-mono">{data.base_topic}</dd>
          </div>
          <div>
            <dt className="label text-[11px] text-muted">Coordinator IEEE</dt>
            <dd className="mt-0.5 truncate font-mono text-xs">{coordinator?.ieee ?? "–"}</dd>
          </div>
        </dl>
        <div className="flex flex-wrap items-center gap-3 border-t border-line px-4 py-3">
          {data.permit_join ? (
            <>
              <span className="inline-flex items-center gap-2 text-sm font-medium">
                <Led state="busy" />
                Joining is open{countdown ? `, ${countdown} left` : ""}. Put the new device in pairing mode now.
              </span>
              <Button busy={busy === "permit"} onClick={() => run("permit", () => api.z2mPermit(0), "Joining closed.")}>
                Close joining
              </Button>
            </>
          ) : (
            <>
              <Button
                variant="primary"
                disabled={!running || data.bridge_state !== "online"}
                busy={busy === "permit"}
                onClick={() => run("permit", () => api.z2mPermit(254), "Joining is open for about 4 minutes.")}
              >
                Allow devices to join
              </Button>
              <span className="text-sm text-muted">Opens the network for about 4 minutes.</span>
            </>
          )}
        </div>
      </Panel>

      <Panel
        title={`Devices (${data.devices.filter((d) => d.type !== "Coordinator").length})`}
        aside={<SearchInput value={query} onChange={setQuery} placeholder="Search name, model, IEEE" />}
      >
        {devices.length === 0 ? (
          <Empty title={running ? "No devices yet" : "Zigbee2MQTT is stopped"}>
            {running ? "Allow devices to join, then pair a device." : "Start Zigbee2MQTT to see its devices."}
          </Empty>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[1000px] text-sm">
              <thead>
                <tr>
                  <Th>Name</Th>
                  <Th>Device</Th>
                  <Th>Role</Th>
                  <Th>Link</Th>
                  <Th>Battery</Th>
                  <Th>Last seen</Th>
                  <Th>Area</Th>
                  <Th className="text-right">Actions</Th>
                </tr>
              </thead>
              <tbody>
                {devices.map((device) => (
                  <tr key={device.ieee} className="border-t border-line hover:bg-sunk/60">
                    <Td>
                      {device.type === "Coordinator" ? (
                        <span className="font-medium">Coordinator</span>
                      ) : (
                        <RenameCell
                          name={device.name}
                          onSave={async (next) => {
                            try {
                              await api.z2mRename(device.ieee, next)
                              feedback.ok(`Renamed to ${next}.`)
                              await view.reload()
                            } catch (exc) {
                              feedback.fail(errorText(exc))
                              throw exc
                            }
                          }}
                        />
                      )}
                      <div className="font-mono text-[11px] text-muted">{device.ieee}</div>
                    </Td>
                    <Td>
                      <div>
                        {[device.vendor, device.model].filter(Boolean).join(" ") ||
                          (device.type === "Coordinator" ? data.coordinator : null) ||
                          "Unknown"}
                      </div>
                      <div className="text-xs text-muted">{device.description}</div>
                      {device.supported === false && <Badge tone="bad">Not supported</Badge>}
                      {device.interview_completed === false && <Badge tone="bad">Interview incomplete</Badge>}
                    </Td>
                    <Td>
                      <div>{device.type}</div>
                      <div className="text-xs text-muted">{device.power_source}</div>
                    </Td>
                    <Td>
                      <div className="flex flex-col gap-1">
                        <Signal value={device.linkquality} />
                        {device.availability && (
                          <span className="inline-flex items-center gap-1.5 text-xs">
                            <Led state={device.availability === "online" ? "ok" : "warn"} />
                            {device.availability}
                          </span>
                        )}
                      </div>
                    </Td>
                    <Td mono>{device.battery !== null && device.battery !== undefined ? `${device.battery}%` : "–"}</Td>
                    <Td className="text-xs text-muted">{timeAgo(device.last_seen) || "–"}</Td>
                    <Td>
                      <AreaSelect
                        areas={areas.data ?? []}
                        value={device.area_id}
                        deviceId={device.ha_device_id}
                        onSaved={() => view.reload()}
                      />
                    </Td>
                    <Td className="text-right">
                      {device.type !== "Coordinator" && (
                        <div className="inline-flex gap-1.5">
                          <Button
                            icon={<Wrench className="size-4" />}
                            busy={busy === `configure:${device.ieee}`}
                            onClick={() =>
                              run(`configure:${device.ieee}`, () => api.z2mConfigure(device.ieee), `Reconfigured ${device.name}.`)
                            }
                          >
                            Reconfigure
                          </Button>
                          <Button
                            variant="ghost"
                            title="Remove"
                            aria-label={`Remove ${device.name}`}
                            icon={<Trash2 className="size-4" />}
                            busy={busy === `remove:${device.ieee}`}
                            onClick={() => remove(device)}
                          />
                        </div>
                      )}
                    </Td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>
    </div>
  )
}
