import type { ReactNode } from 'react'
import { X } from 'lucide-react'

export function AdminDialogShell({ title, description, children, onClose, wide = false }: {
  title: string
  description?: string
  children: ReactNode
  onClose: () => void
  wide?: boolean
}) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/55 p-3 backdrop-blur-[2px]" onMouseDown={onClose}>
      <section
        role="dialog"
        aria-modal="true"
        aria-label={title}
        className={`max-h-[92vh] w-full overflow-hidden rounded-xl border border-border bg-card shadow-2xl ${wide ? 'max-w-6xl' : 'max-w-xl'}`}
        onMouseDown={event => event.stopPropagation()}
      >
        <header className="flex items-start justify-between gap-4 border-b border-border px-5 py-4">
          <div>
            <h2 className="font-semibold tracking-tight">{title}</h2>
            {description ? <p className="mt-1 text-xs text-muted-foreground">{description}</p> : null}
          </div>
          <button type="button" onClick={onClose} className="rounded-md p-1.5 text-muted-foreground transition hover:bg-secondary hover:text-foreground" aria-label="关闭">
            <X className="h-4 w-4" />
          </button>
        </header>
        <div className="max-h-[calc(92vh-73px)] overflow-auto p-5">{children}</div>
      </section>
    </div>
  )
}

export const fieldClassName = 'w-full rounded-md border border-border bg-background px-3 py-2 text-sm outline-none transition focus:border-primary/50 focus:ring-2 focus:ring-primary/15'
export const primaryButtonClassName = 'inline-flex items-center justify-center rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground transition hover:bg-primary/90 disabled:cursor-not-allowed disabled:opacity-50'
export const secondaryButtonClassName = 'inline-flex items-center justify-center rounded-md border border-border bg-background px-4 py-2 text-sm font-medium transition hover:bg-secondary disabled:cursor-not-allowed disabled:opacity-50'
