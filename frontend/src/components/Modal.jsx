import { X } from 'lucide-react'
import { useCallback, useEffect, useRef } from 'react'

// Native <dialog>: focus trapping, Esc to close and the backdrop come from the browser.
// closeOnBackdrop: a click outside the box closes it too (only for read-only dialogs - never mid-form).
export default function Modal({ title, onClose, children, footer, width = '34rem', closeOnBackdrop = false }) {
  const ref = useRef(null)
  useEffect(() => {
    ref.current.showModal()
  }, [])
  const close = useCallback(() => ref.current?.close(), [])
  return (
    // only this dialog's own close event - a dialog opened from inside it must not close it too
    <dialog ref={ref} onClose={(e) => { if (e.target === ref.current) onClose?.(e) }} aria-label={title}
      onClick={closeOnBackdrop ? (e) => { if (e.target === ref.current) close() } : undefined}
      style={{ width: `min(${width}, calc(100vw - 2rem))` }}
      className="m-auto rounded-lg border border-line bg-surface p-0 text-ink shadow-xl backdrop:bg-ink/40">
      <div className="flex items-center justify-between border-b border-line px-4 py-3">
        <h2 className="text-base font-bold text-ink">{title}</h2>
        <button type="button" onClick={close} aria-label="Close" className="rounded-md p-1.5 text-muted hover:bg-subtle">
          <X size={18} aria-hidden="true" />
        </button>
      </div>
      <div className="max-h-[75vh] overflow-y-auto">{typeof children === 'function' ? children(close) : children}</div>
      {footer && <div className="flex justify-end gap-2 border-t border-line bg-subtle px-4 py-3">{footer(close)}</div>}
    </dialog>
  )
}
