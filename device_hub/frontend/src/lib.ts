import { useCallback, useEffect, useRef, useState } from "react"
import type { Source } from "./types"

export function cx(...parts: (string | false | null | undefined)[]) {
  return parts.filter(Boolean).join(" ")
}

export const SOURCES: Record<Source, { label: string; short: string }> = {
  tuya: { label: "Tuya cloud", short: "Tuya" },
  tuya_local: { label: "Tuya Local", short: "Local" },
  z2m: { label: "Zigbee2MQTT", short: "Z2M" },
  zha: { label: "ZHA", short: "ZHA" },
  sonoff: { label: "Sonoff", short: "Sonoff" },
  lan: { label: "Network", short: "LAN" },
}

export const sourceColor = (source: Source) => `var(--src-${source.replace("_", "-")})`

export function timeAgo(value: number | string | null | undefined) {
  if (value === null || value === undefined || value === "") return ""
  const ms = typeof value === "number" ? (value > 1e12 ? value : value * 1000) : Date.parse(value)
  if (Number.isNaN(ms)) return ""
  const seconds = Math.max(0, Math.round((Date.now() - ms) / 1000))
  if (seconds < 45) return "just now"
  if (seconds < 3600) return `${Math.round(seconds / 60)} min ago`
  if (seconds < 86400) return `${Math.round(seconds / 3600)} h ago`
  return `${Math.round(seconds / 86400)} d ago`
}

export function usePoll<T>(load: () => Promise<T>, intervalMs = 0, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const loadRef = useRef(load)
  loadRef.current = load

  const reload = useCallback(async () => {
    try {
      const value = await loadRef.current()
      setData(value)
      setError(null)
      return value
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc))
      return null
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    setLoading(true)
    reload()
    if (!intervalMs) return
    const timer = window.setInterval(() => {
      if (!document.hidden) reload()
    }, intervalMs)
    return () => window.clearInterval(timer)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [intervalMs, reload, ...deps])

  return { data, error, loading, reload, setData }
}

function luminance(color: string) {
  const probe = document.createElement("span")
  probe.style.color = color
  document.body.appendChild(probe)
  const rgb = getComputedStyle(probe).color.match(/\d+(\.\d+)?/g)?.map(Number) ?? [255, 255, 255]
  probe.remove()
  return (0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]) / 255
}

export function prefersDark() {
  try {
    if (window.parent !== window) {
      const parentRoot = window.parent.document.documentElement
      const background = getComputedStyle(parentRoot).getPropertyValue("--primary-background-color").trim()
      if (background) return luminance(background) < 0.4
    }
  } catch {
    // Parent frame not readable; fall back to the OS setting.
  }
  return window.matchMedia("(prefers-color-scheme: dark)").matches
}

export async function copyText(text: string) {
  try {
    await navigator.clipboard.writeText(text)
    return true
  } catch {
    const area = document.createElement("textarea")
    area.value = text
    area.style.position = "fixed"
    area.style.opacity = "0"
    document.body.appendChild(area)
    area.select()
    const ok = document.execCommand("copy")
    area.remove()
    return ok
  }
}

export function openInHa(path: string) {
  if (window.parent === window) {
    window.open(path, "_blank", "noopener")
    return
  }
  window.parent.history.pushState(null, "", path)
  window.parent.dispatchEvent(new CustomEvent("location-changed", { detail: { replace: false } }))
}

export function errorText(exc: unknown) {
  return exc instanceof Error ? exc.message : String(exc)
}

export type StatusFilter = "all" | "online" | "offline"

/** Unknown status (null) only shows under "all". */
export function matchesStatus(online: boolean | null | undefined, filter: StatusFilter) {
  if (filter === "online") return online === true
  if (filter === "offline") return online === false
  return true
}

/** Remove one at a time so a failure does not stop the rest. Returns what could not be removed. */
export async function removeEach<T>(items: T[], remove: (item: T) => Promise<unknown>) {
  const failed: { item: T; error: string }[] = []
  for (const item of items) {
    try {
      await remove(item)
    } catch (exc) {
      failed.push({ item, error: errorText(exc) })
    }
  }
  return failed
}
