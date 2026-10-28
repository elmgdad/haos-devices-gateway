import { ExternalLink } from "lucide-react"
import { useEffect, useState } from "react"
import { api } from "../api"
import { clockTime, copyText, cx, errorText, timeAgo, usePoll } from "../lib"
import type { ShareScope, Status } from "../types"
import { Badge, Button, Field, Input, Notice, Panel, Select, useFeedback } from "../ui"

export default function SettingsTab({ status, onChanged }: { status: Status | null; onChanged: () => void }) {
  const feedback = useFeedback()
  const settings = usePoll(api.settings)
  const networks = usePoll(api.networks)
  const tuya = usePoll(() => api.tuya())
  const [topic, setTopic] = useState("")
  const [cidr, setCidr] = useState("")
  const [busy, setBusy] = useState<string | null>(null)

  useEffect(() => {
    if (settings.data) {
      setTopic(settings.data.z2m_base_topic)
      setCidr(settings.data.default_cidr)
    }
  }, [settings.data])

  const save = async () => {
    setBusy("save")
    try {
      await api.saveSettings({ z2m_base_topic: topic, default_cidr: cidr })
      feedback.ok("Settings saved.")
      onChanged()
    } catch (exc) {
      feedback.fail(errorText(exc))
    } finally {
      setBusy(null)
    }
  }

  const reset = async () => {
    const ok = await feedback.confirm({
      title: "Reset Device Hub?",
      body: (
        <>
          Device Hub forgets devices saved for local control, learned IR buttons, share keys, the Tuya sign-in and its
          settings, and removes the entities it added to Home Assistant. Tuya Local, Zigbee2MQTT and ZHA keep their
          devices. The Tuya account in the app configuration signs in again when the app restarts.
        </>
      ),
      action: "Reset",
      danger: true,
    })
    if (!ok) return
    setBusy("reset")
    try {
      await api.reset()
      feedback.ok("Device Hub was reset.")
      await Promise.all([settings.reload(), tuya.reload()])
      onChanged()
    } catch (exc) {
      feedback.fail(errorText(exc))
    } finally {
      setBusy(null)
    }
  }

  const signOut = async () => {
    const ok = await feedback.confirm({
      title: "Sign out of the Tuya cloud project?",
      body: "Device Hub forgets the Access ID and Secret. Devices already in Tuya Local keep working.",
      action: "Sign out",
      danger: true,
    })
    if (!ok) return
    setBusy("signout")
    try {
      await api.tuyaLogout()
      feedback.ok("Signed out of Tuya.")
      await tuya.reload()
      onChanged()
    } catch (exc) {
      feedback.fail(errorText(exc))
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Panel title="Preferences">
        <form
          className="space-y-4 p-4"
          onSubmit={(e) => {
            e.preventDefault()
            void save()
          }}
        >
          <Field
            label="Zigbee2MQTT base topic"
            hint={
              settings.data?.z2m_topic_discovered
                ? "Read from the Zigbee2MQTT app."
                : "Used only when Zigbee2MQTT is not installed. Otherwise Device Hub reads mqtt.base_topic from that app."
            }
          >
            <Input
              value={topic}
              onChange={(e) => setTopic(e.target.value)}
              className="font-mono"
              placeholder="zigbee2mqtt"
              disabled={settings.data?.z2m_topic_discovered}
            />
          </Field>
          <Field label="Default network to scan" hint="Used by Find on network on the Tuya tab.">
            <Select value={cidr} onChange={(e) => setCidr(e.target.value)}>
              <option value="">First network on this host</option>
              {(networks.data?.networks ?? []).map((n) => (
                <option key={n.cidr} value={n.cidr}>
                  {n.cidr} on {n.interface}
                </option>
              ))}
            </Select>
          </Field>
          <Button variant="primary" type="submit" busy={busy === "save"}>
            Save settings
          </Button>
        </form>
      </Panel>

      <Panel title="Tuya cloud project">
        <div className="space-y-3 p-4 text-sm">
          {tuya.data?.config.connected ? (
            <>
              <p>
                Signed in with <span className="font-mono">{tuya.data.config.access_id}</span> in the{" "}
                <b>{tuya.data.config.region}</b> data center. {tuya.data.config.device_count} devices.
              </p>
              {tuya.data.config.source === "app" ? (
                <p className="text-muted">Taken from the Device Hub app configuration.</p>
              ) : (
                <Button variant="danger" busy={busy === "signout"} onClick={signOut}>
                  Sign out
                </Button>
              )}
            </>
          ) : (
            <p className="text-muted">
              Not signed in. Set the Access ID, Access Secret and data center in the Device Hub app configuration. Home
              Assistant restarts the app and signs in.
            </p>
          )}
        </div>
      </Panel>

      <SharePanel />

      <Panel title="Connection" className="lg:col-span-2">
        <dl className="grid gap-4 p-4 text-sm sm:grid-cols-3">
          <div>
            <dt className="label text-[11px] text-muted">Running as</dt>
            <dd className="mt-1">{status?.mode === "addon" ? "Home Assistant app" : "Dev server"}</dd>
          </div>
          <div>
            <dt className="label text-[11px] text-muted">Home Assistant</dt>
            <dd className="mt-1">
              {status?.ha_connected ? <Badge tone="ok">Connected</Badge> : <Badge tone="bad">Disconnected</Badge>}
            </dd>
          </div>
          <div>
            <dt className="label text-[11px] text-muted">Tuya Local</dt>
            <dd className="mt-1">
              {status?.tuya_local.installed ? `Version ${status.tuya_local.version}` : "Not installed"}
            </dd>
          </div>
        </dl>
        <div className="flex flex-wrap items-center justify-between gap-3 border-t border-line px-4 py-3 text-sm">
          <span className="text-muted">Start over with an empty Device Hub, for example after moving to a new Tuya account.</span>
          <Button variant="danger" busy={busy === "reset"} onClick={reset}>
            Reset Device Hub
          </Button>
        </div>
      </Panel>
    </div>
  )
}

function SharePanel() {
  const feedback = useFeedback()
  const share = usePoll(api.share)
  const [name, setName] = useState("")
  const [picked, setPicked] = useState<Set<string>>(new Set())
  const [everything, setEverything] = useState(false)
  const [scope, setScope] = useState<ShareScope>("control")
  const [days, setDays] = useState("")
  const [busy, setBusy] = useState<string | null>(null)
  const data = share.data
  const docs = data?.docs

  const toggle = (id: string) =>
    setPicked((current) => {
      const next = new Set(current)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })

  const create = async () => {
    setBusy("create")
    try {
      const created = await api.createShare(name, everything ? ["*"] : [...picked], scope, days ? Number(days) : null)
      setName("")
      setPicked(new Set())
      setEverything(false)
      await share.reload()
      if (await copyText(created.token)) feedback.ok(`Created “${created.name}” and copied the key.`)
      else feedback.ok(`Created “${created.name}”. Copy the key below.`)
    } catch (exc) {
      feedback.fail(errorText(exc))
    } finally {
      setBusy(null)
    }
  }

  const revoke = async (id: string, label: string) => {
    const ok = await feedback.confirm({
      title: `Revoke ${label}?`,
      body: "Apps using this key lose access immediately. Devices themselves are not changed.",
      action: "Revoke",
      danger: true,
    })
    if (!ok) return
    setBusy(id)
    try {
      await api.revokeShare(id)
      await share.reload()
      feedback.ok(`Revoked ${label}.`)
    } catch (exc) {
      feedback.fail(errorText(exc))
    } finally {
      setBusy(null)
    }
  }

  return (
    <Panel title="Share" className="lg:col-span-2">
      <div className="grid gap-6 p-4 lg:grid-cols-2">
        <div className="space-y-4 text-sm">
          <p>
            Give another app a key. It can list the devices you pick and change the same controls Home Assistant uses in
            automations. The key cannot see local keys or your Tuya secret.
          </p>
          {data && (
            <p>
              Base URL <button className="font-mono text-xs hover:underline" onClick={() => void copyText(data.base_url)}>{data.base_url}</button>
            </p>
          )}
          <Field label="Key name" hint="For example, the phone or app you are handing it to.">
            <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="Living room display" />
          </Field>
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="What it may do" hint={data?.scopes.find((s) => s.id === scope)?.about}>
              <Select value={scope} onChange={(e) => setScope(e.target.value as ShareScope)}>
                <option value="read">Read only</option>
                <option value="control">Control</option>
                <option value="manage">Manage</option>
              </Select>
            </Field>
            <Field label="Expires" hint="An expired key stops working. Revoke it any time.">
              <Select value={days} onChange={(e) => setDays(e.target.value)}>
                <option value="">Never</option>
                <option value="1">After 1 day</option>
                <option value="7">After 7 days</option>
                <option value="30">After 30 days</option>
                <option value="90">After 90 days</option>
                <option value="365">After a year</option>
              </Select>
            </Field>
          </div>
          <div>
            <p className="mb-1 text-sm font-medium">Devices</p>
            <label className="mb-2 flex items-center gap-2 text-sm">
              <input type="checkbox" checked={everything} onChange={(e) => setEverything(e.target.checked)} />
              All devices, including ones added later
            </label>
            <div className={cx("max-h-56 space-y-1 overflow-y-auto rounded-md border border-line p-2", everything && "pointer-events-none opacity-40")}>
              {(data?.devices ?? []).length === 0 && <p className="text-xs text-muted">No devices are in Home Assistant yet.</p>}
              {(data?.devices ?? []).map((device) => (
                <label key={device.id} className="flex items-center gap-2 text-sm">
                  <input type="checkbox" checked={picked.has(device.id)} onChange={() => toggle(device.id)} />
                  <span className="min-w-0 flex-1 truncate">{device.name}</span>
                  <span className="text-xs text-muted">{device.area || device.source}</span>
                </label>
              ))}
            </div>
          </div>
          <Button variant="primary" busy={busy === "create"} onClick={create}>
            Create key
          </Button>
          {(data?.tokens ?? []).length > 0 && (
            <ul className="space-y-3 border-t border-line pt-3">
              {data!.tokens.map((token) => (
                <li key={token.id} className="space-y-1">
                  <div className="flex items-center justify-between gap-2">
                    <span className="flex flex-wrap items-center gap-2">
                      <span className="font-medium">{token.name}</span>
                      <Badge tone={token.scope === "manage" ? "bad" : token.scope === "control" ? "info" : "neutral"}>{token.scope}</Badge>
                      {token.expired && <Badge tone="bad">expired</Badge>}
                    </span>
                    <Button variant="danger" className="h-8" busy={busy === token.id} onClick={() => revoke(token.id, token.name)}>
                      Revoke
                    </Button>
                  </div>
                  <button className="block max-w-full truncate font-mono text-xs text-muted hover:text-ink" onClick={() => void copyText(token.token)}>
                    {token.token}
                  </button>
                  <p className="text-xs text-muted">
                    {token.device_ids.includes("*") ? "All devices" : `${token.device_ids.length} devices`}
                    {" · "}
                    {token.expires_at ? `${token.expired ? "expired" : "expires"} ${clockTime(token.expires_at)}` : "never expires"}
                    {" · "}
                    {token.last_used ? `last used ${timeAgo(token.last_used)}` : "not used yet"}
                  </p>
                </li>
              ))}
            </ul>
          )}
        </div>
        <div className="space-y-3 text-sm">
          <p className="font-medium">How to call it</p>
          {docs ? (
            <>
              <p className="text-muted">
                Send <span className="font-mono text-xs">{docs.auth}</span> on every request. Version {docs.version}.
              </p>
              <div className="flex flex-wrap gap-2">
                <Button icon={<ExternalLink className="size-4" />} onClick={() => window.open(docs.base_url, "_blank", "noopener")}>
                  API docs
                </Button>
                <Button icon={<ExternalLink className="size-4" />} onClick={() => window.open(docs.openapi, "_blank", "noopener")}>
                  OpenAPI spec
                </Button>
              </div>
              <pre className="overflow-x-auto rounded-md bg-sunk p-3 font-mono text-xs">{`curl -H "Authorization: Bearer dh_your_key" \\\n  ${docs.base_url}/devices`}</pre>
              {docs.endpoints.map((item) => (
                <div key={item.method + item.path}>
                  <p className="flex flex-wrap items-center gap-2 font-mono text-xs">
                    {item.method} {item.path}
                    <Badge>{item.scope}</Badge>
                  </p>
                  <p className="text-muted">{item.about}</p>
                  {item.body !== undefined && (
                    <pre className="mt-1 overflow-x-auto rounded-md bg-sunk p-2 font-mono text-[11px]">{JSON.stringify(item.body)}</pre>
                  )}
                </div>
              ))}
              <p className="font-medium">Values</p>
              <ul className="space-y-1 text-muted">
                {Object.entries(docs.values).map(([kind, text]) => (
                  <li key={kind}>
                    <span className="text-ink">{kind}.</span> {text}
                  </li>
                ))}
              </ul>

            </>
          ) : (
            <Notice>Loading the API description.</Notice>
          )}
        </div>
      </div>
    </Panel>
  )
}
