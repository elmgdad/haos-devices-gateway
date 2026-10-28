import type {
  ActivityEntry,
  AreaCard,
  AreaDevice,
  Automation,
  AutomationSpec,
  AutomationTarget,
  AddStart,
  BulkResult,
  Command,
  ControlDevice,
  DeviceDetail,
  SetupItem,
  ShareScope,
  Area,
  HotspotStatus,
  IrRemote,
  IrView,
  LocalOverview,
  Network,
  ScanStatus,
  Settings,
  ShareToken,
  ShareView,
  SonoffView,
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
  reset: () => post<{ ok: boolean }>("/reset"),
  areas: () => get<Area[]>("/areas"),
  createArea: (name: string) => post<Area>("/areas", { name }),
  share: () => get<ShareView>("/share"),
  createShare: (name: string, device_ids: string[], scope: ShareScope, expires_in_days: number | null) =>
    post<ShareToken>("/share", { name, device_ids, scope, expires_in_days }),
  revokeShare: (id: string) => del(`/share/${id}`),
  updateHaDevice: (id: string, changes: { name_by_user?: string; area_id?: string | null }) =>
    post(`/ha/devices/${id}`, changes),
  allDevices: () => get<{ devices: UnifiedDevice[]; areas: Record<string, string> }>("/devices/all"),
  control: (key: string) => get<ControlDevice | null>(`/control/${encodeURIComponent(key)}`),
  sendControl: (key: string, commands: Command[]) => post<ControlDevice>(`/control/${encodeURIComponent(key)}`, { commands }),
  deviceDetail: (key: string) => get<DeviceDetail>(`/devices/${encodeURIComponent(key)}/detail`),
  updateDevice: (key: string, changes: { name?: string; area_id?: string | null }) =>
    post(`/devices/${encodeURIComponent(key)}`, changes),
  removeDevice: (key: string) => del(`/devices/${encodeURIComponent(key)}`),
  bulk: (keys: string[], action: "area" | "on" | "off" | "remove", area_id?: string | null) =>
    post<{ results: BulkResult[] }>("/devices/bulk", { keys, action, area_id }),
  activity: (kind = "") => get<{ activity: ActivityEntry[] }>(`/activity${kind ? `?kind=${kind}` : ""}`),
  clearActivity: () => del("/activity"),
  setup: () => get<{ items: SetupItem[] }>("/setup"),

  areaOverview: () => get<{ areas: AreaCard[]; unassigned: AreaDevice[] }>("/areas/overview"),
  areaCommand: (id: string, action: "on" | "off") =>
    post<{ area: string; results: { device_id: string; name: string; ok: boolean; error?: string }[] }>(
      `/areas/${encodeURIComponent(id)}/command`,
      { action },
    ),
  renameArea: (id: string, name: string) => post<Area>(`/areas/${encodeURIComponent(id)}`, { name }),
  deleteArea: (id: string) => del(`/areas/${encodeURIComponent(id)}`),

  automations: () => get<{ automations: Automation[] }>("/automations"),
  automationTargets: () => get<{ devices: AutomationTarget[] }>("/automations/targets"),
  createAutomation: (spec: AutomationSpec) => post<Automation>("/automations", spec),
  saveAutomation: (ref: string, spec: AutomationSpec) => post<Automation>(`/automations/${encodeURIComponent(ref)}`, spec),
  deleteAutomation: (ref: string) => del(`/automations/${encodeURIComponent(ref)}`),
  runAutomation: (ref: string) => post(`/automations/${encodeURIComponent(ref)}/run`),
  enableAutomation: (ref: string, enabled: boolean) => post(`/automations/${encodeURIComponent(ref)}/enabled`, { enabled }),

  tuya: (refresh = false) => get<TuyaView>(`/tuya${refresh ? "?refresh=1" : ""}`),
  tuyaLogin: (access_id: string, secret: string, region: string) =>
    post<TuyaConfig>("/tuya/login", { access_id, secret, region }),
  tuyaLogout: () => post<TuyaConfig>("/tuya/logout"),
  tuyaControl: (id: string) => get<TuyaControl>(`/tuya/devices/${encodeURIComponent(id)}/control`),
  tuyaCommand: (id: string, code: string, value: boolean | number | string) =>
    post<TuyaControl>(`/tuya/devices/${encodeURIComponent(id)}/commands`, { commands: [{ code, value }] }),
  tuyaRemove: (id: string) => del(`/tuya/devices/${encodeURIComponent(id)}`),
  ir: () => get<IrView>("/ir"),
  irCreate: (name: string, blaster_id: string) => post<IrRemote>("/ir/remotes", { name, blaster_id }),
  irDelete: (id: string) => del(`/ir/remotes/${encodeURIComponent(id)}`),
  irLearn: (id: string, name: string) => post<IrRemote>(`/ir/remotes/${encodeURIComponent(id)}/learn`, { name }),
  irForget: (id: string, name: string) =>
    del<IrRemote>(`/ir/remotes/${encodeURIComponent(id)}/buttons/${encodeURIComponent(name)}`),
  irCommand: (id: string, code: string, value?: boolean | number | string) =>
    post<IrRemote>(`/ir/remotes/${encodeURIComponent(id)}/command`, { code, value }),
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

  sonoff: () => get<SonoffView>("/sonoff"),
  sonoffSwitch: (entity_id: string, on: boolean) => post<SonoffView>("/sonoff/switch", { entity_id, on }),
  sonoffRemove: (ha_device_id: string) => del(`/sonoff/devices/${ha_device_id}`),

  z2m: () => get<Z2mView>("/z2m"),
  z2mAddon: (action: "start" | "stop" | "restart") => post(`/z2m/addon/${action}`),
  z2mPermit: (seconds: number) => post("/z2m/permit", { seconds }),
  z2mRename: (ieee: string, name: string) => post(`/z2m/devices/${ieee}/rename`, { name }),
  z2mConfigure: (ieee: string) => post(`/z2m/devices/${ieee}/configure`),
  z2mRemove: (ieee: string, force: boolean) => del(`/z2m/devices/${ieee}${force ? "?force=1" : ""}`),
}
