import { CheckCircle2, CircleAlert, CircleDashed, CircleDot, ExternalLink } from "lucide-react"
import { api } from "../api"
import { cx, openInHa, usePoll, type StatusFilter } from "../lib"
import type { SetupItem, Tab } from "../types"
import { Button, Empty, Notice, Panel } from "../ui"

const ICON = {
  ok: <CheckCircle2 className="size-5 text-ok" />,
  warn: <CircleAlert className="size-5 text-bad" />,
  todo: <CircleDot className="size-5 text-ink" />,
  optional: <CircleDashed className="size-5 text-muted" />,
}

const STATE_LABEL = { ok: "Done", warn: "Needs attention", todo: "To do", optional: "Optional" }

export default function SetupTab({ onOpen }: { onOpen: (tab: Tab, status?: StatusFilter) => void }) {
  const view = usePoll(api.setup, 30000)
  const items = view.data?.items ?? []
  const required = items.filter((i) => i.required)
  const ready = required.filter((i) => i.state === "ok").length

  const act = (item: SetupItem) => {
    const action = item.action
    if (!action) return
    if (action.tab) onOpen(action.tab, action.status)
    else if (action.ha) openInHa(action.ha)
    else if (action.url) window.open(action.url, "_blank", "noopener")
  }

  if (view.error && !view.data) return <Notice tone="bad">Could not read the setup checklist: {view.error}</Notice>
  return (
    <Panel
      title={
        <span className="flex items-center gap-2">
          Setup
          {view.data && (
            <span className="font-sans text-sm font-normal tracking-normal text-muted normal-case">
              {ready} of {required.length} required steps done
            </span>
          )}
        </span>
      }
    >
      {view.loading && !view.data ? (
        <Empty title="Checking every system" />
      ) : (
        <ol className="divide-y divide-line">
          {items.map((item) => (
            <li key={item.id} className="flex flex-wrap items-start gap-3 px-4 py-3.5">
              <span className="mt-0.5">{ICON[item.state]}</span>
              <div className="min-w-56 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-medium">{item.title}</span>
                  <span
                    className={cx(
                      "text-xs",
                      item.state === "warn" ? "text-bad" : item.state === "ok" ? "text-ok" : "text-muted",
                    )}
                  >
                    {STATE_LABEL[item.state]}
                    {item.required && item.state !== "ok" ? " · required" : ""}
                  </span>
                </div>
                <p className="mt-0.5 text-sm text-muted">{item.detail}</p>
              </div>
              {item.action && (
                <Button
                  icon={item.action.tab ? undefined : <ExternalLink className="size-4" />}
                  variant={item.state === "todo" || item.state === "warn" ? "primary" : "quiet"}
                  onClick={() => act(item)}
                >
                  {item.action.label}
                </Button>
              )}
            </li>
          ))}
        </ol>
      )}
    </Panel>
  )
}
