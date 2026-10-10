import { CircleAlert, CircleCheck, LoaderCircle } from "lucide-react"
import { useEffect, useRef, useState } from "react"
import { api } from "../api"
import { cx, errorText } from "../lib"
import type { AddStart, Gateway, TuyaDevice } from "../types"
import { Button, Field, Input, Modal, Select, useFeedback } from "../ui"

export const PROTOCOLS = ["auto", "3.1", "3.2", "3.3", "3.4", "3.5", "3.22", "3.42", "3.52"]

export interface AddTarget {
  id: string
  name: string
  lan_ip: string | null
  protocol: string | null
  local_key: string
  child: boolean
  gateway_id: string
}

export function targetFrom(device: TuyaDevice): AddTarget {
  return {
    id: device.id,
    name: device.name,
    lan_ip: device.lan_ip,
    protocol: device.protocol,
    local_key: device.local_key,
    child: device.child,
    gateway_id: device.gateway_id,
  }
}

export function AddDialog({
  target,
  gateways = [],
  onClose,
  onAdded,
}: {
  target: AddTarget | null
  gateways?: Gateway[]
  onClose: () => void
  onAdded: () => void
}) {
  const feedback = useFeedback()
  const [gatewayId, setGatewayId] = useState("")
  const [host, setHost] = useState("")
  const [key, setKey] = useState("")
  const [protocol, setProtocol] = useState("auto")
  const [name, setName] = useState("")
  const [flow, setFlow] = useState<AddStart | null>(null)
  const [type, setType] = useState("")
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const finished = useRef(false)

  useEffect(() => {
    if (!target) return
    const gateway = gateways.find((g) => g.id === target.gateway_id)
    setGatewayId(target.gateway_id || "")
    setHost(target.child ? gateway?.host ?? "" : target.lan_ip ?? "")
    setKey(target.local_key)
    setProtocol(target.protocol && PROTOCOLS.includes(target.protocol) ? target.protocol : "auto")
    setName(target.name)
    setFlow(null)
    setType("")
    setError(null)
    finished.current = false
  }, [target, gateways])

  const close = () => {
    if (flow && !finished.current) void api.addCancel(flow.flow_id)
    onClose()
  }

  const connect = async () => {
    if (!target) return
    setBusy(true)
    setError(null)
    try {
      const result = await api.addStart({
        device_id: target.id,
        host: host.trim() || undefined,
        local_key: key.trim() || undefined,
        protocol_version: protocol,
        gateway_id: target.child ? gatewayId || undefined : undefined,
        name,
      })
      setFlow(result)
      setType(result.default ?? result.options[0]?.value ?? "")
    } catch (exc) {
      setError(errorText(exc))
    } finally {
      setBusy(false)
    }
  }

  const finish = async () => {
    if (!flow) return
    setBusy(true)
    setError(null)
    try {
      const result = await api.addFinish(flow.flow_id, type, name)
      finished.current = true
      feedback.ok(`Added ${result.title || name} to Tuya Local.`)
      onAdded()
      onClose()
    } catch (exc) {
      finished.current = true
      setError(errorText(exc))
      setFlow(null)
    } finally {
      setBusy(false)
    }
  }

  const pickGateway = (id: string) => {
    setGatewayId(id)
    const gateway = gateways.find((g) => g.id === id)
    if (gateway?.host) setHost(gateway.host)
  }

  return (
    <Modal
      open={target !== null}
      title={flow ? "Choose the device type" : "Add to Tuya Local"}
      onClose={close}
      wide={!!flow}
      footer={
        flow ? (
          <>
            <Button onClick={close}>Cancel</Button>
            <Button variant="primary" busy={busy} disabled={!type} onClick={finish}>
              Add to Tuya Local
            </Button>
          </>
        ) : (
          <>
            <Button onClick={close}>Cancel</Button>
            <Button variant="primary" busy={busy} onClick={connect}>
              Connect and detect type
            </Button>
          </>
        )
      }
    >
      {target && !flow && (
        <div className="space-y-4">
          <p className="text-sm text-muted">
            Device Hub opens Tuya Local's setup, tests the connection with these values, then lists the device files that
            match. Testing every protocol with <b>auto</b> can take a minute.
          </p>
          <div className="rounded-md bg-sunk px-3 py-2 font-mono text-[13px]">
            {target.id}
            {target.child && <span className="ml-2 font-sans text-xs text-muted">connects through a gateway</span>}
          </div>
          {target.child && (
            <Field label="Gateway" hint="The hub this device is paired with. Tuya Local talks to the hub's IP.">
              <Select value={gatewayId} onChange={(e) => pickGateway(e.target.value)}>
                <option value="">Pick a gateway</option>
                {gateways.map((g) => (
                  <option key={g.id} value={g.id}>
                    {g.name} {g.host ? `(${g.host})` : "(no IP yet)"}
                  </option>
                ))}
              </Select>
            </Field>
          )}
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label={target.child ? "Gateway IP address" : "IP address"} hint={!host ? "Run a network scan or type it." : undefined}>
              <Input value={host} onChange={(e) => setHost(e.target.value)} placeholder="192.168.100.40" className="font-mono" />
            </Field>
            <Field label="Protocol version">
              <Select value={protocol} onChange={(e) => setProtocol(e.target.value)}>
                {PROTOCOLS.map((p) => (
                  <option key={p} value={p}>
                    {p === "auto" ? "Auto-detect" : p}
                  </option>
                ))}
              </Select>
            </Field>
          </div>
          <Field label="Local key">
            <Input value={key} onChange={(e) => setKey(e.target.value)} className="font-mono" />
          </Field>
          <Field label="Name in Home Assistant">
            <Input value={name} onChange={(e) => setName(e.target.value)} />
          </Field>
          {error && <p className="rounded-md bg-bad/10 px-3 py-2 text-sm text-bad">{error}</p>}
        </div>
      )}
      {flow && (
        <div className="space-y-4">
          <p className="text-sm text-muted">
            Connected to {flow.host}
            {flow.detected_protocol ? ` using protocol ${flow.detected_protocol}` : ""}. The best match is selected.
          </p>
          <div className="max-h-72 overflow-y-auto rounded-md border border-line">
            {flow.options.map((option, index) => (
              <label
                key={option.value}
                className={cx(
                  "flex cursor-pointer items-center gap-3 border-b border-line px-3 py-2 text-sm last:border-b-0",
                  type === option.value && "bg-sunk",
                )}
              >
                <input type="radio" name="type" checked={type === option.value} onChange={() => setType(option.value)} />
                <span className="flex-1">{option.label}</span>
                {index === 0 && <span className="label text-[11px] text-ok">Best match</span>}
              </label>
            ))}
          </div>
          <Field label="Name in Home Assistant">
            <Input value={name} onChange={(e) => setName(e.target.value)} />
          </Field>
          {error && <p className="rounded-md bg-bad/10 px-3 py-2 text-sm text-bad">{error}</p>}
        </div>
      )}
    </Modal>
  )
}

type BulkState = { status: "waiting" | "working" | "added" | "failed" | "skipped"; message?: string }

export function BulkAddDialog({
  devices,
  onClose,
  onDone,
}: {
  devices: TuyaDevice[] | null
  onClose: () => void
  onDone: () => void
}) {
  const [states, setStates] = useState<Record<string, BulkState>>({})
  const [running, setRunning] = useState(false)
  const stop = useRef(false)

  useEffect(() => {
    if (!devices) return
    stop.current = false
    const initial: Record<string, BulkState> = {}
    devices.forEach((d) => (initial[d.id] = { status: "waiting" }))
    setStates(initial)
    setRunning(true)
    ;(async () => {
      for (const device of devices) {
        if (stop.current) break
        const update = (state: BulkState) => setStates((s) => ({ ...s, [device.id]: state }))
        if (!device.lan_ip && !device.child) {
          update({ status: "skipped", message: "No LAN IP. Run a network scan first." })
          continue
        }
        update({ status: "working" })
        try {
          const flow = await api.addStart({ device_id: device.id, name: device.name, protocol_version: device.protocol ?? "auto" })
          const choice = flow.default ?? flow.options[0]?.value
          if (!choice) throw new Error("Tuya Local has no matching device file.")
          await api.addFinish(flow.flow_id, choice, device.name)
          update({ status: "added", message: flow.options.find((o) => o.value === choice)?.label })
        } catch (exc) {
          update({ status: "failed", message: errorText(exc) })
        }
      }
      setRunning(false)
      onDone()
    })()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [devices])

  return (
    <Modal
      open={devices !== null}
      title="Adding to Tuya Local"
      onClose={() => (running ? (stop.current = true) : onClose())}
      wide
      footer={
        running ? (
          <Button onClick={() => (stop.current = true)}>Stop after this device</Button>
        ) : (
          <Button variant="primary" onClick={onClose}>
            Close
          </Button>
        )
      }
    >
      <p className="mb-3 text-sm text-muted">
        Each device is tested and added with its best-matching device file. A setup Tuya Local already
        has open for that device is closed first. You can change the type later in Tuya Local.
      </p>
      <ul className="divide-y divide-line rounded-md border border-line">
        {(devices ?? []).map((device) => {
          const state = states[device.id] ?? { status: "waiting" }
          return (
            <li key={device.id} className="flex items-start gap-3 px-3 py-2.5 text-sm">
              <span className="mt-0.5">
                {state.status === "working" && <LoaderCircle className="size-4 animate-spin text-src-lan" />}
                {state.status === "added" && <CircleCheck className="size-4 text-ok" />}
                {(state.status === "failed" || state.status === "skipped") && <CircleAlert className="size-4 text-bad" />}
                {state.status === "waiting" && <span className="block size-4 rounded-full border border-line" />}
              </span>
              <span className="min-w-0 flex-1">
                <span className="font-medium">{device.name}</span>
                {state.message && <span className="block text-xs text-muted">{state.message}</span>}
              </span>
              <span className="label text-[11px] text-muted">{state.status}</span>
            </li>
          )
        })}
      </ul>
    </Modal>
  )
}
