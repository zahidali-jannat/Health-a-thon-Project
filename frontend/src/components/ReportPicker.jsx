import { ArrowLeft, ExternalLink, FileImage, FileText, Search, Upload, X } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { api, formatDate, formatDateTime, todayIso } from '../api.js'
import { btn, input } from './ui.jsx'
import { useUploadedReports } from './UploadedReports.jsx'

/*
 * "Link a report" - a right-side panel over the page (the table stays visible). Lists EVERY report the patient
 * uploaded, from the page's one cached list: reports of this row's own test first ("Matches this test"), then all
 * others - matching changes the order, never hides a report. Search + type filters, a tooltip with the dates
 * (hover, keyboard focus or tap), a preview of the file with "Link to this value", label corrections, and upload.
 *
 * props: title (what is being linked, e.g. "eGFR 60 mL/min/1.73m² · 20 Sep 2026"), matchType ('egfr' | ...),
 *        linkedId, onLink(report), onUnlink(), onClose(), startPreviewId (open straight on one report)
 */
const TYPES = [['all', 'All'], ['egfr', 'eGFR'], ['hemoglobin', 'Hemoglobin'], ['hba1c', 'HbA1c'], ['sugar', 'Sugar'],
  ['other', 'Other'], ['none', 'Unlabelled']]
const TYPE_OPTIONS = TYPES.filter(([k]) => k !== 'all' && k !== 'none')
const PAGE = 100
const small = 'text-[11px] font-semibold uppercase tracking-wide text-muted'

const byTestDate = (a, b) => (b.test_date ? 1 : 0) - (a.test_date ? 1 : 0)
  || (b.test_date ?? '').localeCompare(a.test_date ?? '') || b.uploaded_at.localeCompare(a.uploaded_at)

export default function ReportPicker({ title, matchType, linkedId, onLink, onUnlink, onClose, startPreviewId }) {
  const { reports, error: loadError, patientId, refresh } = useUploadedReports()
  const [view, setView] = useState(startPreviewId ? 'preview' : 'list')
  const [previewId, setPreviewId] = useState(startPreviewId ?? null)
  const [query, setQuery] = useState('')
  const [type, setType] = useState('all')
  const [limit, setLimit] = useState(PAGE)
  const [confirmUnlink, setConfirmUnlink] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [tip, setTip] = useState(null)              // { id, top, left } - one tooltip at a time
  const panel = useRef(null)
  const searchBox = useRef(null)
  const list = useRef(null)

  // back to where the user was when the panel closes
  useEffect(() => {
    const opener = document.activeElement
    return () => opener?.focus?.()
  }, [])
  useEffect(() => {
    if (view === 'list') searchBox.current?.focus()
  }, [view])
  useEffect(() => {
    const onKey = (e) => {
      if (e.key !== 'Escape') return
      e.stopPropagation()
      if (view !== 'list') setView('list')
      else onClose()
    }
    // a click elsewhere closes it; the paperclip that opened it handles its own toggle
    const away = (e) => { if (!panel.current?.contains(e.target) && !e.target.closest?.('[data-picker-opener]')) onClose() }
    document.addEventListener('keydown', onKey)
    document.addEventListener('pointerdown', away)
    return () => { document.removeEventListener('keydown', onKey); document.removeEventListener('pointerdown', away) }
  }, [view, onClose])

  const all = useMemo(() => reports ?? [], [reports])
  const counts = useMemo(() => Object.fromEntries(TYPES.map(([k]) => [k,
    k === 'all' ? all.length : all.filter((r) => (r.report_type ?? 'none') === k).length])), [all])
  const shown = useMemo(() => {
    const q = query.trim().toLowerCase()
    const hits = all.filter((r) => (type === 'all' || (r.report_type ?? 'none') === type)
      && (!q || r.display_name.toLowerCase().includes(q) || r.report_type_label.toLowerCase().includes(q)
        || r.file_name.toLowerCase().includes(q)))
    return [...hits.filter((r) => r.report_type === matchType).sort(byTestDate),
      ...hits.filter((r) => r.report_type !== matchType).sort(byTestDate)]
  }, [all, type, query, matchType])
  const matches = shown.filter((r) => r.report_type === matchType).length
  const linked = all.find((r) => r.id === linkedId)
  const preview = all.find((r) => r.id === previewId)

  const run = async (fn) => {
    setBusy(true)
    setError('')
    try {
      await fn()
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }
  const open = (id) => { setPreviewId(id); setView('preview'); setConfirmUnlink(false) }
  const moveFocus = (e) => {
    const rows = [...(list.current?.querySelectorAll('[data-report-row]') ?? [])]
    const at = rows.indexOf(document.activeElement)
    const to = { ArrowDown: at + 1, ArrowUp: at - 1, Home: 0, End: rows.length - 1 }[e.key]
    if (to === undefined || !rows.length) return
    e.preventDefault()
    rows[Math.max(0, Math.min(rows.length - 1, to))].focus()
  }
  const focusList = () => {
    list.current?.scrollTo?.({ top: 0 })
    list.current?.querySelector('[data-report-row]')?.focus()
  }

  // Rendered at the page's top level: the paperclip can sit inside another form ("Log data manually"), and a form
  // inside this panel must never submit that one (React passes events up through portals, so stop them here too).
  return createPortal(
    <aside ref={panel} role="dialog" aria-labelledby="picker-title" onSubmit={(e) => e.stopPropagation()}
      className="fade-in fixed inset-y-0 right-0 z-40 flex w-[420px] max-w-full flex-col border-l border-line-strong bg-surface shadow-[-6px_0_16px_rgba(16,24,40,0.07)]">
      <header className="flex items-start gap-2 border-b border-line px-4 py-3">
        <div className="min-w-0 flex-1">
          <h2 id="picker-title" className="text-base font-semibold text-ink">{view === 'upload' ? 'Upload a report' : 'Link a report'}</h2>
          {title && <p className="truncate text-xs text-muted tnum" title={title}>{title}</p>}
        </div>
        <button type="button" onClick={onClose} aria-label="Close report picker" className={`${btn.ghost} ${btn.iconSm} text-muted`}>
          <X size={16} aria-hidden="true" />
        </button>
      </header>

      {(error || loadError) && <p role="alert" className="border-b border-alert-200 bg-alert-50 px-4 py-2 text-sm text-alert-800">{error || loadError}</p>}

      {view === 'preview' && preview && (
        <Preview report={preview} patientId={patientId} linked={preview.id === linkedId} busy={busy}
          onBack={() => setView('list')} onRefresh={refresh}
          onLink={() => run(() => onLink(preview))} />
      )}

      {view === 'upload' && (
        <UploadForm patientId={patientId} onCancel={() => setView('list')}
          onDone={async (r) => { await refresh(); open(r.id) }} />
      )}

      {view === 'list' && (<>
        {linked && (
          <section aria-label="Linked report" className="border-b border-line bg-subtle px-4 py-3">
            <p className={small}>Linked to this value</p>
            <p className="mt-0.5 truncate text-sm font-medium text-ink" title={linked.display_name}>{linked.display_name}</p>
            <p className="text-xs text-muted tnum">{linked.report_type_label} · Test date {linked.test_date ? formatDate(linked.test_date) : 'not recorded'}</p>
            {confirmUnlink ? (
              <div className="mt-2 rounded border border-alert-200 bg-surface px-3 py-2">
                <p className="text-sm text-ink">Unlink this report from the value?</p>
                <div className="mt-2 flex gap-2">
                  <button type="button" disabled={busy} onClick={() => run(onUnlink)} className={`${btn.danger} ${btn.sm}`}>Unlink</button>
                  <button type="button" onClick={() => setConfirmUnlink(false)} className={`${btn.secondary} ${btn.sm}`}>Cancel</button>
                </div>
              </div>
            ) : (
              <div className="mt-2 flex gap-2">
                <button type="button" onClick={focusList} className={`${btn.secondary} ${btn.sm}`}>Change</button>
                <button type="button" onClick={() => setConfirmUnlink(true)} className={`${btn.secondary} ${btn.sm}`}>Unlink</button>
                <button type="button" onClick={() => open(linked.id)} className={`${btn.ghost} ${btn.sm}`}>View</button>
              </div>
            )}
          </section>
        )}

        {reports && all.length === 0 ? (
          <div className="px-4 py-6">
            <p className="text-sm text-muted">This patient hasn’t uploaded any reports yet.</p>
            <button type="button" onClick={() => setView('upload')} className={`${btn.secondary} mt-3`}><Upload size={15} aria-hidden="true" /> Upload</button>
          </div>
        ) : (<>
          <div className="space-y-2 border-b border-line px-4 py-3">
            <div className="relative">
              <Search size={14} aria-hidden="true" className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-muted" />
              <input ref={searchBox} type="search" value={query} onChange={(e) => { setQuery(e.target.value); setLimit(PAGE) }}
                onKeyDown={(e) => { if (e.key === 'ArrowDown') { e.preventDefault(); list.current?.querySelector('[data-report-row]')?.focus() } }}
                aria-label="Search reports by name or type" placeholder="Search name or type" className={`${input} pl-8`} />
            </div>
            <div role="radiogroup" aria-label="Report type" className="flex flex-wrap gap-1.5">
              {TYPES.filter(([k]) => k !== 'other' || counts.other > 0).map(([k, label]) => (
                <button key={k} type="button" role="radio" aria-checked={type === k} onClick={() => { setType(k); setLimit(PAGE) }}
                  className={`rounded border px-2 py-0.5 text-xs tnum ${type === k
                    ? 'border-brand-600 bg-brand-50 font-semibold text-brand-800' : 'border-line text-muted hover:border-line-strong hover:text-ink'}`}>
                  {label} ({counts[k]})
                </button>
              ))}
            </div>
          </div>

          <div ref={list} onKeyDown={moveFocus} className="min-h-0 flex-1 overflow-y-auto" aria-busy={!reports}>
            {!reports && <p className="px-4 py-4 text-sm text-muted">Loading…</p>}
            {reports && shown.length === 0 && <p className="px-4 py-4 text-sm text-muted">No reports match.</p>}
            {shown.slice(0, limit).map((r, i) => (
              <div key={r.id}>
                {i === 0 && matches > 0 && <p className={`${small} border-b border-line bg-subtle px-4 py-1.5`}>Matches this test ({matches})</p>}
                {i === matches && <p className={`${small} border-b border-line bg-subtle px-4 py-1.5`}>{matches ? 'Other reports' : 'All reports'} ({shown.length - matches})</p>}
                <ReportRow report={r} linked={r.id === linkedId} tipShown={tip?.id === r.id} onOpen={() => open(r.id)}
                  onTip={(place) => setTip(place && { id: r.id, ...place })} />
              </div>
            ))}
            {shown.length > limit && (
              <button type="button" onClick={() => setLimit((n) => n + PAGE)} className={`${btn.ghost} m-2`}>
                Show {Math.min(PAGE, shown.length - limit)} more of {shown.length - limit}
              </button>
            )}
          </div>
          {tip && view === 'list' && (() => {
            const r = all.find((x) => x.id === tip.id)
            return r && (
              <div id={`tip-${r.id}`} role="tooltip" style={{ position: 'fixed', top: tip.top, left: tip.left }}
                className="fade-in pointer-events-none z-50 rounded bg-[#1f2933] px-2.5 py-1.5 text-xs leading-5 text-white shadow-sm tnum">
                <div>{r.test_date ? <><span className="text-white/60">Test date:</span> {formatDate(r.test_date)}</> : 'Test date not recorded'}</div>
                <div><span className="text-white/60">Uploaded:</span> {formatDateTime(r.uploaded_at)}</div>
              </div>
            )
          })()}
          <footer className="border-t border-line px-4 py-2">
            <button type="button" onClick={() => setView('upload')} className={`${btn.ghost} ${btn.sm}`}><Upload size={13} aria-hidden="true" /> Upload a report</button>
          </footer>
        </>)}
      </>)}
    </aside>,
    document.body,
  )
}

// One report: name, type, uploader. Its dates show in the panel's tooltip on hover / keyboard focus (after
// ~150 ms) or on a first tap on a touch screen; a second tap (or a click, or Enter) opens the preview.
function ReportRow({ report: r, linked, tipShown, onOpen, onTip }) {
  const timer = useRef(null)
  const pointer = useRef('mouse')
  const row = useRef(null)
  const Icon = r.file_kind === 'pdf' ? FileText : FileImage
  const place = () => {
    const b = row.current.getBoundingClientRect()
    return { top: b.bottom - 4, left: Math.max(8, Math.min(b.left + 44, window.innerWidth - 240)) }
  }
  const show = (delay = 150) => {
    clearTimeout(timer.current)
    timer.current = setTimeout(() => onTip(place()), delay)
  }
  const hide = () => { clearTimeout(timer.current); if (tipShown) onTip(null) }
  useEffect(() => () => clearTimeout(timer.current), [])

  return (
    <div className="border-b border-line">
      <button ref={row} type="button" data-report-row aria-describedby={tipShown ? `tip-${r.id}` : undefined}
        onPointerDown={(e) => { pointer.current = e.pointerType }}
        onMouseEnter={() => show()} onMouseLeave={hide} onFocus={() => show()} onBlur={hide}
        onClick={() => {
          if (pointer.current === 'touch' && !tipShown) { clearTimeout(timer.current); onTip(place()); return }
          hide()
          onOpen()
        }}
        className={`flex w-full items-start gap-3 px-4 py-2.5 text-left hover:bg-subtle focus:bg-subtle focus:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-brand-600 ${linked ? 'bg-brand-50' : ''}`}>
        <Icon size={16} strokeWidth={1.5} aria-hidden="true" className="mt-0.5 shrink-0 text-muted" />
        <span className="min-w-0 flex-1">
          <span className="block truncate text-sm font-medium text-ink" title={r.display_name}>{r.display_name}</span>
          <span className="block truncate text-xs text-muted">
            <span className={r.report_type ? '' : 'italic'}>{r.report_type_label}</span> · {r.uploaded_by_name}
          </span>
        </span>
        <span className="flex shrink-0 flex-col items-end gap-0.5 text-[11px]">
          {linked && <span className="font-semibold uppercase tracking-wide text-brand-700">Linked</span>}
          {r.status === 'not_reviewed' && <span className="text-muted">Not reviewed</span>}
          {!r.usable && <span className="text-alert-800">Not usable</span>}
        </span>
      </button>
    </div>
  )
}

// The file itself (through a short-lived signed link), its details (correctable), and "Link to this value".
function Preview({ report: r, patientId, linked, busy, onBack, onRefresh, onLink }) {
  const [url, setUrl] = useState(null)
  const [fileError, setFileError] = useState('')
  const [editing, setEditing] = useState(false)
  useEffect(() => {
    let live = true
    setUrl(null)
    api.reportFileLink(patientId, r.id).then((l) => live && setUrl(l.url)).catch((e) => live && setFileError(e.message))
    return () => { live = false }
  }, [patientId, r.id])

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="border-b border-line px-4 py-2">
          <button type="button" onClick={onBack} className={`${btn.ghost} ${btn.sm} -ml-2`}><ArrowLeft size={13} aria-hidden="true" /> All reports</button>
        </div>
        {editing ? (
          <EditDetails report={r} patientId={patientId} onCancel={() => setEditing(false)}
            onSaved={async () => { await onRefresh(); setEditing(false) }} />
        ) : (
          <div className="px-4 py-3">
            <p className="text-sm font-semibold text-ink [overflow-wrap:anywhere]">{r.display_name}</p>
            <dl className="mt-2 grid grid-cols-[6.5rem_1fr] gap-x-3 gap-y-1 text-sm">
              <dt className="text-xs text-muted">Type</dt><dd className={r.report_type ? 'text-ink' : 'italic text-muted'}>{r.report_type_label}</dd>
              <dt className="text-xs text-muted">Test date</dt><dd className="tnum text-ink">{r.test_date ? formatDate(r.test_date) : 'Test date not recorded'}</dd>
              <dt className="text-xs text-muted">Uploaded</dt><dd className="tnum text-ink">{formatDateTime(r.uploaded_at)}</dd>
              <dt className="text-xs text-muted">Uploaded by</dt><dd className="text-ink">{r.uploaded_by_name}</dd>
              <dt className="text-xs text-muted">File</dt><dd className="truncate text-muted" title={r.file_name}>{r.file_name}</dd>
            </dl>
            <button type="button" onClick={() => setEditing(true)} className={`${btn.ghost} ${btn.sm} -ml-2 mt-1`}>Edit details</button>
          </div>
        )}
        <div className="px-4 pb-3">
          <div className="h-[46vh] overflow-hidden rounded border border-line bg-subtle">
            {fileError ? <p className="p-3 text-sm text-alert-800">{fileError}</p>
              : !url ? <p className="p-3 text-sm text-muted">Loading the file…</p>
                : r.file_kind === 'pdf' ? <iframe src={url} title={r.display_name} className="block h-full w-full" />
                  : <img src={url} alt={r.display_name} className="mx-auto block h-full w-full object-contain" />}
          </div>
          {url && (
            <a href={url} target="_blank" rel="noreferrer" className="mt-1.5 inline-flex items-center gap-1 text-xs text-brand-700 hover:underline">
              <ExternalLink size={12} aria-hidden="true" /> Open in a new tab
            </a>
          )}
        </div>
      </div>
      <footer className="border-t border-line px-4 py-3">
        {!r.usable && <p className="mb-2 text-xs text-alert-800">This report was marked not usable in review, so it can’t be linked.</p>}
        <button type="button" disabled={busy || linked || !r.usable} onClick={onLink} className={`${btn.primary} w-full`}>
          {linked ? 'Linked to this value' : busy ? 'Linking…' : 'Link to this value'}
        </button>
      </footer>
    </div>
  )
}

function EditDetails({ report: r, patientId, onCancel, onSaved }) {
  const [f, setF] = useState({ display_name: r.display_name, report_type: r.report_type ?? '', test_date: r.test_date ?? '' })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const save = async (e) => {
    e.preventDefault()
    setBusy(true)
    setError('')
    try {
      await api.correctReport(patientId, r.id, { display_name: f.display_name, report_type: f.report_type || null,
        test_date: f.test_date || null })
      await onSaved()
    } catch (e2) {
      setError(e2.message)
      setBusy(false)
    }
  }
  const label = 'block text-xs font-medium text-muted'
  return (
    <form onSubmit={save} className="space-y-2.5 px-4 py-3">
      <label className={label}>Name<input required maxLength={120} value={f.display_name} onChange={(e) => setF({ ...f, display_name: e.target.value })} className={`${input} mt-1`} /></label>
      <label className={label}>Type
        <select value={f.report_type} onChange={(e) => setF({ ...f, report_type: e.target.value })} className={`${input} mt-1`}>
          {!r.report_type && <option value="">Unlabelled</option>}
          {TYPE_OPTIONS.map(([k, l]) => <option key={k} value={k}>{l} report</option>)}
        </select>
      </label>
      <label className={label}>Test date
        <input type="date" max={todayIso()} value={f.test_date} onChange={(e) => setF({ ...f, test_date: e.target.value })} className={`${input} mt-1`} />
      </label>
      {error && <p role="alert" className="text-xs text-alert-800">{error}</p>}
      <div className="flex gap-2">
        <button type="submit" disabled={busy} className={`${btn.primary} ${btn.sm}`}>{busy ? 'Saving…' : 'Save'}</button>
        <button type="button" onClick={onCancel} className={`${btn.secondary} ${btn.sm}`}>Cancel</button>
      </div>
    </form>
  )
}

function UploadForm({ patientId, onCancel, onDone }) {
  const [file, setFile] = useState(null)
  const [f, setF] = useState({ name: '', type: '', date: '' })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const save = async (e) => {
    e.preventDefault()
    setBusy(true)
    setError('')
    try {
      const r = await api.uploadReport(patientId, { file, displayName: f.name, reportType: f.type || null, testDate: f.date || null })
      await onDone(r)
    } catch (e2) {
      setError(e2.message)
      setBusy(false)
    }
  }
  const label = 'block text-xs font-medium text-muted'
  return (
    <form onSubmit={save} className="space-y-2.5 px-4 py-3">
      <label className={label}>File (PDF or photo)
        <input type="file" required accept="application/pdf,image/jpeg,image/png,image/heic"
          onChange={(e) => { const x = e.target.files?.[0] ?? null; setFile(x); if (x && !f.name) setF((s) => ({ ...s, name: x.name.replace(/\.[^.]+$/, '') })) }}
          className="mt-1 block w-full text-sm text-ink file:mr-3 file:rounded file:border file:border-line-strong file:bg-surface file:px-2.5 file:py-1 file:text-sm" />
      </label>
      <label className={label}>Name<input required maxLength={120} value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} className={`${input} mt-1`} /></label>
      <label className={label}>Type
        <select value={f.type} onChange={(e) => setF({ ...f, type: e.target.value })} className={`${input} mt-1`}>
          <option value="">Unlabelled</option>
          {TYPE_OPTIONS.map(([k, l]) => <option key={k} value={k}>{l} report</option>)}
        </select>
      </label>
      <label className={label}>Test date (optional)
        <input type="date" max={todayIso()} value={f.date} onChange={(e) => setF({ ...f, date: e.target.value })} className={`${input} mt-1`} />
      </label>
      {error && <p role="alert" className="text-xs text-alert-800">{error}</p>}
      <div className="flex gap-2">
        <button type="submit" disabled={busy || !file} className={`${btn.primary} ${btn.sm}`}>{busy ? 'Uploading…' : 'Upload'}</button>
        <button type="button" onClick={onCancel} className={`${btn.secondary} ${btn.sm}`}>Cancel</button>
      </div>
    </form>
  )
}
