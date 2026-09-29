import { FileCheck2, Paperclip } from 'lucide-react'
import { useState } from 'react'
import ReportPicker from './ReportPicker.jsx'

/*
 * The paperclip beside a value, and what it is linked to. Opens the "Link a report" panel, which lists EVERY
 * report this patient uploaded (from the page's one cached list), this test's own reports first.
 * value: the linked report ({ id, display_name, ... } from the list) or null. onChange(report | null) may be async.
 */
export default function ReportLink({ field, fieldLabel, title, value, onChange }) {
  const [open, setOpen] = useState(null)          // null | 'list' | report id to preview
  const close = () => setOpen(null)
  return (
    <span className="relative inline-flex min-w-0 items-center gap-1.5">
      <PaperclipButton linked={Boolean(value)} label={fieldLabel} expanded={Boolean(open)}
        onClick={() => setOpen((o) => (o ? null : 'list'))} />
      {value ? (
        <button type="button" onClick={() => setOpen(value.id)} title={value.display_name}
          className="min-w-0 truncate text-left text-xs text-brand-700 underline-offset-2 hover:underline">
          {value.display_name}
        </button>
      ) : <span className="text-xs text-muted">No report linked</span>}
      {open && (
        <ReportPicker title={title ?? fieldLabel} matchType={field} linkedId={value?.id}
          startPreviewId={open === 'list' ? null : open} onClose={close}
          onLink={async (r) => { await onChange(r); close() }} onUnlink={async () => { await onChange(null); close() }} />
      )}
    </span>
  )
}

export function PaperclipButton({ linked, label, expanded, onClick }) {
  return (
    <button type="button" data-picker-opener onClick={onClick} aria-haspopup="dialog" aria-expanded={expanded}
      aria-label={linked ? `Change the report linked to ${label}` : `Link ${label} to a report`}
      title={linked ? 'Change linked report' : 'Link to a report'}
      className={`flex h-7 w-7 shrink-0 items-center justify-center rounded border ${linked
        ? 'border-brand-600 bg-brand-50 text-brand-700' : 'border-line text-muted hover:bg-subtle hover:text-ink'}`}>
      {linked ? <FileCheck2 size={15} aria-hidden="true" /> : <Paperclip size={15} aria-hidden="true" />}
    </button>
  )
}
