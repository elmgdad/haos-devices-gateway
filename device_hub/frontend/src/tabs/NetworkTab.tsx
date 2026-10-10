import { Plus, Radar, Wifi } from "lucide-react"
import { useEffect, useMemo, useState } from "react"
import { api } from "../api"
import { cx, errorText, sourceColor, timeAgo, usePoll } from "../lib"
import type { Gateway, ScanHost, WifiView } from "../types"
import { Badge, Button, Empty, Input, Led, Notice, Panel, SearchInput, Select, Td, Th, Toggle, useFeedback } from "../ui"
import { AddDialog, targetFrom, type AddTarget } from "./AddDialog"

const CUSTOM = "__custom__"

export default function NetworkTab({ onChanged }: { onChanged: () => void }) {
  const feedback = useFeedback()
  const [view, setView] = useState<"hosts" | "wifi">("hosts")
  const networks = usePoll(api.networks)
  const [choice, setChoice] = useState("")
  const [custom, setCustom] = useState("")
  const [polling, setPolling] = useState(false)
  const scan = usePoll(api.scan, polling ? 1200 : 0, [polling])
  const [smartOnly, setSmartOnly] = useState(true)
  const [query, setQuery] = useState("")
  const [adding, setAdding] = useState<AddTarget | null>(null)
  const [gateways, setGateways] = useState<Gateway[]>([])

  useEffect(() => {
    if (!networks.data || choice) return
    setChoice(networks.data.default || scan.data?.cidr || networks.data.networks[0]?.cidr || CUSTOM)
  }, [networks.data, scan.data, choice])

  useEffect(() => {
    if (scan.data?.running && !polling) setPolling(true)
    if (scan.data && !scan.data.running && polling) {
      setPolling(false)
      onChanged()
    }
  }, [scan.data, polling, onChanged])

  const cidr = choice === CUSTOM ? custom.trim() : choice
  const status = scan.data

  const start = async () => {
    try {
      scan.setData(await api.startScan(cidr))
      setPolling(true)
    } catch (exc) {
      feedback.fail(errorText(exc))
    }
  }

  const hosts = useMemo(() => {
    const q = query.trim().toLowerCase()
    return (status?.results ?? []).filter((h) => {
      if (smartOnly && !h.smart) return false
      if (!q) return true
      return [h.ip, h.mac, h.vendor, h.hostname, h.kind, h.title, h.tuya?.cloud_name ?? "", ...h.services.map((s) => s.name)]
        .some((v) => (v || "").toLowerCase().includes(q))
    })
  }, [status, smartOnly, query])

  const addTuya = async (host: ScanHost) => {
    try {
      const tuya = await api.tuya()
      setGateways(tuya.gateways)
      const device = tuya.devices.find((d) => d.id === host.tuya!.id)
      setAdding(
        device
          ? { ...targetFrom(device), lan_ip: host.ip, protocol: host.tuya!.version || device.protocol, child: false }
          : {
              id: host.tuya!.id,
              name: host.hostname || host.ip,
              lan_ip: host.ip,
              protocol: host.tuya!.version,
              local_key: "",
              child: false,
              gateway_id: "",
            },
      )
    } catch (exc) {
      feedback.fail(errorText(exc))
    }
  }

  const smartCount = (status?.results ?? []).filter((h) => h.smart).length

  return (
    <div className="space-y-4">
      <Panel title={<span style={{ color: sourceColor("lan") }}>Scan a network</span>}>
        <div className="flex flex-wrap items-end gap-3 px-4 py-4">
          <label className="block">
            <span className="mb-1 block text-sm font-medium">Network</span>
            <Select value={choice} onChange={(e) => setChoice(e.target.value)} className="w-72">
              {(networks.data?.networks ?? []).map((n) => (
                <option key={n.cidr} value={n.cidr}>
                  {n.cidr} on {n.interface} ({n.address})
                </option>
              ))}
              <option value={CUSTOM}>Another network…</option>
            </Select>
          </label>
          {choice === CUSTOM && (
            <label className="block">
              <span className="mb-1 block text-sm font-medium">Network range</span>
              <Input value={custom} onChange={(e) => setCustom(e.target.value)} placeholder="192.168.1.0/24" className="w-48 font-mono" />
            </label>
          )}
          <Button variant="primary" icon={<Radar className="size-4" />} busy={status?.running} disabled={!cidr} onClick={start}>
            {status?.running ? "Scanning" : "Scan network"}
          </Button>
          <p className="text-sm text-muted">
            Listens for Tuya broadcasts, checks every address for common smart-home ports, reads MAC addresses and mDNS
            names. Takes about 25 seconds.
          </p>
        </div>
        {status?.running && (
          <div className="border-t border-line px-4 py-3">
            <div className="mb-1.5 flex items-center justify-between text-sm">
              <span className="inline-flex items-center gap-2">
                <Led state="busy" />
                {status.phase || "Scanning"}
              </span>
              <span className="font-mono text-xs text-muted">{status.progress}%</span>
            </div>
            <div className="h-1.5 overflow-hidden rounded-full bg-sunk">
              <div className="h-full bg-src-lan transition-[width]" style={{ width: `${status.progress}%` }} />
            </div>
          </div>
        )}
        {!status?.running && status?.finished_at && (
          <p className="border-t border-line px-4 py-2.5 text-sm text-muted">
            Last scan of <span className="font-mono">{status.cidr}</span> {timeAgo(status.finished_at)}: {status.results.length}{" "}
            addresses answered, {smartCount} look like smart devices.
          </p>
        )}
        {status?.error && <p className="border-t border-line px-4 py-2.5 text-sm text-bad">{status.error}</p>}
        {status?.notes?.map((note) => (
          <p key={note} className="border-t border-line px-4 py-2 text-xs text-muted">
            {note}
          </p>
        ))}
      </Panel>

      <div className="inline-flex rounded-md border border-line bg-card p-0.5">
        {(
          [
            ["hosts", "Devices on the network"],
            ["wifi", "Nearby Wi-Fi"],
          ] as const
        ).map(([id, label]) => (
          <button
            key={id}
            onClick={() => setView(id)}
            className={cx(
              "rounded px-4 py-1.5 text-sm whitespace-nowrap",
              view === id ? "bg-ink text-card" : "text-muted hover:text-ink",
            )}
          >
            {label}
          </button>
        ))}
      </div>

      {view === "hosts" ? (
        <Panel
          title={`Found (${hosts.length})`}
          aside={
            <>
              <Toggle checked={smartOnly} onChange={setSmartOnly}>
                Smart devices only
              </Toggle>
              <SearchInput value={query} onChange={setQuery} placeholder="Search IP, name, vendor" />
            </>
          }
        >
          {hosts.length === 0 ? (
            <Empty title={status?.finished_at ? "Nothing matches" : "No scan yet"}>
              {status?.finished_at ? "Turn off Smart devices only to see every address." : "Pick a network and scan it."}
            </Empty>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[1080px] text-sm">
                <thead>
                  <tr>
                    <Th>Address</Th>
                    <Th>Device</Th>
                    <Th>Looks like</Th>
                    <Th>Open ports</Th>
                    <Th>Home Assistant</Th>
                    <Th>Tuya</Th>
                  </tr>
                </thead>
                <tbody>
                  {hosts.map((host) => (
                    <tr key={host.ip} className="border-t border-line hover:bg-sunk/60">
                      <Td mono>
                        <div>
                          {host.ip}
                          {host.is_self && <span className="ml-1.5 font-sans text-xs text-muted">(this host)</span>}
                        </div>
                        <div className="text-[11px] text-muted">{host.mac || "no MAC"}</div>
                      </Td>
                      <Td>
                        <div className="font-medium">
                          {host.tuya?.cloud_name || host.services[0]?.name || host.hostname || host.title || "Unnamed"}
                        </div>
                        <div className="text-xs text-muted">
                          {[host.hostname, host.title].filter((v, i, all) => v && all.indexOf(v) === i).join(" · ")}
                        </div>
                        {host.vendor && <div className="text-xs text-muted">{host.vendor}</div>}
                      </Td>
                      <Td>
                        <span className={cx("font-medium", host.smart ? "" : "text-muted")}>{host.kind}</span>
                        {host.services.length > 0 && (
                          <div className="text-xs text-muted">{[...new Set(host.services.map((s) => s.type))].join(", ")}</div>
                        )}
                      </Td>
                      <Td mono className="text-xs">
                        {host.ports.length ? host.ports.join(", ") : "–"}
                      </Td>
                      <Td>
                        {host.ha ? (
                          <Badge tone="ok">
                            {host.ha.name}
                            {host.ha.integration ? ` · ${host.ha.integration}` : ""}
                          </Badge>
                        ) : (
                          <Badge>Not in HA</Badge>
                        )}
                      </Td>
                      <Td>
                        {host.tuya ? (
                          <div className="flex flex-wrap items-center gap-1.5">
                            <span className="font-mono text-[11px] text-muted">
                              {host.tuya.id} {host.tuya.version && `v${host.tuya.version}`}
                            </span>
                            {host.tuya_local ? (
                              <Badge tone={host.tuya_local.state === "loaded" ? "ok" : "bad"}>
                                In Tuya Local{host.tuya_local.state !== "loaded" ? ", not loaded" : ""}
                              </Badge>
                            ) : (
                              <Button className="h-7" icon={<Plus className="size-3.5" />} onClick={() => addTuya(host)}>
                                Add to Tuya Local
                              </Button>
                            )}
                            {!host.tuya.in_cloud && <Badge>Not in cloud project</Badge>}
                          </div>
                        ) : (
                          <span className="text-muted">–</span>
                        )}
                      </Td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Panel>
      ) : (
        <WifiPanel />
      )}

      <AddDialog
        target={adding}
        gateways={gateways}
        onClose={() => setAdding(null)}
        onAdded={() => {
          void scan.reload()
          onChanged()
        }}
      />
    </div>
  )
}

function WifiPanel() {
  const feedback = useFeedback()
  const [data, setData] = useState<WifiView | null>(null)
  const [busy, setBusy] = useState(false)
  const [pairingOnly, setPairingOnly] = useState(false)

  const scanWifi = async () => {
    setBusy(true)
    try {
      setData(await api.wifi())
    } catch (exc) {
      feedback.fail(errorText(exc))
    } finally {
      setBusy(false)
    }
  }

  const points = (data?.access_points ?? []).filter((p) => !pairingOnly || p.pairing)
  const pairing = (data?.access_points ?? []).filter((p) => p.pairing).length

  return (
    <Panel
      title="Nearby Wi-Fi"
      aside={
        <>
          {data?.available && (
            <Toggle checked={pairingOnly} onChange={setPairingOnly}>
              Pairing hotspots only
            </Toggle>
          )}
          <Button variant="primary" icon={<Wifi className="size-4" />} busy={busy} onClick={scanWifi}>
            Scan Wi-Fi
          </Button>
        </>
      }
    >
      <p className="border-b border-line px-4 py-2.5 text-sm text-muted">
        New smart devices in pairing mode broadcast their own hotspot, such as SmartLife-1A2B, ESP_3C4D or ITEAD-1000.
        This uses the Wi-Fi radio on your Home Assistant host.
      </p>
      {!data ? (
        <Empty title="No Wi-Fi scan yet">Scan to list the networks your Home Assistant host can hear.</Empty>
      ) : !data.available ? (
        <Empty title="No Wi-Fi radio">This Home Assistant host has no wireless interface to scan with.</Empty>
      ) : (
        <>
          {pairing > 0 && (
            <div className="px-4 pt-3">
              <Notice>
                {pairing} device{pairing === 1 ? " is" : "s are"} waiting in pairing mode. Pair{pairing === 1 ? " it" : " them"}{" "}
                with the Smart Life or eWeLink app, then scan the network to find the new address.
              </Notice>
            </div>
          )}
          <div className="overflow-x-auto">
            <table className="mt-2 w-full min-w-[720px] text-sm">
              <thead>
                <tr>
                  <Th>Network name</Th>
                  <Th>Signal</Th>
                  <Th>Band</Th>
                  <Th>Radio</Th>
                </tr>
              </thead>
              <tbody>
                {points.map((point) => (
                  <tr key={`${point.mac}-${point.ssid}`} className={cx("border-t border-line", point.pairing && "bg-src-tuya/6")}>
                    <Td>
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="font-medium">{point.ssid || <span className="text-muted">Hidden network</span>}</span>
                        {point.pairing && <Badge tone="bad">Pairing mode</Badge>}
                        {point.connected && <Badge tone="ok">Connected</Badge>}
                      </div>
                    </Td>
                    <Td>
                      <div className="flex items-center gap-2">
                        <div className="h-1.5 w-20 overflow-hidden rounded-full bg-sunk">
                          <div className="h-full bg-ok" style={{ width: `${point.signal ?? 0}%` }} />
                        </div>
                        <span className="font-mono text-xs text-muted">{point.signal ?? "–"}%</span>
                      </div>
                    </Td>
                    <Td>{point.band}</Td>
                    <Td mono className="text-xs">
                      {point.mac}
                      {point.vendor && <span className="ml-1.5 font-sans text-muted">{point.vendor}</span>}
                    </Td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </Panel>
  )
}
