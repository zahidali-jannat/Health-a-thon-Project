import { CheckCircle2, Copy, History, Palette, RefreshCw, Share2, UserRound } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { Link, Navigate, useParams, useSearchParams } from 'react-router-dom'
import { api, formatDate, formatDateTime, formatTime } from '../api.js'
import { useClinician } from '../auth.jsx'
import { AppShell, btn, ErrorBox, input, SETTINGS_SECTIONS, td, th } from '../components/ui.jsx'

const card = 'rounded-lg border border-line bg-surface px-4 py-4'
const field = `${input} mt-1`
const primary = btn.primary

const PAGES = {
  account: () => <AccountSection />,
  appearance: () => <AppearanceSection />,
  'consultation-hours': () => <ConsultationHoursSection />,
  sharing: () => <SharingSection />,
  'access-log': () => <AccessLogSection />,
  'patient-activity': () => <PatientActivitySection />,
}

// Each Settings option is its own page (/care-team/settings/<section>) showing only that section.
// The pages are listed in the sidebar under Account → Settings.
export default function Settings() {
  const { section } = useParams()
  useEffect(() => {
    window.scrollTo(0, 0)
  }, [section])
  if (!PAGES[section]) return <Navigate to="/care-team/settings/account" replace />
  const current = SETTINGS_SECTIONS.find((s) => s.id === section)
  return (
    <AppShell title={current.label} subtitle={current.text}
      breadcrumbs={[{ label: 'Settings', to: '/care-team/settings/account' }, { label: current.label }]}>
      <div className="max-w-4xl">{PAGES[section]()}</div>
    </AppShell>
  )
}

// The page title already names the section, so each section only adds its short description.
function SectionTitle({ text }) {
  return text ? <p className="mb-4 text-sm text-muted">{text}</p> : null
}

function Saved({ children }) {
  if (!children) return null
  return (
    <p role="status" className="flex items-center gap-1.5 text-sm font-semibold text-ok-700">
      <CheckCircle2 size={16} aria-hidden="true" /> {children}
    </p>
  )
}

// ------------------------------------------------------------------ Consultation hours

// A doctor's own booking hours. Slots last `slot_duration_minutes`; the next one starts after a buffer.
function ConsultationHoursSection() {
  const [f, setF] = useState(null)
  const [error, setError] = useState('')
  const [saved, setSaved] = useState('')
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    api.doctorProfile().then(({ doctor: d }) => {
      setF({ start: d.working_start_time, end: d.working_end_time, length: d.slot_duration_minutes, buffer: d.buffer_minutes })
    }).catch((e) => setError(e.message))
  }, [])
  if (!f) return <ErrorBox>{error}</ErrorBox>

  const set = (k) => (e) => { setSaved(''); setF({ ...f, [k]: e.target.value }) }
  const save = async (e) => {
    e.preventDefault()
    setBusy(true)
    setError('')
    try {
      await api.saveDoctorProfile({ working_start_time: f.start, working_end_time: f.end,
        slot_duration_minutes: Number(f.length), buffer_minutes: Number(f.buffer) })
      setSaved('Saved. Days nobody has booked yet use the new hours.')
    } catch (e2) {
      setError(e2.message)
    } finally {
      setBusy(false)
    }
  }
  const hm = (x) => `${String(Math.floor(x / 60)).padStart(2, '0')}:${String(x % 60).padStart(2, '0')}`
  const [h0, m0] = (f.start || '00:00').split(':').map(Number)
  const first = f.start && Number(f.length) > 0 ? [0, 1].map((i) => {
    const t = h0 * 60 + m0 + i * (Number(f.length) + Number(f.buffer || 0))
    return `${formatTime(hm(t))} – ${formatTime(hm(t + Number(f.length)))}`
  }) : []

  return (
    <section aria-labelledby="settings-page" className={card}>
      <SectionTitle text="Patients on your care team book these times in the patient app." />
      <form onSubmit={save} className="space-y-4">
        <div className="grid gap-3 sm:grid-cols-4">
          <label className="text-sm font-medium text-ink">From<input type="time" step={300} required value={f.start} onChange={set('start')} className={field} /></label>
          <label className="text-sm font-medium text-ink">To<input type="time" step={300} required value={f.end} onChange={set('end')} className={field} /></label>
          <label className="text-sm font-medium text-ink">Slot (minutes)<input type="number" min={5} max={240} required value={f.length} onChange={set('length')} className={field} /></label>
          <label className="text-sm font-medium text-ink">Buffer (minutes)<input type="number" min={0} max={120} required value={f.buffer} onChange={set('buffer')} className={field} /></label>
        </div>
        {first.length > 0 && <p className="text-sm text-muted tnum">First slots: {first.join(', then ')}</p>}
        <ErrorBox>{error}</ErrorBox>
        <Saved>{saved}</Saved>
        <button type="submit" disabled={busy} className={primary}>{busy ? 'Saving…' : 'Save hours'}</button>
      </form>
    </section>
  )
}

// ------------------------------------------------------------------ Account

function AccountSection() {
  const { user, setUser } = useClinician()
  const [f, setF] = useState({ full_name: user.full_name, phone: user.phone })
  const [error, setError] = useState('')
  const [saved, setSaved] = useState('')
  const [busy, setBusy] = useState(false)
  const [copied, setCopied] = useState(false)

  const save = async (e) => {
    e.preventDefault()
    setBusy(true)
    setError('')
    setSaved('')
    try {
      setUser(await api.updateAccount(f))
      setSaved('Account details saved.')
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <section aria-labelledby="settings-page" className={card}>
      <SectionTitle id="account" icon={UserRound} title="Account" />
      <div className="rounded-md border border-line bg-subtle px-3 py-3">
        <p className="text-xs font-semibold uppercase tracking-wide text-muted">Clinician ID</p>
        <p className="mt-1 flex flex-wrap items-center gap-3">
          <span className="text-lg font-semibold tracking-wider text-ink tnum">{user.clinician_code}</span>
          <button type="button" className="inline-flex items-center gap-1 text-sm font-semibold text-brand-700 hover:underline"
            onClick={() => navigator.clipboard?.writeText(user.clinician_code).then(() => setCopied(true)).catch(() => {})}>
            <Copy size={16} aria-hidden="true" /> {copied ? 'Copied' : 'Copy'}
          </button>
        </p>
        <p className="mt-1 text-sm text-muted">Created by the system and can’t be changed. Patients and colleagues use it to share records with you.</p>
      </div>

      <form onSubmit={save} className="mt-5 grid gap-4 sm:grid-cols-2">
        <label className="block text-[13px] font-medium text-ink">Full name
          <input value={f.full_name} onChange={(e) => setF({ ...f, full_name: e.target.value })} required maxLength={120} className={field} />
        </label>
        <label className="block text-[13px] font-medium text-ink">Phone number
          <input value={f.phone} onChange={(e) => setF({ ...f, phone: e.target.value })} required inputMode="tel" maxLength={20} className={field} />
        </label>
        <div className="sm:col-span-2"><ErrorBox>{error}</ErrorBox></div>
        <div className="flex items-center gap-4 sm:col-span-2">
          <button type="submit" disabled={busy} className={primary}>{busy ? 'Saving…' : 'Save changes'}</button>
          <Saved>{saved}</Saved>
        </div>
      </form>

      <ChangePassword />
    </section>
  )
}

function ChangePassword() {
  const [f, setF] = useState({ current: '', next: '', again: '' })
  const [error, setError] = useState('')
  const [saved, setSaved] = useState('')
  const [busy, setBusy] = useState(false)
  const set = (k) => (e) => setF({ ...f, [k]: e.target.value })

  const save = async (e) => {
    e.preventDefault()
    setError('')
    setSaved('')
    if (f.next !== f.again) {
      setError('The new passwords are not the same.')
      return
    }
    setBusy(true)
    try {
      await api.changeClinicianPassword(f.current, f.next)
      setF({ current: '', next: '', again: '' })
      setSaved('Password changed. You were signed out on every other device.')
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <form onSubmit={save} className="mt-6 border-t border-line pt-5">
      <h3 className="text-sm font-semibold text-ink">Change password</h3>
      <div className="mt-3 grid gap-4 sm:grid-cols-3">
        <label className="block text-[13px] font-medium text-ink">Current password
          <input type="password" value={f.current} onChange={set('current')} autoComplete="current-password" required className={field} />
        </label>
        <label className="block text-[13px] font-medium text-ink">New password
          <input type="password" value={f.next} onChange={set('next')} autoComplete="new-password" required minLength={8} className={field} />
        </label>
        <label className="block text-[13px] font-medium text-ink">Type it again
          <input type="password" value={f.again} onChange={set('again')} autoComplete="new-password" required minLength={8} className={field} />
        </label>
      </div>
      <p className="mt-1 text-xs text-muted">At least 8 characters.</p>
      <div className="mt-3"><ErrorBox>{error}</ErrorBox></div>
      <div className="mt-3 flex items-center gap-4">
        <button type="submit" disabled={busy} className={primary}>{busy ? 'Saving…' : 'Change password'}</button>
        <Saved>{saved}</Saved>
      </div>
    </form>
  )
}

// ------------------------------------------------------------------ Appearance

const THEMES = [
  { id: 'light', label: 'Light', text: 'Bright and crisp. The default.', swatch: ['#f3f5f7', '#ffffff', '#1c5ea8', '#17222d'] },
  { id: 'dark', label: 'Dark', text: 'Low glare for dim rooms and night shifts.', swatch: ['#0e1318', '#161d24', '#7fb4ea', '#e6ecf1'] },
  { id: 'pleasant', label: 'Pleasant', text: 'Warm, soft, paper-like. Easy on the eyes.', swatch: ['#f2eee6', '#fbf9f4', '#2d5d8c', '#29241e'] },
]

function AppearanceSection() {
  const { user, setUser } = useClinician()
  const [error, setError] = useState('')

  const choose = async (theme) => {
    if (theme === user.theme) return
    const previous = user
    setUser({ ...user, theme })                     // apply instantly
    try {
      setUser(await api.updateAccount({ theme }))    // and remember it on the account
    } catch (e) {
      setUser(previous)
      setError(e.message)
    }
  }

  return (
    <section aria-labelledby="settings-page" className={card}>
      <SectionTitle id="appearance" icon={Palette} title="Appearance"
        text="Saved to your account, so it follows you to any computer. The patient app is not affected." />
      <div role="radiogroup" aria-label="Theme" className="grid gap-3 sm:grid-cols-3">
        {THEMES.map((t) => {
          const active = user.theme === t.id
          return (
            <button key={t.id} type="button" role="radio" aria-checked={active} onClick={() => choose(t.id)}
              className={`rounded-lg border p-3 text-left ${active ? 'border-brand-600 ring-1 ring-brand-600' : 'border-line hover:border-line-strong'}`}>
              <span className="flex h-16 overflow-hidden rounded-lg border border-line" aria-hidden="true">
                <span className="flex-1" style={{ background: t.swatch[0] }} />
                <span className="flex flex-[2] flex-col justify-center gap-1.5 px-2" style={{ background: t.swatch[1] }}>
                  <span className="h-2 w-3/4 rounded" style={{ background: t.swatch[3], opacity: 0.8 }} />
                  <span className="h-2 w-1/2 rounded" style={{ background: t.swatch[2] }} />
                </span>
              </span>
              <span className="mt-2.5 flex items-center justify-between text-sm font-semibold text-ink">
                {t.label}
                {active && <CheckCircle2 size={18} className="text-brand-700" aria-hidden="true" />}
              </span>
              <span className="mt-0.5 block text-sm text-muted">{t.text}</span>
            </button>
          )
        })}
      </div>
      <div className="mt-3"><ErrorBox>{error}</ErrorBox></div>
    </section>
  )
}

// ------------------------------------------------------------------ Sharing

function usePatients() {
  const [patients, setPatients] = useState(null)
  useEffect(() => {
    api.patients().then((d) => setPatients(d.patients)).catch(() => setPatients([]))
  }, [])
  return patients
}

function PatientSelect({ patients, value, onChange, allowAll }) {
  return (
    <select value={value} onChange={(e) => onChange(e.target.value)} className={`${field} sm:w-96`}>
      {allowAll && <option value="">All my patients</option>}
      {!allowAll && <option value="" disabled>Choose a patient…</option>}
      {patients.map((p) => (
        <option key={p.id} value={p.id}>{p.full_name} · {p.patient_code}</option>
      ))}
    </select>
  )
}

function SharingSection() {
  const patients = usePatients()
  const [params, setParams] = useSearchParams()
  const [patientId, setPatientId] = useState(params.get('patient') || '')
  const [team, setTeam] = useState([])
  const [code, setCode] = useState('')
  const [error, setError] = useState('')
  const [saved, setSaved] = useState('')

  useEffect(() => {
    if (!patientId && patients?.length === 1) setPatientId(patients[0].id)
  }, [patients, patientId])

  useEffect(() => {
    setTeam([])
    if (patientId) api.careTeam(patientId).then(setTeam).catch((e) => setError(e.message))
  }, [patientId])

  const pick = (id) => {
    setPatientId(id)
    setSaved('')
    setError('')
    setParams(id ? { patient: id } : {}, { replace: true })
  }

  const share = async (e) => {
    e.preventDefault()
    setError('')
    setSaved('')
    try {
      setTeam(await api.addToCareTeam(patientId, code))
      setSaved(`Shared with ${code.trim().toUpperCase()}.`)
      setCode('')
    } catch (err) {
      setError(err.message)
    }
  }

  return (
    <section aria-labelledby="settings-page" className={card}>
      <SectionTitle id="sharing" icon={Share2} title="Sharing"
        text="Give a colleague access to one of your patients using their Clinician ID. Only the care team can open a record." />
      {patients && patients.length === 0 ? (
        <p className="text-sm text-muted">You don’t have any patients yet.</p>
      ) : patients && (
        <>
          <label className="block text-[13px] font-medium text-ink">Patient
            <PatientSelect patients={patients} value={patientId} onChange={pick} />
          </label>
          {patientId && (
            <div className="mt-5 grid gap-6 md:grid-cols-2">
              <div>
                <h3 className="text-sm font-semibold text-ink">Current care team</h3>
                <ul className="mt-2 divide-y divide-line rounded-lg border border-line">
                  {team.map((m) => (
                    <li key={m.clinician_code} className="flex items-center justify-between px-3 py-2 text-sm">
                      <span className="text-ink">{m.full_name}</span>
                      <span className="text-sm text-muted">{m.clinician_code}</span>
                    </li>
                  ))}
                </ul>
              </div>
              <form onSubmit={share}>
                <label className="block text-[13px] font-medium text-ink">Share with a colleague (Clinician ID)
                  <span className="mt-1 flex gap-2">
                    <input value={code} onChange={(e) => setCode(e.target.value)} required maxLength={20} placeholder="CLN-XXXXXX"
                      className={`${field} mt-0 uppercase`} />
                    <button type="submit" className={`${primary} shrink-0`}>Share</button>
                  </span>
                </label>
                <div className="mt-2"><ErrorBox>{error}</ErrorBox></div>
                <div className="mt-2"><Saved>{saved}</Saved></div>
              </form>
            </div>
          )}
        </>
      )}
    </section>
  )
}

// ------------------------------------------------------------------ Access log

export const ACTIONS = {
  PATIENT_VIEWED: 'Opened record', PATIENT_CREATED: 'Added patient', DOCUMENT_UPLOADED: 'Uploaded a document',
  DOCUMENT_VIEWED: 'Opened a document', DOCUMENT_CONFIRMED: 'Confirmed a document', DOCUMENT_REJECTED: 'Rejected a document',
  REPORT_CREATED: 'Created a lab report', REPORT_RESULTS_ENTERED: 'Entered lab results', CLINICAL_DATA_ENTERED: 'Entered data',
  CARE_TEAM_ADDED: 'Shared with care team', CARE_TEAM_REMOVED: 'Removed from care team',
  PATIENT_ENTRY_CONFIRMED: 'Confirmed a patient entry', PATIENT_ENTRY_REJECTED: 'Rejected a patient entry',
  PATIENT_PASSWORD_RESET: 'Reset patient password', PASSWORD_CHANGED: 'Changed password', ACCOUNT_CREATED: 'Created account',
  ACCESS_DENIED: 'Access refused', LOGIN: 'Signed in',
  CONSENT_REQUESTED: 'Consent requested', CONSENT_GRANTED: 'Consent approved', CONSENT_DENIED: 'Consent denied',
  CONSENT_EXPIRED: 'Consent expired', CONSENT_DATA_RECEIVED: 'Records received',
  APPOINTMENT_BOOKED: 'Booked an appointment', APPOINTMENT_NEEDS_RESCHEDULE: 'Appointment needs a new time',
  RESCHEDULE_NOTICE_SENT: 'Sent a new-time message', DOCTOR_UNAVAILABLE: 'Marked doctor unavailable',
}

function AccessLogSection() {
  const patients = usePatients()
  const [params] = useSearchParams()
  const [patientId, setPatientId] = useState(params.get('patient') || '')
  const [rows, setRows] = useState(null)
  const [error, setError] = useState('')

  const load = useCallback(() => {
    api.myAccessLog(patientId).then(setRows).catch((e) => setError(e.message))
  }, [patientId])

  useEffect(() => {
    load()
  }, [load])

  return (
    <section aria-labelledby="settings-page" className={card}>
      <SectionTitle id="access-log" icon={History} title="Access log"
        text="Who opened, uploaded or changed your patients’ records. Latest first." />
      {patients && patients.length > 0 && (
        <div className="flex flex-wrap items-end gap-3">
          <label className="block text-[13px] font-medium text-ink">Show
            <PatientSelect patients={patients} value={patientId} onChange={setPatientId} allowAll />
          </label>
          <button type="button" onClick={load}
            className={btn.secondary}>
            <RefreshCw size={16} aria-hidden="true" /> Refresh
          </button>
        </div>
      )}
      <ErrorBox>{error}</ErrorBox>
      {rows && rows.length === 0 && <p className="mt-4 text-sm text-muted">No activity yet.</p>}
      {rows && rows.length > 0 && (
        <div className="mt-4 max-h-[32rem] overflow-auto rounded-lg border border-line">
          <table className="w-full text-left text-sm">
            <thead className="sticky top-0 border-b border-line bg-subtle">
              <tr>
                <th className={th}>Time</th>
                {!patientId && <th className={th}>Patient</th>}
                <th className={th}>What happened</th>
                <th className={th}>By</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((a, i) => (
                <tr key={i} className="border-t border-line hover:bg-subtle">
                  <td className={`${td} whitespace-nowrap text-muted tnum`}>{formatDateTime(a.at)}</td>
                  {!patientId && (
                    <td className={`${td} whitespace-nowrap`}>{a.patient_name}<span className="block text-xs text-muted">{a.patient_code}</span></td>
                  )}
                  <td className={`${td} ${a.action === 'ACCESS_DENIED' ? 'text-alert-800' : ''}`}>
                    <span className="font-semibold">{ACTIONS[a.action] ?? a.action}</span>
                    {a.detail && <span className="block text-muted">{a.detail}</span>}
                  </td>
                  <td className={`${td} text-muted`}>{a.actor_label}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}

// ------------------------------------------------------------------ Activity by patients

// What patients sent from the app (readings, refills, photos, reports) in the last 14 days - your patients only.
function PatientActivitySection() {
  const patients = usePatients()
  const [patientId, setPatientId] = useState('')
  const [rows, setRows] = useState(null)
  const [error, setError] = useState('')

  const load = useCallback(() => {
    api.worklist().then((w) => setRows(w.activity)).catch((e) => setError(e.message))
  }, [])
  useEffect(() => {
    load()
  }, [load])

  const shown = (rows ?? []).filter((a) => !patientId || a.patient_id === patientId)
  return (
    <section aria-labelledby="settings-page" className={card}>
      <SectionTitle text="Readings, refills, photos and reports your patients sent from the app. Latest first." />
      {patients && patients.length > 0 && (
        <div className="flex flex-wrap items-end gap-3">
          <label className="block text-[13px] font-medium text-ink">Show
            <PatientSelect patients={patients} value={patientId} onChange={setPatientId} allowAll />
          </label>
          <button type="button" onClick={load} className={btn.secondary}>
            <RefreshCw size={16} aria-hidden="true" /> Refresh
          </button>
        </div>
      )}
      <ErrorBox>{error}</ErrorBox>
      {rows && shown.length === 0 && <p className="mt-4 text-sm text-muted">No activity from patients in the last 14 days.</p>}
      {shown.length > 0 && (
        <div className="mt-4 max-h-[32rem] overflow-auto rounded-lg border border-line">
          <table className="w-full text-left text-sm">
            <thead className="sticky top-0 border-b border-line bg-subtle">
              <tr>
                <th className={th}>Date</th>
                {!patientId && <th className={th}>Patient</th>}
                <th className={th}>What they sent</th>
              </tr>
            </thead>
            <tbody>
              {shown.map((a, i) => (
                <tr key={i} className="border-t border-line hover:bg-subtle">
                  <td className={`${td} whitespace-nowrap text-muted tnum`}>{formatDate(a.at)}</td>
                  {!patientId && (
                    <td className={`${td} whitespace-nowrap`}>
                      <Link to={`/care-team/patients/${a.patient_id}`} className="font-medium text-ink hover:text-brand-700 hover:underline">{a.patient_name}</Link>
                      <span className="block text-xs text-muted tnum">{a.patient_code}</span>
                    </td>
                  )}
                  <td className={td}>{a.label}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}
