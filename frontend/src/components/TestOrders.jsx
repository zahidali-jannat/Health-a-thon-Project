import { ChevronDown, FileText, Plus } from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { Link } from 'react-router-dom'
import { api, formatDate, todayIso } from '../api.js'
import { useClinician } from '../auth.jsx'
import DocumentPreview from './DocumentPreview.jsx'
import FhirView from './FhirView.jsx'
import FilterMenu from './FilterMenu.jsx'
import Modal from './Modal.jsx'
import { Badge, btn, ErrorBox, input, Notice, Panel, td, th } from './ui.jsx'

/*
 * "Tests to do" (care team): which tests the patient must do before the next appointment, and where each result
 * stands. Only clinician-verified values are shown as results; an upload waiting for review is only a report.
 */
export const STATUS = {
  ordered: ['neutral', 'Ordered'], sample_collected: ['neutral', 'Sample collected'], result_received: ['info', 'Result received'],
  submitted_by_patient: ['info', 'Uploaded by patient'], verified: ['ok', 'Verified'], closed: ['ok', 'Closed'],
  rejected: ['alert', 'Rejected'], cancelled: ['neutral', 'Cancelled'], not_done: ['neutral', 'Not done'],
}
const ROUTE = { clinic_lab: 'Clinic lab', external: 'Outside lab', either: 'Either' }
const SECTIONS = [['open', 'Open'], ['awaiting_verification', 'Awaiting verification'], ['rejected', 'Rejected'], ['completed', 'Completed']]
const WAITING = ['ordered', 'sample_collected', 'rejected']
export const clinicTime = (iso) => (iso ? new Date(iso).toLocaleString('en-GB', {
  timeZone: 'Asia/Kolkata', day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit',
}) : '—')
const addDays = (iso, n) => {
  const d = new Date(`${iso}T00:00:00`)
  d.setDate(d.getDate() + n)
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}
const newKey = () => (crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(36).slice(2)}`)

export default function TestOrdersSection({ patient, onChanged }) {
  const [view, setView] = useState('tests')
  const [data, setData] = useState(null)
  const [catalog, setCatalog] = useState(null)
  const [adding, setAdding] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const load = useCallback(async () => {
    try {
      setData(await api.patientTestOrders(patient.id))
      setError('')
    } catch (e) {
      setError(e.message)
    }
  }, [patient.id])
  useEffect(() => {
    load()
  }, [load])
  useEffect(() => {
    api.testCatalog().then(setCatalog).catch((e) => setError(e.message))
  }, [])
  const changed = (message) => { setNotice(message || ''); load(); onChanged?.() }

  const items = useMemo(() => (data?.sets ?? []).flatMap((s) => s.items), [data])
  const nxt = data?.next_appointment

  return (
    <section aria-labelledby="tests-title" className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <FilterMenu name="view" value={view} onChange={setView} counts={{ tests: data ? items.length : null }}
          options={[{ id: 'tests', label: 'Tests' }, { id: 'fhir', label: 'FHIR view' }]} />
        {view === 'tests' && <button type="button" onClick={() => setAdding(true)} disabled={!catalog} className={btn.primary}><Plus size={15} aria-hidden="true" /> Add tests</button>}
      </div>
      <h2 id="tests-title" className="sr-only">Tests to do</h2>
      <ErrorBox>{error}</ErrorBox>
      <Notice>{notice}</Notice>

      {view === 'fhir' ? <FhirView patient={patient} /> : (<>
        <p className="text-sm text-ink">
          Next appointment: {nxt ? <strong className="tnum">{formatDate(nxt.date)}{nxt.time ? ` · ${nxt.time}` : ''}</strong> : <strong>none booked</strong>}
          {nxt && <span className="text-muted"> ({nxt.source}{nxt.doctor_name ? `, ${nxt.doctor_name}` : ''})</span>}
          {data && <span className="text-muted"> · tests are due {data.buffer_days} days before it by default</span>}
        </p>

        {data?.unlinked_lab_results.length > 0 && (
          <UnlinkedResults patient={patient} results={data.unlinked_lab_results} items={items} onDone={changed} />
        )}

        {data && items.length === 0 && (
          <Panel title="No tests ordered yet"><p className="px-4 py-3 text-sm text-muted">Use “Add tests” after the consultation.</p></Panel>
        )}
        {SECTIONS.map(([key, label]) => {
          const rows = items.filter((i) => i.section === key)
          if (!rows.length) return null
          return (
            <Panel key={key} title={`${label} (${rows.length})`} bodyClass="">
              <div className="overflow-x-auto">
                <table className="w-full">
                  <thead className="border-b border-line bg-subtle"><tr>
                    <th className={th}>Test</th><th className={th}>Due by</th><th className={th}>Status</th><th className={th}>Route</th>
                    <th className={th}>Report / result</th><th className={th}><span className="sr-only">Actions</span></th>
                  </tr></thead>
                  <tbody className="divide-y divide-line">
                    {rows.map((i) => <ItemRow key={i.id} item={i} patient={patient} demo={catalog?.demo_mode} onDone={changed} onError={setError} />)}
                  </tbody>
                </table>
              </div>
            </Panel>
          )
        })}
      </>)}

      {adding && data && catalog && (
        <AddTests patient={patient} data={data} catalog={catalog} onClose={() => setAdding(false)}
          onSaved={(n) => { setAdding(false); changed(`${n} test${n === 1 ? '' : 's'} ordered.`) }} />
      )}
    </section>
  )
}

// ------------------------------------------------------------------ one item

function ItemRow({ item: i, patient, demo, onDone, onError }) {
  const [mode, setMode] = useState(null)          // 'edit' | 'cancel' | 'waive' | 'review' | 'demo'
  const [preview, setPreview] = useState(false)
  const [f, setF] = useState({})
  const [busy, setBusy] = useState(false)
  const [rowError, setRowError] = useState('')
  const [tone, label] = STATUS[i.status]
  const act = async (fn, message) => {
    setBusy(true)
    setRowError('')
    try {
      const r = await fn()
      setMode(null)
      onDone(r?.warnings?.length ? `${message} ${r.warnings.map((w) => w.message).join(' ')}` : message)
    } catch (e) {
      setRowError(e.message)
      if (e.code === 'stale') onDone()
    } finally {
      setBusy(false)
    }
  }
  const open = (m, init = {}) => { setRowError(''); onError(''); setF(init); setMode(mode === m ? null : m) }

  return (
    <>
      <tr className="align-top">
        <td className={td}>
          <span className="font-medium">{i.name}</span>
          <span className="block text-xs text-muted">
            {[i.fasting_required && 'Fasting', i.priority === 'urgent' && 'Urgent', `ordered by ${i.ordered_by}`].filter(Boolean).join(' · ')}
          </span>
        </td>
        <td className={`${td} whitespace-nowrap tnum`}>
          {formatDate(i.due_by)}
          {i.overdue && <span className="block text-xs font-semibold text-alert-800">Overdue ({-i.days_to_due} day{i.days_to_due === -1 ? '' : 's'})</span>}
          {i.flags.map((fl) => <span key={fl.code} className="block max-w-56 whitespace-normal text-xs text-warn-800">{fl.message}</span>)}
        </td>
        <td className={td}>
          <Badge tone={tone}>{label}</Badge>
          {i.status_reason && <span className="mt-0.5 block max-w-56 text-xs text-muted">{i.status_reason}</span>}
        </td>
        <td className={`${td} whitespace-nowrap text-muted`}>{ROUTE[i.fulfilment_route]}</td>
        <td className={`${td} text-sm`}>
          {i.result && (
            <span className="block">
              <strong className="tnum">{i.result.value} {i.result.unit}</strong>
              <span className="block text-xs text-muted tnum">
                Test {formatDate(i.result.test_date)} · verified {clinicTime(i.result.verified_at)}
              </span>
              <span className="block text-xs text-muted">{i.result.lab_name} · {i.result.source === 'clinic_lab_system' ? 'clinic lab' : 'patient upload'}</span>
            </span>
          )}
          {i.report && (
            <button type="button" onClick={() => setPreview(true)} className="mt-0.5 inline-flex items-center gap-1 text-xs text-brand-700 hover:underline">
              <FileText size={13} aria-hidden="true" /> Report from {i.report.lab_name || 'outside lab'} · uploaded {clinicTime(i.report.uploaded_at)}
            </button>
          )}
          {i.review && <span className="block text-xs text-warn-800">Clinic lab sent {i.review.value} {i.review.unit} - needs checking</span>}
          {i.suggested_next_due && <span className="block text-xs text-muted">Repeat suggested around {formatDate(i.suggested_next_due)}</span>}
          {!i.result && !i.report && !i.review && <span className="text-muted">—</span>}
        </td>
        <td className={`${td} whitespace-nowrap text-right`}>
          <span className="inline-flex flex-wrap justify-end gap-1.5">
            {i.status === 'submitted_by_patient' && i.report && (
              <Link to={`/care-team/reports/${patient.id}/${i.report.id}`} className={`${btn.primary} ${btn.sm}`}>Verify report</Link>
            )}
            {i.status === 'result_received' && i.review && <button type="button" onClick={() => open('review')} className={`${btn.primary} ${btn.sm}`}>Check result</button>}
            <ActionsMenu label={i.name} items={[
              ...(WAITING.includes(i.status) ? [
                { key: 'edit', label: 'Edit', hint: 'Due date, priority, route, instructions',
                  onSelect: () => open('edit', { due_by: i.due_by, priority: i.priority, fulfilment_route: i.fulfilment_route, instructions: i.instructions || '', reason: '' }) },
                { key: 'waive', label: 'Waive', hint: 'Not needed - needs a reason', onSelect: () => open('waive', { reason: '' }) },
                { key: 'cancel', label: 'Cancel', hint: 'Remove the order - needs a reason', danger: true, onSelect: () => open('cancel', { reason: '' }) },
              ] : []),
              ...(i.status === 'verified' ? [{ key: 'close', label: 'Close', hint: 'The result was discussed',
                onSelect: () => act(() => api.closeTestItem(i.id, i.version), `${i.name} closed.`) }] : []),
              ...(demo && WAITING.includes(i.status) && i.fulfilment_route !== 'external'
                ? [{ key: 'demo', label: 'Demo simulator', hint: 'Send a made-up clinic lab result', onSelect: () => open('demo', { value: '' }) }] : []),
            ]} disabled={busy} />
          </span>
        </td>
      </tr>
      {mode && (
        <tr className="bg-subtle">
          <td colSpan={6} className="px-3 py-3">
            {mode === 'edit' && (
              <div className="grid gap-3 sm:grid-cols-4">
                <label className="text-xs font-medium text-muted">Due by<input type="date" min={todayIso()} value={f.due_by} onChange={(e) => setF({ ...f, due_by: e.target.value })} className={`${input} mt-1`} /></label>
                <label className="text-xs font-medium text-muted">Priority
                  <select value={f.priority} onChange={(e) => setF({ ...f, priority: e.target.value })} className={`${input} mt-1`}><option value="routine">Routine</option><option value="urgent">Urgent</option></select></label>
                <label className="text-xs font-medium text-muted">Route
                  <select value={f.fulfilment_route} onChange={(e) => setF({ ...f, fulfilment_route: e.target.value })} className={`${input} mt-1`}>
                    {Object.entries(ROUTE).map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></label>
                <label className="text-xs font-medium text-muted">Reason for the change (optional)<input maxLength={200} value={f.reason} onChange={(e) => setF({ ...f, reason: e.target.value })} className={`${input} mt-1`} /></label>
                <label className="text-xs font-medium text-muted sm:col-span-4">Instructions for the patient<input maxLength={500} value={f.instructions} onChange={(e) => setF({ ...f, instructions: e.target.value })} className={`${input} mt-1`} /></label>
                <div className="flex gap-2 sm:col-span-4">
                  <button type="button" disabled={busy} className={`${btn.primary} ${btn.sm}`}
                    onClick={() => act(() => api.editTestItem(i.id, { version: i.version, ...Object.fromEntries(
                      ['due_by', 'priority', 'fulfilment_route', 'instructions'].filter((k) => (f[k] || null) !== (i[k] || null)).map((k) => [k, f[k]])),
                      ...(f.reason ? { reason: f.reason } : {}) }), `${i.name} updated.`)}>Save</button>
                  <button type="button" onClick={() => setMode(null)} className={`${btn.secondary} ${btn.sm}`}>Cancel</button>
                </div>
              </div>
            )}
            {(mode === 'cancel' || mode === 'waive') && (
              <div className="flex flex-wrap items-end gap-2">
                <label className="min-w-64 flex-1 text-xs font-medium text-muted">
                  {mode === 'cancel' ? 'Why is this test cancelled?' : 'Why is this test not needed (waived)?'}
                  <input maxLength={200} value={f.reason} onChange={(e) => setF({ ...f, reason: e.target.value })} className={`${input} mt-1`} />
                </label>
                <button type="button" disabled={busy || f.reason.trim().length < 3}
                  onClick={() => act(() => (mode === 'cancel' ? api.cancelTestItem : api.waiveTestItem)(i.id, i.version, f.reason), `${i.name} ${mode === 'cancel' ? 'cancelled' : 'waived'}.`)}
                  className={`${mode === 'cancel' ? btn.danger : btn.primary} ${btn.sm}`}>{mode === 'cancel' ? 'Cancel test' : 'Waive test'}</button>
                <button type="button" onClick={() => setMode(null)} className={`${btn.secondary} ${btn.sm}`}>Keep</button>
              </div>
            )}
            {mode === 'review' && i.review && (
              <div className="space-y-2 text-sm">
                <p>Clinic lab result: <strong className="tnum">{i.review.value} {i.review.unit}</strong> (test {formatDate(i.review.test_date)}, {i.review.lab_name}).
                  It is outside the usual range or unit for {i.name}, so it was not accepted automatically.</p>
                <div className="flex flex-wrap items-end gap-2">
                  <button type="button" disabled={busy} onClick={() => act(() => api.acceptLabResult(patient.id, i.review.ingestion_id), 'Result accepted and verified.')}
                    className={`${btn.primary} ${btn.sm}`}>Accept as correct</button>
                  <label className="min-w-64 flex-1 text-xs font-medium text-muted">Or reject - reason the patient will see
                    <input maxLength={200} value={f.reason || ''} onChange={(e) => setF({ ...f, reason: e.target.value })} className={`${input} mt-1`} /></label>
                  <button type="button" disabled={busy || (f.reason || '').trim().length < 3}
                    onClick={() => act(() => api.rejectLabResult(patient.id, i.review.ingestion_id, f.reason), 'Result rejected. The patient was told why.')}
                    className={`${btn.danger} ${btn.sm}`}>Reject</button>
                </div>
              </div>
            )}
            {mode === 'demo' && (
              <div className="flex flex-wrap items-end gap-2">
                <p className="w-full text-xs font-semibold uppercase tracking-wide text-warn-800">Demo simulator - not a real lab result</p>
                <label className="text-xs font-medium text-muted">Value ({i.result?.unit || 'in the test’s usual unit'})
                  <input inputMode="decimal" value={f.value} onChange={(e) => setF({ ...f, value: e.target.value })} className={`${input} mt-1 w-32`} /></label>
                <button type="button" disabled={busy || f.value === '' || Number.isNaN(Number(f.value))}
                  onClick={() => act(async () => { const r = await api.demoLabResult(patient.id, i.id, Number(f.value)); return { warnings: [{ message: `Outcome: ${r.outcome.replace('_', ' ')}.` }] } }, 'Simulated clinic lab result sent.')}
                  className={`${btn.secondary} ${btn.sm}`}>Send simulated result</button>
                <button type="button" onClick={() => setMode(null)} className={`${btn.ghost} ${btn.sm}`}>Close</button>
              </div>
            )}
            {rowError && <p role="alert" className="mt-2 text-sm text-alert-800">{rowError}</p>}
          </td>
        </tr>
      )}
      {preview && i.report && (
        <DocumentPreview patientId={patient.id} onClose={() => setPreview(false)}
          report={{ id: i.report.id, test_label: i.name, upload_date: i.report.uploaded_at, content_type: i.report.content_type }} />
      )}
    </>
  )
}

// One "Actions" button per test; its choices open over the page (a table's scroll area can't cut them off).
function ActionsMenu({ label, items, disabled }) {
  const [place, setPlace] = useState(null)
  const button = useRef(null)
  const menu = useRef(null)
  const where = () => {
    const r = button.current.getBoundingClientRect()
    const right = Math.max(8, window.innerWidth - r.right)
    return window.innerHeight - r.bottom > 60 + items.length * 52 ? { top: r.bottom + 4, right } : { bottom: window.innerHeight - r.top + 4, right }
  }
  useEffect(() => {
    if (!place) return undefined
    menu.current?.querySelector('[role=menuitem]')?.focus()
    const away = (e) => { if (!button.current?.contains(e.target) && !menu.current?.contains(e.target)) setPlace(null) }
    const keys = (e) => {
      if (e.key === 'Escape') { setPlace(null); button.current?.focus() }
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        const all = [...menu.current.querySelectorAll('[role=menuitem]')]
        const at = all.indexOf(document.activeElement)
        all[(at + (e.key === 'ArrowDown' ? 1 : all.length - 1)) % all.length]?.focus()
        e.preventDefault()
      }
    }
    const follow = () => setPlace(where())
    document.addEventListener('mousedown', away)
    document.addEventListener('keydown', keys)
    window.addEventListener('scroll', follow, true)
    window.addEventListener('resize', follow)
    return () => {
      document.removeEventListener('mousedown', away); document.removeEventListener('keydown', keys)
      window.removeEventListener('scroll', follow, true); window.removeEventListener('resize', follow)
    }
  }, [place])     // eslint-disable-line react-hooks/exhaustive-deps
  if (!items.length) return null
  return (
    <>
      <button ref={button} type="button" disabled={disabled} aria-haspopup="menu" aria-expanded={Boolean(place)} aria-label={`Actions for ${label}`}
        onClick={() => setPlace((p) => (p ? null : where()))} className={`${btn.secondary} ${btn.sm}`}>
        Actions <ChevronDown size={13} aria-hidden="true" className={place ? 'rotate-180' : ''} />
      </button>
      {place && createPortal(
        <div ref={menu} role="menu" aria-label={`Actions for ${label}`} style={{ position: 'fixed', ...place }}
          className="fade-in z-40 w-64 rounded-md border border-line bg-surface py-1 text-left shadow-sm">
          {items.map((it) => (
            <button key={it.key} type="button" role="menuitem" onClick={() => { setPlace(null); it.onSelect() }}
              className="block w-full px-3 py-2 text-left hover:bg-subtle focus:bg-subtle focus:outline-none">
              <span className={`block text-sm font-medium ${it.danger ? 'text-alert-800' : 'text-ink'}`}>{it.label}</span>
              {it.hint && <span className="block text-xs text-muted">{it.hint}</span>}
            </button>
          ))}
        </div>,
        document.body,
      )}
    </>
  )
}

// ------------------------------------------------------------------ clinic-lab results with no order

function UnlinkedResults({ patient, results, items, onDone }) {
  const [busy, setBusy] = useState(null)
  const [error, setError] = useState('')
  const [reasons, setReasons] = useState({})
  const [links, setLinks] = useState({})
  const run = async (key, fn, message) => {
    setBusy(key)
    setError('')
    try {
      await fn()
      onDone(message)
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(null)
    }
  }
  return (
    <Panel title={`Clinic lab results with no order (${results.length})`} description="Link each to an open order, accept it on its own, or reject it" bodyClass="">
      {error && <div className="px-4 pt-3"><ErrorBox>{error}</ErrorBox></div>}
      <ul className="divide-y divide-line">
        {results.map((g) => {
          const open = items.filter((i) => WAITING.includes(i.status) && i.code === g.test_code)
          const odd = (g.plausible_low != null && g.value < g.plausible_low) || (g.plausible_high != null && g.value > g.plausible_high)
          return (
            <li key={g.id} className="flex flex-wrap items-end gap-2 px-4 py-3 text-sm">
              <span className="min-w-52 flex-1">
                <strong>{g.display_name}</strong> <span className="tnum">{g.value} {g.unit}</span>
                <span className="block text-xs text-muted tnum">Test {formatDate(g.test_date)} · {g.lab_name} · received {clinicTime(g.received_at)}</span>
                {odd && <span className="block text-xs font-semibold text-warn-800">Outside the usual range - check before accepting.</span>}
              </span>
              {open.length > 0 && (
                <select aria-label="Link to order" value={links[g.id] ?? ''} onChange={(e) => setLinks({ ...links, [g.id]: e.target.value })} className={`${input} w-auto!`}>
                  <option value="">No order</option>
                  {open.map((i) => <option key={i.id} value={i.id}>{i.name} · due {formatDate(i.due_by)}</option>)}
                </select>
              )}
              <button type="button" disabled={busy !== null} onClick={() => run(g.id, () => api.acceptLabResult(patient.id, g.id, links[g.id]), 'Lab result accepted.')}
                className={`${btn.primary} ${btn.sm}`}>Accept</button>
              <input aria-label="Reason to reject" placeholder="Reason to reject" maxLength={200} value={reasons[g.id] ?? ''}
                onChange={(e) => setReasons({ ...reasons, [g.id]: e.target.value })} className={`${input} w-48!`} />
              <button type="button" disabled={busy !== null || (reasons[g.id] ?? '').trim().length < 3}
                onClick={() => run(g.id, () => api.rejectLabResult(patient.id, g.id, reasons[g.id]), 'Lab result rejected.')} className={`${btn.danger} ${btn.sm}`}>Reject</button>
            </li>
          )
        })}
      </ul>
    </Panel>
  )
}

// ------------------------------------------------------------------ adding tests: form -> confirmation summary -> save

function AddTests({ patient, data, catalog, onClose, onSaved }) {
  const { user } = useClinician() ?? {}
  const [doctors, setDoctors] = useState([])
  const [doctorId, setDoctorId] = useState('')
  const [rows, setRows] = useState([])
  const [note, setNote] = useState('')
  const [plan, setPlan] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [key] = useState(newKey)                 // one Idempotency-Key for this order: a double click saves it once
  const nxt = data.next_appointment
  const defaultDue = nxt ? [addDays(nxt.date, -data.buffer_days), todayIso()].sort().at(-1) : ''
  useEffect(() => {
    api.doctors().then((list) => setDoctors(list)).catch(() => {})
  }, [])

  const toggle = (t) => setRows((rs) => (rs.some((r) => r.test_catalog_id === t.id) ? rs.filter((r) => r.test_catalog_id !== t.id)
    : [...rs, { test_catalog_id: t.id, name: t.display_name, is_other: t.is_other, fasting: t.fasting_required, custom_name: '',
      due_by: defaultDue, priority: 'routine', fulfilment_route: 'either', instructions: t.default_instructions || '' }]))
  const set = (id, patch) => setRows((rs) => rs.map((r) => (r.test_catalog_id === id ? { ...r, ...patch } : r)))
  const body = () => ({
    ordered_by_doctor_id: doctorId || null, note: note || null, acknowledge_warnings: true,
    items: rows.map((r) => ({ test_catalog_id: r.test_catalog_id, custom_name: r.is_other ? r.custom_name : null, due_by: r.due_by || null,
      priority: r.priority, fulfilment_route: r.fulfilment_route, instructions: r.instructions || null })),
  })
  const review = async () => {
    setBusy(true)
    setError('')
    try {
      setPlan(await api.previewTestOrder(patient.id, body()))
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }
  const save = async () => {
    setBusy(true)
    setError('')
    try {
      const r = await api.createTestOrder(patient.id, body(), key)
      onSaved(r.items)
    } catch (e) {
      setError(e.message)
      if (e.data?.plan) setPlan(e.data.plan)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal title={plan ? 'Check before saving' : `Add tests · ${patient.full_name}`} width="56rem" onClose={onClose}
      footer={(close) => (plan ? (<>
        <button type="button" onClick={() => setPlan(null)} className={btn.secondary}>Back to edit</button>
        <button type="button" disabled={busy || !plan.ok} onClick={save} className={btn.primary}>{busy ? 'Saving…' : `Save ${plan.items.length} test${plan.items.length === 1 ? '' : 's'}`}</button>
      </>) : (<>
        <button type="button" onClick={close} className={btn.secondary}>Cancel</button>
        <button type="button" disabled={busy || rows.length === 0} onClick={review} className={btn.primary}>{busy ? 'Checking…' : 'Review'}</button>
      </>))}>
      <div className="space-y-4 px-4 py-4">
        <p className="rounded border border-line bg-subtle px-3 py-2 text-sm text-ink">
          Next appointment: {nxt ? <strong className="tnum">{formatDate(nxt.date)}</strong> : <strong>none booked - choose a due date for each test</strong>}
          {nxt && <> · default due date <strong className="tnum">{formatDate(defaultDue)}</strong> ({data.buffer_days} days before, to leave time to verify)</>}
        </p>
        <ErrorBox>{error}</ErrorBox>

        {plan ? (
          <div className="space-y-2">
            <table className="w-full rounded border border-line">
              <thead className="border-b border-line bg-subtle"><tr><th className={th}>Test</th><th className={th}>Due by</th><th className={th}>Priority</th><th className={th}>Route</th></tr></thead>
              <tbody className="divide-y divide-line">
                {plan.items.map((i, n) => (
                  <tr key={n} className="align-top">
                    <td className={td}>{i.name}{i.fasting_required && <span className="block text-xs text-muted">Fasting</span>}
                      {i.errors.map((e) => <span key={e.code} className="block text-xs font-semibold text-alert-800">{e.message}</span>)}
                      {i.warnings.map((w) => <span key={w.code} className="block text-xs text-warn-800">{w.message}</span>)}</td>
                    <td className={`${td} whitespace-nowrap tnum`}>{i.due_by ? formatDate(i.due_by) : '—'}</td>
                    <td className={td}>{i.priority === 'urgent' ? 'Urgent' : 'Routine'}</td>
                    <td className={td}>{ROUTE[i.fulfilment_route]}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="text-sm text-ink">Appointment: <strong className="tnum">{plan.next_appointment ? formatDate(plan.next_appointment.date) : 'none booked'}</strong></p>
            {plan.has_warnings && plan.ok && <p className="text-sm text-warn-800">Saving accepts the warnings above.</p>}
            {!plan.ok && <p className="text-sm font-semibold text-alert-800">Fix the errors above before saving.</p>}
          </div>
        ) : (<>
          <div className="grid gap-3 sm:grid-cols-2">
            <label className="text-xs font-medium text-muted">Ordered by
              <select value={doctorId} onChange={(e) => setDoctorId(e.target.value)} className={`${input} mt-1`}>
                <option value="">{user?.role === 'care_team' ? "The patient's doctor" : 'Me'}</option>
                {doctors.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
              </select></label>
            <label className="text-xs font-medium text-muted">Note (optional)<input maxLength={500} value={note} onChange={(e) => setNote(e.target.value)} className={`${input} mt-1`} /></label>
          </div>
          <fieldset>
            <legend className="text-sm font-semibold text-ink">Tests</legend>
            <div className="mt-1.5 grid gap-1.5 sm:grid-cols-3">
              {catalog.tests.map((t) => (
                <label key={t.id} className="flex items-center gap-2 rounded border border-line px-2.5 py-1.5 text-sm text-ink hover:bg-subtle">
                  <input type="checkbox" checked={rows.some((r) => r.test_catalog_id === t.id)} onChange={() => toggle(t)} className="accent-brand-600" />
                  {t.display_name}{t.fasting_required && <span className="text-xs text-muted">· fasting</span>}
                </label>
              ))}
            </div>
          </fieldset>
          {rows.length > 0 && (
            <div className="space-y-2">
              {rows.map((r) => (
                <div key={r.test_catalog_id} className="grid gap-2 rounded border border-line px-3 py-2.5 sm:grid-cols-[minmax(0,1.3fr)_9.5rem_7rem_8rem]">
                  <div className="text-sm">
                    <strong>{r.name}</strong>{r.fasting && <span className="ml-1.5 text-xs text-muted">Fasting needed</span>}
                    {r.is_other && <input aria-label="Name of the test" placeholder="Name of the test" maxLength={80} value={r.custom_name}
                      onChange={(e) => set(r.test_catalog_id, { custom_name: e.target.value })} className={`${input} mt-1`} />}
                    <input aria-label={`${r.name} instructions`} placeholder="Instructions for the patient" maxLength={500} value={r.instructions}
                      onChange={(e) => set(r.test_catalog_id, { instructions: e.target.value })} className={`${input} mt-1`} />
                  </div>
                  <label className="text-xs font-medium text-muted">Due by<input type="date" min={todayIso()} max={nxt?.date} value={r.due_by}
                    onChange={(e) => set(r.test_catalog_id, { due_by: e.target.value })} className={`${input} mt-1`} /></label>
                  <label className="text-xs font-medium text-muted">Priority<select value={r.priority} onChange={(e) => set(r.test_catalog_id, { priority: e.target.value })} className={`${input} mt-1`}>
                    <option value="routine">Routine</option><option value="urgent">Urgent</option></select></label>
                  <label className="text-xs font-medium text-muted">Route<select value={r.fulfilment_route} onChange={(e) => set(r.test_catalog_id, { fulfilment_route: e.target.value })} className={`${input} mt-1`}>
                    {Object.entries(ROUTE).map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></label>
                </div>
              ))}
            </div>
          )}
        </>)}
      </div>
    </Modal>
  )
}

// ------------------------------------------------------------------ clinic dashboard: overdue tests

export function OverdueTestsPanel() {
  const [doctors, setDoctors] = useState([])
  const [filters, setFilters] = useState({ doctor_id: '', within_days: '' })
  const [data, setData] = useState(null)
  const [error, setError] = useState('')
  useEffect(() => {
    api.doctors().then(setDoctors).catch(() => {})
  }, [])
  useEffect(() => {
    api.overdueTests(filters).then(setData).catch((e) => setError(e.message))
  }, [filters])
  return (
    <Panel title={`Tests overdue before appointment${data ? ` (${data.total})` : ''}`} bodyClass=""
      actions={(
        <span className="flex flex-wrap gap-2">
          <select aria-label="Ordered by" value={filters.doctor_id} onChange={(e) => setFilters({ ...filters, doctor_id: e.target.value })} className={`${input} h-8! w-auto! text-xs!`}>
            <option value="">All doctors</option>{doctors.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}</select>
          <select aria-label="Appointment within" value={filters.within_days} onChange={(e) => setFilters({ ...filters, within_days: e.target.value })} className={`${input} h-8! w-auto! text-xs!`}>
            <option value="">Any appointment date</option><option value="3">Appointment within 3 days</option>
            <option value="7">Within 7 days</option><option value="14">Within 14 days</option><option value="30">Within 30 days</option></select>
        </span>
      )}>
      {error && <div className="px-4 pt-3"><ErrorBox>{error}</ErrorBox></div>}
      {data && data.items.length === 0 && <p className="px-4 py-4 text-sm text-muted">No overdue tests.</p>}
      {data?.items.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full">
            <thead className="border-b border-line bg-subtle"><tr>
              <th className={th}>Patient</th><th className={th}>Test</th><th className={th}>Due by</th><th className={th}>Next appointment</th><th className={th}>Ordered by</th>
            </tr></thead>
            <tbody className="divide-y divide-line">
              {data.items.map((r) => (
                <tr key={r.id} className="hover:bg-subtle">
                  <td className={td}><Link to={`/care-team/patients/${r.patient_id}?tab=tests`} className="font-medium hover:text-brand-700 hover:underline">{r.patient_name}</Link>
                    <span className="block text-xs text-muted tnum">{r.patient_code}</span></td>
                  <td className={td}>{r.test}{r.priority === 'urgent' && <span className="block text-xs text-muted">Urgent</span>}</td>
                  <td className={`${td} whitespace-nowrap tnum`}>{formatDate(r.due_by)}<span className="block text-xs font-semibold text-alert-800">Overdue {r.days_overdue} day{r.days_overdue === 1 ? '' : 's'}</span></td>
                  <td className={`${td} whitespace-nowrap tnum`}>{r.next_appointment_date ? <>{formatDate(r.next_appointment_date)}<span className="block text-xs text-muted">in {r.days_to_appointment} day{r.days_to_appointment === 1 ? '' : 's'}</span></> : <span className="text-muted">None booked</span>}</td>
                  <td className={`${td} text-muted`}>{r.ordered_by}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  )
}
