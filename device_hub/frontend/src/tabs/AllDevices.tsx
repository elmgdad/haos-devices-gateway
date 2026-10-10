import { ArrowRight } from "lucide-react"
import { useMemo, useState } from "react"
import { api } from "../api"
import { cx, SOURCES, sourceColor, usePoll } from "../lib"
import type { Source, Tab } from "../types"
import { AreaSelect, Empty, Led, Notice, Panel, SearchInput, SourceTag, Td, Th, Toggle } from "../ui"

const ORDER: Source[] = ["tuya", "tuya_local", "z2m", "zha", "lan"]

export default function AllDevices({ onOpen }: { onOpen: (tab: Tab) => void }) {
  const view = usePoll(api.allDevices, 30000)
  const areas = usePoll(api.areas)
  const [query, setQuery] = useState("")
  const [hidden, setHidden] = useState<Set<Source>>(new Set())
  const [attention, setAttention] = useState(false)

  const all = view.data?.devices ?? []
  const counts = useMemo(() => {
    const result: Record<Source, number> = { tuya: 0, tuya_local: 0, z2m: 0, zha: 0, lan: 0 }
    all.forEach((d) => d.sources.forEach((s) => (result[s] += 1)))
    return result
  }, [all])

  const rows = useMemo(() => {
    const q = query.trim().toLowerCase()
    return all
      .filter((d) => d.sources.some((s) => !hidden.has(s)))
      .filter((d) => !attention || d.warn || d.online === false)
      .filter((d) => !q || [d.name, d.kind, d.area ?? "", d.address, d.status].some((v) => v.toLowerCase().includes(q)))
      .sort((a, b) => a.name.localeCompare(b.name))
  }, [all, hidden, attention, query])

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
        <Empty title="No devices match">Clear the search or turn the sources back on.</Empty>
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
                <Th className="w-12" />
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
                    <button
                      className="rounded p-1.5 text-muted opacity-60 group-hover:opacity-100 hover:bg-card hover:text-ink"
                      onClick={() => onOpen(device.tab)}
                      title="Open in its tab"
                      aria-label={`Open ${device.name}`}
                    >
                      <ArrowRight className="size-4" />
                    </button>
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
