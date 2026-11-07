import { AlertTriangle, Check, ChevronRight, CircleAlert, Info, Loader2, Minus, Pencil, RotateCw } from 'lucide-react'
import { Fragment, useCallback, useEffect, useState } from 'react'
import { api, formatDate } from '../api.js'
import { Badge, btn } from './ui.jsx'

/*
 * What the automatic reading found in an upload (CareCue Phase 1) - suggestions for the clinical team, never a record.
 * Clean by design: one row per value (test, value, an issue icon, Use). A click on a row opens the details - the exact
 * line and page it was read from, the date and range, how sure, and any problem the checks found.
 */
const CHIP = {                                          // [tone, short label]
  uploaded: ['neutral', 'Queued'], processing: ['info', 'Reading'], extraction_completed: ['info', 'Ready'],
  failed: ['alert', 'Failed'], verified: ['ok', 'Verified'], corrected: ['ok', 'Corrected'], rejected: ['neutral', 'Rejected'],
}
const REASON = {                                        // needs_review: why, in a word or two
  handwritten_or_unclear: 'Handwritten · verify manually', ocr_not_configured: 'Scan · read manually',
  unsupported_format: "Can't read file", issues_found: 'Check flagged', nothing_found: 'Nothing found',
  too_many_pages: 'Too many pages',
}
const chip = (status, reason) => (status === 'needs_review' ? ['warn', REASON[reason] ?? 'Check'] : CHIP[status])
const METHOD = { pdf_text: 'PDF text', ocr_sarvam: 'OCR', 'pdf_text+ocr_sarvam': 'PDF text + OCR' }
const CONF = { high: 'High', medium: 'Medium', low: 'Low' }
const SEVERITY = {
  error: { icon: AlertTriangle, cls: 'text-alert-700', rank: 2 },
  warning: { icon: CircleAlert, cls: 'text-warn-700', rank: 1 },
  info: { icon: Info, cls: 'text-muted', rank: 0 },
}
const DECIDED = {
  verified: { icon: Check, cls: 'text-ok-700', label: 'Verified' },
  corrected: { icon: Pencil, cls: 'text-warn-800', label: 'Corrected' },
  rejected: { icon: Minus, cls: 'text-muted', label: 'Not used' },
}

// The short chip on the Pending reports list.
export function ReadStatus({ p, patientId, onChanged }) {
  const [busy, setBusy] = useState(false)
  if (!p) return null
  const [tone, label] = p.mismatch && !['verified', 'corrected', 'rejected'].includes(p.status) ? ['warn', 'Manual check · mismatch']
    : p.status === 'extraction_completed' ? ['info', `${p.fields} read`] : chip(p.status, p.review_reason)
  const retry = async () => {
    setBusy(true)
    try { await api.retryExtraction(patientId, p.id) } finally { setBusy(false); onChanged?.() }
  }
  return (
    <span className="mt-1 flex flex-wrap items-center gap-1.5">
      <Badge tone={tone}>{p.status === 'processing' && <Loader2 size={12} aria-hidden="true" className="animate-spin" />}{label}</Badge>
      {p.status === 'failed' && (
        <button type="button" disabled={busy} onClick={retry} className="inline-flex items-center gap-1 text-xs font-medium text-brand-700 hover:underline">
          <RotateCw size={12} aria-hidden="true" /> {busy ? 'Reading' : 'Retry'}
        </button>
      )}
    </span>
  )
}

export function useExtraction(patientId, kind, sourceId) {
  const [data, setData] = useState(undefined)            // undefined = loading, null = not read automatically
  const load = useCallback(() => api.extraction(patientId, kind, sourceId).then(setData).catch(() => setData(null)),
    [patientId, kind, sourceId])
  useEffect(() => {
    load()
  }, [load])
  useEffect(() => {                                      // still reading: check again shortly
    if (!data || !['uploaded', 'processing'].includes(data.status)) return undefined
    const t = setTimeout(load, 2500)
    return () => clearTimeout(t)
  }, [data, load])
  return [data, load, setData]
}

const shown = (f) => `${f.value_text ?? '—'}${f.unit ? (f.unit === '%' ? ' %' : ` ${f.unit}`) : ''}`
const worst = (issues) => issues.filter((i) => i.severity !== 'info').sort((a, b) => SEVERITY[b.severity].rank - SEVERITY[a.severity].rank)[0]

// mode: 'single' (Use per row) | 'lab' (Use all) | 'view' (read only, e.g. a prescription)
export default function ExtractionPanel({ patientId, kind, sourceId, mode = 'view', onUse, decided = false, onReupload }) {
  const [data, load, setData] = useExtraction(patientId, kind, sourceId)
  const [busy, setBusy] = useState(false)
  const [asking, setAsking] = useState(false)          // "Ask to re-upload" -> confirm
  const [open, setOpen] = useState(null)                 // the row whose details are shown
  const [notesOpen, setNotesOpen] = useState(false)
  if (data === undefined || data === null) return null
  const retry = async () => {
    setBusy(true)
    try { setData(await api.retryExtraction(patientId, data.id)) } catch { /* the chip shows the state */ } finally { setBusy(false) }
  }
  const usable = data.fields.filter((f) => f.status === 'needs_review' && f.field_kind === 'observation' && f.value_text)
  const canRetry = ['failed', 'needs_review', 'extraction_completed'].includes(data.status) && !decided
  const open_ = !['verified', 'corrected', 'rejected'].includes(data.status) && !decided
  const [tone, label] = data.mismatch && open_ ? ['warn', 'Manual check'] : chip(data.status, data.review_reason)
  const reupload = async () => {
    setBusy(true)
    try {
      const r = await api.requestReupload(patientId, data.id)
      if (onReupload) onReupload(r.message)
      else load()
    } finally {
      setBusy(false)
      setAsking(false)
    }
  }
  const notes = data.document_issues.filter((i) => i.severity !== 'info')

  return (
    <section aria-label="Read from report" className="overflow-hidden rounded-lg border border-line bg-surface">
      <header className="flex items-center justify-between gap-2 border-b border-line px-4 py-2.5">
        <h2 className="text-sm font-semibold text-ink">Read from report</h2>
        <span className="flex items-center gap-1.5">
          <Badge tone={tone}>{['uploaded', 'processing'].includes(data.status) && <Loader2 size={12} aria-hidden="true" className="animate-spin" />}{label}</Badge>
          {mode === 'lab' && usable.length > 0 && (
            <button type="button" onClick={() => onUse?.(usable)} className={`${btn.secondary} ${btn.sm}`}>Use all</button>
          )}
          {canRetry && (
            <button type="button" disabled={busy} onClick={retry} aria-label="Read again" title="Read again"
              className="rounded-md p-1.5 text-muted hover:bg-subtle hover:text-ink">
              <RotateCw size={15} aria-hidden="true" className={busy ? 'animate-spin' : ''} />
            </button>
          )}
        </span>
      </header>

      {data.mismatch && (
        <div className="flex flex-wrap items-center gap-2 border-b border-line bg-warn-50 px-4 py-2.5">
          <AlertTriangle size={15} aria-hidden="true" className="shrink-0 text-warn-700" />
          <span className="flex-1 text-sm font-semibold text-warn-800">{data.mismatch}</span>
          {open_ && (asking ? (
            <span className="flex items-center gap-1.5">
              <button type="button" disabled={busy} onClick={reupload} className={`${btn.danger} ${btn.sm}`}>Send request</button>
              <button type="button" onClick={() => setAsking(false)} className={`${btn.secondary} ${btn.sm}`}>Cancel</button>
            </span>
          ) : (
            <button type="button" onClick={() => setAsking(true)} className={`${btn.secondary} ${btn.sm}`}>Ask to re-upload</button>
          ))}
        </div>
      )}
      {notes.length > 0 && (
        <button type="button" onClick={() => setNotesOpen((o) => !o)} aria-expanded={notesOpen}
          className="flex w-full items-center gap-2 border-b border-line px-4 py-2 text-left text-sm text-warn-800 hover:bg-subtle">
          <CircleAlert size={15} aria-hidden="true" /> {notes.length} note{notes.length > 1 ? 's' : ''}
          <ChevronRight size={14} aria-hidden="true" className={`ml-auto text-muted ${notesOpen ? 'rotate-90' : ''}`} />
        </button>
      )}
      {notesOpen && <ul className="space-y-1 border-b border-line bg-subtle px-4 py-2">{notes.map((i, n) => <IssueLine key={n} i={i} />)}</ul>}

      {data.fields.length > 0 && (
        <table className="w-full text-sm">
          <tbody className="divide-y divide-line">
            {data.fields.map((f) => {
              const issue = worst(f.issues)
              const Icon = issue && SEVERITY[issue.severity].icon
              const done = DECIDED[f.status]
              const isOpen = open === f.id
              return (
                <Fragment key={f.id}>
                  <tr onClick={() => setOpen(isOpen ? null : f.id)} aria-expanded={isOpen}
                    className={`cursor-pointer hover:bg-subtle ${f.status === 'rejected' ? 'text-muted' : ''}`}>
                    <td className="w-6 py-2.5 pl-3 align-middle">
                      <ChevronRight size={14} aria-hidden="true" className={`text-muted transition-transform ${isOpen ? 'rotate-90' : ''}`} />
                    </td>
                    <td className="py-2.5 pr-2 text-muted">{f.label ?? f.name}</td>
                    <td className="py-2.5 pr-2 text-right font-semibold text-ink tnum whitespace-nowrap">{shown(f)}</td>
                    <td className="w-6 py-2.5">
                      {Icon && <Icon size={15} aria-label={issue.severity === 'error' ? 'Problem' : 'Check'} className={SEVERITY[issue.severity].cls} />}
                    </td>
                    <td className="w-16 py-2.5 pr-3 text-right">
                      {done ? <span title={done.label} className={`inline-flex ${done.cls}`}><done.icon size={15} aria-label={done.label} /></span>
                        : mode === 'single' && f.field_kind === 'observation' && f.value_text && (
                          <button type="button" onClick={(e) => { e.stopPropagation(); onUse?.(f) }} className={`${btn.secondary} ${btn.sm}`}>Use</button>
                        )}
                    </td>
                  </tr>
                  {isOpen && (
                    <tr className="bg-subtle">
                      <td />
                      <td colSpan={4} className="py-2.5 pr-3 text-xs text-muted">
                        <p className="text-ink">p.{f.page_no ?? '?'} · “{f.source_text}”</p>
                        <p className="mt-0.5 tnum">
                          {[f.observed_on && formatDate(f.observed_on), f.reference_range && `Ref ${f.reference_range}`,
                            f.normalized_value != null && `= ${f.normalized_value} ${f.normalized_unit}`,
                            `${CONF[f.confidence]} · ${METHOD[f.method]}`, done && `${done.label}${f.final_value_text ? ` → ${f.final_value_text}` : ''}`]
                            .filter(Boolean).join(' · ')}
                        </p>
                        {f.issues.length > 0 && <ul className="mt-1 space-y-0.5">{f.issues.map((i, n) => <IssueLine key={n} i={i} />)}</ul>}
                      </td>
                    </tr>
                  )}
                </Fragment>
              )
            })}
          </tbody>
        </table>
      )}
      {data.status === 'failed' && <p className="px-4 py-2.5 text-sm text-alert-800">{data.error}</p>}
    </section>
  )
}

function IssueLine({ i }) {
  const s = SEVERITY[i.severity]
  const Icon = s.icon
  return (
    <li className={`flex items-start gap-1.5 text-xs ${s.cls === 'text-muted' ? 'text-muted' : s.cls.replace('700', '800')}`}>
      <Icon size={13} aria-hidden="true" className="mt-px shrink-0" /> <span>{i.message}</span>
    </li>
  )
}
