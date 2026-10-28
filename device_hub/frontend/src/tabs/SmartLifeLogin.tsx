import { QrCode, RotateCw } from "lucide-react"
import { useEffect, useState } from "react"
import { api } from "../api"
import { errorText } from "../lib"
import type { TuyaConfig, TuyaQr } from "../types"
import { Button, Field, Input, useFeedback } from "../ui"

/** Sign in to Tuya with the Smart Life app: the user code, then a QR code scanned in the app. No cloud project. */
export function SmartLifeLogin({ onDone }: { onDone: (config: TuyaConfig) => void }) {
  const feedback = useFeedback()
  const [code, setCode] = useState("")
  const [qr, setQr] = useState<TuyaQr | null>(null)
  const [left, setLeft] = useState(0)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!qr) return
    let cancelled = false // each code polls on its own; a new code or leaving the page stops this one
    const started = Date.now()
    const tick = window.setInterval(() => setLeft(Math.max(0, qr.expires_in - Math.round((Date.now() - started) / 1000))), 1000)
    const poll = async () => {
      while (!cancelled && Date.now() - started < qr.expires_in * 1000) {
        await new Promise((resolve) => window.setTimeout(resolve, 2000))
        if (cancelled) return
        try {
          const result = await api.tuyaAccountWait(qr.token)
          if (result.done) {
            feedback.ok(
              `Signed in${result.config.account.username ? ` as ${result.config.account.username}` : ""}. ${result.config.device_count} devices found.`,
            )
            setQr(null)
            onDone(result.config)
            return
          }
        } catch (exc) {
          setError(errorText(exc))
          setQr(null)
          return
        }
      }
      if (!cancelled) {
        setError("The QR code expired before it was scanned. Show a new one.")
        setQr(null)
      }
    }
    void poll()
    return () => {
      cancelled = true
      window.clearInterval(tick)
    }
    // feedback and onDone are stable enough; restarting the poll on their change would show a second code
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [qr])

  const start = async () => {
    setBusy(true)
    setError(null)
    try {
      const next = await api.tuyaAccountQr(code.trim())
      setLeft(next.expires_in)
      setQr(next)
    } catch (exc) {
      setError(errorText(exc))
    } finally {
      setBusy(false)
    }
  }

  if (qr) {
    return (
      <div className="space-y-3 text-sm">
        <div className="flex flex-wrap items-start gap-4">
          <div
            className="size-56 shrink-0 rounded-lg bg-white p-2 shadow-sm [&_svg]:block [&_svg]:size-full"
            role="img"
            aria-label="QR code to scan with the Smart Life app"
            dangerouslySetInnerHTML={{ __html: qr.svg }}
          />
          <ol className="min-w-48 flex-1 list-decimal space-y-1.5 pl-5 text-muted">
            <li>Open the Smart Life app on your phone.</li>
            <li>Tap the scan icon at the top right of the Home screen.</li>
            <li>Scan this code and confirm the sign-in.</li>
            <li>Device Hub finishes on its own. Keep this page open.</li>
          </ol>
        </div>
        <p className="text-xs text-muted">
          Waiting for the scan · {left} s left
        </p>
        <div className="flex gap-2">
          <Button icon={<RotateCw className="size-4" />} onClick={() => void start()} busy={busy}>
            New code
          </Button>
          <Button variant="ghost" onClick={() => setQr(null)}>
            Cancel
          </Button>
        </div>
      </div>
    )
  }

  return (
    <form
      className="space-y-3"
      onSubmit={(e) => {
        e.preventDefault()
        void start()
      }}
    >
      <Field label="User code" hint="In Smart Life: Me, the settings gear, Account and Security, User Code.">
        <Input value={code} onChange={(e) => setCode(e.target.value)} className="font-mono" autoComplete="off" required />
      </Field>
      {error && <p className="rounded-md bg-bad/10 px-3 py-2 text-sm text-bad">{error}</p>}
      <Button variant="primary" type="submit" icon={<QrCode className="size-4" />} busy={busy} disabled={!code.trim()}>
        Show QR code
      </Button>
    </form>
  )
}
