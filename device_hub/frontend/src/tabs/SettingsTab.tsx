import { useEffect, useState } from "react"
import { api } from "../api"
import { errorText, usePoll } from "../lib"
import type { Status } from "../types"
import { Badge, Button, Field, Input, Panel, Select, useFeedback } from "../ui"

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
          <Field label="Zigbee2MQTT base topic" hint="Matches mqtt.base_topic in Zigbee2MQTT's configuration.">
            <Input value={topic} onChange={(e) => setTopic(e.target.value)} className="font-mono" placeholder="zigbee2mqtt" />
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
              <Button variant="danger" busy={busy === "signout"} onClick={signOut}>
                Sign out
              </Button>
            </>
          ) : (
            <p className="text-muted">Not signed in. Use the Tuya cloud tab to connect your project.</p>
          )}
        </div>
      </Panel>

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
      </Panel>
    </div>
  )
}
