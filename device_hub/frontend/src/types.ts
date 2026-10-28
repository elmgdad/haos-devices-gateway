export type Source = "tuya" | "tuya_local" | "z2m" | "zha" | "sonoff" | "lan"
export type Tab = "all" | "areas" | "automations" | Source | "network" | "settings" | "setup" | "activity"

export interface Status {
  mode: "addon" | "dev"
  ha_connected: boolean
  tuya: { connected: boolean; count: number; error: string | null }
  tuya_local: { installed: boolean; version: string | null; count: number; problems: number; hacs_url: string }
  zha: { entries: number; loaded: boolean; count: number }
  z2m: {
    installed: boolean
    state: string | null
    bridge: string | null
    count: number
    permit_join: boolean | null
  }
  lan: { count: number; hosts: number; finished_at: number | null; running: boolean }
  sonoff: { installed: boolean; count: number; local: number; offline: number }
  areas?: { count: number | null }
  automations?: { count: number; on: number; ours: number }
}

export interface SonoffSwitch {
  entity_id: string
  name: string
  domain: string
  state: string | null
}

export interface SonoffDevice {
  id: string
  ha_device_id: string
  name: string
  model: string
  firmware: string | null
  mac: string | null
  area_id: string | null
  area: string | null
  online: boolean | null
  local: boolean
  ip: string | null
  rssi: number | null
  switches: SonoffSwitch[]
}

export interface SonoffView {
  installed: boolean
  entries: { entry_id: string; title: string; state: string }[]
  devices: SonoffDevice[]
  cloud_error: string | null
}

export interface UnifiedDevice {
  key: string
  name: string
  sources: Source[]
  kind: string
  area: string | null
  area_id: string | null
  online: boolean | null
  status: string
  warn: boolean
  address: string
  ha_device_id: string | null
  tab: Tab
}

export interface TuyaLocalLink {
  entry_id: string
  title: string
  state: string
  host: string | null
  protocol_version: string | number | null
  area: string | null
  area_id?: string | null
  ha_device_id: string | null
  broken: boolean
}

export interface TuyaStatusItem {
  code: string
  value: boolean | number | string | null
}

export interface TuyaFunction {
  code: string
  type: string
  values: string
  desc: string
}

export interface TuyaControl {
  id: string
  name: string
  online: boolean | null
  via?: "local" | "cloud"
  status: TuyaStatusItem[]
  functions: TuyaFunction[]
  blocked?: string | null
}

export interface LocalLink {
  id: string
  name: string
  ip: string
  version: string
  state: "connecting" | "online" | "offline"
  error: string | null
  last_seen: number | null
  entities: number
  saved_at: number | null
  ha_device_id?: string | null
  area?: string | null
  area_id?: string | null
  controls?: DeviceControl[]
}

export interface DeviceControl {
  code: string
  name: string
  type: string
  entity_id: string | null
  state: boolean | number | string | null
  writable: boolean
  primary?: boolean
}

export interface IrKey {
  code: string
  name: string
  primary: boolean
  local: boolean
}

export interface IrAc {
  power: boolean
  mode: string
  temp: number
  fan: string
  modes: string[]
  fans: string[]
  min: number
  max: number
}

export interface IrRemote {
  id: string
  name: string
  kind: "ac" | "keys"
  source: "cloud" | "learned"
  blaster_id: string
  blaster_name: string
  keys: IrKey[]
  learned: { name: string }[]
  ac: IrAc | null
  ha_device_id?: string | null
  area_id?: string | null
  area?: string | null
}

export interface IrBlaster {
  id: string
  name: string
  ip: string | null
  linked: boolean
  learning: boolean
}

export interface IrView {
  blasters: IrBlaster[]
  remotes: IrRemote[]
  cloud_ir: boolean | null
  cloud_ir_help: string | null
}

export type ShareScope = "read" | "control" | "manage"

export interface ShareToken {
  id: string
  name: string
  token: string
  device_ids: string[]
  created_at: number
  last_used: number | null
  scope: ShareScope
  expires_at: number | null
  expired: boolean
}

export interface ShareDocs {
  name: string
  version: string
  base_url: string
  openapi: string
  swagger: string
  mcp: string
  auth: string
  scopes: Record<ShareScope, string>
  endpoints: { method: string; path: string; about: string; scope: ShareScope; body?: unknown }[]
  values: Record<string, string>
}

export interface ShareDeviceChoice {
  id: string
  name: string
  area: string | null
  source: string
  online: boolean | null
}

export interface ShareView {
  base_url: string
  docs: ShareDocs
  tokens: ShareToken[]
  scopes: { id: ShareScope; about: string }[]
  devices: ShareDeviceChoice[]
}

export interface Control {
  code: string
  name: string
  type: string
  entity_id: string | null
  state: boolean | number | string | null
  writable: boolean
  primary: boolean
  options?: string[]
  min?: number
  max?: number
  step?: number
  scale?: number
  unit?: string
  dimmable?: boolean
  brightness?: number
  target?: number | null
  current?: number | null
  position?: number
}

export interface ControlDevice {
  id: string
  name: string
  area: string | null
  area_id: string | null
  online: boolean | null
  source: Source
  role: string | null
  detail: string | null
  ha_device_id: string | null
  controls: Control[]
}

export interface Command {
  code: string
  value?: boolean | number | string | null
  brightness?: number
}

export type ActivityKind = "command" | "scene" | "automation" | "remove" | "change" | "offline" | "online" | "key" | "error"

export interface ActivityEntry {
  id: string
  at: number
  kind: ActivityKind
  text: string
  device_id: string | null
  device: string | null
  ok: boolean
  via: string
}

export interface DeviceDetail {
  device: UnifiedDevice
  ha: {
    id: string
    name: string | null
    name_by_user: string | null
    manufacturer: string | null
    model: string | null
    model_id: string | null
    sw_version: string | null
    hw_version: string | null
    serial_number: string | null
    configuration_url: string | null
    via: string | null
    integrations: string[]
    disabled: boolean
  } | null
  entities: { entity_id: string; name: string; domain: string; state: string | null; unit: string | null; category: string | null }[]
  activity: ActivityEntry[]
  removable: boolean
}

export interface BulkResult {
  key: string
  name: string
  ok: boolean
  error?: string
}

export interface SetupItem {
  id: string
  title: string
  required: boolean
  state: "ok" | "todo" | "warn" | "optional"
  detail: string
  action: { label: string; tab?: Tab; ha?: string; url?: string; status?: "offline" } | null
}

export interface LocalSaveResult {
  id: string
  name: string
  ok: boolean
  reason: string | null
  entities?: number
}

export interface LocalOverview {
  mqtt: { state: "connected" | "connecting" | "off"; error: string | null; host: string | null }
  devices: LocalLink[]
  last_results: LocalSaveResult[]
  results?: LocalSaveResult[]
}

export interface TuyaDevice {
  id: string
  name: string
  category: string
  category_name: string
  product_name: string
  product_id: string
  model: string
  online: boolean | null
  sub: boolean
  child: boolean
  node_id: string
  gateway_id: string
  gateway_name: string | null
  uuid: string
  mac: string
  local_key: string
  icon: string
  lan_ip: string | null
  protocol: string | null
  seen_at: number | null
  tuya_local: TuyaLocalLink | null
  local: LocalLink | null
  drift: ("key" | "ip")[]
}

export interface TuyaLocalEntry {
  entry_id: string
  title: string
  state: string
  reason: string | null
  device_id: string
  device_cid: string
  ha_device_id: string | null
  area: string | null
  host: string | null
  protocol_version: string | number | null
  broken: boolean
}

export interface Region {
  id: string
  name: string
}

export interface TuyaConfig {
  access_id: string
  region: string
  has_secret: boolean
  connected: boolean
  device_count: number
  fetched_at: number
  error: string | null
  regions: Region[]
  source: string
}

export interface Gateway {
  id: string
  name: string
  host: string | null
  in_tuya_local?: boolean
}

export interface TuyaView {
  config: TuyaConfig
  tuya_local: { installed: boolean; version: string | null; hacs_url: string }
  devices: TuyaDevice[]
  unmatched_entries: TuyaLocalEntry[]
  gateways: Gateway[]
}

export interface TypeOption {
  value: string
  label: string
}

export interface AddStart {
  flow_id: string
  options: TypeOption[]
  default: string | null
  detected_protocol: string | null
  name: string
  host: string
}

export interface ScanHost {
  ip: string
  mac: string
  vendor: string
  hostname: string
  ports: number[]
  services: { type: string; name: string; host: string }[]
  title: string
  is_self: boolean
  kind: string
  smart: boolean
  private_mac: boolean
  tuya: {
    id: string
    version: string
    product_key: string
    cloud_name: string | null
    in_cloud: boolean
  } | null
  tuya_local: { entry_id: string; title: string; state: string } | null
  ha: { device_id: string; name: string; integration: string | null } | null
}

export interface ScanStatus {
  running: boolean
  phase: string
  progress: number
  cidr: string | null
  started_at: number | null
  finished_at: number | null
  error: string | null
  notes: string[]
  results: ScanHost[]
}

export interface Network {
  cidr: string
  interface: string
  address: string
  hosts: number
}

export interface AccessPoint {
  ssid: string
  mac: string
  signal: number | null
  frequency: number
  band: string
  pairing: boolean
  connected: boolean
  vendor: string
}

export interface HotspotStatus {
  available: boolean
  reason: string | null
  interface: string | null
  running: boolean
  ssid: string | null
  address: string | null
  signal: number | null
  ethernet_ipv4: boolean
  upstream: string | null
  previous: string | null
}

export interface WifiView {
  available: boolean
  interface: string | null
  connected_ssid: string | null
  access_points: AccessPoint[]
}

export interface ZhaDevice {
  ieee: string
  nwk: string
  name: string
  manufacturer: string
  model: string
  quirk: string
  device_type: string
  power_source: string
  lqi: number | null
  rssi: number | null
  last_seen: string | null
  available: boolean | null
  ha_device_id: string | null
  area_id: string | null
  area: string | null
}

export interface ZhaView {
  entries: { entry_id: string; title: string; state: string; reason: string | null; disabled_by: string | null }[]
  loaded: boolean
  devices: ZhaDevice[]
  error: string | null
}

export interface Z2mDevice {
  ieee: string
  name: string
  type: string
  network_address: number
  model: string
  vendor: string
  description: string
  power_source: string
  supported: boolean
  interview_completed: boolean
  disabled: boolean
  availability: string | null
  linkquality: number | null
  last_seen: string | number | null
  battery: number | null
  ha_device_id: string | null
  area_id: string | null
  area: string | null
}

export interface Z2mView {
  addon: {
    installed: boolean
    slug?: string
    name?: string
    state?: string
    version?: string
    update_available?: boolean
    panel_path?: string | null
    serial_port?: string | null
    error?: string
  }
  base_topic: string
  bridge_state: string | null
  subscribed: boolean
  subscribe_error: string | null
  version: string | null
  coordinator: string | null
  permit_join: boolean | null
  permit_join_end: number | null
  channel: number | null
  devices: Z2mDevice[]
}

export interface Area {
  id: string
  name: string
}

export interface Settings {
  z2m_base_topic: string
  z2m_topic_discovered: boolean
  default_cidr: string
}

export interface AreaDevice {
  id: string
  name: string
  online: boolean | null
  source: Source
  switchable: boolean
  on: boolean
}

export interface AreaCard {
  id: string
  name: string
  icon: string | null
  devices: AreaDevice[]
  online: number
  offline: number
  on: number
  switchable: number
}

export interface AutomationTrigger {
  type: "time" | "sun" | "device"
  at?: string
  event?: "sunrise" | "sunset"
  offset_minutes?: number
  device_id?: string
  code?: string
  to?: boolean | number | string
  above?: number
  below?: number
  for_seconds?: number
}

export interface AutomationAction {
  device_id?: string
  code?: string
  value?: boolean | number | string | null
  brightness?: number
  area_id?: string
  action?: "on" | "off"
  delay_seconds?: number
}

export interface AutomationSpec {
  name: string
  trigger: AutomationTrigger
  days: string[]
  actions: AutomationAction[]
}

export interface Automation {
  entity_id: string
  id: string | null
  name: string
  enabled: boolean
  last_triggered: string | null
  mode: string | null
  running: number
  ours: boolean
  spec: AutomationSpec | null
}

export interface AutomationTarget {
  id: string
  name: string
  area: string | null
  controls: (Pick<Control, "code" | "name" | "type" | "entity_id" | "state" | "options" | "min" | "max" | "unit" | "dimmable"> & {
    action: boolean
    trigger: boolean
  })[]
}
