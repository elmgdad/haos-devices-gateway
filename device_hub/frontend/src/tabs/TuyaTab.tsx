import {
  ExternalLink,
  HardDriveDownload,
  Plus,
  Radar,
  RefreshCw,
  RotateCw,
  SlidersHorizontal,
  Trash2,
  Unplug,
  Wrench,
} from "lucide-react"
import { useEffect, useMemo, useState } from "react"
import { api } from "../api"
import { copyText, cx, errorText, openInHa, sourceColor, timeAgo, usePoll } from "../lib"
import type {
  LocalLink,
  LocalOverview,
  LocalSaveResult,
  TuyaConfig,
  TuyaControl,
  TuyaDevice,
  TuyaFunction,
  TuyaLocalEntry,
} from "../types"
import {
  AreaSelect,
  Badge,
  Button,
  Empty,
  Field,
  Input,
  KeyCell,
  Led,
  Modal,
  Notice,
  Panel,
  SearchInput,
  Select,
  Td,
  Th,
  useFeedback,
} from "../ui"
import { AddDialog, BulkAddDialog, targetFrom, type AddTarget } from "./AddDialog"

type Filter = "all" | "local" | "missing" | "attention"

const FILTERS: { id: Filter; label: string }[] = [
  { id: "all", label: "All" },
  { id: "local", label: "In Tuya Local" },
  { id: "missing", label: "Not added" },
  { id: "attention", label: "Needs attention" },
]

function needsAttention(device: TuyaDevice) {
  const link = device.tuya_local
  return device.drift.length > 0 || (link !== null && (link.broken || link.state !== "loaded"))
}

function LocalStatus({ device }: { device: TuyaDevice }) {
  const link = device.tuya_local
  if (!link) return <Badge>Not added</Badge>
  if (link.broken) return <Badge tone="bad">Entry broken</Badge>
  if (device.drift.includes("key")) return <Badge tone="bad">Local key changed</Badge>
  if (device.drift.includes("ip")) return <Badge tone="bad">IP changed to {device.lan_ip}</Badge>
  if (link.state !== "loaded") return <Badge tone="bad">Not loaded</Badge>
  return <Badge tone="ok">In Tuya Local</Badge>
}

function LocalBadge({ link }: { link: LocalLink | null }) {
  if (!link) return <Badge>Not saved</Badge>
  if (link.state === "online") return <Badge tone="ok">Local · live</Badge>
  if (link.state === "connecting") return <Badge tone="info">Connecting</Badge>
  return <Badge tone="bad">Local · offline</Badge>
}

export async function findTuyaOnLan() {
  const { networks, default: preferred } = await api.networks()
  const cidr = preferred || networks[0]?.cidr
  if (!cidr) throw new Error("No local network found to search.")
  let status = await api.startScan(cidr, false)
  while (status.running) {
    await new Promise((resolve) => setTimeout(resolve, 1500))
    status = await api.scan()
  }
  if (status.error) throw new Error(status.error)
  return status
}

export default function TuyaTab({ focus, onChanged }: { focus: "all" | "local"; onChanged: () => void }) {
  const feedback = useFeedback()
  const view = usePoll(() => api.tuya(), 60000)
  const areas = usePoll(api.areas)
  const [filter, setFilter] = useState<Filter>(focus === "local" ? "local" : "all")
  const [query, setQuery] = useState("")
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [adding, setAdding] = useState<AddTarget | null>(null)
  const [controlling, setControlling] = useState<TuyaDevice | null>(null)
  const [bulk, setBulk] = useState<TuyaDevice[] | null>(null)
  const [busy, setBusy] = useState<string | null>(null)

  const data = view.data
  const devices = useMemo(() => {
    const q = query.trim().toLowerCase()
    return (data?.devices ?? []).filter((d) => {
      if (filter === "local" && !d.tuya_local) return false
      if (filter === "missing" && d.tuya_local) return false
      if (filter === "attention" && !needsAttention(d)) return false
      if (!q) return true
      return [d.name, d.product_name, d.category_name, d.id, d.lan_ip ?? ""].some((v) => v.toLowerCase().includes(q))
    })
  }, [data, filter, query])

  const refresh = async (cloud = false) => {
    if (cloud) {
      setBusy("refresh")
      try {
        view.setData(await api.tuya(true))
      } catch (exc) {
        feedback.fail(errorText(exc))
      } finally {
        setBusy(null)
      }
    } else {
      await view.reload()
    }
    onChanged()
  }

  const run = async (id: string, action: () => Promise<unknown>, done: string) => {
    setBusy(id)
    try {
      await action()
      feedback.ok(done)
      await refresh()
    } catch (exc) {
      feedback.fail(errorText(exc))
    } finally {
      setBusy(null)
    }
  }

  const locate = () =>
    run(
      "locate",
      async () => {
        const status = await findTuyaOnLan()
        return status
      },
      "Searched the network for Tuya devices.",
    )

  const fix = (device: TuyaDevice) => {
    const link = device.tuya_local!
    const changes: { local_key?: string; host?: string; protocol_version?: string } = {}
    if (device.drift.includes("key")) changes.local_key = device.local_key
    if (device.drift.includes("ip") && device.lan_ip) {
      changes.host = device.lan_ip
      if (device.protocol) changes.protocol_version = device.protocol
    }
    return run(`fix:${device.id}`, () => api.syncEntry(link.entry_id, changes), `Updated ${device.name} in Tuya Local.`)
  }

  const fixAll = async () => {
    const targets = (data?.devices ?? []).filter((d) => d.drift.length && d.tuya_local)
    for (const device of targets) await fix(device)
  }

  const removeEntry = async (entryId: string, title: string) => {
    const ok = await feedback.confirm({
      title: "Remove from Tuya Local?",
      body: `${title} and its entities will be removed from Home Assistant. You can add it again from this list.`,
      action: "Remove",
      danger: true,
    })
    if (ok) await run(`remove:${entryId}`, () => api.removeEntry(entryId), `Removed ${title} from Tuya Local.`)
  }

  const readd = async (device: TuyaDevice) => {
    const link = device.tuya_local!
    const ok = await feedback.confirm({
      title: `Add ${device.name} again?`,
      body: "Tuya Local cannot open this entry's settings. Device Hub removes the broken entry, then opens the add dialog with the current IP and local key.",
      action: "Remove and add again",
      danger: true,
    })
    if (!ok) return
    setBusy(`remove:${link.entry_id}`)
    try {
      await api.removeEntry(link.entry_id)
      setAdding(targetFrom(device))
    } catch (exc) {
      feedback.fail(errorText(exc))
    } finally {
      setBusy(null)
    }
  }

  if (view.loading && !data) return <Panel><Empty title="Loading Tuya devices" /></Panel>
  if (view.error && !data) {
    return <Notice tone="bad">Could not load Tuya devices: {view.error}</Notice>
  }
  if (!data) return null
  if (!data.config.connected) return <TuyaLogin config={data.config} onDone={() => refresh()} />

  const selectable = devices.filter((d) => !d.tuya_local)
  const chosen = (data.devices ?? []).filter((d) => selected.has(d.id) && !d.tuya_local)
  const driftCount = data.devices.filter((d) => d.drift.length && d.tuya_local).length
  const toggle = (id: string) =>
    setSelected((s) => {
      const next = new Set(s)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  const unscanned = data.devices.filter((d) => !d.lan_ip && !d.child).length

  return (
    <div className="space-y-4">
      {!data.tuya_local.installed && (
        <Notice
          tone="bad"
          action={<Button onClick={() => openInHa(data.tuya_local.hacs_url)}>Open in HACS</Button>}
        >
          Tuya Local is not installed. Download it from HACS and restart Home Assistant, then come back to add devices.
        </Notice>
      )}
      {unscanned > 0 && data.tuya_local.installed && (
        <Notice
          action={
            <Button icon={<Radar className="size-4" />} busy={busy === "locate"} onClick={locate}>
              Find on network
            </Button>
          }
        >
          {unscanned} device{unscanned === 1 ? " has" : "s have"} no LAN address yet. Find on network listens for Tuya
          broadcasts for about 12 seconds.
        </Notice>
      )}

      <LocalPanel devices={data.devices} onChanged={() => refresh()} />

      <Panel
        title={
          <span className="flex items-center gap-2">
            <span style={{ color: sourceColor("tuya") }}>Tuya cloud</span>
            <span className="font-sans text-sm font-normal tracking-normal text-muted normal-case">
              {data.config.access_id} · {data.config.region} · updated {timeAgo(data.config.fetched_at)}
            </span>
          </span>
        }
        aside={
          <>
            {driftCount > 0 && (
              <Button icon={<Wrench className="size-4" />} onClick={fixAll} busy={busy?.startsWith("fix")}>
                Fix {driftCount} in Tuya Local
              </Button>
            )}
            <Button icon={<Radar className="size-4" />} busy={busy === "locate"} onClick={locate}>
              Find on network
            </Button>
            <Button icon={<RefreshCw className="size-4" />} busy={busy === "refresh"} onClick={() => refresh(true)}>
              Refresh from cloud
            </Button>
            <Button
              variant="primary"
              icon={<Plus className="size-4" />}
              disabled={!chosen.length || !data.tuya_local.installed}
              onClick={() => setBulk(chosen)}
            >
              Add {chosen.length || ""} to Tuya Local
            </Button>
          </>
        }
      >
        <div className="flex flex-wrap items-center gap-3 border-b border-line px-4 py-3">
          <div className="flex rounded-md border border-line p-0.5">
            {FILTERS.map((f) => (
              <button
                key={f.id}
                onClick={() => setFilter(f.id)}
                className={cx(
                  "rounded px-3 py-1 text-sm",
                  filter === f.id ? "bg-ink text-card" : "text-muted hover:text-ink",
                )}
              >
                {f.label}
              </button>
            ))}
          </div>
          <SearchInput value={query} onChange={setQuery} placeholder="Search name, product, IP" />
          {data.config.error && <span className="text-sm text-bad">{data.config.error}</span>}
        </div>
        {devices.length === 0 ? (
          <Empty title="No devices here">
            {filter === "all"
              ? "Link your Smart Life account to the cloud project on iot.tuya.com (Devices, Link App Account), then refresh."
              : "Nothing matches this filter."}
          </Empty>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[1100px] border-collapse text-sm">
              <thead>
                <tr>
                  <Th className="w-10">
                    <input
                      type="checkbox"
                      aria-label="Select all not added"
                      checked={selectable.length > 0 && selectable.every((d) => selected.has(d.id))}
                      onChange={(e) => setSelected(e.target.checked ? new Set(selectable.map((d) => d.id)) : new Set())}
                    />
                  </Th>
                  <Th>Device</Th>
                  <Th>LAN address</Th>
                  <Th>Local key</Th>
                  <Th>Cloud</Th>
                  <Th>Tuya Local</Th>
                  <Th>Home Assistant</Th>
                  <Th className="text-right">Actions</Th>
                </tr>
              </thead>
              <tbody>
                {devices.map((device) => (
                  <tr key={device.id} className="border-t border-line hover:bg-sunk/60">
                    <Td>
                      {!device.tuya_local && (
                        <input
                          type="checkbox"
                          aria-label={`Select ${device.name}`}
                          checked={selected.has(device.id)}
                          onChange={() => toggle(device.id)}
                        />
                      )}
                    </Td>
                    <Td>
                      <div className="font-medium">{device.name}</div>
                      <div className="text-xs text-muted">
                        {device.category_name || device.category} · {device.product_name}
                      </div>
                      <div className="font-mono text-[11px] text-muted">{device.id}</div>
                    </Td>
                    <Td mono>
                      {device.lan_ip ? (
                        <>
                          {device.lan_ip}
                          {device.protocol && <span className="ml-1.5 text-xs text-muted">v{device.protocol}</span>}
                        </>
                      ) : device.child ? (
                        <span className="font-sans text-xs text-muted">
                          via {device.gateway_name ?? "a gateway"}
                          <span className="block font-mono">node {device.node_id}</span>
                        </span>
                      ) : (
                        <span className="font-sans text-xs text-muted">not found yet</span>
                      )}
                    </Td>
                    <Td>
                      <KeyCell value={device.local_key} />
                    </Td>
                    <Td>
                      <span className="inline-flex items-center gap-1.5 text-xs">
                        <Led state={device.online ? "ok" : "off"} />
                        {device.online ? "Online" : "Offline"}
                      </span>
                    </Td>
                    <Td>
                      <LocalStatus device={device} />
                      {device.tuya_local && device.tuya_local.title !== device.name && (
                        <div className="mt-0.5 text-xs text-muted">as {device.tuya_local.title}</div>
                      )}
                    </Td>
                    <Td>
                      <LocalBadge link={device.local} />
                      <div className="mt-1.5">
                        <AreaSelect
                          areas={areas.data ?? []}
                          value={device.local?.area_id ?? device.tuya_local?.area_id ?? null}
                          deviceId={device.local?.ha_device_id ?? device.tuya_local?.ha_device_id ?? null}
                          onSaved={() => {
                            void view.reload()
                            void areas.reload()
                          }}
                        />
                      </div>
                    </Td>
                    <Td className="text-right">
                      <div className="inline-flex gap-1.5">
                        <Button icon={<SlidersHorizontal className="size-4" />} onClick={() => setControlling(device)}>
                          Control
                        </Button>
                        {!device.tuya_local && (
                          <Button
                            icon={<Plus className="size-4" />}
                            disabled={!data.tuya_local.installed}
                            onClick={() => setAdding(targetFrom(device))}
                          >
                            Add
                          </Button>
                        )}
                        {device.tuya_local && device.drift.length > 0 && (
                          <Button icon={<Wrench className="size-4" />} busy={busy === `fix:${device.id}`} onClick={() => fix(device)}>
                            Fix
                          </Button>
                        )}
                        {device.tuya_local?.broken && (
                          <Button
                            icon={<Plus className="size-4" />}
                            busy={busy === `remove:${device.tuya_local.entry_id}`}
                            onClick={() => readd(device)}
                          >
                            Add again
                          </Button>
                        )}
                        {device.tuya_local && !device.tuya_local.broken && device.tuya_local.state !== "loaded" && (
                          <Button
                            icon={<RotateCw className="size-4" />}
                            busy={busy === `reload:${device.tuya_local.entry_id}`}
                            onClick={() =>
                              run(`reload:${device.tuya_local!.entry_id}`, () => api.reloadEntry(device.tuya_local!.entry_id), `Reloaded ${device.name}.`)
                            }
                          >
                            Reload
                          </Button>
                        )}
                        {device.tuya_local && (
                          <Button
                            variant="ghost"
                            aria-label={`Remove ${device.name} from Tuya Local`}
                            title="Remove from Tuya Local"
                            busy={busy === `remove:${device.tuya_local.entry_id}`}
                            onClick={() => removeEntry(device.tuya_local!.entry_id, device.tuya_local!.title)}
                            icon={<Trash2 className="size-4" />}
                          />
                        )}
                      </div>
                    </Td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>

      {data.unmatched_entries.length > 0 && (
        <UnmatchedEntries
          entries={data.unmatched_entries}
          busy={busy}
          onReload={(e) => run(`reload:${e.entry_id}`, () => api.reloadEntry(e.entry_id), `Reloaded ${e.title}.`)}
          onRemove={(e) => removeEntry(e.entry_id, e.title)}
        />
      )}

      <ControlDialog device={controlling} onClose={() => setControlling(null)} onSaved={() => refresh()} />
      <AddDialog target={adding} gateways={data.gateways} onClose={() => setAdding(null)} onAdded={() => refresh()} />
      <BulkAddDialog
        devices={bulk}
        onClose={() => {
          setBulk(null)
          setSelected(new Set())
        }}
        onDone={() => refresh()}
      />
    </div>
  )
}

function fnSpec(fn: TuyaFunction): { range?: string[]; min?: number; max?: number; step?: number; unit?: string } {
  if (!fn.values) return {}
  try {
    const parsed = JSON.parse(fn.values) as { range?: string[]; min?: number; max?: number; step?: number; unit?: string }
    return parsed && typeof parsed === "object" ? parsed : {}
  } catch {
    return {}
  }
}

function dpLabel(code: string) {
  return code.replace(/_/g, " ").replace(/\b\w/g, (letter) => letter.toUpperCase())
}

function showDp(value: boolean | number | string | null) {
  if (value === true) return "On"
  if (value === false) return "Off"
  if (value == null || value === "") return "—"
  if (typeof value === "string" && /^[a-z0-9_]+$/.test(value)) return dpLabel(value)
  return String(value)
}

function ControlDialog({ device, onClose, onSaved }: { device: TuyaDevice | null; onClose: () => void; onSaved: () => void }) {
  const feedback = useFeedback()
  const [detail, setDetail] = useState<TuyaControl | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [drafts, setDrafts] = useState<Record<string, string>>({})
  const [busy, setBusy] = useState<string | null>(null)
  const [reloads, setReloads] = useState(0)
  const [savedHere, setSavedHere] = useState(false)

  useEffect(() => {
    if (!device) {
      setDetail(null)
      setError(null)
      setSavedHere(false)
      return
    }
    let cancelled = false
    setLoading(true)
    setError(null)
    api
      .tuyaControl(device.id)
      .then((result) => {
        if (cancelled) return
        setDetail(result)
        const next: Record<string, string> = {}
        for (const fn of result.functions) {
          const current = result.status.find((item) => item.code === fn.code)
          if (current?.value != null) next[fn.code] = String(current.value)
        }
        setDrafts(next)
      })
      .catch((exc) => {
        if (!cancelled) setError(errorText(exc))
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [device, reloads])

  const saveLocal = async () => {
    if (!device) return
    setBusy("save")
    setError(null)
    try {
      const result = await api.saveLocal([device.id])
      const outcome = result.results?.find((item) => item.id === device.id)
      if (!outcome?.ok) throw new Error(outcome?.reason || "Device Hub could not save this device.")
      setSavedHere(true)
      feedback.ok(`Saved ${device.name}. ${outcome.entities} entities are in Home Assistant.`)
      setReloads((n) => n + 1)
      onSaved()
    } catch (exc) {
      setError(errorText(exc))
    } finally {
      setBusy(null)
    }
  }

  const saved = Boolean(device?.local) || savedHere || detail?.via === "local"
  const canSave = Boolean(device?.lan_ip) && !device?.child

  const send = async (code: string, value: boolean | number | string) => {
    if (!device) return
    setBusy(code)
    setError(null)
    try {
      const result = await api.tuyaCommand(device.id, code, value)
      setDetail(result)
      setDrafts((current) => {
        const next = { ...current }
        for (const item of result.status) {
          if (item.value != null) next[item.code] = String(item.value)
        }
        return next
      })
      feedback.ok(`${dpLabel(code)} sent.`)
    } catch (exc) {
      setError(errorText(exc))
    } finally {
      setBusy(null)
    }
  }

  const statusOf = (code: string) => detail?.status.find((item) => item.code === code)?.value

  return (
    <Modal
      open={device !== null}
      title={device ? `Control ${device.name}` : "Control"}
      onClose={onClose}
      wide
      footer={
        canSave &&
        (saved ? (
          <span className="mr-auto self-center text-sm text-muted">Saved for Home Assistant. It runs on your network.</span>
        ) : (
          <Button variant="primary" icon={<HardDriveDownload className="size-4" />} busy={busy === "save"} onClick={saveLocal}>
            Save for Home Assistant
          </Button>
        ))
      }
    >
      {loading && <p className="text-sm text-muted">Reading the device…</p>}
      {error && <p className="mb-3 rounded-md bg-bad/10 px-3 py-2 text-sm text-bad">{error}</p>}
      {detail && (
        <div className="space-y-4">
          <p className="flex items-center gap-2 text-sm text-muted">
            <Led state={detail.online ? "ok" : "off"} />
            {detail.online ? "Online" : "Offline"}.{" "}
            {detail.via === "local"
              ? `Live over your network at ${device?.local?.ip ?? device?.lan_ip ?? "its LAN address"} with the local key.`
              : "Commands go through the Tuya cloud API."}
          </p>
          {detail.blocked && <p className="rounded-md bg-sunk px-3 py-2 text-sm text-ink">{detail.blocked}</p>}
          {detail.status.length > 0 && (
            <div className="flex flex-wrap gap-2">
              {detail.status.map((item) => (
                <span key={item.code} className="rounded-md border border-line bg-sunk px-2 py-1 text-xs">
                  <span className="text-muted">{dpLabel(item.code)}</span> <b>{showDp(item.value)}</b>
                </span>
              ))}
            </div>
          )}
          {detail.blocked ? null : detail.functions.length === 0 ? (
            <p className="text-sm text-muted">Tuya did not publish any standard commands for this device.</p>
          ) : (
            <div className="grid gap-3 sm:grid-cols-2">
              {detail.functions.map((fn) => {
                const spec = fnSpec(fn)
                const current = statusOf(fn.code)
                return (
                  <div key={fn.code} className="rounded-md border border-line px-3 py-3">
                    <div className="mb-2 text-sm font-medium">{fn.desc || dpLabel(fn.code)}</div>
                    {fn.type === "Boolean" ? (
                      <div className="flex gap-2">
                        <Button variant={current === true ? "primary" : "ghost"} busy={busy === fn.code} onClick={() => send(fn.code, true)}>
                          On
                        </Button>
                        <Button variant={current === false ? "primary" : "ghost"} busy={busy === fn.code} onClick={() => send(fn.code, false)}>
                          Off
                        </Button>
                      </div>
                    ) : Array.isArray(spec.range) ? (
                      <div className="flex items-center gap-2">
                        <Select value={drafts[fn.code] ?? ""} onChange={(e) => setDrafts((d) => ({ ...d, [fn.code]: e.target.value }))}>
                          {spec.range.map((item) => (
                            <option key={item} value={item}>
                              {dpLabel(item)}
                            </option>
                          ))}
                        </Select>
                        <Button busy={busy === fn.code} onClick={() => send(fn.code, drafts[fn.code] ?? spec.range![0])}>
                          Apply
                        </Button>
                      </div>
                    ) : (
                      <div className="flex items-center gap-2">
                        <Input
                          type={fn.type === "Integer" ? "number" : "text"}
                          min={spec.min}
                          max={spec.max}
                          step={spec.step || (fn.type === "Integer" ? 1 : undefined)}
                          value={drafts[fn.code] ?? ""}
                          onChange={(e) => setDrafts((d) => ({ ...d, [fn.code]: e.target.value }))}
                        />
                        <Button busy={busy === fn.code} onClick={() => send(fn.code, drafts[fn.code] ?? "")}>
                          Apply
                        </Button>
                      </div>
                    )}
                  </div>
                )
              })}
            </div>
          )}
        </div>
      )}
    </Modal>
  )
}

function MqttBadge({ mqtt }: { mqtt: LocalOverview["mqtt"] | undefined }) {
  if (!mqtt) return null
  if (mqtt.state === "connected") return <Badge tone="ok">MQTT connected</Badge>
  if (mqtt.state === "connecting") return <Badge tone="info">MQTT connecting</Badge>
  return <Badge tone="bad">MQTT off</Badge>
}

function LinkState({ link }: { link: LocalLink }) {
  if (link.state === "online") {
    return (
      <span className="inline-flex items-center gap-1.5 text-xs">
        <Led state="ok" />
        Live · {timeAgo(link.last_seen)}
      </span>
    )
  }
  if (link.state === "connecting") {
    return (
      <span className="inline-flex items-center gap-1.5 text-xs">
        <Led state="busy" />
        Connecting
      </span>
    )
  }
  return (
    <span className="text-xs">
      <span className="inline-flex items-center gap-1.5">
        <Led state="warn" />
        Offline, retrying
      </span>
      {link.error && <span className="mt-0.5 block text-muted">{link.error}</span>}
    </span>
  )
}

function AutomationList({ link }: { link: LocalLink }) {
  const controls = (link.controls ?? []).filter((control) => control.primary && control.entity_id)
  const extra = (link.controls ?? []).filter((control) => !control.primary).length
  if (!link.ha_device_id) {
    return <span className="text-xs text-muted">Shows up in Home Assistant once the link is online.</span>
  }
  if (controls.length === 0) {
    return <span className="text-xs text-muted">Waiting for Home Assistant to add the entities.</span>
  }
  return (
    <div className="max-w-xs">
      <ul className="space-y-0.5">
        {controls.slice(0, 6).map((control) => (
          <li key={control.entity_id} className="flex items-center gap-1">
            <button
              className="truncate font-mono text-[11px] text-muted hover:text-ink"
              title="Copy the entity id for an automation"
              onClick={() => void copyText(control.entity_id || "")}
            >
              {control.entity_id}
            </button>
          </li>
        ))}
      </ul>
      {extra > 0 && <p className="mt-1 text-[11px] text-muted">{extra} more settings on the device page</p>}
    </div>
  )
}

function LocalPanel({ devices, onChanged }: { devices: TuyaDevice[]; onChanged: () => void }) {
  const feedback = useFeedback()
  const view = usePoll(() => api.local(), 5000)
  const areas = usePoll(api.areas)
  const [busy, setBusy] = useState<string | null>(null)
  const [skipped, setSkipped] = useState<LocalSaveResult[]>([])

  const data = view.data
  const saved = data?.devices ?? []
  const savedIds = new Set(saved.map((link) => link.id))
  const failed = new Set((data?.last_results ?? []).filter((result) => !result.ok).map((result) => result.id))
  const ready = devices.filter(
    (d) => d.lan_ip && !d.child && !savedIds.has(d.id) && !failed.has(d.id) && d.tuya_local?.state !== "loaded",
  )
  const haDevice = new Map(devices.map((d) => [d.id, d.local?.ha_device_id ?? null]))

  const saveAll = async () => {
    setBusy("save")
    try {
      const result = await api.saveLocal()
      view.setData(result)
      const items = result.results ?? []
      const added = items.filter((item) => item.ok && !item.reason)
      setSkipped(items.filter((item) => !item.ok))
      feedback.ok(
        added.length
          ? `Saved ${added.length} device${added.length === 1 ? "" : "s"} with ${added.reduce((n, item) => n + (item.entities ?? 0), 0)} entities for Home Assistant.`
          : "Nothing new to save.",
      )
      onChanged()
    } catch (exc) {
      feedback.fail(errorText(exc))
    } finally {
      setBusy(null)
    }
  }

  const forget = async (link: LocalLink) => {
    const ok = await feedback.confirm({
      title: `Forget ${link.name}?`,
      body: "Device Hub closes its local connection and removes its entities from Home Assistant. The device itself is not changed.",
      action: "Forget",
      danger: true,
    })
    if (!ok) return
    setBusy(`forget:${link.id}`)
    try {
      view.setData(await api.forgetLocal(link.id))
      feedback.ok(`Forgot ${link.name}.`)
      onChanged()
    } catch (exc) {
      feedback.fail(errorText(exc))
    } finally {
      setBusy(null)
    }
  }

  return (
    <Panel
      title={
        <span className="flex items-center gap-2">
          Local control
          <span className="font-sans text-sm font-normal tracking-normal text-muted normal-case">
            Home Assistant entities on your network, no Tuya cloud
          </span>
        </span>
      }
      aside={
        <>
          <MqttBadge mqtt={data?.mqtt} />
          <Button
            variant="primary"
            icon={<HardDriveDownload className="size-4" />}
            busy={busy === "save"}
            disabled={!ready.length}
            onClick={saveAll}
          >
            Save {ready.length || ""} on network
          </Button>
        </>
      }
    >
      {data && data.mqtt.state !== "connected" && data.mqtt.error && (
        <p className="border-b border-line px-4 py-2.5 text-sm text-bad">{data.mqtt.error}</p>
      )}
      {skipped.length > 0 && (
        <div className="border-b border-line px-4 py-3 text-sm">
          <div className="mb-1 flex items-center justify-between gap-3">
            <span className="font-medium">Not saved</span>
            <button className="text-xs text-muted hover:text-ink" onClick={() => setSkipped([])}>
              Dismiss
            </button>
          </div>
          <ul className="space-y-0.5 text-muted">
            {skipped.map((item) => (
              <li key={item.id}>
                <span className="text-ink">{item.name}</span>: {item.reason}
              </li>
            ))}
          </ul>
        </div>
      )}
      {saved.length === 0 ? (
        <p className="px-4 py-4 text-sm text-muted">
          Saving a device keeps its local key, address and data points in Device Hub. Its switches and sensors then show up
          in Home Assistant automations and work over your network, without the Tuya cloud.
        </p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[760px] border-collapse text-sm">
            <thead>
              <tr>
                <Th>Device</Th>
                <Th>Address</Th>
                <Th>Link</Th>
                <Th>Area</Th>
                <Th>Automations</Th>
                <Th className="text-right">Actions</Th>
              </tr>
            </thead>
            <tbody>
              {saved.map((link) => {
                const haId = link.ha_device_id ?? haDevice.get(link.id)
                return (
                  <tr key={link.id} className="border-t border-line">
                    <Td>
                      <div className="font-medium">{link.name}</div>
                      <div className="font-mono text-[11px] text-muted">{link.id}</div>
                    </Td>
                    <Td mono>
                      {link.ip}
                      <span className="ml-1.5 text-xs text-muted">v{link.version}</span>
                    </Td>
                    <Td>
                      <LinkState link={link} />
                    </Td>
                    <Td>
                      <AreaSelect
                        areas={areas.data ?? []}
                        value={link.area_id ?? null}
                        deviceId={haId ?? null}
                        onSaved={() => {
                          void view.reload()
                          void areas.reload()
                          onChanged()
                        }}
                      />
                    </Td>
                    <Td>
                      <AutomationList link={link} />
                    </Td>
                    <Td className="text-right">
                      <div className="inline-flex gap-1.5">
                        {haId && (
                          <Button icon={<ExternalLink className="size-4" />} onClick={() => openInHa(`/config/devices/device/${haId}`)}>
                            Open in HA
                          </Button>
                        )}
                        <Button
                          variant="ghost"
                          aria-label={`Forget ${link.name}`}
                          title="Forget local control"
                          busy={busy === `forget:${link.id}`}
                          onClick={() => forget(link)}
                          icon={<Unplug className="size-4" />}
                        />
                      </div>
                    </Td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  )
}

function UnmatchedEntries({
  entries,
  busy,
  onReload,
  onRemove,
}: {
  entries: TuyaLocalEntry[]
  busy: string | null
  onReload: (entry: TuyaLocalEntry) => void
  onRemove: (entry: TuyaLocalEntry) => void
}) {
  return (
    <Panel title={<span style={{ color: sourceColor("tuya_local") }}>Tuya Local entries without a cloud match</span>}>
      <p className="border-b border-line px-4 py-2.5 text-sm text-muted">
        These entries are in Home Assistant but don't match any device in the cloud project or on the network scan.
      </p>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[720px] text-sm">
          <thead>
            <tr>
              <Th>Entry</Th>
              <Th>Host</Th>
              <Th>Protocol</Th>
              <Th>State</Th>
              <Th className="text-right">Actions</Th>
            </tr>
          </thead>
          <tbody>
            {entries.map((entry) => (
              <tr key={entry.entry_id} className="border-t border-line">
                <Td>
                  <div className="font-medium">{entry.title}</div>
                  {entry.device_id && <div className="font-mono text-[11px] text-muted">{entry.device_id}</div>}
                </Td>
                <Td mono>{entry.host || "–"}</Td>
                <Td mono>{entry.protocol_version ?? "–"}</Td>
                <Td>
                  {entry.state === "loaded" ? <Badge tone="ok">Loaded</Badge> : <Badge tone="bad">{entry.state.replace("_", " ")}</Badge>}
                  {entry.broken && <div className="mt-0.5 text-xs text-muted">Settings unreadable; remove and add again.</div>}
                </Td>
                <Td className="text-right">
                  <div className="inline-flex gap-1.5">
                    <Button icon={<RotateCw className="size-4" />} busy={busy === `reload:${entry.entry_id}`} onClick={() => onReload(entry)}>
                      Reload
                    </Button>
                    <Button variant="danger" icon={<Trash2 className="size-4" />} busy={busy === `remove:${entry.entry_id}`} onClick={() => onRemove(entry)}>
                      Remove
                    </Button>
                  </div>
                </Td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Panel>
  )
}

function TuyaLogin({ config, onDone }: { config: TuyaConfig; onDone: () => void }) {
  const feedback = useFeedback()
  const [accessId, setAccessId] = useState(config.access_id)
  const [secret, setSecret] = useState("")
  const [region, setRegion] = useState(config.region || "eu")
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(config.error)

  const submit = async () => {
    setBusy(true)
    setError(null)
    try {
      const result = await api.tuyaLogin(accessId, secret, region)
      feedback.ok(`Signed in. ${result.device_count} devices found.`)
      onDone()
    } catch (exc) {
      setError(errorText(exc))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="grid gap-4 lg:grid-cols-[minmax(0,420px)_1fr]">
      <Panel title={<span style={{ color: sourceColor("tuya") }}>Sign in to your Tuya cloud project</span>}>
        <form
          className="space-y-4 p-4"
          onSubmit={(e) => {
            e.preventDefault()
            void submit()
          }}
        >
          {config.source === "app" && (
            <p className="text-sm text-muted">
              These values come from the Device Hub app configuration. Change them there, then Home Assistant restarts this
              app and signs in again.
            </p>
          )}
          <Field label="Access ID / Client ID">
            <Input value={accessId} onChange={(e) => setAccessId(e.target.value)} className="font-mono" autoComplete="off" required />
          </Field>
          <Field label="Access Secret / Client Secret" hint={config.has_secret ? "Leave empty to keep the saved secret." : undefined}>
            <Input
              type="password"
              value={secret}
              onChange={(e) => setSecret(e.target.value)}
              className="font-mono"
              autoComplete="off"
              placeholder={config.has_secret ? "Saved" : ""}
            />
          </Field>
          <Field label="Data center" hint="Must match the data center of your cloud project.">
            <Select value={region} onChange={(e) => setRegion(e.target.value)}>
              {config.regions.map((r) => (
                <option key={r.id} value={r.id}>
                  {r.name} ({r.id})
                </option>
              ))}
            </Select>
          </Field>
          {error && <p className="rounded-md bg-bad/10 px-3 py-2 text-sm text-bad">{error}</p>}
          <Button variant="primary" type="submit" busy={busy}>
            Connect
          </Button>
        </form>
      </Panel>
      <Panel title="Where to find these">
        <ol className="list-decimal space-y-2 py-4 pr-4 pl-9 text-sm text-muted">
          <li>Open iot.tuya.com, then Cloud, Development, and your project.</li>
          <li>Copy the Access ID and Access Secret from the Overview tab.</li>
          <li>
            Under Devices, Link App Account, scan the QR code with the Smart Life app so your devices and their local keys
            appear here.
          </li>
          <li>
            Paste them into Settings, Apps, Device Hub, Configuration. Saving restarts this app and signs in. You can also
            connect from the form on the left.
          </li>
        </ol>
      </Panel>
    </div>
  )
}
