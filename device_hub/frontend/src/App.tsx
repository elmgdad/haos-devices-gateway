import { RefreshCw, Settings as SettingsIcon } from "lucide-react"
import { useEffect, useState } from "react"
import { api } from "./api"
import { cx, sourceColor, timeAgo, usePoll } from "./lib"
import AllDevices from "./tabs/AllDevices"
import NetworkTab from "./tabs/NetworkTab"
import SettingsTab from "./tabs/SettingsTab"
import TuyaTab from "./tabs/TuyaTab"
import Z2mTab from "./tabs/Z2mTab"
import ZhaTab from "./tabs/ZhaTab"
import type { Source, Status, Tab } from "./types"
import { Led, Notice } from "./ui"

type LedState = "ok" | "warn" | "off" | "busy"

interface Port {
  tab: Tab
  label: string
  source?: Source
  led: LedState
  count: number | null
  caption: string
}

function ports(status: Status | null): Port[] {
  if (!status) {
    return [{ tab: "all", label: "All devices", led: "off", count: null, caption: "loading" }]
  }
  const { tuya, tuya_local: local, z2m, zha, lan } = status
  return [
    {
      tab: "all",
      label: "All devices",
      led: status.ha_connected ? "ok" : "warn",
      count: tuya.count + z2m.count + zha.count + lan.count,
      caption: status.ha_connected ? "every system" : "HA offline",
    },
    {
      tab: "tuya",
      label: "Tuya cloud",
      source: "tuya",
      led: tuya.error ? "warn" : tuya.connected ? "ok" : "off",
      count: tuya.connected ? tuya.count : null,
      caption: tuya.error ? "login problem" : tuya.connected ? "devices + keys" : "not signed in",
    },
    {
      tab: "tuya_local",
      label: "Tuya Local",
      source: "tuya_local",
      led: !local.installed ? "off" : local.problems ? "warn" : "ok",
      count: local.installed ? local.count : null,
      caption: !local.installed ? "not installed" : local.problems ? `${local.problems} not loaded` : "entries",
    },
    {
      tab: "z2m",
      label: "Zigbee2MQTT",
      source: "z2m",
      led: z2m.permit_join
        ? "busy"
        : z2m.state === "started"
          ? z2m.bridge === "online"
            ? "ok"
            : "warn"
          : "off",
      count: z2m.installed ? z2m.count : null,
      caption: !z2m.installed
        ? "not installed"
        : z2m.permit_join
          ? "joining open"
          : z2m.state !== "started"
            ? "stopped"
            : `bridge ${z2m.bridge ?? "unknown"}`,
    },
    {
      tab: "zha",
      label: "ZHA",
      source: "zha",
      led: zha.loaded ? "ok" : "off",
      count: zha.loaded ? zha.count : null,
      caption: zha.loaded ? "devices" : zha.entries ? "not running" : "not set up",
    },
    {
      tab: "network",
      label: "Network",
      source: "lan",
      led: lan.running ? "busy" : lan.finished_at ? "ok" : "off",
      count: lan.finished_at ? lan.count : null,
      caption: lan.running ? "scanning" : lan.finished_at ? `smart, ${timeAgo(lan.finished_at)}` : "not scanned",
    },
  ]
}

const TABS: Tab[] = ["all", "tuya", "tuya_local", "z2m", "zha", "network", "settings"]

function initialTab(): Tab {
  const hash = window.location.hash.replace("#", "") as Tab
  return TABS.includes(hash) ? hash : "all"
}

export default function App() {
  const [tab, setTab] = useState<Tab>(initialTab)
  const status = usePoll(api.status, 15000)

  useEffect(() => {
    window.history.replaceState(null, "", `#${tab}`)
  }, [tab])

  useEffect(() => {
    const onHash = () => setTab(initialTab())
    window.addEventListener("hashchange", onHash)
    return () => window.removeEventListener("hashchange", onHash)
  }, [])

  const go = (next: Tab) => setTab(next)
  const refreshStatus = () => void status.reload()

  return (
    <div className="mx-auto flex min-h-screen max-w-[1500px] flex-col px-3 pb-10 sm:px-5">
      <header className="flex flex-wrap items-end justify-between gap-3 pt-5 pb-4">
        <div>
          <h1 className="label text-[26px] leading-none font-bold tracking-[0.08em]">Device Hub</h1>
          <p className="mt-1.5 text-sm text-muted">
            Tuya, Tuya Local, Zigbee2MQTT, ZHA and your network, wired into Home Assistant.
          </p>
        </div>
        <div className="flex items-center gap-2 text-xs text-muted">
          {status.data?.mode === "dev" && (
            <span className="label rounded-sm border border-line px-1.5 py-0.5 text-[11px]">Dev mode</span>
          )}
          <button
            className="inline-flex items-center gap-1.5 rounded-md px-2 py-1 hover:bg-card hover:text-ink"
            onClick={refreshStatus}
            title="Refresh status"
          >
            <RefreshCw className="size-3.5" />
            Refresh
          </button>
        </div>
      </header>

      <nav
        role="tablist"
        aria-label="Systems"
        className="flex overflow-x-auto rounded-lg border border-line bg-card shadow-[inset_0_-1px_0_var(--line)]"
      >
        {ports(status.data).map((port) => {
          const active = tab === port.tab
          return (
            <button
              key={port.tab}
              role="tab"
              aria-selected={active}
              onClick={() => go(port.tab)}
              className={cx(
                "relative flex min-w-[136px] flex-1 basis-0 flex-col gap-1.5 overflow-hidden border-r border-line px-4 pt-3 pb-3.5 text-left transition-colors",
                active ? "bg-card" : "bg-sunk/60 hover:bg-card",
              )}
            >
              <span className="flex items-center gap-2">
                <Led state={port.led} />
                <span className="label text-[13px]" style={port.source ? { color: sourceColor(port.source) } : undefined}>
                  {port.label}
                </span>
              </span>
              <span className="flex min-w-0 items-baseline gap-2">
                <span className="font-mono text-[22px] leading-none font-medium">{port.count ?? "–"}</span>
                <span className="min-w-0 truncate text-xs text-muted">{port.caption}</span>
              </span>
              <span
                className={cx("absolute inset-x-0 bottom-0 h-[3px] transition-opacity", active ? "opacity-100" : "opacity-0")}
                style={{ background: port.source ? sourceColor(port.source) : "var(--ink)" }}
              />
            </button>
          )
        })}
        <button
          role="tab"
          aria-selected={tab === "settings"}
          onClick={() => go("settings")}
          className={cx(
            "sticky right-0 flex w-14 shrink-0 items-center justify-center border-l border-line text-muted hover:text-ink",
            tab === "settings" ? "bg-card text-ink" : "bg-sunk",
          )}
          title="Settings"
          aria-label="Settings"
        >
          <SettingsIcon className="size-5" />
          {tab === "settings" && <span className="absolute inset-x-0 bottom-0 h-[3px] bg-ink" />}
        </button>
      </nav>

      {status.error && (
        <div className="mt-4">
          <Notice tone="bad">Device Hub cannot reach its backend: {status.error}</Notice>
        </div>
      )}

      <main className="mt-5 flex-1">
        {tab === "all" && <AllDevices onOpen={go} />}
        {(tab === "tuya" || tab === "tuya_local") && (
          <TuyaTab key={tab} focus={tab === "tuya_local" ? "local" : "all"} onChanged={refreshStatus} />
        )}
        {tab === "z2m" && <Z2mTab onChanged={refreshStatus} zhaLoaded={!!status.data?.zha.loaded} />}
        {tab === "zha" && <ZhaTab onChanged={refreshStatus} z2mRunning={status.data?.z2m.state === "started"} />}
        {tab === "network" && <NetworkTab onChanged={refreshStatus} />}
        {tab === "settings" && <SettingsTab status={status.data} onChanged={refreshStatus} />}
      </main>
    </div>
  )
}
