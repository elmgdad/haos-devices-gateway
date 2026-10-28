import { ExternalLink, Pencil, Play, Plus, Trash2, X } from "lucide-react"
import { useMemo, useState } from "react"
import { api } from "../api"
import { cx, errorText, openInHa, timeAgo, usePoll } from "../lib"
import type { AreaCard, Automation, AutomationAction, AutomationSpec, AutomationTarget, AutomationTrigger } from "../types"
import { Badge, Button, Empty, Field, Input, Modal, Notice, Panel, SearchInput, Select, Toggle, useFeedback } from "../ui"

const DAYS = [
  ["mon", "Mon"],
  ["tue", "Tue"],
  ["wed", "Wed"],
  ["thu", "Thu"],
  ["fri", "Fri"],
  ["sat", "Sat"],
  ["sun", "Sun"],
] as const

const ON_OFF = new Set(["Boolean", "switch", "light", "fan", "input_boolean", "binary_sensor", "siren", "humidifier"])

type Control = AutomationTarget["controls"][number]

export default function AutomationsTab() {
  const feedback = useFeedback()
  const view = usePoll(api.automations, 30000)
  const targets = usePoll(api.automationTargets)
  const areas = usePoll(api.areaOverview)
  const [query, setQuery] = useState("")
  const [oursOnly, setOursOnly] = useState(false)
  const [busy, setBusy] = useState<string | null>(null)
  const [editing, setEditing] = useState<Automation | "new" | null>(null)

  const items = useMemo(() => {
    const q = query.trim().toLowerCase()
    return (view.data?.automations ?? []).filter(
      (a) => (!oursOnly || a.ours) && (!q || `${a.name} ${a.entity_id}`.toLowerCase().includes(q)),
    )
  }, [view.data, query, oursOnly])

  const run = async (id: string, action: () => Promise<unknown>, done: string) => {
    setBusy(id)
    try {
      await action()
      feedback.ok(done)
      await view.reload()
    } catch (exc) {
      feedback.fail(errorText(exc))
    } finally {
      setBusy(null)
    }
  }

  const remove = async (item: Automation) => {
    const ok = await feedback.confirm({
      title: `Delete ${item.name}?`,
      body: "The automation is removed from Home Assistant.",
      action: "Delete",
      danger: true,
    })
    if (ok) await run(`${item.entity_id}:delete`, () => api.deleteAutomation(item.entity_id), `Deleted ${item.name}.`)
  }

  if (view.error && !view.data) return <Notice tone="bad">Could not read the automations: {view.error}</Notice>
  return (
    <>
      <Panel
        title={`Automations (${items.length})`}
        aside={
          <>
            <Toggle checked={oursOnly} onChange={setOursOnly}>
              Made in Device Hub
            </Toggle>
            <SearchInput value={query} onChange={setQuery} placeholder="Search automations" />
            <Button variant="primary" icon={<Plus className="size-4" />} onClick={() => setEditing("new")}>
              New automation
            </Button>
          </>
        }
      >
        <p className="border-b border-line px-4 py-2.5 text-sm text-muted">
          These run in Home Assistant, so they keep working when Device Hub is stopped. Device Hub edits and deletes the ones it
          made; the rest can be turned on or off, run, or opened in Home Assistant.
        </p>
        {view.loading && !view.data ? (
          <Empty title="Reading automations" />
        ) : items.length === 0 ? (
          <Empty title="No automations here">Make one with New automation.</Empty>
        ) : (
          <ul className="divide-y divide-line">
            {items.map((item) => (
              <li key={item.entity_id} className="flex flex-wrap items-center gap-3 px-4 py-3">
                <label className="inline-flex cursor-pointer items-center" title={item.enabled ? "Turn off" : "Turn on"}>
                  <input
                    type="checkbox"
                    className="size-4 accent-[var(--ok)]"
                    checked={item.enabled}
                    disabled={busy === `${item.entity_id}:enabled`}
                    aria-label={`${item.name} ${item.enabled ? "on" : "off"}`}
                    onChange={() =>
                      run(
                        `${item.entity_id}:enabled`,
                        () => api.enableAutomation(item.entity_id, !item.enabled),
                        `Turned ${item.name} ${item.enabled ? "off" : "on"}.`,
                      )
                    }
                  />
                </label>
                <div className="min-w-56 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className={cx("font-medium", !item.enabled && "text-muted")}>{item.name}</span>
                    <Badge tone={item.ours ? "info" : "neutral"}>{item.ours ? "Device Hub" : "Home Assistant"}</Badge>
                    {item.running > 0 && <Badge tone="ok">running</Badge>}
                  </div>
                  <p className="text-xs text-muted">
                    {item.spec ? describe(item.spec, targets.data?.devices ?? [], areas.data?.areas ?? []) : item.entity_id}
                    {" · "}
                    {item.last_triggered ? `last ran ${timeAgo(item.last_triggered)}` : "never ran"}
                  </p>
                </div>
                <span className="flex flex-wrap gap-1">
                  <Button
                    className="h-8"
                    icon={<Play className="size-4" />}
                    busy={busy === `${item.entity_id}:run`}
                    onClick={() => run(`${item.entity_id}:run`, () => api.runAutomation(item.entity_id), `Ran ${item.name}.`)}
                  >
                    Run
                  </Button>
                  {item.ours ? (
                    <>
                      <Button className="h-8" icon={<Pencil className="size-4" />} onClick={() => setEditing(item)}>
                        Edit
                      </Button>
                      <Button
                        variant="ghost"
                        className="h-8 px-1.5"
                        title="Delete"
                        aria-label={`Delete ${item.name}`}
                        icon={<Trash2 className="size-4" />}
                        busy={busy === `${item.entity_id}:delete`}
                        onClick={() => remove(item)}
                      />
                    </>
                  ) : (
                    item.id && (
                      <Button
                        className="h-8"
                        icon={<ExternalLink className="size-4" />}
                        onClick={() => openInHa(`/config/automation/edit/${item.id}`)}
                      >
                        Open in HA
                      </Button>
                    )
                  )}
                </span>
              </li>
            ))}
          </ul>
        )}
      </Panel>
      {editing !== null && (
        <Builder
          existing={editing === "new" ? null : editing}
          devices={targets.data?.devices ?? []}
          areas={areas.data?.areas ?? []}
          onClose={() => setEditing(null)}
          onSaved={() => {
            setEditing(null)
            void view.reload()
          }}
        />
      )}
    </>
  )
}

/** One line for a Device Hub spec, such as: At sunset −15 min on Fri, Sat → Kitchen plug on, wait 30 s. */
function describe(spec: AutomationSpec, devices: AutomationTarget[], areas: AreaCard[]) {
  const device = (id?: string) => devices.find((d) => d.id === id)
  const control = (id?: string, code?: string) => device(id)?.controls.find((c) => c.code === code)
  const t = spec.trigger
  let when = ""
  if (t.type === "time") when = `At ${t.at}`
  else if (t.type === "sun") when = `At ${t.event}${t.offset_minutes ? ` ${t.offset_minutes > 0 ? "+" : "−"}${Math.abs(t.offset_minutes)} min` : ""}`
  else {
    const what = control(t.device_id, t.code)?.name ?? device(t.device_id)?.name ?? "a device"
    when =
      t.above !== undefined || t.below !== undefined
        ? `When ${what} ${t.above !== undefined ? `goes above ${t.above}` : `goes below ${t.below}`}`
        : `When ${what} becomes ${say(t.to)}`
    if (t.for_seconds) when += ` for ${t.for_seconds} s`
  }
  if (spec.days.length && spec.days.length < 7) when += ` on ${spec.days.map((d) => d[0].toUpperCase() + d.slice(1)).join(", ")}`
  const steps = spec.actions.map((a) => {
    if (a.delay_seconds !== undefined) return `wait ${a.delay_seconds} s`
    if (a.area_id) return `${areas.find((x) => x.id === a.area_id)?.name ?? a.area_id} all ${a.action}`
    const c = control(a.device_id, a.code)
    const name = c?.name ?? device(a.device_id)?.name ?? a.code
    return a.brightness !== undefined ? `${name} ${a.brightness}%` : `${name} ${say(a.value)}`
  })
  return `${when} → ${steps.join(", ")}`
}

function say(value: unknown) {
  if (value === true) return "on"
  if (value === false) return "off"
  if (value === null || value === undefined || value === "") return "pressed"
  return String(value)
}

function emptySpec(): AutomationSpec {
  return { name: "", trigger: { type: "time", at: "18:00" }, days: [], actions: [{ device_id: "", code: "", value: true }] }
}

function Builder({
  existing,
  devices,
  areas,
  onClose,
  onSaved,
}: {
  existing: Automation | null
  devices: AutomationTarget[]
  areas: AreaCard[]
  onClose: () => void
  onSaved: () => void
}) {
  const feedback = useFeedback()
  const [spec, setSpec] = useState<AutomationSpec>(() => (existing?.spec ? structuredClone(existing.spec) : emptySpec()))
  const [busy, setBusy] = useState(false)
  const set = (patch: Partial<AutomationSpec>) => setSpec((current) => ({ ...current, ...patch }))
  const setTrigger = (patch: Partial<AutomationTrigger>) => setSpec((current) => ({ ...current, trigger: { ...current.trigger, ...patch } }))
  const setAction = (index: number, next: AutomationAction) =>
    setSpec((current) => ({ ...current, actions: current.actions.map((a, i) => (i === index ? next : a)) }))

  const save = async () => {
    setBusy(true)
    try {
      if (existing) await api.saveAutomation(existing.entity_id, spec)
      else await api.createAutomation(spec)
      feedback.ok(`Saved ${spec.name}.`)
      onSaved()
    } catch (exc) {
      feedback.fail(errorText(exc))
    } finally {
      setBusy(false)
    }
  }

  const t = spec.trigger
  const triggerControl = devices.find((d) => d.id === t.device_id)?.controls.find((c) => c.code === t.code)
  return (
    <Modal
      open
      wide
      title={existing ? `Edit ${existing.name}` : "New automation"}
      onClose={onClose}
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" busy={busy} onClick={save}>
            Save automation
          </Button>
        </>
      }
    >
      <div className="space-y-5">
        <Field label="Name">
          <Input value={spec.name} onChange={(e) => set({ name: e.target.value })} placeholder="Kitchen lights at sunset" />
        </Field>

        <div className="space-y-2">
          <p className="text-sm font-medium">When</p>
          <div className="flex flex-wrap items-center gap-2">
            <Select
              className="w-auto"
              value={t.type === "sun" ? t.event : t.type}
              onChange={(e) => {
                const v = e.target.value
                if (v === "time") set({ trigger: { type: "time", at: t.at ?? "18:00" } })
                else if (v === "sunrise" || v === "sunset") set({ trigger: { type: "sun", event: v, offset_minutes: t.offset_minutes ?? 0 } })
                else set({ trigger: { type: "device", device_id: "", code: "", to: true } })
              }}
            >
              <option value="time">At a time</option>
              <option value="sunrise">At sunrise</option>
              <option value="sunset">At sunset</option>
              <option value="device">When a device changes</option>
            </Select>
            {t.type === "time" && (
              <Input type="time" className="w-32" value={t.at ?? ""} onChange={(e) => setTrigger({ at: e.target.value })} />
            )}
            {t.type === "sun" && (
              <span className="inline-flex items-center gap-2 text-sm">
                <Input
                  type="number"
                  className="w-24"
                  value={t.offset_minutes ?? 0}
                  onChange={(e) => setTrigger({ offset_minutes: Number(e.target.value) })}
                />
                minutes {(t.offset_minutes ?? 0) < 0 ? "before" : "after"} (negative is before)
              </span>
            )}
          </div>
          {t.type === "device" && (
            <div className="flex flex-wrap items-center gap-2">
              <ControlPicker
                devices={devices}
                deviceId={t.device_id}
                code={t.code}
                use="trigger"
                onChange={(device_id, code) => setTrigger({ device_id, code, to: true, above: undefined, below: undefined })}
              />
              {triggerControl && <TriggerValue control={triggerControl} trigger={t} onChange={(patch) => setSpec((c) => ({ ...c, trigger: { ...patch } }))} />}
              {triggerControl && (
                <span className="inline-flex items-center gap-2 text-sm text-muted">
                  for
                  <Input
                    type="number"
                    min={0}
                    className="w-20"
                    value={t.for_seconds ?? 0}
                    onChange={(e) => setTrigger({ for_seconds: Number(e.target.value) || undefined })}
                  />
                  seconds
                </span>
              )}
            </div>
          )}
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="mr-1 text-sm text-muted">Only on</span>
            {DAYS.map(([id, label]) => {
              const on = spec.days.includes(id)
              return (
                <button
                  key={id}
                  type="button"
                  aria-pressed={on}
                  onClick={() => set({ days: on ? spec.days.filter((d) => d !== id) : [...spec.days, id] })}
                  className={cx("rounded-md border px-2.5 py-1 text-xs", on ? "border-transparent bg-ink text-card" : "border-line text-muted")}
                >
                  {label}
                </button>
              )
            })}
            <span className="text-xs text-muted">{spec.days.length === 0 ? "every day" : ""}</span>
          </div>
        </div>

        <div className="space-y-2">
          <p className="text-sm font-medium">Then</p>
          {spec.actions.map((action, index) => (
            <ActionRow
              key={index}
              action={action}
              devices={devices}
              areas={areas}
              onChange={(next) => setAction(index, next)}
              onRemove={spec.actions.length > 1 ? () => set({ actions: spec.actions.filter((_, i) => i !== index) }) : undefined}
            />
          ))}
          <div className="flex flex-wrap gap-2">
            <Button className="h-8" icon={<Plus className="size-4" />} onClick={() => set({ actions: [...spec.actions, { device_id: "", code: "", value: true }] })}>
              Device
            </Button>
            <Button className="h-8" icon={<Plus className="size-4" />} onClick={() => set({ actions: [...spec.actions, { area_id: areas[0]?.id ?? "", action: "off" }] })}>
              Area
            </Button>
            <Button className="h-8" icon={<Plus className="size-4" />} onClick={() => set({ actions: [...spec.actions, { delay_seconds: 60 }] })}>
              Wait
            </Button>
          </div>
        </div>
      </div>
    </Modal>
  )
}

function ControlPicker({
  devices,
  deviceId,
  code,
  use,
  onChange,
}: {
  devices: AutomationTarget[]
  deviceId?: string
  code?: string
  use: "trigger" | "action"
  onChange: (deviceId: string, code: string) => void
}) {
  const usable = devices.filter((d) => d.controls.some((c) => c[use]))
  const device = usable.find((d) => d.id === deviceId)
  const controls = (device?.controls ?? []).filter((c) => c[use])
  return (
    <>
      <Select
        className="w-auto max-w-56"
        value={deviceId ?? ""}
        onChange={(e) => {
          const next = usable.find((d) => d.id === e.target.value)
          onChange(e.target.value, next?.controls.find((c) => c[use])?.code ?? "")
        }}
      >
        <option value="">Pick a device…</option>
        {usable.map((d) => (
          <option key={d.id} value={d.id}>
            {d.name}
            {d.area ? ` · ${d.area}` : ""}
          </option>
        ))}
      </Select>
      {device && (
        <Select className="w-auto max-w-64" value={code ?? ""} onChange={(e) => onChange(device.id, e.target.value)}>
          {controls.map((c) => (
            <option key={c.code} value={c.code}>
              {c.name}
            </option>
          ))}
        </Select>
      )}
    </>
  )
}

function TriggerValue({
  control,
  trigger,
  onChange,
}: {
  control: Control
  trigger: AutomationTrigger
  onChange: (next: AutomationTrigger) => void
}) {
  const base = { type: "device" as const, device_id: trigger.device_id, code: trigger.code, for_seconds: trigger.for_seconds }
  if (ON_OFF.has(control.type)) {
    return (
      <Select className="w-auto" value={trigger.to === false ? "off" : "on"} onChange={(e) => onChange({ ...base, to: e.target.value === "on" })}>
        <option value="on">turns on</option>
        <option value="off">turns off</option>
      </Select>
    )
  }
  if (control.options?.length) {
    return (
      <Select className="w-auto" value={String(trigger.to ?? "")} onChange={(e) => onChange({ ...base, to: e.target.value })}>
        {control.options.map((o) => (
          <option key={o} value={o}>
            becomes {o}
          </option>
        ))}
      </Select>
    )
  }
  const numeric = typeof control.state === "number" || !Number.isNaN(Number(control.state)) || control.unit
  if (numeric) {
    const way = trigger.below !== undefined ? "below" : "above"
    const value = trigger.below ?? trigger.above ?? 0
    return (
      <span className="inline-flex items-center gap-2">
        <Select className="w-auto" value={way} onChange={(e) => onChange({ ...base, [e.target.value]: value })}>
          <option value="above">goes above</option>
          <option value="below">goes below</option>
        </Select>
        <Input type="number" className="w-24" value={value} onChange={(e) => onChange({ ...base, [way]: Number(e.target.value) })} />
        {control.unit && <span className="text-sm text-muted">{control.unit}</span>}
      </span>
    )
  }
  return <Input className="w-40" placeholder="becomes…" value={String(trigger.to ?? "")} onChange={(e) => onChange({ ...base, to: e.target.value })} />
}

function ActionRow({
  action,
  devices,
  areas,
  onChange,
  onRemove,
}: {
  action: AutomationAction
  devices: AutomationTarget[]
  areas: AreaCard[]
  onChange: (next: AutomationAction) => void
  onRemove?: () => void
}) {
  const control = devices.find((d) => d.id === action.device_id)?.controls.find((c) => c.code === action.code)
  let body
  if (action.delay_seconds !== undefined) {
    body = (
      <span className="inline-flex items-center gap-2 text-sm">
        Wait
        <Input type="number" min={1} className="w-24" value={action.delay_seconds} onChange={(e) => onChange({ delay_seconds: Number(e.target.value) })} />
        seconds
      </span>
    )
  } else if (action.area_id !== undefined) {
    body = (
      <span className="inline-flex flex-wrap items-center gap-2 text-sm">
        Turn everything in
        <Select className="w-auto" value={action.area_id} onChange={(e) => onChange({ ...action, area_id: e.target.value })}>
          {areas.map((a) => (
            <option key={a.id} value={a.id}>
              {a.name}
            </option>
          ))}
        </Select>
        <Select className="w-auto" value={action.action} onChange={(e) => onChange({ ...action, action: e.target.value as "on" | "off" })}>
          <option value="on">on</option>
          <option value="off">off</option>
        </Select>
      </span>
    )
  } else {
    body = (
      <span className="inline-flex flex-wrap items-center gap-2">
        <ControlPicker
          devices={devices}
          deviceId={action.device_id}
          code={action.code}
          use="action"
          onChange={(device_id, code) => onChange({ device_id, code, value: true })}
        />
        {control && <ActionValue control={control} action={action} onChange={onChange} />}
      </span>
    )
  }
  return (
    <div className="flex items-center gap-2 rounded-md border border-line p-2">
      <div className="min-w-0 flex-1">{body}</div>
      {onRemove && <Button variant="ghost" className="h-8 px-1.5" aria-label="Remove this step" icon={<X className="size-4" />} onClick={onRemove} />}
    </div>
  )
}

function ActionValue({
  control,
  action,
  onChange,
}: {
  control: Control
  action: AutomationAction
  onChange: (next: AutomationAction) => void
}) {
  const base = { device_id: action.device_id, code: action.code }
  if (control.type === "button") return <span className="text-sm text-muted">press</span>
  if (ON_OFF.has(control.type) || control.type === "lock" || control.type === "valve") {
    const mode = action.brightness !== undefined ? "dim" : action.value === false ? "off" : "on"
    return (
      <span className="inline-flex items-center gap-2">
        <Select
          className="w-auto"
          value={mode}
          onChange={(e) =>
            onChange(e.target.value === "dim" ? { ...base, brightness: 50 } : { ...base, value: e.target.value === "on" })
          }
        >
          <option value="on">{control.type === "lock" ? "unlock" : "turn on"}</option>
          <option value="off">{control.type === "lock" ? "lock" : "turn off"}</option>
          {control.dimmable && <option value="dim">set brightness</option>}
        </Select>
        {mode === "dim" && (
          <span className="inline-flex items-center gap-1 text-sm">
            <Input type="number" min={0} max={100} className="w-20" value={action.brightness} onChange={(e) => onChange({ ...base, brightness: Number(e.target.value) })} />%
          </span>
        )}
      </span>
    )
  }
  if (control.type === "cover") {
    return (
      <Select className="w-auto" value={String(action.value ?? "open")} onChange={(e) => onChange({ ...base, value: e.target.value })}>
        <option value="open">open</option>
        <option value="close">close</option>
        <option value="stop">stop</option>
      </Select>
    )
  }
  if (control.options?.length) {
    return (
      <Select className="w-auto" value={String(action.value ?? control.options[0])} onChange={(e) => onChange({ ...base, value: e.target.value })}>
        {control.options.map((o) => (
          <option key={o} value={o}>
            {o}
          </option>
        ))}
      </Select>
    )
  }
  return (
    <span className="inline-flex items-center gap-1 text-sm">
      set to
      <Input
        type="number"
        className="w-24"
        min={control.min}
        max={control.max}
        value={typeof action.value === "number" ? action.value : ""}
        onChange={(e) => onChange({ ...base, value: Number(e.target.value) })}
      />
      {control.unit}
    </span>
  )
}
