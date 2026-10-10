import { Check, Copy, Eye, EyeOff, LoaderCircle, X } from "lucide-react"
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ButtonHTMLAttributes,
  type InputHTMLAttributes,
  type ReactNode,
  type SelectHTMLAttributes,
} from "react"
import { copyText, cx, SOURCES, sourceColor } from "./lib"
import type { Source } from "./types"

type Variant = "primary" | "quiet" | "danger" | "ghost"

export function Button({
  variant = "quiet",
  busy,
  icon,
  className,
  children,
  disabled,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant; busy?: boolean; icon?: ReactNode }) {
  return (
    <button
      {...props}
      disabled={disabled || busy}
      className={cx(
        "inline-flex h-9 items-center gap-2 rounded-md px-3 text-sm font-medium whitespace-nowrap transition-colors",
        "disabled:cursor-not-allowed disabled:opacity-50",
        variant === "primary" && "bg-ink text-card hover:opacity-90",
        variant === "quiet" && "border border-line bg-card text-ink hover:bg-sunk",
        variant === "danger" && "border border-bad/40 bg-card text-bad hover:bg-bad/10",
        variant === "ghost" && "text-muted hover:bg-sunk hover:text-ink",
        className,
      )}
    >
      {busy ? <LoaderCircle className="size-4 animate-spin" /> : icon}
      {children}
    </button>
  )
}

export function Led({ state, title }: { state: "ok" | "warn" | "off" | "busy"; title?: string }) {
  return (
    <span
      title={title}
      className={cx(
        "inline-block size-2 shrink-0 rounded-full",
        state === "ok" && "bg-ok shadow-[0_0_6px_var(--ok)]",
        state === "warn" && "bg-bad shadow-[0_0_6px_var(--bad)]",
        state === "off" && "bg-idle",
        state === "busy" && "led-pulse bg-src-lan",
      )}
    />
  )
}

export function SourceTag({ source }: { source: Source }) {
  return (
    <span
      className="label inline-flex h-5 items-center rounded-sm border px-1.5 text-[11px]"
      style={{ color: sourceColor(source), borderColor: `color-mix(in srgb, ${sourceColor(source)} 45%, transparent)` }}
    >
      {SOURCES[source].short}
    </span>
  )
}

export function Badge({ tone = "neutral", children }: { tone?: "neutral" | "ok" | "bad" | "info"; children: ReactNode }) {
  return (
    <span
      className={cx(
        "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium whitespace-nowrap",
        tone === "neutral" && "bg-sunk text-muted",
        tone === "ok" && "bg-ok/12 text-ok",
        tone === "bad" && "bg-bad/12 text-bad",
        tone === "info" && "bg-src-lan/12 text-src-lan",
      )}
    >
      {children}
    </span>
  )
}

export function Field({ label, hint, children }: { label: string; hint?: ReactNode; children: ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1 block text-sm font-medium">{label}</span>
      {children}
      {hint && <span className="mt-1 block text-xs text-muted">{hint}</span>}
    </label>
  )
}

const inputClass =
  "h-9 w-full rounded-md border border-line bg-card px-3 text-sm text-ink placeholder:text-muted/70 focus:border-src-lan focus:outline-none"

export function Input({ className, ...props }: InputHTMLAttributes<HTMLInputElement>) {
  return <input {...props} className={cx(inputClass, className)} />
}

export function Select({ className, children, ...props }: SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select {...props} className={cx(inputClass, "pr-8", className)}>
      {children}
    </select>
  )
}

export function Toggle({ checked, onChange, children }: { checked: boolean; onChange: (v: boolean) => void; children: ReactNode }) {
  return (
    <label className="inline-flex cursor-pointer items-center gap-2 text-sm select-none">
      <input type="checkbox" className="size-4 accent-[var(--ink)]" checked={checked} onChange={(e) => onChange(e.target.checked)} />
      {children}
    </label>
  )
}

export function Panel({
  title,
  aside,
  children,
  className,
}: {
  title?: ReactNode
  aside?: ReactNode
  children: ReactNode
  className?: string
}) {
  return (
    <section className={cx("rounded-lg border border-line bg-card", className)}>
      {(title || aside) && (
        <header className="flex flex-wrap items-center justify-between gap-3 border-b border-line px-4 py-3">
          <h2 className="label text-[15px]">{title}</h2>
          {aside && <div className="flex flex-wrap items-center gap-2">{aside}</div>}
        </header>
      )}
      {children}
    </section>
  )
}

export function Notice({ tone = "info", children, action }: { tone?: "info" | "bad"; children: ReactNode; action?: ReactNode }) {
  return (
    <div
      className={cx(
        "flex flex-wrap items-center justify-between gap-3 rounded-lg border px-4 py-3 text-sm",
        tone === "info" && "border-src-lan/30 bg-src-lan/8",
        tone === "bad" && "border-bad/30 bg-bad/8",
      )}
    >
      <div className="min-w-0 flex-1">{children}</div>
      {action}
    </div>
  )
}

export function Empty({ title, children, action }: { title: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <div className="flex flex-col items-center gap-2 px-6 py-12 text-center">
      <p className="label text-base">{title}</p>
      {children && <p className="max-w-md text-sm text-muted">{children}</p>}
      {action && <div className="mt-2">{action}</div>}
    </div>
  )
}

export function Th({ children, className }: { children?: ReactNode; className?: string }) {
  return (
    <th className={cx("label sticky top-0 z-[1] bg-sunk px-3 py-2 text-left text-xs font-semibold text-muted", className)}>
      {children}
    </th>
  )
}

export function Td({ children, className, mono }: { children?: ReactNode; className?: string; mono?: boolean }) {
  return <td className={cx("px-3 py-2 align-middle", mono && "font-mono text-[13px]", className)}>{children}</td>
}

export function KeyCell({ value }: { value: string }) {
  const [shown, setShown] = useState(false)
  const [copied, setCopied] = useState(false)
  if (!value) return <span className="text-muted">none</span>
  return (
    <span className="inline-flex items-center gap-1">
      <code className="font-mono text-[13px]">{shown ? value : "•".repeat(10)}</code>
      <button className="rounded p-1 text-muted hover:bg-sunk hover:text-ink" onClick={() => setShown(!shown)} title={shown ? "Hide key" : "Show key"}>
        {shown ? <EyeOff className="size-3.5" /> : <Eye className="size-3.5" />}
      </button>
      <button
        className="rounded p-1 text-muted hover:bg-sunk hover:text-ink"
        title="Copy key"
        onClick={async () => {
          if (await copyText(value)) {
            setCopied(true)
            window.setTimeout(() => setCopied(false), 1200)
          }
        }}
      >
        {copied ? <Check className="size-3.5 text-ok" /> : <Copy className="size-3.5" />}
      </button>
    </span>
  )
}

export function Modal({
  open,
  title,
  onClose,
  children,
  footer,
  wide,
}: {
  open: boolean
  title: ReactNode
  onClose: () => void
  children: ReactNode
  footer?: ReactNode
  wide?: boolean
}) {
  const ref = useRef<HTMLDialogElement>(null)
  useEffect(() => {
    const dialog = ref.current
    if (!dialog) return
    if (open && !dialog.open) dialog.showModal()
    if (!open && dialog.open) dialog.close()
  }, [open])
  return (
    <dialog
      ref={ref}
      onCancel={(e) => {
        e.preventDefault()
        onClose()
      }}
      className={cx(
        "m-auto w-[calc(100%-24px)] rounded-lg border border-line bg-card p-0 text-ink shadow-2xl backdrop:bg-black/45",
        wide ? "max-w-3xl" : "max-w-lg",
      )}
    >
      {open && (
        <>
          <header className="flex items-center justify-between border-b border-line px-5 py-3">
            <h3 className="label text-base">{title}</h3>
            <button className="rounded p-1 text-muted hover:bg-sunk hover:text-ink" onClick={onClose} aria-label="Close">
              <X className="size-4" />
            </button>
          </header>
          <div className="max-h-[70vh] overflow-y-auto px-5 py-4">{children}</div>
          {footer && <footer className="flex flex-wrap justify-end gap-2 border-t border-line px-5 py-3">{footer}</footer>}
        </>
      )}
    </dialog>
  )
}

interface Toast {
  id: number
  tone: "ok" | "bad"
  text: string
}

interface Confirm {
  title: string
  body: ReactNode
  action: string
  danger?: boolean
  resolve: (value: boolean) => void
}

const FeedbackContext = createContext<{
  ok: (text: string) => void
  fail: (text: string) => void
  confirm: (options: Omit<Confirm, "resolve">) => Promise<boolean>
} | null>(null)

export function FeedbackProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([])
  const [pending, setPending] = useState<Confirm | null>(null)
  const push = useCallback((tone: Toast["tone"], text: string) => {
    const id = Date.now() + Math.random()
    setToasts((items) => [...items, { id, tone, text }])
    window.setTimeout(() => setToasts((items) => items.filter((t) => t.id !== id)), tone === "bad" ? 8000 : 4000)
  }, [])
  const value = {
    ok: (text: string) => push("ok", text),
    fail: (text: string) => push("bad", text),
    confirm: (options: Omit<Confirm, "resolve">) =>
      new Promise<boolean>((resolve) => setPending({ ...options, resolve })),
  }
  const settle = (answer: boolean) => {
    pending?.resolve(answer)
    setPending(null)
  }
  return (
    <FeedbackContext.Provider value={value}>
      {children}
      <div className="pointer-events-none fixed right-4 bottom-4 z-50 flex w-[min(380px,calc(100%-32px))] flex-col gap-2" aria-live="polite">
        {toasts.map((toast) => (
          <div
            key={toast.id}
            className={cx(
              "pointer-events-auto rounded-md border bg-card px-4 py-3 text-sm shadow-lg",
              toast.tone === "ok" ? "border-ok/40" : "border-bad/50",
            )}
          >
            <span className={cx("mr-2 font-semibold", toast.tone === "ok" ? "text-ok" : "text-bad")}>
              {toast.tone === "ok" ? "Done" : "Failed"}
            </span>
            {toast.text}
          </div>
        ))}
      </div>
      <Modal
        open={pending !== null}
        title={pending?.title}
        onClose={() => settle(false)}
        footer={
          <>
            <Button onClick={() => settle(false)}>Cancel</Button>
            <Button variant={pending?.danger ? "danger" : "primary"} onClick={() => settle(true)}>
              {pending?.action}
            </Button>
          </>
        }
      >
        <div className="text-sm">{pending?.body}</div>
      </Modal>
    </FeedbackContext.Provider>
  )
}

export function useFeedback() {
  const context = useContext(FeedbackContext)
  if (!context) throw new Error("FeedbackProvider missing")
  return context
}

export function SearchInput({ value, onChange, placeholder }: { value: string; onChange: (v: string) => void; placeholder: string }) {
  return (
    <Input
      type="search"
      value={value}
      onChange={(e) => onChange(e.target.value)}
      placeholder={placeholder}
      className="w-full sm:w-64"
    />
  )
}

export function Signal({ value, max = 255 }: { value: number | null; max?: number }) {
  if (value === null || value === undefined) return <span className="text-muted">–</span>
  const bars = Math.max(1, Math.min(4, Math.ceil((value / max) * 4)))
  return (
    <span className="inline-flex items-end gap-0.5" title={`${value}`}>
      {[1, 2, 3, 4].map((bar) => (
        <span
          key={bar}
          className={cx("w-1 rounded-[1px]", bar <= bars ? (bars <= 1 ? "bg-bad" : "bg-ok") : "bg-line")}
          style={{ height: `${bar * 3 + 2}px` }}
        />
      ))}
      <span className="ml-1.5 font-mono text-xs text-muted">{value}</span>
    </span>
  )
}
