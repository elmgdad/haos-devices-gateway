import { Minus, Plus, Power, Radio, Trash2, X } from "lucide-react"
import { useState } from "react"
import { api } from "../api"
import { cx, errorText, usePoll } from "../lib"
import type { IrBlaster, IrRemote, IrView } from "../types"
import { AreaSelect, Badge, Button, Field, Input, Modal, Panel, Select, useFeedback } from "../ui"

const MODE_LABELS: Record<string, string> = { cool: "Cool", heat: "Heat", auto: "Auto", fan_only: "Fan", dry: "Dry" }
const FAN_LABELS: Record<string, string> = { auto: "Auto", low: "Low", medium: "Medium", high: "High" }

function useRemoteActions(remote: IrRemote, onChanged: (next: IrRemote) => void) {
  const feedback = useFeedback()
  const [busy, setBusy] = useState<string | null>(null)
  const send = async (code: string, value?: boolean | number | string) => {
    setBusy(code)
    try {
      onChanged(await api.irCommand(remote.id, code, value))
    } catch (exc) {
      feedback.fail(errorText(exc))
    } finally {
      setBusy(null)
    }
  }
  return { busy, send }
}

function AcControls({ remote, onChanged }: { remote: IrRemote; onChanged: (next: IrRemote) => void }) {
  const { busy, send } = useRemoteActions(remote, onChanged)
  const ac = remote.ac!
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <Button
          variant={ac.power ? "primary" : "quiet"}
          icon={<Power className="size-4" />}
          busy={busy === "power"}
          onClick={() => send("power", !ac.power)}
        >
          {ac.power ? "On" : "Off"}
        </Button>
        <div className="inline-flex items-center gap-1 rounded-md border border-line px-1">
          <Button variant="ghost" className="h-8 px-2" aria-label="Colder" disabled={ac.temp <= ac.min} busy={busy === "temp"} onClick={() => send("temp", ac.temp - 1)}>
            <Minus className="size-4" />
          </Button>
          <span className="w-14 text-center font-mono text-lg">{ac.temp}°</span>
          <Button variant="ghost" className="h-8 px-2" aria-label="Warmer" disabled={ac.temp >= ac.max} busy={busy === "temp"} onClick={() => send("temp", ac.temp + 1)}>
            <Plus className="size-4" />
          </Button>
        </div>
      </div>
      <div className="grid grid-cols-2 gap-2">
        <Field label="Mode">
          <Select value={ac.mode} disabled={busy === "mode"} onChange={(e) => send("mode", e.target.value)}>
            {ac.modes.map((mode) => (
              <option key={mode} value={mode}>
                {MODE_LABELS[mode] ?? mode}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="Fan">
          <Select value={ac.fan} disabled={busy === "fan"} onChange={(e) => send("fan", e.target.value)}>
            {ac.fans.map((fan) => (
              <option key={fan} value={fan}>
                {FAN_LABELS[fan] ?? fan}
              </option>
            ))}
          </Select>
        </Field>
      </div>
      <p className="text-xs text-muted">IR is one way. Device Hub shows what it last sent, not what the AC reports.</p>
    </div>
  )
}

function KeyGrid({ remote, onChanged }: { remote: IrRemote; onChanged: (next: IrRemote) => void }) {
  const { busy, send } = useRemoteActions(remote, onChanged)
  const [more, setMore] = useState(false)
  const primary = remote.keys.filter((key) => key.primary)
  const rest = remote.keys.filter((key) => !key.primary)
  const shown = more ? [...primary, ...rest] : primary
  if (remote.keys.length === 0) {
    return <p className="text-sm text-muted">No buttons yet. Learn one from your remote.</p>
  }
  return (
    <div>
      <div className="grid grid-cols-3 gap-1.5 sm:grid-cols-4">
        {shown.map((key) => (
          <Button
            key={key.code}
            className={cx("justify-center px-2", key.code === "Power" && "text-bad")}
            busy={busy === key.code}
            onClick={() => send(key.code)}
            title={key.local ? "Learned. Sent on your network." : "Sent through the Tuya cloud."}
          >
            {key.name}
            {key.local && <span className="size-1.5 rounded-full bg-ok" />}
          </Button>
        ))}
      </div>
      {rest.length > 0 && (
        <button className="mt-2 text-xs text-muted hover:text-ink" onClick={() => setMore(!more)}>
          {more ? "Fewer keys" : `${rest.length} more keys`}
        </button>
      )}
    </div>
  )
}

function LearnDialog({
  remote,
  onClose,
  onLearned,
}: {
  remote: IrRemote | null
  onClose: () => void
  onLearned: (next: IrRemote) => void
}) {
  const feedback = useFeedback()
  const [name, setName] = useState("")
  const [listening, setListening] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const close = () => {
    if (listening) return
    setName("")
    setError(null)
    onClose()
  }
  const learn = async () => {
    if (!remote) return
    setListening(true)
    setError(null)
    try {
      const next = await api.irLearn(remote.id, name)
      feedback.ok(`Learned ${name.trim()} on ${remote.name}.`)
      onLearned(next)
      setName("")
      setListening(false)
      onClose()
    } catch (exc) {
      setError(errorText(exc))
      setListening(false)
    }
  }
  return (
    <Modal
      open={remote !== null}
      title={remote ? `Learn a button for ${remote.name}` : "Learn a button"}
      onClose={close}
      footer={
        <>
          <Button onClick={close} disabled={listening}>
            Cancel
          </Button>
          <Button variant="primary" busy={listening} disabled={!name.trim()} onClick={learn}>
            {listening ? "Listening…" : "Start listening"}
          </Button>
        </>
      }
    >
      <div className="space-y-3 text-sm">
        <Field label="Button name" hint="Use a Tuya key name such as Power to replace that key with a local one.">
          <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="Power" disabled={listening} autoFocus />
        </Field>
        {listening ? (
          <p className="rounded-md bg-src-lan/10 px-3 py-2">
            Point your remote at <b>{remote?.blaster_name || "the blaster"}</b> and press the button once now.
          </p>
        ) : (
          <p className="text-muted">
            Device Hub stores the code it hears and sends it later from {remote?.blaster_name || "the blaster"} on your network,
            without the Tuya cloud.
          </p>
        )}
        {error && <p className="rounded-md bg-bad/10 px-3 py-2 text-bad">{error}</p>}
      </div>
    </Modal>
  )
}

function NewRemoteDialog({
  open,
  blasters,
  onClose,
  onCreated,
}: {
  open: boolean
  blasters: IrBlaster[]
  onClose: () => void
  onCreated: (remote: IrRemote) => void
}) {
  const feedback = useFeedback()
  const [name, setName] = useState("")
  const [blaster, setBlaster] = useState("")
  const [busy, setBusy] = useState(false)
  const create = async () => {
    setBusy(true)
    try {
      const remote = await api.irCreate(name, blaster || blasters[0]?.id || "")
      feedback.ok(`Added ${remote.name}. Learn its buttons next.`)
      setName("")
      onCreated(remote)
    } catch (exc) {
      feedback.fail(errorText(exc))
    } finally {
      setBusy(false)
    }
  }
  return (
    <Modal
      open={open}
      title="New remote"
      onClose={onClose}
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" busy={busy} disabled={!name.trim() || blasters.length === 0} onClick={create}>
            Add remote
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        <Field label="Name">
          <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="Bedroom fan" autoFocus />
        </Field>
        <Field label="Blaster" hint="The IR blaster or control panel that can see the device.">
          <Select value={blaster || blasters[0]?.id || ""} onChange={(e) => setBlaster(e.target.value)}>
            {blasters.map((item) => (
              <option key={item.id} value={item.id}>
                {item.name}
                {item.ip ? ` · ${item.ip}` : " · not found on network yet"}
              </option>
            ))}
          </Select>
        </Field>
      </div>
    </Modal>
  )
}

export function RemoteCard({
  remote,
  areas,
  onChanged,
  onLearn,
  onReload,
}: {
  remote: IrRemote
  areas: { id: string; name: string }[]
  onChanged: (next: IrRemote) => void
  onLearn: (remote: IrRemote) => void
  onReload: () => void
}) {
  const feedback = useFeedback()
  const forgetButton = async (name: string) => {
    try {
      onChanged(await api.irForget(remote.id, name))
    } catch (exc) {
      feedback.fail(errorText(exc))
    }
  }
  const remove = async () => {
    const ok = await feedback.confirm({
      title: remote.source === "learned" ? `Delete ${remote.name}?` : `Forget the learned buttons on ${remote.name}?`,
      body:
        remote.source === "learned"
          ? "Its learned buttons and Home Assistant buttons are removed."
          : "Tuya keys keep working through the cloud.",
      action: remote.source === "learned" ? "Delete" : "Forget",
      danger: true,
    })
    if (!ok) return
    try {
      await api.irDelete(remote.id)
      onReload()
    } catch (exc) {
      feedback.fail(errorText(exc))
    }
  }
  return (
    <div className="flex flex-col gap-3 rounded-lg border border-line p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="font-medium">{remote.name}</div>
          <div className="text-xs text-muted">
            {remote.kind === "ac" ? "Air conditioner" : "Remote"} on {remote.blaster_name || "unknown blaster"}
          </div>
        </div>
        {remote.source === "cloud" ? <Badge>Tuya remote</Badge> : <Badge tone="ok">Learned · local</Badge>}
      </div>
      {remote.kind === "ac" ? <AcControls remote={remote} onChanged={onChanged} /> : <KeyGrid remote={remote} onChanged={onChanged} />}
      {remote.learned.length > 0 && (
        <div className="flex flex-wrap gap-1.5 text-xs">
          <span className="text-muted">Learned:</span>
          {remote.learned.map((item) => (
            <span key={item.name} className="inline-flex items-center gap-1 rounded-full bg-sunk px-2 py-0.5">
              {item.name}
              <button className="text-muted hover:text-bad" aria-label={`Forget ${item.name}`} onClick={() => forgetButton(item.name)}>
                <X className="size-3" />
              </button>
            </span>
          ))}
        </div>
      )}
      <div className="mt-auto flex flex-wrap items-center gap-2 border-t border-line pt-3">
        <AreaSelect areas={areas} value={remote.area_id ?? null} deviceId={remote.ha_device_id ?? null} onSaved={onReload} />
        <Button icon={<Radio className="size-4" />} onClick={() => onLearn(remote)}>
          Learn button
        </Button>
        {(remote.source === "learned" || remote.learned.length > 0) && (
          <Button variant="ghost" aria-label={`Remove ${remote.name}`} onClick={remove} icon={<Trash2 className="size-4" />} />
        )}
      </div>
    </div>
  )
}

export function RemotesPanel({ onlyId }: { onlyId?: string }) {
  const view = usePoll(api.ir, 20000)
  const areas = usePoll(api.areas)
  const [learning, setLearning] = useState<IrRemote | null>(null)
  const [creating, setCreating] = useState(false)
  const data = view.data
  const replace = (next: IrRemote) =>
    view.setData((current: IrView | null) =>
      current
        ? {
            ...current,
            remotes: current.remotes.map((item) =>
              item.id === next.id ? { ...next, ha_device_id: item.ha_device_id, area_id: item.area_id, area: item.area } : item,
            ),
          }
        : current,
    )
  const remotes = (data?.remotes ?? []).filter((remote) => !onlyId || remote.id === onlyId)
  const reload = () => {
    void view.reload()
    void areas.reload()
  }
  const body = (
    <>
      {view.loading && !data ? (
        <p className="px-4 py-4 text-sm text-muted">Reading remotes…</p>
      ) : remotes.length === 0 ? (
        <p className="px-4 py-4 text-sm text-muted">
          No IR remotes yet. Add one in Smart Life on a control panel or Smart IR, or add a remote here and learn its buttons.
        </p>
      ) : (
        <div className={cx("grid gap-3 p-4", !onlyId && "md:grid-cols-2 xl:grid-cols-3")}>
          {remotes.map((remote) => (
            <RemoteCard
              key={remote.id}
              remote={remote}
              areas={areas.data ?? []}
              onChanged={replace}
              onLearn={setLearning}
              onReload={reload}
            />
          ))}
        </div>
      )}
      <LearnDialog remote={learning} onClose={() => setLearning(null)} onLearned={(next) => { replace(next); void view.reload() }} />
      <NewRemoteDialog
        open={creating}
        blasters={data?.blasters ?? []}
        onClose={() => setCreating(false)}
        onCreated={(remote) => {
          setCreating(false)
          void view.reload()
          setLearning(remote)
        }}
      />
    </>
  )
  if (onlyId) return body
  return (
    <Panel
      title={
        <span className="flex items-center gap-2">
          Remotes
          <span className="font-sans text-sm font-normal tracking-normal text-muted normal-case">
            TV, AC and other IR devices on your panels and Smart IR
          </span>
        </span>
      }
      aside={
        <Button icon={<Plus className="size-4" />} disabled={!data?.blasters.length} onClick={() => setCreating(true)}>
          New remote
        </Button>
      }
    >
      {view.error && <p className="border-b border-line px-4 py-2.5 text-sm text-bad">{view.error}</p>}
      {body}
    </Panel>
  )
}
