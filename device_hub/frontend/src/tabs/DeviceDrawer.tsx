import { ExternalLink, Minus, Plus, Power, Trash2 } from "lucide-react"
import { useEffect, useRef, useState } from "react"
import { api } from "../api"
import { cx, errorText, openInHa, removalEffect, SOURCES, useLiveEvents, usePoll } from "../lib"
import type { Command, Control, ControlDevice, DeviceDetail } from "../types"
import { AreaSelect, Badge, Button, Empty, Input, Led, Modal, Notice, Select, SourceTag, useFeedback } from "../ui"
import { ActivityList } from "./ActivityList"

type Section = "control" | "details" | "activity"

const TOGGLES = new Set(["Boolean", "switch", "light", "fan", "input_boolean", "humidifier", "siren", "valve"])
const NUMBERS = new Set(["Integer", "number", "input_number"])
const CHOICES = new Set(["Enum", "select", "input_select"])

function isOn(state: Control["state"]) {
  return state === true || state === "on" || state === "open" || state === "unlocked" || state === "true"
}

function shown(control: Control) {
  if (control.state === null || control.state === undefined || control.state === "") return "–"
  if (control.state === true) return "On"
  if (control.state === false) return "Off"
  const factor = control.type === "Integer" && control.scale ? 10 ** control.scale : 1
  const value = typeof control.state === "number" && factor !== 1 ? control.state / factor : control.state
  return `${value}${control.unit ? ` ${control.unit}` : ""}`
}

export default function DeviceDrawer({
  deviceKey,
  onClose,
  onChanged,
}: {
  deviceKey: string | null
  onClose: () => void
  onChanged: () => void
}) {
  const open = deviceKey !== null
  const [section, setSection] = useState<Section>("control")
  useEffect(() => setSection("control"), [deviceKey])
  const detail = usePoll(() => (deviceKey ? api.deviceDetail(deviceKey) : Promise.resolve(null)), open ? 30000 : 0, [deviceKey])
  const control = usePoll(() => (deviceKey ? api.control(deviceKey) : Promise.resolve(null)), open ? 15000 : 0, [deviceKey])

  const pending = useRef<number | null>(null)
  useLiveEvents((kind, data) => {
    if (data.device_id !== deviceKey) return
    if (kind === "activity") void detail.reload()
    if (pending.current) window.clearTimeout(pending.current)
    pending.current = window.setTimeout(() => void control.reload(), 400)
  }, open)

  const info = detail.data
  const device = info?.device
  const title = device ? device.name : "Device"
  return (
    <Modal open={open} title={title} onClose={onClose} wide>
      {detail.error && !detail.data ? (
        <Notice tone="bad">Could not read this device: {detail.error}</Notice>
      ) : !info || !device ? (
        <Empty title="Reading the device" />
      ) : (
        <div className="space-y-4">
          <div className="flex flex-wrap items-center gap-2 text-sm">
            {device.sources.map((source) => (
              <SourceTag key={source} source={source} />
            ))}
            <span className={cx("inline-flex items-center gap-1.5", device.warn && "text-bad")}>
              <Led state={device.online === false ? "warn" : device.online ? "ok" : "off"} />
              {device.status}
            </span>
            {device.area && <Badge>{device.area}</Badge>}
            <span className="ml-auto font-mono text-xs text-muted">{device.key}</span>
          </div>
          <div className="flex rounded-md border border-line p-0.5" role="tablist">
            {(["control", "details", "activity"] as Section[]).map((id) => (
              <button
                key={id}
                role="tab"
                aria-selected={section === id}
                onClick={() => setSection(id)}
                className={cx(
                  "flex-1 rounded px-3 py-1.5 text-sm capitalize",
                  section === id ? "bg-ink text-card" : "text-muted hover:text-ink",
                )}
              >
                {id}
                {id === "activity" && info.activity.length > 0 && (
                  <span className="ml-1.5 font-mono text-xs opacity-70">{info.activity.length}</span>
                )}
              </button>
            ))}
          </div>
          {section === "control" && (
            <Controls
              deviceKey={deviceKey!}
              data={control.data}
              loading={control.loading}
              error={control.error}
              onResult={(next) => {
                control.setData(next)
                void detail.reload()
                onChanged()
              }}
            />
          )}
          {section === "details" && (
            <Details
              detail={info}
              onSaved={() => {
                void detail.reload()
                void control.reload()
                onChanged()
              }}
              onRemoved={() => {
                onChanged()
                onClose()
              }}
            />
          )}
          {section === "activity" &&
            (info.activity.length ? (
              <div className="-mx-5 border-y border-line">
                <ActivityList entries={info.activity} showDevice={false} />
              </div>
            ) : (
              <Empty title="Nothing yet">Commands, renames and this device going offline or online show up here.</Empty>
            ))}
        </div>
      )}
    </Modal>
  )
}

function Controls({
  deviceKey,
  data,
  loading,
  error,
  onResult,
}: {
  deviceKey: string
  data: ControlDevice | null
  loading: boolean
  error: string | null
  onResult: (next: ControlDevice) => void
}) {
  const feedback = useFeedback()
  const [busy, setBusy] = useState<string | null>(null)
  const [more, setMore] = useState(false)

  const send = async (control: Control, command: Omit<Command, "code">) => {
    setBusy(control.code)
    try {
      onResult(await api.sendControl(deviceKey, [{ code: control.code, ...command }]))
    } catch (exc) {
      feedback.fail(errorText(exc))
    } finally {
      setBusy(null)
    }
  }

  if (loading && !data) return <Empty title="Reading controls" />
  if (error && !data) return <Notice tone="bad">Could not read the controls: {error}</Notice>
  if (!data || data.controls.length === 0) {
    return (
      <Empty title="No controls">
        Device Hub has nothing to switch or read on this device. Devices only seen on the network, and Tuya devices the cloud
        project cannot read, have no controls.
      </Empty>
    )
  }
  // Things to switch come before readings.
  const byUse = (a: Control, b: Control) => Number(b.writable) - Number(a.writable)
  const primary = data.controls.filter((c) => c.primary).sort(byUse)
  const rest = data.controls.filter((c) => !c.primary).sort(byUse)
  const list = (items: Control[]) => (
    <ul className="divide-y divide-line rounded-md border border-line">
      {items.map((item) => (
        <li key={item.code} className="flex flex-wrap items-center gap-3 px-3 py-2.5">
          <div className="min-w-40 flex-1">
            <div className="text-sm font-medium">{item.name}</div>
            <div className="truncate font-mono text-[11px] text-muted">{item.entity_id ?? item.code}</div>
          </div>
          <ControlInput control={item} busy={busy === item.code} onSend={(command) => send(item, command)} />
        </li>
      ))}
    </ul>
  )
  return (
    <div className="space-y-3">
      {data.online === false && <Notice>This device is offline, so commands may not reach it.</Notice>}
      {primary.length > 0 ? list(primary) : <p className="text-sm text-muted">Only settings and diagnostics.</p>}
      {rest.length > 0 && (
        <div>
          <button className="text-sm text-muted hover:text-ink" onClick={() => setMore(!more)} aria-expanded={more}>
            {more ? "Hide" : "Show"} settings and diagnostics ({rest.length})
          </button>
          {more && <div className="mt-2">{list(rest)}</div>}
        </div>
      )}
    </div>
  )
}

function ControlInput({
  control,
  busy,
  onSend,
}: {
  control: Control
  busy: boolean
  onSend: (command: Omit<Command, "code">) => void
}) {
  const [draft, setDraft] = useState<string>("")
  if (!control.writable) return <span className="font-mono text-sm">{shown(control)}</span>

  if (control.type === "button") {
    return (
      <Button busy={busy} onClick={() => onSend({ value: true })}>
        Press
      </Button>
    )
  }
  if (control.type === "lock") {
    const unlocked = control.state === "unlocked"
    return (
      <Button busy={busy} onClick={() => onSend({ value: !unlocked })}>
        {unlocked ? "Lock" : "Unlock"}
      </Button>
    )
  }
  if (control.type === "cover") {
    return (
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="mr-1 text-xs text-muted">{shown(control)}</span>
        {(["open", "stop", "close"] as const).map((value) => (
          <Button key={value} className="h-8 capitalize" busy={busy} onClick={() => onSend({ value })}>
            {value}
          </Button>
        ))}
        {control.position !== undefined && (
          <Slider value={control.position} min={0} max={100} step={1} unit="%" busy={busy} onCommit={(v) => onSend({ value: v })} />
        )}
      </div>
    )
  }
  if (control.type === "climate") {
    const target = control.target ?? null
    const step = control.step ?? 0.5
    return (
      <div className="flex flex-wrap items-center gap-2">
        {control.current !== null && control.current !== undefined && (
          <span className="text-xs text-muted">now {control.current}°</span>
        )}
        <Select
          className="h-8 text-xs"
          value={String(control.state ?? "")}
          disabled={busy}
          onChange={(e) => onSend({ value: e.target.value })}
        >
          {(control.options ?? []).map((option) => (
            <option key={option} value={option}>
              {option.replace("_", " ")}
            </option>
          ))}
        </Select>
        {target !== null && (
          <span className="inline-flex items-center gap-1">
            <Button className="h-8 px-2" aria-label="Lower" busy={busy} onClick={() => onSend({ value: target - step })} icon={<Minus className="size-4" />} />
            <span className="w-14 text-center font-mono text-sm">{target}°</span>
            <Button className="h-8 px-2" aria-label="Raise" busy={busy} onClick={() => onSend({ value: target + step })} icon={<Plus className="size-4" />} />
          </span>
        )}
      </div>
    )
  }
  if (TOGGLES.has(control.type) && !(control.type === "valve" && typeof control.state === "number")) {
    const on = isOn(control.state)
    return (
      <div className="flex items-center gap-3">
        {control.dimmable && (
          <Slider
            value={on ? control.brightness ?? 0 : 0}
            min={0}
            max={100}
            step={1}
            unit="%"
            busy={busy}
            onCommit={(v) => onSend({ brightness: v })}
          />
        )}
        <button
          onClick={() => onSend({ value: !on })}
          disabled={busy}
          aria-pressed={on}
          className={cx(
            "inline-flex h-8 min-w-20 items-center justify-center gap-1.5 rounded-md border px-3 text-xs font-semibold transition-colors",
            "disabled:cursor-not-allowed disabled:opacity-60",
            on ? "border-transparent bg-ok text-card" : "border-line bg-card text-ink hover:bg-sunk",
          )}
        >
          <Power className="size-3.5" />
          {busy ? "…" : on ? "ON" : "OFF"}
        </button>
      </div>
    )
  }
  if (NUMBERS.has(control.type)) {
    const factor = control.type === "Integer" && control.scale ? 10 ** control.scale : 1
    const raw = typeof control.state === "number" ? control.state : Number(control.state)
    return (
      <Slider
        value={Number.isFinite(raw) ? raw : control.min ?? 0}
        min={control.min ?? 0}
        max={control.max ?? 100}
        step={control.step ?? 1}
        factor={factor}
        unit={control.unit}
        busy={busy}
        onCommit={(v) => onSend({ value: v })}
      />
    )
  }
  if (CHOICES.has(control.type) && control.options?.length) {
    return (
      <Select
        className="h-8 min-w-36 text-xs"
        value={String(control.state ?? "")}
        disabled={busy}
        onChange={(e) => onSend({ value: e.target.value })}
      >
        {control.state === null && <option value="">–</option>}
        {control.options.map((option) => (
          <option key={option} value={option}>
            {option}
          </option>
        ))}
      </Select>
    )
  }
  return (
    <form
      className="flex gap-1.5"
      onSubmit={(e) => {
        e.preventDefault()
        onSend({ value: draft })
      }}
    >
      <Input className="h-8 w-40 text-xs" placeholder={shown(control)} value={draft} onChange={(e) => setDraft(e.target.value)} />
      <Button type="submit" className="h-8" busy={busy}>
        Send
      </Button>
    </form>
  )
}

/** A slider that sends once, when the thumb is let go. */
function Slider({
  value,
  min,
  max,
  step,
  factor = 1,
  unit,
  busy,
  onCommit,
}: {
  value: number
  min: number
  max: number
  step: number
  factor?: number
  unit?: string
  busy: boolean
  onCommit: (value: number) => void
}) {
  const [draft, setDraft] = useState(value)
  const dragging = useRef(false)
  useEffect(() => {
    if (!dragging.current) setDraft(value)
  }, [value])
  const commit = () => {
    dragging.current = false
    if (draft !== value) onCommit(draft)
  }
  return (
    <span className="inline-flex items-center gap-2">
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={draft}
        disabled={busy}
        aria-label="Value"
        className="w-36 accent-[var(--ink)]"
        onPointerDown={() => (dragging.current = true)}
        onChange={(e) => setDraft(Number(e.target.value))}
        onPointerUp={commit}
        onKeyUp={commit}
      />
      <span className="w-16 text-right font-mono text-xs">
        {busy ? "…" : `${Math.round((draft / factor) * 100) / 100}${unit ? ` ${unit}` : ""}`}
      </span>
    </span>
  )
}

function Details({ detail, onSaved, onRemoved }: { detail: DeviceDetail; onSaved: () => void; onRemoved: () => void }) {
  const feedback = useFeedback()
  const areas = usePoll(api.areas)
  const { device, ha } = detail
  const [name, setName] = useState(ha?.name_by_user ?? device.name)
  const [busy, setBusy] = useState<string | null>(null)
  useEffect(() => setName(ha?.name_by_user ?? device.name), [ha?.name_by_user, device.name])

  const save = async (next: string) => {
    setBusy("name")
    try {
      await api.updateDevice(device.key, { name: next })
      feedback.ok(next ? `Renamed to ${next}.` : "Name reset.")
      onSaved()
    } catch (exc) {
      feedback.fail(errorText(exc))
    } finally {
      setBusy(null)
    }
  }

  const effect = removalEffect(device.key)
  const remove = async () => {
    if (!effect) return
    const ok = await feedback.confirm({ title: `Remove ${device.name}?`, body: effect, action: "Remove device", danger: true })
    if (!ok) return
    setBusy("remove")
    try {
      await api.removeDevice(device.key)
      feedback.ok(`Removed ${device.name}.`)
      onRemoved()
    } catch (exc) {
      feedback.fail(errorText(exc))
      setBusy(null)
    }
  }

  const facts: [string, string | null | undefined][] = [
    ["Type", device.kind],
    ["Address", device.address],
    ["Systems", device.sources.map((s) => SOURCES[s].label).join(", ")],
    ["Manufacturer", ha?.manufacturer],
    ["Model", [ha?.model, ha?.model_id].filter(Boolean).join(" · ")],
    ["Firmware", ha?.sw_version],
    ["Hardware", ha?.hw_version],
    ["Serial", ha?.serial_number],
    ["Connected via", ha?.via],
    ["Integrations", ha?.integrations.join(", ")],
  ]
  return (
    <div className="space-y-5">
      <div className="grid gap-4 sm:grid-cols-2">
        <div>
          <p className="mb-1 text-sm font-medium">Name</p>
          {ha ? (
            <form
              className="flex gap-1.5"
              onSubmit={(e) => {
                e.preventDefault()
                void save(name.trim())
              }}
            >
              <Input value={name} onChange={(e) => setName(e.target.value)} placeholder={ha.name ?? ""} />
              <Button type="submit" variant="primary" busy={busy === "name"} disabled={name.trim() === (ha.name_by_user ?? device.name)}>
                Save
              </Button>
            </form>
          ) : (
            <p className="text-sm text-muted">Not in Home Assistant yet, so it keeps the name its system gives it.</p>
          )}
          {ha?.name_by_user && (
            <button className="mt-1 text-xs text-muted hover:text-ink" onClick={() => void save("")}>
              Go back to {ha.name}
            </button>
          )}
        </div>
        <div>
          <p className="mb-1 text-sm font-medium">Area</p>
          <AreaSelect
            areas={areas.data ?? []}
            value={device.area_id}
            deviceId={device.ha_device_id}
            onSaved={() => {
              void areas.reload()
              onSaved()
            }}
          />
        </div>
      </div>

      <dl className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-1.5 text-sm">
        {facts
          .filter(([, value]) => value)
          .map(([label, value]) => (
            <div key={label} className="contents">
              <dt className="text-muted">{label}</dt>
              <dd className="min-w-0 break-words">{value}</dd>
            </div>
          ))}
      </dl>

      <div className="flex flex-wrap gap-2">
        {ha && (
          <Button icon={<ExternalLink className="size-4" />} onClick={() => openInHa(`/config/devices/device/${ha.id}`)}>
            Open in Home Assistant
          </Button>
        )}
        {ha?.configuration_url && (
          <Button icon={<ExternalLink className="size-4" />} onClick={() => window.open(ha.configuration_url!, "_blank", "noopener")}>
            Device's own page
          </Button>
        )}
      </div>

      {detail.entities.length > 0 && (
        <div>
          <p className="mb-1 text-sm font-medium">Entities ({detail.entities.length})</p>
          <ul className="divide-y divide-line rounded-md border border-line text-sm">
            {detail.entities.map((entity) => (
              <li key={entity.entity_id} className="flex flex-wrap items-baseline gap-x-3 px-3 py-2">
                <span className="min-w-0 flex-1">
                  {entity.name}
                  <span className="block truncate font-mono text-[11px] text-muted">{entity.entity_id}</span>
                </span>
                {entity.category && <Badge>{entity.category}</Badge>}
                <span className="font-mono text-xs">
                  {entity.state ?? "–"}
                  {entity.unit ? ` ${entity.unit}` : ""}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {effect && (
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-bad/30 p-3">
          <p className="max-w-md text-sm text-muted">{effect}</p>
          <Button variant="danger" icon={<Trash2 className="size-4" />} busy={busy === "remove"} onClick={remove}>
            Remove device
          </Button>
        </div>
      )}
    </div>
  )
}
