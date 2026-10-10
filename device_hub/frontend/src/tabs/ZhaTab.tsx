import { Trash2, Wrench } from "lucide-react"
import { useMemo, useState } from "react"
import { api } from "../api"
import { errorText, sourceColor, timeAgo, usePoll } from "../lib"
import type { ZhaDevice } from "../types"
import { AreaSelect, Badge, Button, Empty, Led, Notice, Panel, SearchInput, Signal, Td, Th, useFeedback } from "../ui"
import { RenameCell } from "./Z2mTab"

export default function ZhaTab({ onChanged, z2mRunning }: { onChanged: () => void; z2mRunning: boolean }) {
  const feedback = useFeedback()
  const view = usePoll(api.zha, 15000)
  const areas = usePoll(api.areas)
  const [busy, setBusy] = useState<string | null>(null)
  const [query, setQuery] = useState("")
  const [permitUntil, setPermitUntil] = useState<number | null>(null)
  const data = view.data

  const devices = useMemo(() => {
    const q = query.trim().toLowerCase()
    return (data?.devices ?? []).filter(
      (d) => !q || [d.name, d.model, d.manufacturer, d.ieee].some((v) => (v || "").toLowerCase().includes(q)),
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

  const remove = async (device: ZhaDevice) => {
    const ok = await feedback.confirm({
      title: `Remove ${device.name}?`,
      body: "ZHA asks the device to leave the network and removes it from Home Assistant.",
      action: "Remove device",
      danger: true,
    })
    if (ok) await run(`remove:${device.ieee}`, () => api.zhaRemove(device.ieee), `Removed ${device.name}.`)
  }

  if (view.loading && !data) return <Panel><Empty title="Loading ZHA" /></Panel>
  if (!data) return <Notice tone="bad">Could not load ZHA: {view.error}</Notice>

  if (data.entries.length === 0) {
    return (
      <Panel>
        <Empty title="ZHA is not set up">
          {z2mRunning
            ? "Your Zigbee devices run on Zigbee2MQTT, which is the usual choice when ZHA is not set up."
            : "Add the Zigbee Home Automation integration in Home Assistant to manage a coordinator here."}
        </Empty>
      </Panel>
    )
  }

  const joining = permitUntil !== null && permitUntil > Date.now()

  return (
    <div className="space-y-4">
      <Panel title={<span style={{ color: sourceColor("zha") }}>ZHA integration</span>}>
        <ul className="divide-y divide-line">
          {data.entries.map((entry) => (
            <li key={entry.entry_id} className="flex flex-wrap items-center gap-3 px-4 py-3 text-sm">
              <Led state={entry.state === "loaded" ? "ok" : "off"} />
              <span className="font-medium">{entry.title || "Zigbee Home Automation"}</span>
              <Badge tone={entry.state === "loaded" ? "ok" : "neutral"}>{entry.state.replace("_", " ")}</Badge>
              {entry.disabled_by && <Badge>disabled by {entry.disabled_by}</Badge>}
              {entry.reason && <span className="text-muted">{entry.reason}</span>}
            </li>
          ))}
        </ul>
        {!data.loaded && (
          <p className="border-t border-line px-4 py-3 text-sm text-muted">
            {z2mRunning
              ? "ZHA is set up but not running. Zigbee2MQTT is using the coordinator, so leave ZHA off unless you plug in a second dongle for it."
              : "ZHA is set up but not running. Check its coordinator in Settings, Devices & services."}
          </p>
        )}
        {data.loaded && (
          <div className="flex flex-wrap items-center gap-3 border-t border-line px-4 py-3">
            {joining ? (
              <span className="inline-flex items-center gap-2 text-sm font-medium">
                <Led state="busy" />
                Joining is open. Put the new device in pairing mode now.
              </span>
            ) : (
              <span className="text-sm text-muted">Opens the network for about 4 minutes.</span>
            )}
            <Button
              variant={joining ? "quiet" : "primary"}
              busy={busy === "permit"}
              onClick={() =>
                run(
                  "permit",
                  async () => {
                    await api.zhaPermit(joining ? 0 : 254)
                    setPermitUntil(joining ? null : Date.now() + 254_000)
                  },
                  joining ? "Joining closed." : "Joining is open for about 4 minutes.",
                )
              }
            >
              {joining ? "Close joining" : "Allow devices to join"}
            </Button>
          </div>
        )}
      </Panel>

      {data.error && <Notice tone="bad">{data.error}</Notice>}

      {data.loaded && (
        <Panel
          title={`Devices (${data.devices.length})`}
          aside={<SearchInput value={query} onChange={setQuery} placeholder="Search name, model, IEEE" />}
        >
          {devices.length === 0 ? (
            <Empty title="No devices">Allow devices to join, then pair a device.</Empty>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[1000px] text-sm">
                <thead>
                  <tr>
                    <Th>Name</Th>
                    <Th>Device</Th>
                    <Th>Role</Th>
                    <Th>LQI</Th>
                    <Th>RSSI</Th>
                    <Th>Last seen</Th>
                    <Th>Area</Th>
                    <Th className="text-right">Actions</Th>
                  </tr>
                </thead>
                <tbody>
                  {devices.map((device) => (
                    <tr key={device.ieee} className="border-t border-line hover:bg-sunk/60">
                      <Td>
                        {device.ha_device_id ? (
                          <RenameCell
                            name={device.name}
                            onSave={async (next) => {
                              try {
                                await api.updateHaDevice(device.ha_device_id!, { name_by_user: next })
                                feedback.ok(`Renamed to ${next}.`)
                                await view.reload()
                              } catch (exc) {
                                feedback.fail(errorText(exc))
                                throw exc
                              }
                            }}
                          />
                        ) : (
                          <span className="font-medium">{device.name}</span>
                        )}
                        <div className="font-mono text-[11px] text-muted">{device.ieee}</div>
                      </Td>
                      <Td>
                        <div>{[device.manufacturer, device.model].filter(Boolean).join(" ")}</div>
                        {device.quirk && <div className="text-xs text-muted">quirk {device.quirk}</div>}
                      </Td>
                      <Td>
                        <div>{device.device_type}</div>
                        <div className="text-xs text-muted">{device.power_source}</div>
                      </Td>
                      <Td>
                        <div className="flex flex-col gap-1">
                          <Signal value={device.lqi} />
                          <span className="inline-flex items-center gap-1.5 text-xs">
                            <Led state={device.available ? "ok" : "warn"} />
                            {device.available ? "available" : "unavailable"}
                          </span>
                        </div>
                      </Td>
                      <Td mono>{device.rssi ?? "–"}</Td>
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
                        {device.device_type !== "Coordinator" && (
                          <div className="inline-flex gap-1.5">
                            <Button
                              icon={<Wrench className="size-4" />}
                              busy={busy === `configure:${device.ieee}`}
                              onClick={() =>
                                run(`configure:${device.ieee}`, () => api.zhaReconfigure(device.ieee), `Reconfiguring ${device.name}.`)
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
      )}
    </div>
  )
}
