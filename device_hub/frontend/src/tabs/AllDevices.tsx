import { ArrowRight, Power, PowerOff, SlidersHorizontal, Trash2, X } from "lucide-react"
import { useMemo, useState } from "react"
import { api } from "../api"
import {
  cx,
  errorText,
  matchesStatus,
  removalEffect,
  removeEach,
  SOURCES,
  sourceColor,
  usePoll,
  type StatusFilter,
} from "../lib"
import type { BulkResult, Source, Tab, UnifiedDevice } from "../types"
import {
  AreaSelect,
  Button,
  Empty,
  Led,
  Notice,
  Panel,
  SearchInput,
  Select,
  SourceTag,
  StatusFilterTabs,
  Td,
  Th,
  Toggle,
  useFeedback,
} from "../ui"
import DeviceDrawer from "./DeviceDrawer"

const ORDER: Source[] = ["tuya", "tuya_local", "z2m", "zha", "sonoff", "lan"]

export default function AllDevices({
  onOpen,
  initialStatus = "all",
}: {
  onOpen: (tab: Tab) => void
  initialStatus?: StatusFilter
}) {
  const feedback = useFeedback()
  const view = usePoll(api.allDevices, 30000)
  const areas = usePoll(api.areas)
  const [query, setQuery] = useState("")
  const [hidden, setHidden] = useState<Set<Source>>(new Set())
  const [attention, setAttention] = useState(false)
  const [status, setStatus] = useState<StatusFilter>(initialStatus)
  const [busy, setBusy] = useState<string | null>(null)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [opened, setOpened] = useState<string | null>(null)

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
  const offline = shown.filter((d) => d.online === false && removalEffect(d.key))
  const picked = rows.filter((d) => selected.has(d.key))
  const allPicked = rows.length > 0 && picked.length === rows.length

  const toggleSource = (source: Source) =>
    setHidden((current) => {
      const next = new Set(current)
      if (next.has(source)) next.delete(source)
      else next.add(source)
      return next
    })

  const togglePick = (key: string) =>
    setSelected((current) => {
      const next = new Set(current)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })

  const report = (results: BulkResult[], done: string) => {
    const failed = results.filter((r) => !r.ok)
    if (failed.length) feedback.fail(failed.map((r) => `${r.name}: ${r.error}`).join(" "))
    if (failed.length < results.length) feedback.ok(`${done} ${results.length - failed.length} of ${results.length}.`)
  }

  const runBulk = async (action: "area" | "on" | "off" | "remove", areaId?: string | null) => {
    const items = picked
    if (!items.length) return
    if (action === "remove") {
      const removable = items.filter((d) => removalEffect(d.key))
      const ok = await feedback.confirm({
        title: `Remove ${removable.length} devices?`,
        body: (
          <ul className="space-y-1.5">
            {removable.map((d) => (
              <li key={d.key}>
                <span className="font-medium">{d.name}</span>
                <span className="text-muted"> · {removalEffect(d.key)}</span>
              </li>
            ))}
            {removable.length < items.length && (
              <li className="text-muted">{items.length - removable.length} devices only seen on the network are skipped.</li>
            )}
          </ul>
        ),
        action: `Remove ${removable.length}`,
        danger: true,
      })
      if (!ok) return
    }
    setBusy(`bulk:${action}`)
    try {
      const keys = action === "remove" ? items.filter((d) => removalEffect(d.key)).map((d) => d.key) : items.map((d) => d.key)
      const { results } = await api.bulk(keys, action, areaId)
      const verb = { area: "Moved", on: "Turned on", off: "Turned off", remove: "Removed" }[action]
      report(results, verb)
      if (action === "remove") setSelected(new Set())
      await view.reload()
    } catch (exc) {
      feedback.fail(errorText(exc))
    } finally {
      setBusy(null)
    }
  }

  const remove = async (device: UnifiedDevice) => {
    const effect = removalEffect(device.key)
    if (!effect) return
    const ok = await feedback.confirm({ title: `Remove ${device.name}?`, body: effect, action: "Remove device", danger: true })
    if (!ok) return
    setBusy(device.key)
    try {
      await api.removeDevice(device.key)
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
              <span className="text-muted"> · {removalEffect(d.key)}</span>
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
      const failed = await removeEach(offline, (d) => api.removeDevice(d.key))
      if (failed.length) feedback.fail(failed.map((f) => `${f.item.name}: ${f.error}`).join(" "))
      if (failed.length < offline.length) feedback.ok(`Removed ${offline.length - failed.length} offline devices.`)
      await view.reload()
    } finally {
      setBusy(null)
    }
  }

  if (view.error && !view.data) return <Notice tone="bad">Could not load devices: {view.error}</Notice>

  return (
    <>
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
        {picked.length > 0 && (
          <div className="sticky top-0 z-10 flex flex-wrap items-center gap-2 border-b border-line bg-sunk px-4 py-2.5 text-sm">
            <span className="font-medium">{picked.length} selected</span>
            <Select
              className="h-8 w-auto min-w-40 text-xs"
              value=""
              disabled={busy !== null}
              aria-label="Move selected to area"
              onChange={(e) => {
                if (e.target.value) void runBulk("area", e.target.value === "__none__" ? null : e.target.value)
              }}
            >
              <option value="">Move to area…</option>
              {(areas.data ?? []).map((area) => (
                <option key={area.id} value={area.id}>
                  {area.name}
                </option>
              ))}
              <option value="__none__">No area</option>
            </Select>
            <Button className="h-8" icon={<Power className="size-4" />} busy={busy === "bulk:on"} onClick={() => runBulk("on")}>
              Turn on
            </Button>
            <Button className="h-8" icon={<PowerOff className="size-4" />} busy={busy === "bulk:off"} onClick={() => runBulk("off")}>
              Turn off
            </Button>
            <Button
              variant="danger"
              className="h-8"
              icon={<Trash2 className="size-4" />}
              busy={busy === "bulk:remove"}
              onClick={() => runBulk("remove")}
            >
              Remove
            </Button>
            <Button variant="ghost" className="ml-auto h-8" icon={<X className="size-4" />} onClick={() => setSelected(new Set())}>
              Clear
            </Button>
          </div>
        )}
        {view.loading && !view.data ? (
          <Empty title="Collecting devices from every system" />
        ) : rows.length === 0 ? (
          <Empty title="No devices match">Clear the search, set the status to any, or turn the sources back on.</Empty>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[960px] border-collapse text-sm">
              <thead>
                <tr>
                  <Th className="w-1 p-0" />
                  <Th className="w-8">
                    <input
                      type="checkbox"
                      aria-label="Select every device shown"
                      checked={allPicked}
                      onChange={() => setSelected(allPicked ? new Set() : new Set(rows.map((d) => d.key)))}
                    />
                  </Th>
                  <Th>Name</Th>
                  <Th>Type</Th>
                  <Th>Area</Th>
                  <Th>Status</Th>
                  <Th>Address</Th>
                  <Th>Systems</Th>
                  <Th className="w-28" />
                </tr>
              </thead>
              <tbody>
                {rows.map((device) => (
                  <tr
                    key={device.key}
                    className={cx("group border-t border-line hover:bg-sunk/60", selected.has(device.key) && "bg-sunk/60")}
                  >
                    <td className="w-1 p-0">
                      <span className="block h-full min-h-12 w-1" style={{ background: sourceColor(device.sources[0]) }} />
                    </td>
                    <Td>
                      <input
                        type="checkbox"
                        aria-label={`Select ${device.name}`}
                        checked={selected.has(device.key)}
                        onChange={() => togglePick(device.key)}
                      />
                    </Td>
                    <Td className="font-medium">
                      <button className="text-left hover:underline" onClick={() => setOpened(device.key)}>
                        {device.name}
                      </button>
                    </Td>
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
                        <Button
                          variant="ghost"
                          className="h-8 px-1.5 opacity-70 group-hover:opacity-100"
                          title="Control and details"
                          aria-label={`Control ${device.name}`}
                          icon={<SlidersHorizontal className="size-4" />}
                          onClick={() => setOpened(device.key)}
                        />
                        {removalEffect(device.key) && (
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
                          aria-label={`Open ${device.name} in its tab`}
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
      <DeviceDrawer deviceKey={opened} onClose={() => setOpened(null)} onChanged={() => void view.reload()} />
    </>
  )
}
