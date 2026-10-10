export type Source = "tuya" | "tuya_local" | "z2m" | "zha" | "lan"
export type Tab = "all" | Source | "network" | "settings"

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
}

export interface UnifiedDevice {
  key: string
  name: string
  sources: Source[]
  kind: string
  area: string | null
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
  ha_device_id: string | null
  broken: boolean
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
  default_cidr: string
}
