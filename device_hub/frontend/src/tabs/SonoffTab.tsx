import { ExternalLink, Trash2 } from "lucide-react"
import { useMemo, useState } from "react"
import { api } from "../api"
import { cx, errorText, matchesStatus, openInHa, removeEach, sourceColor, usePoll, type StatusFilter } from "../lib"
import type { SonoffDevice, SonoffSwitch } from "../types"
import {
  AreaSelect,
  Badge,
  Button,
  Empty,
  Led,
  Notice,
  Panel,
  SearchInput,
  Signal,
  StatusFilterTabs,
  Td,
  Th,
  useFeedback,
} from "../ui"

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
  const [status, setStatus] = useState<StatusFilter>("all")
  const [busy, setBusy] = useState<string | null>(null)
  const data = view.data

  const devices = useMemo(() => {
    const q = query.trim().toLowerCase()
    return (data?.devices ?? [])
      .filter((d) => matchesStatus(d.online, status))
      .filter((d) => !q || [d.name, d.model, d.ip ?? "", d.id, d.area ?? ""].some((v) => v.toLowerCase().includes(q)))
  }, [data, query, status])

  const offline = (data?.devices ?? []).filter((d) => d.online === false)
  const returnsOne = "SonoffLAN adds it back when it starts again while it is still in your eWeLink account. Delete it in the eWeLink app to remove it for good."
  const returnsMany = "SonoffLAN adds them back when it starts again while they are still in your eWeLink account. Delete them in the eWeLink app to remove them for good."

  const remove = async (device: SonoffDevice) => {
    const ok = await feedback.confirm({
      title: `Remove ${device.name}?`,
      body: `It and its entities are removed from Home Assistant. ${returnsOne}`,
      action: "Remove device",
      danger: true,
    })
    if (!ok) return
    setBusy(`remove:${device.id}`)
    try {
      await api.sonoffRemove(device.ha_device_id)
      feedback.ok(`Removed ${device.name}.`)
      await view.reload()
      onChanged()
    } catch (exc) {
      feedback.fail(errorText(exc))
    } finally {
      setBusy(null)
    }
  }

  const removeOffline = async () => {
    const ok = await feedback.confirm({
      title: `Remove ${offline.length} offline Sonoff devices?`,
      body: `${offline.map((d) => d.name).join(", ")}. They and their entities are removed from Home Assistant. ${returnsMany}`,
      action: `Remove ${offline.length}`,
      danger: true,
    })
    if (!ok) return
    setBusy("remove:offline")
    try {
      const failed = await removeEach(offline, (d) => api.sonoffRemove(d.ha_device_id))
      if (failed.length) feedback.fail(failed.map((f) => `${f.item.name}: ${f.error}`).join(" "))
      if (failed.length < offline.length) feedback.ok(`Removed ${offline.length - failed.length} offline devices.`)
      await view.reload()
      onChanged()
    } finally {
      setBusy(null)
    }
  }

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
            <StatusFilterTabs
              value={status}
              onChange={setStatus}
              online={(data?.devices ?? []).filter((d) => d.online === true).length}
              offline={offline.length}
            />
            {status === "offline" && offline.length > 0 && (
              <Button
                variant="danger"
                icon={<Trash2 className="size-4" />}
                busy={busy === "remove:offline"}
                onClick={removeOffline}
              >
                Remove {offline.length} offline
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
            {query || status !== "all"
              ? "Nothing matches the search or status."
              : "SonoffLAN has no devices yet. Add them in the eWeLink app."}
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
                      <div className="flex justify-end gap-1">
                        <Button
                          icon={<ExternalLink className="size-4" />}
                          onClick={() => openInHa(`/config/devices/device/${device.ha_device_id}`)}
                        >
                          Open in HA
                        </Button>
                        <Button
                          variant="ghost"
                          title="Remove from Home Assistant"
                          aria-label={`Remove ${device.name}`}
                          icon={<Trash2 className="size-4" />}
                          busy={busy === `remove:${device.id}`}
                          onClick={() => remove(device)}
                        />
                      </div>
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
