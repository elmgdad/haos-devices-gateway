import { Pencil, Plus, Power, PowerOff, Trash2 } from "lucide-react"
import { useRef, useState } from "react"
import { api } from "../api"
import { cx, errorText, sourceColor, useLiveEvents, usePoll } from "../lib"
import type { AreaCard, AreaDevice } from "../types"
import { Button, Empty, Input, Led, Notice, Panel, useFeedback } from "../ui"
import DeviceDrawer from "./DeviceDrawer"

export default function AreasTab() {
  const feedback = useFeedback()
  const view = usePoll(api.areaOverview, 30000)
  const [busy, setBusy] = useState<string | null>(null)
  const [opened, setOpened] = useState<string | null>(null)
  const [adding, setAdding] = useState("")

  const pending = useRef<number | null>(null)
  useLiveEvents((kind) => {
    if (kind !== "state" && kind !== "device") return
    if (pending.current) window.clearTimeout(pending.current)
    pending.current = window.setTimeout(() => void view.reload(), 800)
  })

  const run = async (id: string, action: () => Promise<unknown>, done?: string) => {
    setBusy(id)
    try {
      await action()
      if (done) feedback.ok(done)
      await view.reload()
    } catch (exc) {
      feedback.fail(errorText(exc))
    } finally {
      setBusy(null)
    }
  }

  const switchArea = (area: AreaCard, action: "on" | "off") =>
    run(`${area.id}:${action}`, async () => {
      const { results } = await api.areaCommand(area.id, action)
      const failed = results.filter((r) => !r.ok)
      if (failed.length) feedback.fail(failed.map((r) => `${r.name}: ${r.error}`).join(" "))
      if (failed.length < results.length) feedback.ok(`${area.name}: ${results.length - failed.length} turned ${action}.`)
    })

  const remove = async (area: AreaCard) => {
    const ok = await feedback.confirm({
      title: `Delete ${area.name}?`,
      body: `The area is removed from Home Assistant. Its ${area.devices.length} devices stay, without an area.`,
      action: "Delete area",
      danger: true,
    })
    if (ok) await run(`${area.id}:delete`, () => api.deleteArea(area.id), `Deleted ${area.name}.`)
  }

  if (view.error && !view.data) return <Notice tone="bad">Could not read the areas: {view.error}</Notice>
  const areas = view.data?.areas ?? []
  return (
    <>
      <Panel
        title={`Areas (${areas.length})`}
        aside={
          <form
            className="flex gap-1.5"
            onSubmit={(e) => {
              e.preventDefault()
              const name = adding.trim()
              if (name) void run("add", () => api.createArea(name).then(() => setAdding("")), `Added ${name}.`)
            }}
          >
            <Input className="h-9 w-48" placeholder="New area name" value={adding} onChange={(e) => setAdding(e.target.value)} />
            <Button type="submit" icon={<Plus className="size-4" />} busy={busy === "add"} disabled={!adding.trim()}>
              Add area
            </Button>
          </form>
        }
      >
        {view.loading && !view.data ? (
          <Empty title="Reading the areas" />
        ) : (
          <div className="grid gap-3 p-4 sm:grid-cols-2 xl:grid-cols-3">
            {areas.map((area) => (
              <AreaBox
                key={area.id}
                area={area}
                busy={busy}
                onSwitch={(action) => switchArea(area, action)}
                onRename={(name) => run(`${area.id}:rename`, () => api.renameArea(area.id, name), `Renamed to ${name}.`)}
                onDelete={() => remove(area)}
                onDevice={setOpened}
                onToggleDevice={(device) =>
                  run(`device:${device.id}`, () => api.bulk([device.id], device.on ? "off" : "on"))
                }
              />
            ))}
            {(view.data?.unassigned.length ?? 0) > 0 && (
              <section className="rounded-lg border border-dashed border-line p-3">
                <h3 className="label text-sm">Not in an area</h3>
                <p className="mt-0.5 text-xs text-muted">Open a device to give it an area. Apps and automations group by area.</p>
                <ul className="mt-2 space-y-1">
                  {view.data!.unassigned.map((device) => (
                    <li key={device.id}>
                      <button className="text-sm hover:underline" onClick={() => setOpened(device.id)}>
                        {device.name}
                      </button>
                    </li>
                  ))}
                </ul>
              </section>
            )}
          </div>
        )}
      </Panel>
      <DeviceDrawer deviceKey={opened} onClose={() => setOpened(null)} onChanged={() => void view.reload()} />
    </>
  )
}

function AreaBox({
  area,
  busy,
  onSwitch,
  onRename,
  onDelete,
  onDevice,
  onToggleDevice,
}: {
  area: AreaCard
  busy: string | null
  onSwitch: (action: "on" | "off") => void
  onRename: (name: string) => void
  onDelete: () => void
  onDevice: (id: string) => void
  onToggleDevice: (device: AreaDevice) => void
}) {
  const [editing, setEditing] = useState(false)
  const [name, setName] = useState(area.name)
  return (
    <section className="flex flex-col rounded-lg border border-line bg-card">
      <header className="flex items-start justify-between gap-2 border-b border-line px-3 py-2.5">
        {editing ? (
          <form
            className="flex flex-1 gap-1.5"
            onSubmit={(e) => {
              e.preventDefault()
              if (name.trim() && name.trim() !== area.name) onRename(name.trim())
              setEditing(false)
            }}
          >
            <Input className="h-8" autoFocus value={name} onChange={(e) => setName(e.target.value)} />
            <Button type="submit" className="h-8" variant="primary">
              Save
            </Button>
          </form>
        ) : (
          <div className="min-w-0">
            <h3 className="label truncate text-[15px]">{area.name}</h3>
            <p className="text-xs text-muted">
              {area.devices.length} devices
              {area.switchable > 0 && ` · ${area.on} of ${area.switchable} on`}
              {area.offline > 0 && <span className="text-bad"> · {area.offline} offline</span>}
            </p>
          </div>
        )}
        {!editing && (
          <span className="flex shrink-0 gap-0.5">
            <Button variant="ghost" className="h-8 px-1.5" title="Rename" aria-label={`Rename ${area.name}`} icon={<Pencil className="size-4" />} onClick={() => setEditing(true)} />
            <Button
              variant="ghost"
              className="h-8 px-1.5"
              title="Delete area"
              aria-label={`Delete ${area.name}`}
              icon={<Trash2 className="size-4" />}
              busy={busy === `${area.id}:delete`}
              onClick={onDelete}
            />
          </span>
        )}
      </header>
      {area.devices.length === 0 ? (
        <p className="flex-1 px-3 py-4 text-sm text-muted">No devices yet.</p>
      ) : (
        <ul className="flex-1 divide-y divide-line">
          {area.devices.map((device) => (
            <li key={device.id} className="flex items-center gap-2 px-3 py-1.5">
              <span className="size-2 shrink-0 rounded-full" style={{ background: sourceColor(device.source) }} />
              <button className="min-w-0 flex-1 truncate text-left text-sm hover:underline" onClick={() => onDevice(device.id)}>
                {device.name}
              </button>
              {device.online === false && <Led state="warn" title="Offline" />}
              {device.switchable && (
                <button
                  onClick={() => onToggleDevice(device)}
                  disabled={busy === `device:${device.id}`}
                  aria-pressed={device.on}
                  aria-label={`Turn ${device.name} ${device.on ? "off" : "on"}`}
                  className={cx(
                    "h-7 w-12 rounded-md border text-[11px] font-semibold disabled:opacity-50",
                    device.on ? "border-transparent bg-ok text-card" : "border-line text-muted hover:text-ink",
                  )}
                >
                  {busy === `device:${device.id}` ? "…" : device.on ? "ON" : "OFF"}
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
      {area.switchable > 0 && (
        <footer className="flex gap-2 border-t border-line px-3 py-2">
          <Button className="h-8 flex-1" icon={<Power className="size-4" />} busy={busy === `${area.id}:on`} onClick={() => onSwitch("on")}>
            All on
          </Button>
          <Button className="h-8 flex-1" icon={<PowerOff className="size-4" />} busy={busy === `${area.id}:off`} onClick={() => onSwitch("off")}>
            All off
          </Button>
        </footer>
      )}
    </section>
  )
}
