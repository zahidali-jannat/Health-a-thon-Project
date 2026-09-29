import {
  AlertTriangle, Building2, CheckCircle2, ChevronDown, ChevronRight, ClipboardList, ClipboardPen, Clock, Database, FileImage, FlaskConical, FolderOpen, IdCard, PenLine, Pencil, Globe, History, KeyRound, OctagonAlert, ReceiptText, Smartphone, XCircle,
} from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { useClinician } from '../auth.jsx'
import { api, daysBetween, formatAbha, formatDate, formatDateTime, formatTime, todayIso } from '../api.js'
import LabResults from '../components/LabResults.jsx'
import Modal from '../components/Modal.jsx'
import DocumentPreview from '../components/DocumentPreview.jsx'
import EnterResults from '../components/EnterResults.jsx'
import ReportLink, { PaperclipButton } from '../components/ReportLink.jsx'
import ReportPicker from '../components/ReportPicker.jsx'
import { UploadedReportsProvider } from '../components/UploadedReports.jsx'
import {
  AppShell, Badge, btn, ErrorBox, Field, input, LEVELS, Notice, Panel, PatientCredentials, SignalFacts, td, th, TYPE, PanelTier,
} from '../components/ui.jsx'

const TABS = ['summary', 'labs', 'reports', 'sources', 'record', 'values', 'bills']

export default function PatientDetail() {
  const { id } = useParams()
  const [params, setParams] = useSearchParams()
  const tab = TABS.includes(params.get('tab')) ? params.get('tab') : 'summary'
  const setTab = (t) => setParams(t === 'summary' ? {} : { tab: t }, { replace: true })
  const [patient, setPatient] = useState(null)
  // "Priority Details" lives in the Summary tab. Whether it is open, and which of its rows are expanded, is pure view
  // state kept here - so switching tabs or hiding the panel never loses it. The flags themselves come from
  // patient.assessment, which only changes when the patient record is reloaded.
  const [priorityOpen, setPriorityOpen] = useState(false)   // collapsed on every page load
  const [priorityRows, setPriorityRows] = useState({})
  const [error, setError] = useState('')

  const reload = useCallback(async () => {
    try {
      setPatient(await api.patient(id))
      setError('')
    } catch (e) {
      setError(e.message)
    }
  }, [id])

  useEffect(() => {
    reload()
  }, [reload])

  if (!patient) {
    return (
      <AppShell breadcrumbs={[{ label: 'Patients', to: '/care-team/patients' }, { label: '…' }]}>
        {error ? <ErrorBox>{error}</ErrorBox> : <p className="text-sm text-muted">Loading patient…</p>}
      </AppShell>
    )
  }

  const a = patient.assessment
  return (
    <AppShell
      breadcrumbs={[{ label: 'Patients', to: '/care-team/patients' }, { label: patient.full_name }]}
      meta={<>Data as of {formatDate(patient.as_of)}</>}
      title={<span className="flex flex-wrap items-center gap-2"><span className={TYPE.identity}>{patient.full_name}</span>
        <PriorityBadge level={a.level} open={tab === 'summary' && priorityOpen} onToggle={() => {
          if (tab === 'summary') setPriorityOpen((o) => !o)
          else { setTab('summary'); setPriorityOpen(true) }   // from another tab: go to Summary and show it
        }} /></span>}
      subtitle={<ClinicalChips patient={patient} />}
      actions={<PatientDetailsButton patient={patient} />}>

      {error && <div className="mb-3"><ErrorBox>{error}</ErrorBox></div>}

      <UploadedReportsProvider patientId={patient.id}>
      <div>
        <SectionCards value={tab} onChange={setTab} />
        <PanelTier.Provider value="section">
        <div role="tabpanel" id="record-section" aria-labelledby={`section-${tab}`} className="pt-4">
          {tab === 'summary' && <UpcomingAppointments patient={patient} />}
          {tab === 'summary' && (
            <PriorityDetails patient={patient} open={priorityOpen} onToggle={() => setPriorityOpen((o) => !o)}
              rowsOpen={priorityRows} setRowsOpen={setPriorityRows} />
          )}
          {tab === 'labs' && <LabResults patient={patient} />}
          {tab === 'reports' && <DocumentsAndReports patient={patient} onChanged={reload} />}
          {tab === 'sources' && <DataSources patient={patient} onChanged={reload} />}
          {tab === 'record' && (
            <div className="space-y-4">
              <ManualEntry patientId={patient.id} onSaved={setPatient} onDone={() => setTab('summary')} />
            </div>
          )}
          {tab === 'values' && <ManualValuesPanel patientId={patient.id} refresh={patient} />}
          {tab === 'bills' && <MedicalBills patient={patient} onChanged={reload} />}
        </div>
        </PanelTier.Provider>
      </div>
      </UploadedReportsProvider>
    </AppShell>
  )
}

// The patient record's sections as separate boxes, each with a short, real summary. Clicking a box opens that
// section below it. Titles only. (Same sections and URLs as before - ?tab=summary|labs|reports|sources|record.)
function SectionCards({ value, onChange }) {
  const cards = [
    { id: 'summary', title: 'Overview', icon: ClipboardList },
    { id: 'labs', title: 'Lab Result', icon: FlaskConical },
    { id: 'reports', title: 'Reports', icon: FolderOpen },
    { id: 'sources', title: 'Data Sources', icon: Database },
    { id: 'record', title: 'Record Manually', icon: PenLine },
    { id: 'values', title: 'Entered Values', icon: ClipboardPen },
    { id: 'bills', title: 'Medical Bills', icon: ReceiptText },
  ]
  return (
    <div role="tablist" aria-label="Patient record" className="grid grid-cols-2 gap-3 md:grid-cols-4 min-[1360px]:flex">
      {cards.map(({ id, title, icon: Icon }) => {
        const active = id === value
        return (
          <button key={id} id={`section-${id}`} type="button" role="tab" aria-selected={active} aria-controls="record-section"
            onClick={() => onChange(id)}
            className={`flex min-h-16 items-center justify-center rounded-lg border px-2 py-3 text-center min-[1360px]:min-w-max min-[1360px]:flex-1 min-[1360px]:basis-0 ${active
              ? 'border-brand-600 bg-brand-50 ring-1 ring-brand-600' : 'border-line bg-surface hover:border-line-strong hover:bg-subtle'}`}>
            <span className={`flex items-center gap-2 whitespace-nowrap ${TYPE.section} ${active ? 'text-brand-800!' : ''}`}>
              <Icon size={16} aria-hidden="true" className={`shrink-0 ${active ? 'text-brand-700' : 'text-muted'}`} /> {title}
            </span>
          </button>
        )
      })}
    </div>
  )
}

// Demographic / admin details: kept off the main screen (it is for the consultation) and shown on demand.
function identityItems(p) {
  return [
    ['Patient ID', p.patient_code],
    ['Age · Sex', `${p.age != null ? `${p.age} y` : '—'} · ${{ M: 'Male', F: 'Female', O: 'Other' }[p.sex] ?? '—'}`,
      p.age_source === 'told by patient' && 'age told by patient'],
    ['Date of birth', formatDate(p.date_of_birth)],
    ['Mobile', p.phone ?? '—'],
    ['ABHA', formatAbha(p.abha_number)],
    ['Last visit', formatDate(p.last_visit), p.last_visit && `${daysBetween(p.last_visit, p.as_of)} days ago`],
    ['Next visit', p.next_visit ? formatDate(p.next_visit) : 'Not booked', p.next_visit && `in ${daysBetween(p.as_of, p.next_visit)} days`],
  ]
}

// "Details": the ONE control in the corner. It opens a small menu - Patient Detail, Sharing, Access - and each item
// opens its own dialog. Nothing is ever placed back on the clinical screen; every dialog starts closed on page load.
const DETAIL_MENU = [['detail', 'Patient Detail'], ['sharing', 'Sharing'], ['access', 'Access']]

function PatientDetailsButton({ patient: p }) {
  const [menu, setMenu] = useState(false)
  const [panel, setPanel] = useState(null)
  const [focus, setFocus] = useState(0)
  const wrap = useRef(null)
  const button = useRef(null)
  const items = useRef([])

  useEffect(() => {
    if (!menu) return undefined
    items.current[focus]?.focus()
    const outside = (e) => { if (!wrap.current?.contains(e.target)) setMenu(false) }
    document.addEventListener('pointerdown', outside)
    return () => document.removeEventListener('pointerdown', outside)
  }, [menu, focus])

  const choose = (id) => {
    setMenu(false)
    setPanel(id)
  }
  const onKeyDown = (e) => {
    if (e.key === 'Escape') { e.preventDefault(); setMenu(false); button.current?.focus() }
    else if (e.key === 'ArrowDown') { e.preventDefault(); setFocus((f) => (f + 1) % DETAIL_MENU.length) }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setFocus((f) => (f + DETAIL_MENU.length - 1) % DETAIL_MENU.length) }
    else if (e.key === 'Tab') setMenu(false)
  }

  return (
    <div ref={wrap} className="relative">
      <button ref={button} type="button" aria-haspopup="menu" aria-expanded={menu} aria-controls={menu ? 'details-menu' : undefined}
        onClick={() => { setFocus(0); setMenu((m) => !m) }} className={`${btn.secondary} h-10! px-4! text-base! font-bold!`}>
        <IdCard size={17} aria-hidden="true" /> Details
        <ChevronDown size={15} aria-hidden="true" className={`text-muted ${menu ? 'rotate-180' : ''}`} />
      </button>
      {menu && (
        <ul id="details-menu" role="menu" aria-label="Details" onKeyDown={onKeyDown}
          className="absolute right-0 top-full z-30 mt-1 w-40 rounded-md border border-line-strong bg-surface py-1 text-sm shadow-sm">
          {DETAIL_MENU.map(([id, label], i) => (
            <li key={id} role="none">
              <button ref={(el) => { items.current[i] = el }} type="button" role="menuitem" tabIndex={i === focus ? 0 : -1}
                onClick={() => choose(id)} onMouseEnter={() => setFocus(i)}
                className="block w-full px-3 py-1.5 text-left text-ink hover:bg-subtle focus:bg-subtle focus:outline-none">
                {label}
              </button>
            </li>
          ))}
        </ul>
      )}
      {panel === 'detail' && <PatientDetailDialog patient={p} onClose={() => setPanel(null)} />}
      {panel === 'sharing' && <SharingDialog patient={p} onClose={() => setPanel(null)} />}
      {panel === 'access' && <AccessDialog patient={p} onClose={() => setPanel(null)} />}
    </div>
  )
}

function PatientDetailDialog({ patient: p, onClose }) {
  return (
    <Modal title={`Patient details · ${p.full_name}`} width="36rem" closeOnBackdrop onClose={onClose}
      footer={(close) => <button type="button" onClick={close} className={btn.secondary}>Close</button>}>
      <dl className="grid grid-cols-1 gap-x-6 gap-y-4 px-4 py-4 sm:grid-cols-2">
        {identityItems(p).map(([k, v, sub]) => (
          <div key={k} className="min-w-0">
            <dt className={TYPE.meta}>{k}</dt>
            <dd className={`break-words ${TYPE.body} tnum`}>
              {v}
              {sub && <span className="block text-xs text-muted">{sub}</span>}
            </dd>
          </div>
        ))}
      </dl>
      <div className="flex flex-wrap items-center justify-between gap-2 border-t border-line px-4 py-3">
        <p className={TYPE.meta}>Patient app sign-in</p>
        <ResetPassword patient={p} />
      </div>
    </Modal>
  )
}

// Give a colleague access by their Clinician ID (every UC2 Care account has one).
function ShareForm({ patient: p, onShared, cta = 'Share' }) {
  const [code, setCode] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const submit = async (e) => {
    e.preventDefault()
    const clean = code.trim().toUpperCase()
    if (!clean) { setError('Enter the colleague’s Clinician ID.'); return }
    setBusy(true)
    setError('')
    try {
      const team = await api.addToCareTeam(p.id, clean)
      setCode('')
      onShared(team, team.find((m) => m.clinician_code === clean))
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }
  return (
    <form onSubmit={submit} noValidate>
      <Field label="Colleague’s Clinician ID" htmlFor="share-code" required error={error}
        hint="Any colleague with a UC2 Care account, e.g. CLN-7K3Q9P">
        <div className="flex gap-2">
          <input id="share-code" value={code} onChange={(e) => { setCode(e.target.value.toUpperCase()); setError('') }}
            maxLength={20} placeholder="CLN-" autoComplete="off" className={`${input} uppercase tracking-wide`} />
          <button type="submit" disabled={busy} className={btn.primary}>{busy ? 'Sharing…' : cta}</button>
        </div>
      </Field>
    </form>
  )
}

function SharingDialog({ patient: p, onClose }) {
  const [shared, setShared] = useState(null)
  return (
    <Modal title={`Share ${p.full_name}’s record`} width="32rem" closeOnBackdrop onClose={onClose}
      footer={(close) => <button type="button" onClick={close} className={btn.secondary}>Close</button>}>
      <div className="space-y-3 px-4 py-4">
        <p className={TYPE.body}>They’ll see this patient in their list and can open the record.</p>
        <ShareForm patient={p} onShared={(_, who) => setShared(who)} />
        {shared && <Notice>Shared with {shared.full_name} ({shared.clinician_code}).</Notice>}
      </div>
    </Modal>
  )
}

function AccessDialog({ patient: p, onClose }) {
  const { user } = useClinician()
  const navigate = useNavigate()
  const [team, setTeam] = useState(null)
  const [error, setError] = useState('')
  const [confirming, setConfirming] = useState(null)
  const [busy, setBusy] = useState(false)
  useEffect(() => {
    api.careTeam(p.id).then(setTeam).catch((e) => setError(e.message))
  }, [p.id])

  const remove = async (m) => {
    setBusy(true)
    setError('')
    try {
      const next = await api.removeFromCareTeam(p.id, m.clinician_code)
      if (m.clinician_code === user.clinician_code) { navigate('/care-team/patients', { replace: true }); return }
      setTeam(next)
      setConfirming(null)
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal title={`Who can access ${p.full_name}’s record`} width="36rem" closeOnBackdrop onClose={onClose}
      footer={(close) => (<>
        <Link to={`/care-team/settings/access-log?patient=${p.id}`} className={`${btn.ghost} mr-auto`}>
          <History size={15} aria-hidden="true" /> View access log
        </Link>
        <button type="button" onClick={close} className={btn.secondary}>Close</button>
      </>)}>
      {error && <div className="px-4 pt-3"><ErrorBox>{error}</ErrorBox></div>}
      {!team && !error && <p className="px-4 py-4 text-sm text-muted">Loading…</p>}
      {team && (
        <ul className="divide-y divide-line border-y border-line">
          {team.map((m) => {
            const me = m.clinician_code === user.clinician_code
            return (
              <li key={m.clinician_code} className="px-4 py-2.5">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span className="min-w-0">
                    <span className={`block ${TYPE.row}`}>{m.full_name}{me && <span className="font-normal text-muted"> (you)</span>}</span>
                    <span className={`block ${TYPE.meta} tnum`}>
                      {m.clinician_code} · {!m.granted_by_name ? 'added by the patient'
                        : m.granted_by_name === m.full_name ? 'added the patient' : `given by ${m.granted_by_name}`} · {formatDate(m.granted_at)}
                    </span>
                  </span>
                  {confirming !== m.clinician_code && (
                    <button type="button" disabled={team.length === 1} onClick={() => setConfirming(m.clinician_code)}
                      title={team.length === 1 ? 'The only person with access can’t be removed' : undefined}
                      className={`${btn.danger} ${btn.sm}`}>Remove</button>
                  )}
                </div>
                {confirming === m.clinician_code && (
                  <div className="mt-2 flex flex-wrap items-center gap-2 rounded-md border border-alert-200 bg-alert-50 px-3 py-2 text-sm">
                    <span className="flex-1 text-ink">{me ? 'Remove your own access? You will no longer see this patient.' : `Remove ${m.full_name}’s access?`}</span>
                    <button type="button" disabled={busy} onClick={() => remove(m)} className={`${btn.danger} ${btn.sm}`}>Yes, remove</button>
                    <button type="button" onClick={() => setConfirming(null)} className={`${btn.secondary} ${btn.sm}`}>No</button>
                  </div>
                )}
              </li>
            )
          })}
        </ul>
      )}
      {team && team.length === 1 && <p className="px-4 pt-2 text-xs text-muted">The only person with access can’t be removed.</p>}
      <div className="px-4 py-4">
        <ShareForm patient={p} cta="Give access" onShared={(next) => setTeam(next)} />
      </div>
    </Modal>
  )
}

// Latest HbA1c stays on the main screen (it feeds the risk detector) as a small chip under the patient's name.
function ClinicalChips({ patient: p }) {
  const chip = `inline-flex items-baseline gap-1.5 rounded-md border border-line bg-surface px-2 py-0.5 ${TYPE.meta}`
  return (
    <span className="mt-1 flex flex-wrap gap-2">
      <span className={chip}>
        Latest HbA1c
        <span className={`${TYPE.body} tnum`}>{p.latest_hba1c ? `${p.latest_hba1c.value.toFixed(1)}%` : '—'}</span>
        {p.latest_hba1c && <span className="tnum">· {formatDate(p.latest_hba1c.date)}</span>}
      </span>
    </span>
  )
}

// ------------------------------------------------------------------ Summary: flag reasoning

// Appointments the patient booked (confirmed at once - nothing to accept here), or ones waiting for a new time.
function UpcomingAppointments({ patient }) {
  const [list, setList] = useState(null)
  useEffect(() => {
    api.patientAppointments(patient.id).then(setList).catch(() => setList([]))
  }, [patient])
  if (!list) return null
  return (
    <section aria-labelledby="appt-title" className="mb-4 rounded-lg border border-line bg-surface">
      <header className={`flex flex-wrap items-center justify-between gap-2 px-4 py-2.5 ${list.length ? 'border-b border-line' : ''}`}>
        <h2 id="appt-title" className={TYPE.section}>Upcoming appointment{list.length > 1 ? 's' : ''}</h2>
        {list.length === 0 && <span className={TYPE.meta}>None booked</span>}
      </header>
      {list.length > 0 && (
        <ul className="divide-y divide-line">
          {list.map((x) => (
            <li key={x.id} className="flex flex-wrap items-center gap-x-3 gap-y-1 px-4 py-2.5">
              <span className={`${TYPE.row} tnum`}>{formatDate(x.date)} · {formatTime(x.start_time)} – {formatTime(x.end_time)}</span>
              <span className={TYPE.body}>{x.doctor_name}</span>
              <Badge tone={x.status === 'confirmed' ? 'ok' : 'warn'} className="ml-auto">
                {x.status === 'confirmed' ? 'Confirmed' : 'Needs a new time'}
              </Badge>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}

// The badge next to the patient's name: shows the level, and opens / closes "Priority Details".
function PriorityBadge({ level, open, onToggle }) {
  const l = LEVELS[level]
  return (
    <button type="button" onClick={onToggle} aria-expanded={open} aria-controls="priority-details"
      title={open ? 'Hide priority details' : 'Show priority details'} className="rounded focus-visible:outline-2 focus-visible:outline-brand-600">
      <Badge tone={l.tone} icon={l.icon} className="cursor-pointer hover:brightness-95">
        {l.short}
        <ChevronDown size={13} aria-hidden="true" className={open ? 'rotate-180' : ''} />
      </Badge>
    </button>
  )
}

const ROW_STYLE = {
  hard: { rail: 'border-l-alert-700', icon: OctagonAlert, iconCls: 'text-alert-700', tone: 'alert', tag: 'High priority' },
  soft: { rail: 'border-l-warn-700', icon: AlertTriangle, iconCls: 'text-warn-700', tone: 'warn', tag: 'Present' },
  quiet: { rail: 'border-l-ok-700', icon: CheckCircle2, iconCls: 'text-ok-700', tone: 'ok', tag: 'Not present' },
}

// Why this patient is (or isn't) flagged: ONE list, one row per check, same look for every row.
// Flags first (high priority, then present), then the checks that did not trigger. Rows start collapsed.
function PriorityDetails({ patient, open: panelOpen, onToggle, rowsOpen: open, setRowsOpen: setOpen }) {
  const a = patient.assessment
  const [historyOpen, setHistoryOpen] = useState(false)
  const hard = a.signals.filter((s) => s.fired && s.tier === 'hard')
  const soft = a.signals.filter((s) => s.fired && s.tier === 'soft')
  const quiet = a.signals.filter((s) => !s.fired)
  const fired = hard.length + soft.length
  const rows = [...hard.map((s) => [s, 'hard']), ...soft.map((s) => [s, 'soft']), ...quiet.map((s) => [s, 'quiet'])]

  return (
    <section id="priority-details" aria-labelledby="priority-title" className="rounded-lg border border-line bg-surface">
      <header className={`flex flex-wrap items-center justify-between gap-2 px-4 py-2.5 ${panelOpen ? 'border-b border-line' : ''}`}>
        <h2 id="priority-title" className={TYPE.section}>Priority Details</h2>
        <button type="button" onClick={onToggle} aria-expanded={panelOpen} aria-controls="priority-rows" className={`${btn.ghost} ${btn.sm}`}>
          {panelOpen ? 'Hide priority details' : 'Show priority details'}
          <ChevronDown size={14} aria-hidden="true" className={panelOpen ? 'rotate-180' : ''} />
        </button>
      </header>
      {panelOpen && <ul id="priority-rows" className="divide-y divide-line">
        {rows.map(([s, kind], i) => {
          const st = ROW_STYLE[kind]
          const Icon = st.icon
          const expanded = Boolean(open[s.key])
          const firstQuiet = kind === 'quiet' && (i === 0 || rows[i - 1][1] !== 'quiet')
          return (
            <li key={s.key} className={`border-l-4 ${st.rail}`}>
              {firstQuiet && fired > 0 && (
                <p className="border-b border-line bg-subtle px-4 py-1.5 text-[11px] font-semibold uppercase tracking-wide text-muted">
                  Not flagged ({quiet.length})
                </p>
              )}
              <button type="button" onClick={() => setOpen((o) => ({ ...o, [s.key]: !o[s.key] }))} aria-expanded={expanded}
                aria-controls={`signal-${s.key}`} className="flex w-full items-center gap-3 px-4 py-2 text-left hover:bg-subtle">
                <Icon size={17} aria-hidden="true" className={`shrink-0 ${st.iconCls}`} />
                <span className="min-w-0 flex-1 text-[19px] font-semibold text-ink">{s.label}</span>
                <Badge tone={st.tone}>{st.tag}</Badge>
                <ChevronDown size={16} aria-hidden="true" className={`shrink-0 text-muted ${expanded ? 'rotate-180' : ''}`} />
              </button>
              {expanded && (
                <div id={`signal-${s.key}`} className="border-t border-line bg-subtle/50 px-4 pb-2 pl-11">
                  <SignalFacts signal={s} />
                  {s.key === 'emergency_visit' && (
                    <button type="button" onClick={() => setHistoryOpen(true)}
                      className="mt-1 inline-flex items-center gap-1 text-[15px] font-medium text-brand-700 hover:underline">
                      View all emergency visits ({patient.emergency_visit_count}) <ChevronRight size={14} aria-hidden="true" />
                    </button>
                  )}
                </div>
              )}
            </li>
          )
        })}
      </ul>}
      {historyOpen && <EmergencyHistory patient={patient} onClose={() => setHistoryOpen(false)} />}
    </section>
  )
}

function EmergencyHistory({ patient, onClose }) {
  const [data, setData] = useState(null)
  const [error, setError] = useState('')
  useEffect(() => {
    api.emergencyVisits(patient.id).then(setData).catch((e) => setError(e.message))
  }, [patient])

  return (
    <Modal title="Emergency visits" onClose={onClose} width="46rem">
      <div className="px-4 py-3">
        <p className="text-sm text-muted">
          {patient.full_name} · {patient.patient_code}
          {data && <> · {data.visits.length} on record · last clinic visit {formatDate(data.last_clinic_visit)}</>}
        </p>
        <ErrorBox>{error}</ErrorBox>
        {data && data.visits.length === 0 && <p className="py-6 text-center text-sm text-muted">No emergency visits on record.</p>}
        {data && data.visits.length > 0 && (
          <>
            <p className="mt-1 text-xs text-muted">Newest first. Only visits on or after {formatDate(data.counts_from)} (since the last clinic visit) raise a flag.</p>
            <ol className="mt-3 space-y-3">
              {data.visits.map((v) => (
                <li key={v.id} className={`rounded-md border border-line border-l-4 ${v.counts_toward_flag ? 'border-l-alert-700' : 'border-l-line-strong'}`}>
                  <div className="flex flex-wrap items-center justify-between gap-2 border-b border-line px-3 py-2">
                    <span className="text-sm font-semibold text-ink tnum">{formatDate(v.date)}</span>
                    {v.counts_toward_flag
                      ? <Badge tone="alert" icon={OctagonAlert}>Counts toward current flag</Badge>
                      : <Badge>Before last clinic visit</Badge>}
                  </div>
                  <dl className="divide-y divide-line px-3 text-[13px]">
                    {[['Hospital', v.hospital], ['Problem', v.problem], ['Treating doctor', v.doctor],
                      ['Recorded by', `${v.recorded_by} · ${v.source_label}`]].map(([k, val]) => (
                      <div key={k} className="grid grid-cols-[minmax(6rem,8rem)_1fr] gap-3 py-1.5">
                        <dt className="text-muted">{k}</dt>
                        <dd className="text-ink">{val || <span className="text-muted">Not recorded</span>}</dd>
                      </div>
                    ))}
                  </dl>
                </li>
              ))}
            </ol>
          </>
        )}
      </div>
    </Modal>
  )
}

// ------------------------------------------------------------------ Data sources: exactly three paths

const SOURCES = [
  { key: 'hospital', icon: Database, title: 'Hospital records', text: 'Already in our system', count: 'hospital_internal' },
  { key: 'upload', icon: Smartphone, title: 'Patient upload', text: 'Sent by the patient', count: 'patient_upload' },
  { key: 'abha', icon: Globe, title: 'Other hospital (via ABHA)', text: 'With patient consent', count: 'external_hospital_abdm' },
]

function DataSources({ patient, onChanged }) {
  const [params] = useSearchParams()
  const [open, setOpen] = useState(params.get('source') ?? 'hospital')
  return (
    <div>
      <div role="tablist" aria-label="Data sources" className="grid overflow-hidden rounded-lg border border-line bg-surface sm:grid-cols-3 sm:divide-x sm:divide-line">
        {SOURCES.map(({ key, icon: Icon, title, text, count }) => {
          const active = open === key
          return (
            <button key={key} type="button" role="tab" aria-selected={active} onClick={() => setOpen(key)}
              className={`flex items-center gap-3 border-b-2 px-4 py-3 text-left sm:border-b-2 ${active ? 'border-b-brand-600 bg-brand-50' : 'border-b-transparent hover:bg-subtle'}`}>
              <Icon size={18} aria-hidden="true" className={active ? 'text-brand-700' : 'text-muted'} />
              <span>
                <span className={`block text-sm font-semibold ${active ? 'text-brand-800' : 'text-ink'}`}>{title}</span>
                <span className="block text-xs text-muted tnum">{text} · {patient.source_counts[count] ?? 0} records</span>
              </span>
            </button>
          )
        })}
      </div>
      <div role="tabpanel" className="mt-3">
        {open === 'hospital' && <HospitalPanel patientId={patient.id} />}
        {open === 'upload' && <UploadsPanel patientId={patient.id} refresh={patient} />}
        {open === 'abha' && <AbhaPanel patient={patient} onChanged={onChanged} />}
      </div>
    </div>
  )
}

const TYPE_LABELS = {
  refill: 'Refill', engagement_log: 'Sugar log', lab: 'Lab', visit: 'Visit', vital: 'Vital',
  screening: 'Screening', hypo_event: 'Hypoglycaemia',
}

function describe(e) {
  const value = e.value == null ? '' : e.unit === '%' ? ` ${e.value}%` : ` ${e.value} ${e.unit}`
  const status = {
    refill: { ordered: ' — due, not collected', stopped: ' — stopped' },
    visit: { ordered: ' — booked, not attended' },
    screening: { ordered: ' — overdue' },
  }[e.event_type]?.[e.status] ?? ''
  const where = e.event_type === 'visit' && e.facility ? ` — ${e.facility}${e.clinician ? `, ${e.clinician}` : ''}` : ''
  return `${e.name ?? ''}${value}${status}${where}`
}

function HospitalPanel({ patientId }) {
  const [events, setEvents] = useState(null)
  useEffect(() => {
    api.events(patientId, 'hospital_internal', 10).then(setEvents).catch(() => setEvents([]))
  }, [patientId])
  return (
    <Panel title="Hospital records" description="Latest 10 entries" bodyClass="">
      {events && events.length === 0 && <p className="px-4 py-5 text-sm text-muted">Nothing here yet.</p>}
      {events && events.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full">
            <thead className="border-b border-line bg-subtle"><tr>
              <th className={th}>Date</th><th className={th}>Type</th><th className={th}>Detail</th><th className={th}>Recorded by</th>
            </tr></thead>
            <tbody className="divide-y divide-line">
              {events.map((e) => (
                <tr key={e.id} className="hover:bg-subtle">
                  <td className={`${td} whitespace-nowrap tnum`}>{formatDate(e.effective_date)}</td>
                  <td className={td}>{TYPE_LABELS[e.event_type]}</td>
                  <td className={td}>{describe(e)}{e.note && <span className="block text-xs text-muted">{e.note}</span>}</td>
                  <td className={`${td} text-muted`}>{e.asserted_by}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  )
}

const REVIEW_TONE = { pending: 'warn', confirmed: 'ok', rejected: 'alert' }
function ReviewChip({ status, by }) {
  const who = by ? ` · ${by}` : ''
  const text = status === 'confirmed' ? `Confirmed${who}` : status === 'rejected' ? `Rejected${who}` : 'Awaiting review'
  return <Badge tone={REVIEW_TONE[status]}>{text}</Badge>
}

// Read-only record of what the patient sent, with a View button for every file.
// Reviewing (confirm / reject, and who did it) happens in one place only: "Reports & documents".
function UploadsPanel({ patientId, refresh }) {
  const [all, setAll] = useState(false)
  const [data, setData] = useState(null)
  const [error, setError] = useState('')
  useEffect(() => {
    api.uploads(patientId, all).then(setData).catch((e) => setError(e.message))
  }, [patientId, all, refresh])

  const rows = data ? [
    ...data.documents.map((d) => ({
      key: `d${d.id}`, date: d.uploaded_at, what: d.document_type === 'prescription' ? 'Prescription' : 'Lab report',
      detail: d.description, file: d.id, fileLabel: d.content_type === 'application/pdf' ? 'PDF' : 'Photo',
    })),
    ...data.events.map((e) => ({
      key: `e${e.id}`, date: e.effective_date, what: TYPE_LABELS[e.event_type], detail: describe(e), note: e.note,
      file: e.photo_id, fileLabel: 'Meter photo',
    })),
    ...data.external_reports.map((x) => ({
      key: `x${x.id}`, date: x.upload_date, what: x.document_type === 'medicine_bill' ? 'Medicine bill' : 'Outside lab report',
      detail: x.document_type === 'medicine_bill' ? `Diabetes medicine · ${BILL_TEXT[x.status]}` : `${x.test_label} · test on ${formatDate(x.test_date)}`,
      url: api.externalReportUrl(patientId, x.id), fileLabel: x.content_type === 'application/pdf' ? 'PDF' : 'Photo',
    })),
  ].sort((a, b) => b.date.localeCompare(a.date)) : []

  return (
    <Panel title="Patient upload" bodyClass=""
      description={all ? 'Everything the patient has sent' : 'What the patient sent in the last 60 days'}
      actions={<button type="button" onClick={() => setAll((v) => !v)} aria-pressed={all} className={`${btn.secondary} ${btn.sm}`}>
        {all ? 'Last 60 days' : 'View all'}
      </button>}>
      {error && <div className="px-4 pt-3"><ErrorBox>{error}</ErrorBox></div>}
      {data && rows.length === 0 && (
        <p className="px-4 py-5 text-sm text-muted">{all ? 'Nothing received from the patient yet.' : 'Nothing received in the last 60 days.'}</p>
      )}
      {rows.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full">
            <thead className="border-b border-line bg-subtle"><tr>
              <th className={th}>Date</th><th className={th}>Type</th><th className={th}>What was sent</th><th className={th}><span className="sr-only">File</span></th>
            </tr></thead>
            <tbody className="divide-y divide-line">
              {rows.map((r) => (
                <tr key={r.key} className="hover:bg-subtle">
                  <td className={`${td} whitespace-nowrap text-muted tnum`}>{r.date.length > 10 ? formatDateTime(r.date) : formatDate(r.date)}</td>
                  <td className={`${td} whitespace-nowrap`}>{r.what}</td>
                  <td className={td}>{r.detail}{r.note && <span className="block text-xs text-muted">{r.note}</span>}</td>
                  <td className={`${td} whitespace-nowrap text-right`}>
                    {r.file || r.url ? (
                      <a href={r.url ?? api.documentUrl(patientId, r.file)} target="_blank" rel="noreferrer" className={`${btn.secondary} ${btn.sm}`}
                        aria-label={`View ${r.fileLabel.toLowerCase()}: ${r.what}${r.detail ? ` ${r.detail}` : ''}`}>
                        <FileImage size={14} aria-hidden="true" /> View
                      </a>
                    ) : <span className="text-xs text-muted">Typed in</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {rows.length > 0 && (
        <p className="border-t border-line px-4 py-2 text-xs text-muted">
          To confirm or reject what the patient sent, go to{' '}
          <Link to="?tab=reports" className="font-medium text-brand-700 hover:underline">Reports &amp; documents</Link>.
        </p>
      )}
    </Panel>
  )
}

// ------------------------------------------------------------------ Other hospital via ABHA (mock ABDM)

function consentStatus(r) {
  switch (r.status) {
    case 'REQUESTED':
      return { icon: Clock, tone: 'warn', text: `Waiting for the patient to approve · expires ${formatDateTime(r.expires_at)}` }
    case 'GRANTED':
      return { icon: CheckCircle2, tone: 'ok', text: `Approved · ${r.records_received} record(s) received from ${r.hip_name}` }
    case 'DENIED':
      return { icon: XCircle, tone: 'alert', text: `Not approved — the patient declined on ${formatDateTime(r.responded_at)}. Nothing was shared.` }
    default:
      return { icon: OctagonAlert, tone: 'alert', text: `No response from ${r.hip_name} — the request expired unanswered on ${formatDateTime(r.expires_at)}.` }
  }
}
const TONE_TEXT = { warn: 'text-warn-800', ok: 'text-ok-700', alert: 'text-alert-800' }

function AbhaPanel({ patient, onChanged }) {
  const [types, setTypes] = useState([])
  const [selected, setSelected] = useState(['Prescription', 'DiagnosticReport'])
  const [abha, setAbha] = useState('')
  const [requests, setRequests] = useState([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const prevStatuses = useRef('')

  const load = useCallback(async () => {
    const list = await api.consents(patient.id)
    const sig = list.map((r) => `${r.id}:${r.status}`).join(',')
    if (prevStatuses.current && sig !== prevStatuses.current) onChanged()
    prevStatuses.current = sig
    setRequests(list)
  }, [patient.id, onChanged])

  useEffect(() => {
    api.hiTypes().then(setTypes)
    load()
  }, [load])

  const waiting = requests.some((r) => r.status === 'REQUESTED')
  useEffect(() => {
    if (!waiting) return
    const t = setInterval(load, 4000)
    return () => clearInterval(t)
  }, [waiting, load])

  const submit = async (e) => {
    e.preventDefault()
    setBusy(true)
    setError('')
    try {
      await api.requestConsent(patient.id, abha, selected)
      setAbha('')
      await load()
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }
  const toggle = (code) => setSelected((s) => (s.includes(code) ? s.filter((c) => c !== code) : [...s, code]))

  return (
    <div className="grid gap-4 lg:grid-cols-[22rem_minmax(0,1fr)]">
      <Panel title="Request records" description="The patient must approve on their phone">
        <form onSubmit={submit} className="space-y-3">
          <Field label="ABHA number" htmlFor="abha" required hint={`On file: ${formatAbha(patient.abha_number)}`}>
            <input id="abha" value={abha} onChange={(e) => setAbha(e.target.value.replace(/[^\d-]/g, ''))} inputMode="numeric"
              placeholder="14 digits" maxLength={17} required className={`${input} tracking-wide tnum`} />
          </Field>
          <fieldset>
            <legend className="mb-1 text-[13px] font-medium text-ink">Records needed</legend>
            <div className="space-y-1.5">
              {types.map((t) => (
                <label key={t.code} className="flex items-center gap-2 text-sm text-ink">
                  <input type="checkbox" checked={selected.includes(t.code)} onChange={() => toggle(t.code)} className="h-4 w-4 accent-brand-600" />
                  {t.label}
                </label>
              ))}
            </div>
          </fieldset>
          <ErrorBox>{error}</ErrorBox>
          <button type="submit" disabled={busy || selected.length === 0} className={btn.primary}>
            {busy ? 'Sending…' : 'Send consent request'}
          </button>
        </form>
      </Panel>

      <Panel title="Requests" bodyClass="">
        {requests.length === 0 && <p className="px-4 py-5 text-sm text-muted">No requests yet.</p>}
        <ul className="divide-y divide-line">
          {requests.map((r) => {
            const s = consentStatus(r)
            const Icon = s.icon
            return (
              <li key={r.id} className="px-4 py-3">
                <div className="flex items-start gap-2">
                  <Building2 size={16} className="mt-0.5 shrink-0 text-muted" aria-hidden="true" />
                  <div className="min-w-0 flex-1">
                    <span className="block text-sm font-semibold text-ink">{r.hip_name}</span>
                    <span className="block text-xs text-muted">{r.hi_type_labels.join(', ')} · {formatDate(r.date_from)} – {formatDate(r.date_to)}</span>
                    <p className={`mt-1.5 flex items-start gap-1.5 text-sm font-medium ${TONE_TEXT[s.tone]}`}>
                      <Icon size={15} className="mt-0.5 shrink-0" aria-hidden="true" /> {s.text}
                    </p>
                    <details className="mt-1.5">
                      <summary className="cursor-pointer text-xs font-medium text-brand-700">Audit trail ({r.audit.length})</summary>
                      <ol className="mt-1 space-y-0.5 text-xs text-muted">
                        {r.audit.map((a, i) => (
                          <li key={i}><span className="font-medium text-ink tnum">{formatDateTime(a.at)} · {a.action}</span> — {a.actor}. {a.detail}</li>
                        ))}
                      </ol>
                    </details>
                    {r.status === 'REQUESTED' && (
                      <button type="button" onClick={() => api.simulateExpiry(patient.id, r.id).then(load)}
                        className="mt-1.5 text-xs text-muted underline hover:text-ink">Demo only: simulate no response</button>
                    )}
                  </div>
                </div>
              </li>
            )
          })}
        </ul>
      </Panel>
    </div>
  )
}

// ------------------------------------------------------------------ Record data (manual entry)

const blank = () => ({
  sugar: '', sugarDate: todayIso(), hba1c: '', hba1cDate: todayIso(),
  hemoglobin: '', hemoglobinDate: todayIso(), egfr: '', egfrDate: todayIso(),
  sugarDoc: null, hba1cDoc: null, hemoglobinDoc: null, egfrDoc: null,       // "Link to Report" for each value
  refill: null, refillDate: todayIso(), screening: '', screeningDate: todayIso(), hypo: null,
  er: null, erDate: todayIso(), erProblem: '', erDoctor: '', erHospital: 'UC2 Hospital — Emergency Department',
})

function YesNo({ value, onChange, label }) {
  return (
    <div role="group" aria-label={label} className="inline-flex rounded-md border border-line-strong p-0.5">
      {['yes', 'no'].map((v) => (
        <button key={v} type="button" aria-pressed={value === v} onClick={() => onChange(value === v ? null : v)}
          className={`h-7 w-12 rounded text-sm ${value === v ? 'bg-brand-600 font-medium text-white' : 'text-muted hover:text-ink'}`}>
          {v === 'yes' ? 'Yes' : 'No'}
        </button>
      ))}
    </div>
  )
}

function ManualEntry({ patientId, onSaved, onDone }) {
  const [f, setF] = useState(blank)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [saved, setSaved] = useState(null)
  const set = (k) => (v) => {
    setF((s) => ({ ...s, [k]: v?.target ? v.target.value : v }))
    setError('')
  }
  const today = todayIso()

  const submit = async (e) => {
    e.preventDefault()
    const body = {}
    for (const k of ['sugar', 'hba1c', 'hemoglobin', 'egfr']) {
      // the link travels with the value, so it is saved in the same step
      if (f[k] !== '') body[k] = { value: Number(f[k]), date: f[`${k}Date`], linked_document_id: f[`${k}Doc`]?.id ?? null }
    }
    if (f.refill) body.missed_refill = { missed: f.refill === 'yes', date: f.refillDate }
    if (f.screening) body.overdue_screening = { type: f.screening, date: f.screeningDate }
    if (f.hypo === 'yes') body.hypoglycemia = true
    if (f.er === 'yes') {
      const missing = [['erProblem', 'the problem'], ['erDoctor', 'the treating doctor'], ['erHospital', 'the hospital']]
        .filter(([k]) => f[k].trim().length < 2).map(([, name]) => name)
      if (missing.length) {
        setError(`Emergency visit: please enter ${missing.join(', ')}.`)
        return
      }
      body.emergency = { date: f.erDate, problem: f.erProblem, doctor: f.erDoctor, hospital: f.erHospital }
    }
    if (Object.keys(body).length === 0) {
      setError('Fill in at least one field.')
      return
    }
    setBusy(true)
    setError('')
    try {
      const res = await api.manual(patientId, body)
      setSaved(res.saved)
      setF(blank())
      onSaved((p) => ({ ...p, ...res, source_counts: { ...p.source_counts, care_team_manual: (p.source_counts.care_team_manual ?? 0) + res.saved.length } }))
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  const measure = (label, key, unit, aria) => (
    <tr className="border-b border-line">
      <th scope="row" className="w-28 py-2 pr-3 text-left align-top pt-4 text-sm font-medium text-ink sm:w-44">{label}</th>
      <td className="py-2">
        <span className="flex flex-wrap items-center gap-2">
          <span className="flex items-center gap-2">
            <input aria-label={aria} inputMode="decimal" value={f[key]} onChange={set(key)} className={`${input} w-28! tnum`} />
            <span className="w-24 text-xs text-muted">{unit}</span>
          </span>
          <input aria-label={`${label} date`.replace('Test date', 'date')} type="date" max={today} value={f[`${key}Date`]} onChange={set(`${key}Date`)} className={`${input} w-40!`} />
          <ReportLink field={key} fieldLabel={label.replace(' Test', '')}
            title={`${label.replace(' Test', '')}${f[key] ? ` ${f[key]} ${unit}` : ''} · ${formatDate(f[`${key}Date`])}`}
            value={f[`${key}Doc`]} onChange={(doc) => setF((s) => ({ ...s, [`${key}Doc`]: doc }))} />
        </span>
      </td>
    </tr>
  )

  return (
    <form onSubmit={submit} aria-labelledby="manual" className="max-w-3xl">
      <Panel title={<span id="manual">Log data manually</span>} description="Fill in only what you need">
        {saved && (
          <div className="mb-3 flex flex-wrap items-center gap-3 rounded-md border border-ok-700/20 bg-ok-50 px-3 py-2">
            <Notice>Saved: {saved.join(' · ')}.</Notice>
            <button type="button" onClick={onDone} className="text-sm font-medium text-brand-700 hover:underline">See updated summary</button>
          </div>
        )}

        <h3 className={TYPE.row}>Measurements</h3>
        <table className="mt-1 w-full">
          <thead className="sr-only"><tr><th>Measurement</th><th>Value and date</th></tr></thead>
          <tbody>
            {measure('Sugar Level', 'sugar', 'mg/dL', 'Sugar level in mg/dL')}
            {measure('HbA1c Test', 'hba1c', '%', 'HbA1c percent')}
            {measure('Hemoglobin', 'hemoglobin', 'g/dL', 'Hemoglobin in g/dL')}
            {measure('eGFR', 'egfr', 'mL/min/1.73m²', 'eGFR in mL/min/1.73m²')}
          </tbody>
        </table>

        <h3 className={`mt-5 ${TYPE.row}`}>Events</h3>
        <table className="mt-1 w-full">
          <tbody>
            <tr className="border-b border-line">
              <th scope="row" className="w-28 py-2 pr-3 text-left align-top pt-4 text-sm font-medium text-ink sm:w-44">Missed Refill?</th>
              <td className="py-2">
                <span className="flex flex-wrap items-center gap-2">
                  <span className="w-[13.5rem]"><YesNo label="Missed refill" value={f.refill} onChange={set('refill')} /></span>
                  <input aria-label="Refill date" type="date" max={today} value={f.refillDate} onChange={set('refillDate')} className={`${input} w-40!`} />
                </span>
              </td>
            </tr>
            <tr className="border-b border-line">
              <th scope="row" className="w-28 py-2 pr-3 text-left align-top pt-4 text-sm font-medium text-ink sm:w-44">Overdue Screening</th>
              <td className="py-2">
                <span className="flex flex-wrap items-center gap-2">
                  <span className="w-[13.5rem]">
                    <select aria-label="Overdue screening type" value={f.screening} onChange={set('screening')} className={`${input} w-28!`}>
                      <option value="">—</option><option>Eye</option><option>Foot</option><option>Kidney</option>
                    </select>
                  </span>
                  <input aria-label="Screening due date" type="date" max={today} value={f.screeningDate} onChange={set('screeningDate')} className={`${input} w-40!`} />
                </span>
              </td>
            </tr>
            <tr className="border-b border-line">
              <th scope="row" className="w-28 py-2 pr-3 text-left align-top pt-4 text-sm font-medium text-ink sm:w-44">Hypoglycemia Event?</th>
              <td className="py-2">
                <span className="flex flex-wrap items-center gap-3">
                  <YesNo label="Hypoglycemia event" value={f.hypo} onChange={set('hypo')} />
                  {f.hypo === 'yes' && <Badge tone="alert" icon={OctagonAlert}>Saving raises a High priority flag immediately.</Badge>}
                </span>
              </td>
            </tr>
            <tr className="border-b border-line">
              <th scope="row" className="w-28 py-2 pr-3 text-left align-top pt-4 text-sm font-medium text-ink sm:w-44">Emergency Visit?</th>
              <td className="py-2">
                <span className="flex flex-wrap items-center gap-3">
                  <YesNo label="Emergency visit" value={f.er} onChange={set('er')} />
                  {f.er === 'yes' && <Badge tone="alert" icon={OctagonAlert}>Saving raises a High priority flag immediately.</Badge>}
                </span>
              </td>
            </tr>
          </tbody>
        </table>

        {f.er === 'yes' && (
          <fieldset className="mt-3 grid gap-3 rounded-md border border-line border-l-4 border-l-alert-700 p-3 sm:grid-cols-2">
            <legend className="px-1 text-xs font-semibold text-alert-800">Emergency visit details — all required</legend>
            <Field label="Date of visit" htmlFor="er-date" required>
              <input id="er-date" type="date" max={today} value={f.erDate} onChange={set('erDate')} className={input} />
            </Field>
            <Field label="Hospital" htmlFor="er-hospital" required>
              <input id="er-hospital" value={f.erHospital} onChange={set('erHospital')} maxLength={120} className={input} />
            </Field>
            <Field label="Problem / reason for visit" htmlFor="er-problem" required className="sm:col-span-2">
              <input id="er-problem" value={f.erProblem} onChange={set('erProblem')} maxLength={300}
                placeholder="e.g. Very high sugar with vomiting, admitted for 1 day" className={input} />
            </Field>
            <Field label="Treating doctor / surgeon" htmlFor="er-doctor" required className="sm:col-span-2">
              <input id="er-doctor" value={f.erDoctor} onChange={set('erDoctor')} maxLength={120} placeholder="e.g. Dr. Anil Rao (Surgeon)" className={input} />
            </Field>
          </fieldset>
        )}

        <div className="mt-3"><ErrorBox>{error}</ErrorBox></div>
        <div className="mt-4 flex gap-2 border-t border-line pt-3">
          <button type="submit" disabled={busy} className={btn.primary}>{busy ? 'Saving…' : 'Save'}</button>
          <button type="button" onClick={() => { setF(blank()); setError('') }} className={btn.secondary}>Clear</button>
        </div>
      </Panel>
    </form>
  )
}

// Values the care team typed in, each with its report link - fix a wrong link here at any time.
// Changing the link updates the same value (never a copy) and the change is recorded.
// Values the care team typed in: edit a value (pencil) and link / change / unlink the report it was read from.
// Link changes show at once and are rolled back with a clear message if the save fails.
function ManualValuesPanel({ patientId, refresh }) {
  const { user } = useClinician()
  const [values, setValues] = useState(null)
  const [error, setError] = useState('')
  const [picker, setPicker] = useState(null)         // { id, preview } - the row whose picker is open
  const [editing, setEditing] = useState(null)       // { id, value, unit, date, error, busy }
  const load = useCallback(() => {
    api.manualValues(patientId).then(setValues).catch((e) => setError(e.message))
  }, [patientId])
  useEffect(() => {
    load()
  }, [load, refresh])

  const test = (v) => (v.name === 'Blood glucose' ? 'Sugar level' : v.name)
  const unitText = (u) => (u === '%' ? ' %' : ` ${u}`)
  const put = (row) => setValues((list) => list.map((x) => (x.id === row.id ? row : x)))

  const relink = async (v, report) => {
    const text = !report ? 'Report unlinked' : v.linked ? 'Report changed' : 'Report linked'
    setError('')
    setPicker(null)
    put({ ...v, linked: report, latest_action: { text, by: user.full_name, at: new Date().toISOString() } })   // at once
    try {
      put(await api.editManualValue(patientId, v.id, { report_id: report?.id ?? null }))
    } catch (e) {
      put(v)                                                                                // back as it was
      setError(`${test(v)}: the report could not be ${text.replace('Report ', '')} — ${e.message} Nothing was changed.`)
    }
  }
  const saveEdit = async (v) => {
    setEditing((x) => ({ ...x, busy: true, error: '' }))
    try {
      put(await api.editManualValue(patientId, v.id, { value: Number(editing.value), unit: editing.unit, effective_date: editing.date }))
      setEditing(null)
    } catch (e) {
      setEditing((x) => ({ ...x, busy: false, error: e.message }))
    }
  }
  const open = values?.find((v) => v.id === picker?.id)

  return (
    <Panel title={`Values entered by the care team (${values?.length ?? '…'})`} bodyClass="" className="max-w-5xl"
      description="Link or change each value’s report">
      {error && <div className="px-4 pt-3"><ErrorBox>{error}</ErrorBox></div>}
      {values && values.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full">
            <thead className="border-b border-line bg-subtle"><tr>
              <th className={th}>Test</th><th className={`${th} text-right`}>Value</th><th className={th}>Test date</th>
              <th className={th}>Entered by</th><th className={th}>Report</th><th className={th}><span className="sr-only">Actions</span></th>
            </tr></thead>
            <tbody className="divide-y divide-line">
              {values.map((v) => {
                const edit = editing?.id === v.id ? editing : null
                const la = v.latest_action
                return (
                  <tr key={v.id} className="align-top hover:bg-subtle">
                    <td className={td}>{test(v)}</td>
                    <td className={`${td} whitespace-nowrap text-right tnum`}>
                      {edit ? (
                        <span className="inline-flex items-center gap-1.5">
                          <input aria-label={`${test(v)} value`} inputMode="decimal" value={edit.value}
                            onChange={(e) => setEditing({ ...edit, value: e.target.value })} className={`${input} w-20! text-right tnum`} />
                          <select aria-label={`${test(v)} unit`} value={edit.unit} onChange={(e) => setEditing({ ...edit, unit: e.target.value })}
                            className={`${input} w-auto!`}>
                            {v.units.map((u) => <option key={u} value={u}>{u}</option>)}
                          </select>
                        </span>
                      ) : <span className="font-medium">{v.value}{unitText(v.unit)}</span>}
                      {edit?.error && <span role="alert" className="mt-1 block whitespace-normal text-left text-xs text-alert-800">{edit.error}</span>}
                    </td>
                    <td className={`${td} whitespace-nowrap tnum`}>
                      {edit ? (
                        <input aria-label={`${test(v)} test date`} type="date" max={todayIso()} value={edit.date}
                          onChange={(e) => setEditing({ ...edit, date: e.target.value })} className={`${input} w-36!`} />
                      ) : formatDate(v.effective_date)}
                    </td>
                    <td className={`${td} text-muted`}>{v.entered_by ?? '—'}</td>
                    <td className={`${td} max-w-64`}>
                      <span className="flex min-w-0 items-center gap-1.5">
                        <PaperclipButton linked={Boolean(v.linked)} label={test(v)} expanded={picker?.id === v.id}
                          onClick={() => setPicker((p) => (p?.id === v.id ? null : { id: v.id, preview: null }))} />
                        {v.linked ? (
                          <button type="button" onClick={() => setPicker({ id: v.id, preview: v.linked.id })} title={v.linked.display_name}
                            className="min-w-0 truncate text-left text-sm text-brand-700 underline-offset-2 hover:underline">
                            {v.linked.display_name}
                          </button>
                        ) : <span className="text-sm text-muted">No report linked</span>}
                      </span>
                      {la && <span className="mt-1 block text-xs text-muted tnum">{la.text} by {la.by} · {formatDateTime(la.at)}</span>}
                    </td>
                    <td className={`${td} whitespace-nowrap text-right`}>
                      {edit ? (
                        <span className="inline-flex gap-1.5">
                          <button type="button" disabled={edit.busy} onClick={() => saveEdit(v)} className={`${btn.primary} ${btn.sm}`}>
                            {edit.busy ? 'Saving…' : 'Save'}
                          </button>
                          <button type="button" onClick={() => setEditing(null)} className={`${btn.secondary} ${btn.sm}`}>Cancel</button>
                        </span>
                      ) : (
                        <button type="button" onClick={() => setEditing({ id: v.id, value: String(v.value), unit: v.unit, date: v.effective_date })}
                          aria-label={`Edit ${test(v)} value`} title="Edit value, unit or test date" className={`${btn.ghost} ${btn.iconSm} text-muted`}>
                          <Pencil size={14} aria-hidden="true" />
                        </button>
                      )}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
      {open && (
        <ReportPicker key={open.id} title={`${test(open)} ${open.value}${unitText(open.unit)} · ${formatDate(open.effective_date)}`}
          matchType={open.field} linkedId={open.linked?.id} startPreviewId={picker.preview}
          onClose={() => setPicker(null)} onLink={(r) => relink(open, r)} onUnlink={() => relink(open, null)} />
      )}
    </Panel>
  )
}

// ------------------------------------------------------------------ Reports & documents

const SOURCE_TEXT = {
  hospital_internal: 'Hospital lab', patient_upload: 'Patient upload',
  external_hospital_abdm: 'Other hospital (ABHA)', care_team_manual: 'Care team entry',
}

// Files the patient sent are listed under Data sources → Patient upload and decided in Pending reports,
// so this tab shows what is worked on here: outside-lab reports, typed readings and lab reports' values (bills have their own box).
function DocumentsAndReports({ patient, onChanged }) {
  const [reports, setReports] = useState(null)
  const [readings, setReadings] = useState(null)
  const [external, setExternal] = useState(null)
  const [error, setError] = useState('')

  const load = useCallback(() => {
    Promise.all([api.reports(patient.id), api.uploads(patient.id, true)])
      .then(([r, u]) => {
        setReports(r)
        setExternal(u.external_reports.filter((x) => x.document_type === 'lab_report'))
        // values the patient typed in without a photo: reviewed here (a reading with a photo follows its photo)
        setReadings(u.events.filter((e) => !e.photo_id)
          .sort((a, b) => (a.review_status === 'pending' ? 0 : 1) - (b.review_status === 'pending' ? 0 : 1)))
      })
      .catch((e) => setError(e.message))
  }, [patient.id])

  useEffect(() => {
    load()
  }, [load, patient])

  const reviewReading = async (eventId, status) => {
    try {
      await api.reviewEvent(patient.id, eventId, status)
      load()
      onChanged()
    } catch (e) {
      setError(e.message)
    }
  }

  return (
    <section aria-labelledby="docs" className="space-y-4">
      <h2 id="docs" className="sr-only">Documents & lab reports</h2>
      <ErrorBox>{error}</ErrorBox>
      <ExternalReportsPanel reports={external} />

      <Panel title={`Readings typed in by the patient (${readings?.length ?? '…'})`}
        description="Sent without a photo" bodyClass="">
        {readings && readings.length > 0 && (
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead className="border-b border-line bg-subtle"><tr>
                <th className={th}>Reading</th><th className={th}>Date</th><th className={th}>Review</th><th className={th}><span className="sr-only">Actions</span></th>
              </tr></thead>
              <tbody className="divide-y divide-line">
                {readings.map((e) => (
                  <tr key={e.id} className="hover:bg-subtle">
                    <td className={td}>{TYPE_LABELS[e.event_type]}: {describe(e)}{e.note && <span className="block text-xs text-muted">{e.note}</span>}</td>
                    <td className={`${td} whitespace-nowrap text-muted tnum`}>{formatDate(e.effective_date)}</td>
                    <td className={td}><ReviewChip status={e.review_status} by={e.reviewed_by_name} /></td>
                    <td className={`${td} whitespace-nowrap text-right`}>
                      {e.review_status === 'pending' && (
                        <span className="inline-flex items-center gap-1.5">
                          <button type="button" onClick={() => reviewReading(e.id, 'confirmed')} className={`${btn.secondary} ${btn.sm}`}>Confirm</button>
                          <button type="button" onClick={() => reviewReading(e.id, 'rejected')} className={`${btn.danger} ${btn.sm}`}>Reject</button>
                        </span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>

      <Panel title={`Lab reports (${reports?.length ?? '…'})`} description="Results exactly as printed" bodyClass="">
        {reports && reports.length === 0 && <p className="px-4 py-5 text-sm text-muted">No lab reports yet.</p>}
        <ul className="divide-y divide-line">
          {reports?.map((r) => (
            <LabReportItem key={r.id} report={r} patientId={patient.id} onSaved={() => { load(); onChanged() }} />
          ))}
        </ul>
      </Panel>
    </section>
  )
}

const EXTERNAL_TONE = { pending_review: 'warn', reviewed: 'ok', rejected: 'alert' }
const EXTERNAL_TEXT = { pending_review: 'Waiting for review', reviewed: 'Reviewed', rejected: 'Not usable' }

// Reports from outside labs (External Report Review). Each value is read on the split-screen review page.
function ExternalReportsPanel({ reports }) {
  return (
    <Panel title={`Reports from outside labs (${reports?.length ?? '…'})`} bodyClass=""
      description="Each value links to its report">
      {reports && reports.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full">
            <thead className="border-b border-line bg-subtle"><tr>
              <th className={th}>Test</th><th className={th}>Test date</th><th className={th}>Uploaded</th><th className={th}>Value</th>
              <th className={th}>Status</th><th className={th}><span className="sr-only">Action</span></th>
            </tr></thead>
            <tbody className="divide-y divide-line">
              {reports.map((x) => (
                <tr key={x.id} className="hover:bg-subtle">
                  <td className={td}>{x.test_label}</td>
                  <td className={`${td} whitespace-nowrap tnum`}>{formatDate(x.test_date)}</td>
                  <td className={`${td} whitespace-nowrap text-muted tnum`}>{formatDateTime(x.upload_date)}</td>
                  <td className={`${td} whitespace-nowrap font-medium tnum`}>
                    {x.result ? `${x.result.value}${x.result.unit === '%' ? ' %' : ` ${x.result.unit}`}` : <span className="font-normal text-muted">—</span>}
                  </td>
                  <td className={td}>
                    <Badge tone={EXTERNAL_TONE[x.status]}>{EXTERNAL_TEXT[x.status]}{x.reviewed_by_name ? ` · ${x.reviewed_by_name}` : ''}</Badge>
                  </td>
                  <td className={`${td} whitespace-nowrap text-right`}>
                    <Link to={`/care-team/reports/${x.patient_id}/${x.id}`} className={`${x.status === 'pending_review' ? btn.primary : btn.secondary} ${btn.sm}`}>
                      {x.status === 'pending_review' ? 'Review' : 'Open'}
                    </Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  )
}

const BILL_TONE = { pending_review: 'warn', reviewed_approved: 'ok', reviewed_rejected: 'alert' }
const BILL_TEXT = { pending_review: 'Pending review', reviewed_approved: 'Approved', reviewed_rejected: 'Rejected' }

const BILL_REASONS = [['unclear', 'Bill image unclear or unreadable'], ['medicine_mismatch', 'Does not match the prescribed medicine'],
  ['date_mismatch', 'Bill date does not match upload'], ['other', 'Other']]

// "Medical Bills": every bill this patient sent (they can send as many as they like). Each pending one is decided
// here with Decide -> Confirm / Reject. Confirming records the refill; rejecting tells the patient why.
function MedicalBills({ patient, onChanged }) {
  const [bills, setBills] = useState(null)
  const [error, setError] = useState('')
  const [done, setDone] = useState('')
  const [preview, setPreview] = useState(null)
  const load = useCallback(() => {
    api.patientBills(patient.id).then(setBills).catch((e) => setError(e.message))
  }, [patient.id])
  useEffect(() => {
    load()
  }, [load])

  const decided = (message) => { setDone(message); setError(''); load(); onChanged() }
  const pending = bills?.filter((x) => x.status === 'pending_review').length ?? 0

  return (
    <Panel title={`Medical bills (${bills?.length ?? '…'})`} bodyClass=""
      description={pending ? `${pending} waiting for a decision` : 'Confirming a bill records the refill'}>
      {(error || done) && <div className="space-y-2 px-4 pt-3"><ErrorBox>{error}</ErrorBox><Notice>{done}</Notice></div>}
      {bills && bills.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full">
            <thead className="border-b border-line bg-subtle"><tr>
              <th className={th}>Bill</th><th className={th}>Uploaded</th><th className={th}>Status</th><th className={th}>Refill dated</th>
              <th className={th}><span className="sr-only">Decision</span></th>
            </tr></thead>
            <tbody className="divide-y divide-line">
              {bills.map((x, i) => (
                <tr key={x.id} className="hover:bg-subtle">
                  <td className={td}>
                    <button type="button" onClick={() => setPreview(x)} className="inline-flex items-center gap-1.5 font-medium text-brand-700 hover:underline">
                      <ReceiptText size={15} aria-hidden="true" /> Bill {bills.length - i}
                    </button>
                  </td>
                  <td className={`${td} whitespace-nowrap tnum`}>{formatDateTime(x.upload_date)}</td>
                  <td className={td}>
                    <Badge tone={BILL_TONE[x.status]}>{BILL_TEXT[x.status]}</Badge>
                    {x.reviewed_by_name && <span className="mt-0.5 block text-xs text-muted">by {x.reviewed_by_name}</span>}
                    {x.reject_reason && <span className="block text-xs text-muted">{x.reject_reason}</span>}
                  </td>
                  <td className={`${td} whitespace-nowrap text-muted tnum`}>
                    {x.status === 'reviewed_approved' ? formatDate(x.purchase_date || x.upload_date) : '—'}
                  </td>
                  <td className={`${td} whitespace-nowrap text-right`}>
                    {x.status === 'pending_review'
                      ? <BillDecision bill={x} onDecided={decided} onError={(m) => { setDone(''); setError(m) }} />
                      : <Link to={`/care-team/bills/${x.patient_id}/${x.id}`} className={`${btn.secondary} ${btn.sm}`}>Open</Link>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {preview && (
        <DocumentPreview patientId={patient.id} onClose={() => setPreview(null)}
          title={`Medicine bill · uploaded ${formatDateTime(preview.upload_date)}`}
          report={{ id: preview.id, test_label: 'Medicine bill', upload_date: preview.upload_date, content_type: preview.content_type }} />
      )}
    </Panel>
  )
}

// Decide -> a small menu: Confirm (records the refill at once) or Reject (asks why - the patient is told).
function BillDecision({ bill, onDecided, onError }) {
  const [open, setOpen] = useState(false)
  const [rejecting, setRejecting] = useState(false)
  const [reason, setReason] = useState('')
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [place, setPlace] = useState(null)
  const box = useRef(null)
  const menu = useRef(null)
  // The menu is drawn over the page (not inside the table's scroll area, which would cut it off): under the
  // button, or above it when there is no room below; it follows the button when the page scrolls.
  const where = () => {
    const r = box.current.getBoundingClientRect()
    const right = Math.max(8, window.innerWidth - r.right)
    return window.innerHeight - r.bottom > 280 ? { top: r.bottom + 4, right } : { bottom: window.innerHeight - r.top + 4, right }
  }
  useEffect(() => {
    if (!open) return undefined
    const away = (e) => { if (!box.current?.contains(e.target) && !menu.current?.contains(e.target)) setOpen(false) }
    const esc = (e) => { if (e.key === 'Escape') { setOpen(false); box.current?.querySelector('button')?.focus() } }
    const follow = () => setPlace(where())
    document.addEventListener('mousedown', away)
    document.addEventListener('keydown', esc)
    window.addEventListener('scroll', follow, true)
    window.addEventListener('resize', follow)
    return () => {
      document.removeEventListener('mousedown', away); document.removeEventListener('keydown', esc)
      window.removeEventListener('scroll', follow, true); window.removeEventListener('resize', follow)
    }
  }, [open])

  const act = async (fn, message) => {
    setBusy(true)
    try {
      await fn()
      setOpen(false)
      onDecided(message)
    } catch (e) {
      onError(e.message)
    } finally {
      setBusy(false)
    }
  }
  const confirm = () => act(() => api.approveBill(bill.patient_id, bill.id, null), 'Bill confirmed. The refill is recorded and the patient has been told.')
  const reject = () => act(() => api.rejectBill(bill.patient_id, bill.id, reason, note), 'Bill rejected. The patient has been told why.')

  return (
    <div ref={box} className="relative inline-block text-left">
      <button type="button" onClick={() => { setPlace(where()); setOpen((o) => !o); setRejecting(false) }} aria-haspopup="menu" aria-expanded={open}
        className={`${btn.primary} ${btn.sm}`}>
        Decide <ChevronDown size={13} aria-hidden="true" className={open ? 'rotate-180' : ''} />
      </button>
      {open && place && createPortal(
        <div ref={menu} role="menu" aria-label="Decide on this bill" style={{ position: 'fixed', ...place }}
          className="fade-in z-40 w-64 whitespace-normal rounded-md border border-line bg-surface py-1 text-left shadow-sm">
          {!rejecting ? (<>
            <button type="button" role="menuitem" disabled={busy} onClick={confirm}
              className="flex w-full items-center gap-2 px-3 py-2 text-sm text-ink hover:bg-subtle">
              <CheckCircle2 size={15} aria-hidden="true" className="text-ok-700" /> {busy ? 'Confirming…' : 'Confirm'}
            </button>
            <button type="button" role="menuitem" onClick={() => setRejecting(true)}
              className="flex w-full items-center gap-2 px-3 py-2 text-sm text-ink hover:bg-subtle">
              <XCircle size={15} aria-hidden="true" className="text-alert-700" /> Reject
            </button>
          </>) : (
            <fieldset className="px-3 py-2">
              <legend className="text-sm font-semibold text-ink">Why is it rejected?</legend>
              <div className="mt-1.5 space-y-1">
                {BILL_REASONS.map(([code, label]) => (
                  <label key={code} className="flex items-start gap-2 text-sm text-ink">
                    <input type="radio" name={`reason-${bill.id}`} value={code} checked={reason === code}
                      onChange={() => setReason(code)} className="mt-1 accent-brand-600" /> {label}
                  </label>
                ))}
              </div>
              {reason === 'other' && (
                <input aria-label="Reason" maxLength={200} value={note} onChange={(e) => setNote(e.target.value)}
                  placeholder="Write the reason" className={`${input} mt-2`} />
              )}
              <div className="mt-2.5 flex gap-2">
                <button type="button" disabled={busy || !reason || (reason === 'other' && note.trim().length < 3)} onClick={reject}
                  className={`${btn.danger} ${btn.sm}`}>{busy ? 'Rejecting…' : 'Reject bill'}</button>
                <button type="button" onClick={() => setRejecting(false)} className={`${btn.secondary} ${btn.sm}`}>Back</button>
              </div>
            </fieldset>
          )}
        </div>,
        document.body,
      )}
    </div>
  )
}

const cap = (t) => (t ? t.charAt(0).toUpperCase() + t.slice(1) : t)

function LabReportItem({ report: r, patientId, onSaved }) {
  const [entering, setEntering] = useState(false)
  const count = r.results.length
  const rejected = r.document_status === 'rejected'
  return (
    <li>
      <details open={entering || undefined}>
        <summary className="flex cursor-pointer flex-wrap items-center gap-x-3 gap-y-1 px-4 py-2.5 text-sm hover:bg-subtle">
          <span className="font-medium text-ink">{r.report_type ? cap(r.report_type) : r.laboratory_name}</span>
          <span className="text-muted tnum">{formatDate(r.report_date)}</span>
          <span className="text-xs text-muted">{SOURCE_TEXT[r.source]}{r.report_type && r.source !== 'patient_upload' ? ` · ${r.laboratory_name}` : ''}</span>
          <span className="ml-auto flex items-center gap-2">
            {count ? <span className="text-xs text-muted tnum">{count} {count === 1 ? 'result' : 'results'}</span>
              : <Badge tone="warn">Values not entered yet</Badge>}
            {r.document_status && <ReviewChip status={r.document_status} />}
          </span>
        </summary>
        <div className="border-t border-line bg-subtle px-4 py-3">
          {r.document_id && (
            <a href={api.documentUrl(patientId, r.document_id)} target="_blank" rel="noreferrer"
              className="mb-2 inline-flex items-center gap-1 text-sm font-medium text-brand-700 hover:underline">
              <FileImage size={14} aria-hidden="true" /> Open the file ({r.document_name})</a>
          )}
          {count > 0 && (
            <table className="mb-2 w-full rounded-md border border-line bg-surface">
              <thead className="border-b border-line"><tr>
                <th className={th}>Test</th><th className={`${th} text-right`}>Result</th><th className={th}>Reference (as printed)</th>
              </tr></thead>
              <tbody className="divide-y divide-line">
                {r.results.map((t) => (
                  <tr key={t.id}>
                    <td className={td}>{t.test_name}</td>
                    <td className={`${td} text-right font-medium tnum`}>{t.value_text}{t.unit ? (t.unit === '%' ? ' %' : ` ${t.unit}`) : ''}</td>
                    <td className={`${td} tnum`}>{t.reference_text ?? <span className="text-muted">Not printed</span>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {rejected ? (
            <p className="text-sm text-muted">This report was rejected, so its values are not used.</p>
          ) : entering ? (
            <EnterResults report={r} patientId={patientId} onCancel={() => setEntering(false)} onSaved={() => { setEntering(false); onSaved() }} />
          ) : r.source === 'patient_upload' && (
            <button type="button" onClick={() => setEntering(true)} className={count ? btn.secondary : btn.primary}>
              {count ? 'Add more results' : 'Enter results from this report'}
            </button>
          )}
        </div>
      </details>
    </li>
  )
}

// ------------------------------------------------------------------ patient sign-in help

function ResetPassword({ patient }) {
  const [open, setOpen] = useState(false)
  const [result, setResult] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const reset = async () => {
    setBusy(true)
    setError('')
    try {
      setResult(await api.resetPatientPassword(patient.id))
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <button type="button" onClick={() => setOpen(true)} className={btn.secondary}>
        <KeyRound size={15} aria-hidden="true" /> Reset patient password
      </button>
      {open && (
        <Modal title="Reset patient password" width="30rem" onClose={() => { setOpen(false); setResult(null) }}
          footer={(close) => result
            ? <button type="button" onClick={close} className={btn.primary}>Done</button>
            : <>
                <button type="button" onClick={close} className={btn.secondary}>Cancel</button>
                <button type="button" onClick={reset} disabled={busy} className={btn.primary}>{busy ? 'Resetting…' : 'Create temporary password'}</button>
              </>}>
          <div className="px-4 py-4">
            {result ? <PatientCredentials title="New temporary password" credentials={result} /> : (
              <>
                <p className="text-sm text-ink">
                  Use this if {patient.full_name} forgot their password. A new temporary password is created, their old
                  password stops working, and they are signed out of the patient app.
                </p>
                <div className="mt-3"><ErrorBox>{error}</ErrorBox></div>
              </>
            )}
          </div>
        </Modal>
      )}
    </>
  )
}
