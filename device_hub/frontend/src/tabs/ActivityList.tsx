import { cx, clockTime } from "../lib"
import type { ActivityEntry, ActivityKind } from "../types"
import { Badge } from "../ui"

export const KIND_LABEL: Record<ActivityKind, string> = {
  command: "Command",
  scene: "Scene",
  remove: "Removed",
  change: "Changed",
  offline: "Offline",
  online: "Online",
  key: "API key",
  error: "Error",
}

const KIND_TONE: Partial<Record<ActivityKind, "ok" | "bad" | "info">> = {
  online: "ok",
  offline: "bad",
  remove: "bad",
  error: "bad",
  key: "info",
}

export function ActivityList({
  entries,
  onDevice,
  showDevice = true,
}: {
  entries: ActivityEntry[]
  onDevice?: (key: string) => void
  showDevice?: boolean
}) {
  return (
    <ul className="divide-y divide-line">
      {entries.map((entry) => (
        <li key={entry.id} className="flex flex-wrap items-baseline gap-x-3 gap-y-1 px-4 py-2.5 text-sm">
          <span className="w-20 shrink-0 font-mono text-xs text-muted">{clockTime(entry.at)}</span>
          <span className="w-20 shrink-0">
            <Badge tone={entry.ok ? KIND_TONE[entry.kind] ?? "neutral" : "bad"}>{KIND_LABEL[entry.kind] ?? entry.kind}</Badge>
          </span>
          <span className={cx("min-w-0 flex-1", !entry.ok && "text-bad")}>
            {showDevice && entry.device_id && onDevice ? (
              <button className="font-medium hover:underline" onClick={() => onDevice(entry.device_id!)}>
                {entry.text}
              </button>
            ) : (
              entry.text
            )}
          </span>
          <span className="text-xs text-muted">{entry.via}</span>
        </li>
      ))}
    </ul>
  )
}
