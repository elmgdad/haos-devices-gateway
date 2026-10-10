import type {
  AddStart,
  Area,
  HotspotStatus,
  LocalOverview,
  Network,
  ScanStatus,
  Settings,
  Status,
  TuyaConfig,
  TuyaControl,
  TuyaView,
  UnifiedDevice,
  WifiView,
  Z2mView,
  ZhaView,
} from "./types"

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const response = await fetch(`./api${path}`, {
    method,
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  const data = await response.json().catch(() => ({}))
  if (!response.ok) {
    throw new Error(data.error || `Request failed (${response.status}).`)
  }
  return data as T
}

const get = <T>(path: string) => request<T>("GET", path)
const post = <T>(path: string, body: unknown = {}) => request<T>("POST", path, body)
const del = <T>(path: string) => request<T>("DELETE", path)

export const api = {
  status: () => get<Status>("/status"),
  settings: () => get<Settings>("/settings"),
  saveSettings: (settings: Partial<Settings>) => post<Settings>("/settings", settings),
  areas: () => get<Area[]>("/areas"),
  updateHaDevice: (id: string, changes: { name_by_user?: string; area_id?: string | null }) =>
    post(`/ha/devices/${id}`, changes),
  allDevices: () => get<{ devices: UnifiedDevice[]; areas: Record<string, string> }>("/devices/all"),

  tuya: (refresh = false) => get<TuyaView>(`/tuya${refresh ? "?refresh=1" : ""}`),
  tuyaLogin: (access_id: string, secret: string, region: string) =>
    post<TuyaConfig>("/tuya/login", { access_id, secret, region }),
  tuyaLogout: () => post<TuyaConfig>("/tuya/logout"),
  tuyaControl: (id: string) => get<TuyaControl>(`/tuya/devices/${encodeURIComponent(id)}/control`),
  tuyaCommand: (id: string, code: string, value: boolean | number | string) =>
    post<TuyaControl>(`/tuya/devices/${encodeURIComponent(id)}/commands`, { commands: [{ code, value }] }),
  local: () => get<LocalOverview>("/local"),
  saveLocal: (ids: string[] = []) => post<LocalOverview>("/local/save", { ids }),
  forgetLocal: (id: string) => del<LocalOverview>(`/local/${encodeURIComponent(id)}`),

  addStart: (body: {
    device_id: string
    host?: string
    local_key?: string
    protocol_version?: string
    gateway_id?: string
    name?: string
  }) => post<AddStart>("/tuya_local/add/start", body),
  addFinish: (flow_id: string, type: string, name: string) =>
    post<{ entry_id: string; title: string }>("/tuya_local/add/finish", { flow_id, type, name }),
  addCancel: (flow_id: string) => post("/tuya_local/add/cancel", { flow_id }),
  syncEntry: (entry_id: string, changes: { local_key?: string; host?: string; protocol_version?: string }) =>
    post(`/tuya_local/entries/${entry_id}/sync`, changes),
  reloadEntry: (entry_id: string) => post(`/tuya_local/entries/${entry_id}/reload`),
  removeEntry: (entry_id: string) => del(`/tuya_local/entries/${entry_id}`),

  scan: () => get<ScanStatus>("/scan"),
  networks: () => get<{ networks: Network[]; default: string }>("/scan/networks"),
  startScan: (cidr: string, sweep = true) => post<ScanStatus>("/scan", { cidr, sweep, mdns: sweep }),
  wifi: () => get<WifiView>("/scan/wifi"),
  hotspot: () => get<HotspotStatus>("/hotspot"),
  startHotspot: (ssid: string, password: string) => post<HotspotStatus>("/hotspot", { ssid, password }),
  stopHotspot: () => del<HotspotStatus>("/hotspot"),

  zha: () => get<ZhaView>("/zha"),
  zhaPermit: (duration: number) => post("/zha/permit", { duration }),
  zhaReconfigure: (ieee: string) => post(`/zha/devices/${ieee}/reconfigure`),
  zhaRemove: (ieee: string) => del(`/zha/devices/${ieee}`),

  z2m: () => get<Z2mView>("/z2m"),
  z2mAddon: (action: "start" | "stop" | "restart") => post(`/z2m/addon/${action}`),
  z2mPermit: (seconds: number) => post("/z2m/permit", { seconds }),
  z2mRename: (ieee: string, name: string) => post(`/z2m/devices/${ieee}/rename`, { name }),
  z2mConfigure: (ieee: string) => post(`/z2m/devices/${ieee}/configure`),
  z2mRemove: (ieee: string, force: boolean) => del(`/z2m/devices/${ieee}${force ? "?force=1" : ""}`),
}
