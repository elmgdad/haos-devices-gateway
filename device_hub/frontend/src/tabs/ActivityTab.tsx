import { Trash2 } from "lucide-react"
import { useMemo, useState } from "react"
import { api } from "../api"
import { cx, errorText, useLiveEvents, usePoll } from "../lib"
import type { ActivityEntry, ActivityKind } from "../types"
import { Button, Empty, Notice, Panel, SearchInput, Toggle, useFeedback } from "../ui"
import { ActivityList } from "./ActivityList"
import DeviceDrawer from "./DeviceDrawer"

const FILTERS: { id: "" | ActivityKind | "status"; label: string }[] = [
  { id: "", label: "Everything" },
  { id: "command", label: "Commands" },
  { id: "automation", label: "Automations" },
  { id: "status", label: "Online / offline" },
  { id: "change", label: "Changes" },
  { id: "remove", label: "Removals" },
  { id: "key", label: "API keys" },
]

export default function ActivityTab() {
  const feedback = useFeedback()
  const view = usePoll(() => api.activity(), 30000)
  const [filter, setFilter] = useState<(typeof FILTERS)[number]["id"]>("")
  const [query, setQuery] = useState("")
  const [failedOnly, setFailedOnly] = useState(false)
  const [opened, setOpened] = useState<string | null>(null)

  useLiveEvents((kind, data) => {
    if (kind !== "activity") return
    const entry = data as unknown as ActivityEntry
    view.setData((current) =>
      current && !current.activity.some((e) => e.id === entry.id)
        ? { activity: [entry, ...current.activity].slice(0, 500) }
        : current,
    )
  })

  const entries = useMemo(() => {
    const q = query.trim().toLowerCase()
    return (view.data?.activity ?? []).filter((entry) => {
      if (filter === "status" && entry.kind !== "online" && entry.kind !== "offline") return false
      if (filter && filter !== "status" && entry.kind !== filter) return false
      if (failedOnly && entry.ok) return false
      return !q || `${entry.text} ${entry.via}`.toLowerCase().includes(q)
    })
  }, [view.data, filter, query, failedOnly])

  const clear = async () => {
    const ok = await feedback.confirm({
      title: "Clear the activity log?",
      body: "Every entry is deleted. Devices and settings are not changed.",
      action: "Clear log",
      danger: true,
    })
    if (!ok) return
    try {
      await api.clearActivity()
      await view.reload()
    } catch (exc) {
      feedback.fail(errorText(exc))
    }
  }

  if (view.error && !view.data) return <Notice tone="bad">Could not read the activity log: {view.error}</Notice>
  return (
    <>
      <Panel
        title={`Activity (${entries.length})`}
        aside={
          <>
            <Toggle checked={failedOnly} onChange={setFailedOnly}>
              Failed only
            </Toggle>
            <SearchInput value={query} onChange={setQuery} placeholder="Search device, key, text" />
            <Button variant="ghost" icon={<Trash2 className="size-4" />} onClick={clear} disabled={!view.data?.activity.length}>
              Clear
            </Button>
          </>
        }
      >
        <div className="flex flex-wrap gap-1 border-b border-line px-4 py-3">
          {FILTERS.map((f) => (
            <button
              key={f.id}
              onClick={() => setFilter(f.id)}
              aria-pressed={filter === f.id}
              className={cx(
                "rounded px-3 py-1 text-sm",
                filter === f.id ? "bg-ink text-card" : "text-muted hover:text-ink",
              )}
            >
              {f.label}
            </button>
          ))}
        </div>
        {view.loading && !view.data ? (
          <Empty title="Reading the log" />
        ) : entries.length === 0 ? (
          <Empty title="Nothing here yet">
            Commands from the panel and the API, renames, removals, API keys, and devices going offline or coming back are
            listed here. Device Hub checks every minute.
          </Empty>
        ) : (
          <ActivityList entries={entries} onDevice={setOpened} />
        )}
      </Panel>
      <DeviceDrawer deviceKey={opened} onClose={() => setOpened(null)} onChanged={() => void view.reload()} />
    </>
  )
}
