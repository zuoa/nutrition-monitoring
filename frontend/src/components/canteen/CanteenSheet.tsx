import type { ReactNode } from 'react'
import * as Dialog from '@radix-ui/react-dialog'
import { X } from 'lucide-react'

type Props = {
  open: boolean
  onClose: () => void
  title: string
  description: string
  busy?: boolean
  suspended?: boolean
  children: ReactNode
  footer?: ReactNode
}

export function CanteenSheet({ open, onClose, title, description, busy, suspended, children, footer }: Props) {
  return (
    <Dialog.Root open={open && !suspended} onOpenChange={(next) => { if (!next && !busy) onClose() }}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-50 bg-foreground/40" />
        <Dialog.Content className="canteen-mobile fixed inset-x-0 bottom-0 z-50 mx-auto flex max-h-[94dvh] w-full max-w-xl flex-col rounded-t-3xl border border-border bg-card shadow-xl outline-none">
          <div className="flex shrink-0 items-start justify-between gap-3 border-b border-border px-5 py-4">
            <div className="min-w-0">
              <Dialog.Title className="text-lg font-semibold">{title}</Dialog.Title>
              <Dialog.Description className="mt-1 text-sm text-muted-foreground">{description}</Dialog.Description>
            </div>
            <Dialog.Close disabled={busy} aria-label="关闭" className="canteen-icon-button shrink-0 bg-secondary">
              <X className="h-5 w-5" />
            </Dialog.Close>
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain p-5">{children}</div>
          {footer && <div className="canteen-sheet-footer shrink-0 border-t border-border bg-card px-5 pt-3">{footer}</div>}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}
