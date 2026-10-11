import { ExternalLink } from "lucide-react"
import { useMemo, useState } from "react"
import { api } from "../api"
import { cx, errorText, openInHa, sourceColor, usePoll } from "../lib"
import type { SonoffDevice, SonoffSwitch } from "../types"
import { AreaSelect, Badge, Button, Empty, Led, Notice, Panel, SearchInput, Signal, Td, Th, useFeedback } from "../ui"

function SwitchButton({
  item,
  busy,
  onToggle,
}: {
  item: SonoffSwitch
  busy: boolean
  onToggle: (item: SonoffSwitch) => void
}) {
  const on = item.state === "on" || item.state === "open"
  const unavailable = item.state === null || item.state === "unavailable"
  return (
    <button
      disabled={busy || unavailable}
      onClick={() => onToggle(item)}
      aria-pressed={on}
      title={unavailable ? "Unavailable in Home Assistant" : on ? "Turn off" : "Turn on"}
      className={cx(
        "inline-flex h-8 min-w-24 items-center justify-between gap-2 rounded-md border px-2.5 text-xs font-medium transition-colors",
        "disabled:cursor-not-allowed disabled:opacity-50",
        on ? "border-transparent text-card" : "border-line bg-card text-ink hover:bg-sunk",
      )}
      style={on ? { background: sourceColor("sonoff") } : undefined}
    >
      <span className="truncate">{item.name}</span>
      <span className="font-mono">{busy ? "…" : on ? "ON" : "OFF"}</span>
    </button>
  )
}

export default function SonoffTab({ onChanged }: { onChanged: () => void }) {
  const feedback = useFeedback()
  const view = usePoll(api.sonoff, 10000)
  const areas = usePoll(api.areas)
  const [query, setQuery] = useState("")
  const [busy, setBusy] = useState<string | null>(null)
  const data = view.data

  const devices = useMemo(() => {
    const q = query.trim().toLowerCase()
    return (data?.devices ?? []).filter(
      (d) => !q || [d.name, d.model, d.ip ?? "", d.id, d.area ?? ""].some((v) => v.toLowerCase().includes(q)),
    )
  }, [data, query])

  const toggle = async (item: SonoffSwitch) => {
    const on = !(item.state === "on" || item.state === "open")
    setBusy(item.entity_id)
    view.setData((current) =>
      current
        ? {
            ...current,
            devices: current.devices.map((d) => ({
              ...d,
              switches: d.switches.map((s) => (s.entity_id === item.entity_id ? { ...s, state: on ? "on" : "off" } : s)),
            })),
          }
        : current,
    )
    try {
      await api.sonoffSwitch(item.entity_id, on)
      window.setTimeout(() => void view.reload(), 800)
      onChanged()
    } catch (exc) {
      feedback.fail(errorText(exc))
      void view.reload()
    } finally {
      setBusy(null)
    }
  }

  if (view.error && !data) return <Notice tone="bad">Could not read Sonoff devices: {view.error}</Notice>
  if (data && !data.installed) {
    return (
      <Notice>
        The Sonoff integration (SonoffLAN) is not set up in Home Assistant. Add it under Settings, Devices &amp; services, and
        its devices show up here.
      </Notice>
    )
  }

  const local = devices.filter((d) => d.local && d.online !== false).length
  return (
    <div className="space-y-4">
      {data?.cloud_error && <Notice>{data.cloud_error}</Notice>}
      <Panel
        title={
          <span className="flex items-center gap-2">
            <span style={{ color: sourceColor("sonoff") }}>Sonoff</span>
            <span className="font-sans text-sm font-normal tracking-normal text-muted normal-case">
              {data ? `${local} of ${data.devices.length} on your network through SonoffLAN` : "Reading SonoffLAN"}
            </span>
          </span>
        }
        aside={
          <>
            {data?.entries[0] && (
              <Button
                icon={<ExternalLink className="size-4" />}
                onClick={() => openInHa(`/config/integrations/integration/sonoff`)}
              >
                Open SonoffLAN
              </Button>
            )}
            <SearchInput value={query} onChange={setQuery} placeholder="Search name, model, IP" />
          </>
        }
      >
        {view.loading && !data ? (
          <Empty title="Reading Sonoff devices" />
        ) : devices.length === 0 ? (
          <Empty title="No Sonoff devices">
            {query ? "Nothing matches the search." : "SonoffLAN has no devices yet. Add them in the eWeLink app."}
          </Empty>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[1000px] border-collapse text-sm">
              <thead>
                <tr>
                  <Th>Device</Th>
                  <Th>Connection</Th>
                  <Th>Signal</Th>
                  <Th>Switches</Th>
                  <Th>Area</Th>
                  <Th className="text-right">Actions</Th>
                </tr>
              </thead>
              <tbody>
                {devices.map((device: SonoffDevice) => (
                  <tr key={device.id} className="border-t border-line hover:bg-sunk/60">
                    <Td>
                      <div className="font-medium">{device.name}</div>
                      <div className="text-xs text-muted">
                        {device.model}
                        {device.firmware ? ` · firmware ${device.firmware}` : ""}
                      </div>
                      <div className="font-mono text-[11px] text-muted">{device.id}</div>
                    </Td>
                    <Td>
                      <span className="inline-flex items-center gap-1.5 text-xs">
                        <Led state={device.online === false ? "warn" : device.online ? "ok" : "off"} />
                        {device.online === false ? "Offline" : device.local ? "Local" : "Cloud"}
                      </span>
                      {device.ip && <div className="mt-0.5 font-mono text-xs">{device.ip}</div>}
                    </Td>
                    <Td>
                      {device.rssi !== null ? (
                        <Signal value={Math.max(0, Math.min(100, 2 * (device.rssi + 100)))} max={100} />
                      ) : (
                        <span className="text-muted">–</span>
                      )}
                      {device.rssi !== null && <div className="font-mono text-[11px] text-muted">{device.rssi} dBm</div>}
                    </Td>
                    <Td>
                      <div className="flex flex-wrap gap-1.5">
                        {device.switches.length === 0 ? (
                          <Badge>No switches</Badge>
                        ) : (
                          device.switches.map((item) => (
                            <SwitchButton key={item.entity_id} item={item} busy={busy === item.entity_id} onToggle={toggle} />
                          ))
                        )}
                      </div>
                    </Td>
                    <Td>
                      <AreaSelect
                        areas={areas.data ?? []}
                        value={device.area_id}
                        deviceId={device.ha_device_id}
                        onSaved={() => {
                          void view.reload()
                          void areas.reload()
                        }}
                      />
                    </Td>
                    <Td className="text-right">
                      <Button
                        icon={<ExternalLink className="size-4" />}
                        onClick={() => openInHa(`/config/devices/device/${device.ha_device_id}`)}
                      >
                        Open in HA
                      </Button>
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
