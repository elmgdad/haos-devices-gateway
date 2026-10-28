import { ArrowRight, Trash2 } from "lucide-react"
import { useMemo, useState } from "react"
import { api } from "../api"
import { cx, errorText, matchesStatus, removeEach, SOURCES, sourceColor, usePoll, type StatusFilter } from "../lib"
import type { Source, Tab, UnifiedDevice } from "../types"
import {
  AreaSelect,
  Button,
  Empty,
  Led,
  Notice,
  Panel,
  SearchInput,
  SourceTag,
  StatusFilterTabs,
  Td,
  Th,
  Toggle,
  useFeedback,
} from "../ui"

const ORDER: Source[] = ["tuya", "tuya_local", "z2m", "zha", "sonoff", "lan"]

/** How each system removes one of its devices. Devices only seen on the network have nothing to remove. */
function removal(device: UnifiedDevice): { run: () => Promise<unknown>; effect: string } | null {
  const [prefix, ...rest] = device.key.split(":")
  const id = rest.join(":")
  switch (prefix) {
    case "tuya":
      return { run: () => api.tuyaRemove(id), effect: "Tuya unbinds it from your Smart Life account." }
    case "tuya_local":
      return { run: () => api.removeEntry(id), effect: "Its Tuya Local entry and entities are removed from Home Assistant." }
    case "zha":
      return { run: () => api.zhaRemove(id), effect: "ZHA asks it to leave the network and removes it from Home Assistant." }
    case "z2m":
      return { run: () => api.z2mRemove(id, false), effect: "Zigbee2MQTT asks it to leave the network." }
    case "sonoff":
      return device.ha_device_id
        ? {
            run: () => api.sonoffRemove(device.ha_device_id!),
            effect: "It is removed from Home Assistant. SonoffLAN adds it back on its next start while it is still in eWeLink.",
          }
        : null
    case "ir":
      return { run: () => api.irDelete(id), effect: "Device Hub forgets this learned remote." }
    default:
      return null
  }
}

export default function AllDevices({ onOpen }: { onOpen: (tab: Tab) => void }) {
  const feedback = useFeedback()
  const view = usePoll(api.allDevices, 30000)
  const areas = usePoll(api.areas)
  const [query, setQuery] = useState("")
  const [hidden, setHidden] = useState<Set<Source>>(new Set())
  const [attention, setAttention] = useState(false)
  const [status, setStatus] = useState<StatusFilter>("all")
  const [busy, setBusy] = useState<string | null>(null)

  const all = view.data?.devices ?? []
  const counts = useMemo(() => {
    const result: Record<Source, number> = { tuya: 0, tuya_local: 0, z2m: 0, zha: 0, sonoff: 0, lan: 0 }
    all.forEach((d) => d.sources.forEach((s) => (result[s] += 1)))
    return result
  }, [all])

  const rows = useMemo(() => {
    const q = query.trim().toLowerCase()
    return all
      .filter((d) => d.sources.some((s) => !hidden.has(s)))
      .filter((d) => !attention || d.warn || d.online === false)
      .filter((d) => matchesStatus(d.online, status))
      .filter((d) => !q || [d.name, d.kind, d.area ?? "", d.address, d.status].some((v) => v.toLowerCase().includes(q)))
      .sort((a, b) => a.name.localeCompare(b.name))
  }, [all, hidden, attention, status, query])

  const shown = all.filter((d) => d.sources.some((s) => !hidden.has(s)))
  const offline = shown.filter((d) => d.online === false && removal(d))

  const remove = async (device: UnifiedDevice) => {
    const how = removal(device)
    if (!how) return
    const ok = await feedback.confirm({
      title: `Remove ${device.name}?`,
      body: how.effect,
      action: "Remove device",
      danger: true,
    })
    if (!ok) return
    setBusy(device.key)
    try {
      await how.run()
      feedback.ok(`Removed ${device.name}.`)
      await view.reload()
    } catch (exc) {
      feedback.fail(errorText(exc))
    } finally {
      setBusy(null)
    }
  }

  const removeOffline = async () => {
    const ok = await feedback.confirm({
      title: `Remove ${offline.length} offline devices?`,
      body: (
        <ul className="space-y-1.5">
          {offline.map((d) => (
            <li key={d.key}>
              <span className="font-medium">{d.name}</span>
              <span className="text-muted"> · {removal(d)!.effect}</span>
            </li>
          ))}
        </ul>
      ),
      action: `Remove ${offline.length}`,
      danger: true,
    })
    if (!ok) return
    setBusy("offline")
    try {
      const failed = await removeEach(offline, (d) => removal(d)!.run())
      if (failed.length) feedback.fail(failed.map((f) => `${f.item.name}: ${f.error}`).join(" "))
      if (failed.length < offline.length) feedback.ok(`Removed ${offline.length - failed.length} offline devices.`)
      await view.reload()
    } finally {
      setBusy(null)
    }
  }

  const toggleSource = (source: Source) =>
    setHidden((current) => {
      const next = new Set(current)
      if (next.has(source)) next.delete(source)
      else next.add(source)
      return next
    })

  if (view.error && !view.data) return <Notice tone="bad">Could not load devices: {view.error}</Notice>

  return (
    <Panel
      title={`Every device (${rows.length})`}
      aside={
        <>
          <StatusFilterTabs
            value={status}
            onChange={setStatus}
            online={shown.filter((d) => d.online === true).length}
            offline={shown.filter((d) => d.online === false).length}
          />
          {status === "offline" && offline.length > 0 && (
            <Button variant="danger" icon={<Trash2 className="size-4" />} busy={busy === "offline"} onClick={removeOffline}>
              Remove {offline.length} offline
            </Button>
          )}
          <Toggle checked={attention} onChange={setAttention}>
            Needs attention
          </Toggle>
          <SearchInput value={query} onChange={setQuery} placeholder="Search name, area, address" />
        </>
      }
    >
      <div className="flex flex-wrap gap-2 border-b border-line px-4 py-3">
        {ORDER.map((source) => {
          const off = hidden.has(source)
          return (
            <button
              key={source}
              onClick={() => toggleSource(source)}
              aria-pressed={!off}
              className={cx(
                "inline-flex items-center gap-2 rounded-full border px-3 py-1 text-sm transition-opacity",
                off ? "opacity-45" : "opacity-100",
              )}
              style={{ borderColor: `color-mix(in srgb, ${sourceColor(source)} 50%, transparent)` }}
            >
              <span className="size-2 rounded-full" style={{ background: sourceColor(source) }} />
              {SOURCES[source].label}
              <span className="font-mono text-xs text-muted">{counts[source]}</span>
            </button>
          )
        })}
      </div>
      {view.loading && !view.data ? (
        <Empty title="Collecting devices from every system" />
      ) : rows.length === 0 ? (
        <Empty title="No devices match">Clear the search, set the status to any, or turn the sources back on.</Empty>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[900px] border-collapse text-sm">
            <thead>
              <tr>
                <Th className="w-1 p-0" />
                <Th>Name</Th>
                <Th>Type</Th>
                <Th>Area</Th>
                <Th>Status</Th>
                <Th>Address</Th>
                <Th>Systems</Th>
                <Th className="w-20" />
              </tr>
            </thead>
            <tbody>
              {rows.map((device) => (
                <tr key={device.key} className="group border-t border-line hover:bg-sunk/60">
                  <td className="w-1 p-0">
                    <span className="block h-full min-h-12 w-1" style={{ background: sourceColor(device.sources[0]) }} />
                  </td>
                  <Td className="font-medium">{device.name}</Td>
                  <Td className="text-muted">{device.kind}</Td>
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
                  <Td className="whitespace-nowrap">
                    <span className={cx("inline-flex items-center gap-2", device.warn && "text-bad")}>
                      <Led state={device.warn ? "warn" : device.online === false ? "off" : device.online ? "ok" : "off"} />
                      {device.status}
                    </span>
                  </Td>
                  <Td mono className="text-xs">
                    {device.address || "–"}
                  </Td>
                  <Td>
                    <span className="flex gap-1">
                      {device.sources.map((source) => (
                        <SourceTag key={source} source={source} />
                      ))}
                    </span>
                  </Td>
                  <Td>
                    <span className="flex justify-end gap-0.5">
                      {removal(device) && (
                        <Button
                          variant="ghost"
                          className="h-8 px-1.5 opacity-60 group-hover:opacity-100"
                          title="Remove"
                          aria-label={`Remove ${device.name}`}
                          icon={<Trash2 className="size-4" />}
                          busy={busy === device.key}
                          onClick={() => remove(device)}
                        />
                      )}
                      <button
                        className="rounded p-1.5 text-muted opacity-60 group-hover:opacity-100 hover:bg-card hover:text-ink"
                        onClick={() => onOpen(device.tab)}
                        title="Open in its tab"
                        aria-label={`Open ${device.name}`}
                      >
                        <ArrowRight className="size-4" />
                      </button>
                    </span>
                  </Td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  )
}
