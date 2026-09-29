import { ChevronDown, UserPlus } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api, daysBetween, formatDate, formatDateTime } from '../api.js'
import { useClinician } from '../auth.jsx'
import AddPatientDialog from '../components/AddPatient.jsx'
import { AppShell, Badge, btn, ErrorBox, Panel, StatusBadge, td, th } from '../components/ui.jsx'
import { mainConcern } from './Patients.jsx'

const REVIEW_KIND = {
  document: 'File to review', entry: 'Value to confirm', results: 'Report values to enter', external: 'Outside lab report',
  bill: 'Medicine bill to verify',
}
const reviewLink = (r) => (r.kind === 'external' ? `/care-team/reports/${r.patient_id}/${r.id}`
  : r.kind === 'bill' ? `/care-team/bills/${r.patient_id}/${r.id}` : `/care-team/patients/${r.patient_id}?tab=reports`)

export default function Overview() {
  const { user } = useClinician()
  const navigate = useNavigate()
  const [patients, setPatients] = useState(null)
  const [work, setWork] = useState(null)
  const [error, setError] = useState('')
  const [adding, setAdding] = useState(false)

  useEffect(() => {
    Promise.all([api.patients(), api.worklist()])
      .then(([p, w]) => { setPatients(p); setWork(w) })
      .catch((e) => setError(e.message))
  }, [])

  const asOf = work?.as_of
  const list = patients?.patients ?? []
  const flagged = list.filter((p) => p.assessment.flagged)
  const high = flagged.filter((p) => p.assessment.level === 'high_priority').length
  const today = work?.upcoming_visits.filter((v) => v.date === asOf) ?? []
  const firstName = user.full_name.replace(/^Dr\.?\s+/i, 'Dr. ').split(' ').slice(0, 2).join(' ')
  const open = (id) => navigate(`/care-team/patients/${id}`)

  return (
    <AppShell title="Overview" breadcrumbs={[{ label: 'Overview' }]}
      subtitle={asOf && `${firstName} · ${new Date(`${asOf}T00:00:00`).toLocaleDateString('en-GB', { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' })}`}
      actions={<button type="button" onClick={() => setAdding(true)} className={btn.primary}><UserPlus size={16} aria-hidden="true" /> Add patient</button>}>
      <ErrorBox>{error}</ErrorBox>

      {patients && work && (
        <>
          {/* key figures: one strip, not four decorative cards */}
          <dl className="grid grid-cols-2 divide-line overflow-hidden rounded-lg border border-line bg-surface sm:grid-cols-4 sm:divide-x">
            {[
              ['Patients under care', list.length, null, '/care-team/patients'],
              ['High priority', high, high ? 'text-alert-800' : null, '/care-team/patients'],
              ['Worsening since last visit', flagged.length - high, flagged.length - high ? 'text-warn-800' : null, '/care-team/patients'],
              ['Not reviewed', work.reviews.length, work.reviews.length ? 'text-brand-700' : null, null],
            ].map(([label, value, tone], i) => (
              <div key={label} className={`px-4 py-3 ${i > 1 ? 'border-t border-line sm:border-t-0' : ''} ${i === 1 ? 'border-l border-line sm:border-l-0' : ''} ${i === 3 ? 'border-l border-line sm:border-l-0' : ''}`}>
                <dt className="text-xs text-muted">{label}</dt>
                <dd className={`mt-0.5 text-2xl font-semibold tnum ${tone ?? 'text-ink'}`}>{value}</dd>
              </div>
            ))}
          </dl>

          {list.length === 0 && (
            <Panel title="Getting started" className="mt-4">
              <ol className="list-decimal space-y-1 pl-5 text-sm text-ink">
                <li>Add a patient — either an existing Patient ID, or register someone new.</li>
                <li>Or give your Clinician ID <strong>{user.clinician_code}</strong> to patients; they add you under “My doctors”.</li>
                <li>Readings, refills and reports they send appear here for review.</li>
              </ol>
            </Panel>
          )}

          <div className="mt-4 grid gap-4 xl:grid-cols-[minmax(0,1fr)_22rem]">
            <div className="min-w-0 space-y-4">
              <Panel title={<span className="text-base font-bold">Attention</span>} bodyClass=""
                actions={<Link to="/care-team/patients" className="text-sm text-brand-700 hover:underline">All patients</Link>}>
                {flagged.length === 0 ? (
                  <p className="px-4 py-6 text-sm text-muted">No flagged patients. All patients are within the rules.</p>
                ) : (<>
                  <ul className="divide-y divide-line md:hidden" aria-label="Flagged patients">
                    {flagged.map((p) => (
                      <li key={p.id}>
                        <Link to={`/care-team/patients/${p.id}`} className="block px-4 py-2.5 hover:bg-subtle">
                          <span className="flex items-center justify-between gap-2">
                            <span className="font-medium text-ink">{p.full_name}</span>
                            <StatusBadge level={p.assessment.level} short />
                          </span>
                          <span className="mt-0.5 block text-xs text-muted">{mainConcern(p)}</span>
                          <span className="block text-xs text-muted tnum">{p.patient_code} · Next visit {formatDate(p.next_visit)}</span>
                        </Link>
                      </li>
                    ))}
                  </ul>
                  <div className="hidden overflow-x-auto md:block">
                    <table className="w-full">
                      <thead className="border-b border-line bg-subtle"><tr>
                        <th className={th}>Patient</th><th className={th}>Status</th><th className={th}>Reason</th><th className={`${th} whitespace-nowrap`}>Next visit</th>
                      </tr></thead>
                      <tbody className="divide-y divide-line">
                        {flagged.map((p) => (
                          <tr key={p.id} onClick={() => open(p.id)} className="cursor-pointer hover:bg-subtle">
                            <td className={td}>
                              <Link to={`/care-team/patients/${p.id}`} onClick={(e) => e.stopPropagation()} className="font-medium hover:text-brand-700 hover:underline">{p.full_name}</Link>
                              <span className="block text-xs text-muted tnum">{p.patient_code}</span>
                            </td>
                            <td className={td}><StatusBadge level={p.assessment.level} short /></td>
                            <td className={td}><ReasonCell patient={p} /></td>
                            <td className={`${td} whitespace-nowrap tnum`}>{formatDate(p.next_visit)}
                              {p.next_visit && <span className="block text-xs text-muted">in {daysBetween(asOf, p.next_visit)} days</span>}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </>)}
              </Panel>

              <Panel bodyClass=""
                title={<NotReviewedTitle n={work.reviews.length} />}
                actions={<Link to="/care-team/reports" className="text-sm text-brand-700 hover:underline">Open Pending reports</Link>}>
                {work.reviews.length > 0 ? (<>
                  <ul className="divide-y divide-line md:hidden" aria-label="Waiting for review">
                    {work.reviews.map((r) => (
                      <li key={`${r.kind}-${r.id}`}>
                        <Link to={reviewLink(r)} className="block px-4 py-2.5 hover:bg-subtle">
                          <span className="flex items-center justify-between gap-2">
                            <span className="font-medium text-ink">{r.patient_name}</span>
                            <Badge tone={['results', 'external', 'bill'].includes(r.kind) ? 'info' : 'warn'}>{REVIEW_KIND[r.kind]}</Badge>
                          </span>
                          <span className="mt-0.5 block text-sm text-ink">{r.label}</span>
                          <span className="block text-xs text-muted tnum">Received {r.at.length > 10 ? formatDateTime(r.at) : formatDate(r.at)}</span>
                        </Link>
                      </li>
                    ))}
                  </ul>
                  <div className="hidden overflow-x-auto md:block">
                    <table className="w-full">
                      <thead className="border-b border-line bg-subtle"><tr>
                        <th className={th}>Patient</th><th className={th}>Item</th><th className={th}>Type</th><th className={th}>Received</th><th className={th}><span className="sr-only">Action</span></th>
                      </tr></thead>
                      <tbody className="divide-y divide-line">
                        {work.reviews.map((r) => (
                          <tr key={`${r.kind}-${r.id}`} className="hover:bg-subtle">
                            <td className={td}>{r.patient_name}<span className="block text-xs text-muted tnum">{r.patient_code}</span></td>
                            <td className={td}>{r.label}</td>
                            <td className={td}><Badge tone={['results', 'external', 'bill'].includes(r.kind) ? 'info' : 'warn'}>{REVIEW_KIND[r.kind]}</Badge></td>
                            <td className={`${td} whitespace-nowrap text-muted tnum`}>{r.at.length > 10 ? formatDateTime(r.at) : formatDate(r.at)}</td>
                            <td className={`${td} text-right`}>
                              <Link to={reviewLink(r)}
                                className="text-sm font-medium text-brand-700 hover:underline">Review</Link>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </>) : null}
              </Panel>
            </div>

            <div className="space-y-4">
              <Panel title="Upcoming appointments" description="Next 30 days" bodyClass="">
                {work.upcoming_visits.length === 0 ? <p className="px-4 py-5 text-sm text-muted">No appointments booked.</p> : (
                  <ul className="divide-y divide-line">
                    {work.upcoming_visits.map((v, i) => {
                      const d = new Date(`${v.date}T00:00:00`)
                      const days = daysBetween(asOf, v.date)
                      return (
                        <li key={i}>
                          <Link to={`/care-team/patients/${v.patient_id}`} className="flex items-center gap-3 px-4 py-2 hover:bg-subtle">
                            <span className={`w-11 shrink-0 rounded border py-0.5 text-center leading-tight ${days === 0 ? 'border-brand-600 bg-brand-50 text-brand-800' : 'border-line text-ink'}`}>
                              <span className="block text-[10px] uppercase text-muted">{d.toLocaleDateString('en-GB', { month: 'short' })}</span>
                              <span className="block text-sm font-semibold tnum">{d.getDate()}</span>
                            </span>
                            <span className="min-w-0">
                              <span className="block truncate text-sm font-medium text-ink">{v.patient_name}</span>
                              <span className="block text-xs text-muted">{v.name} · {days === 0 ? 'Today' : days === 1 ? 'Tomorrow' : `in ${days} days`}</span>
                            </span>
                          </Link>
                        </li>
                      )
                    })}
                  </ul>
                )}
                {today.length > 0 && <p className="border-t border-line px-4 py-2 text-xs font-medium text-brand-800">{today.length} appointment{today.length > 1 ? 's' : ''} today</p>}
              </Panel>

              <Panel title="Consent requests" description="Waiting for the patient to answer" bodyClass="">
                {work.consents.length === 0 ? <p className="px-4 py-5 text-sm text-muted">No open requests.</p> : (
                  <ul className="divide-y divide-line">
                    {work.consents.map((c) => (
                      <li key={c.id} className="px-4 py-2 text-sm">
                        <Link to={`/care-team/patients/${c.patient_id}?tab=sources&source=abha`} className="font-medium text-ink hover:underline">{c.patient_name}</Link>
                        <span className="block text-xs text-muted">{c.hip_name} · expires {formatDateTime(c.expires_at)}</span>
                      </li>
                    ))}
                  </ul>
                )}
              </Panel>
            </div>
          </div>
        </>
      )}
      {adding && <AddPatientDialog onClose={() => setAdding(false)} />}
    </AppShell>
  )
}


// "Not Reviewed" + its count. Clicking it tells the number of pending reports in a small note;
// clicking again, clicking elsewhere or pressing Escape closes it.
// A check's detail without repeating its title: "Emergency visit on 27 Sep…" → "27 Sep…"
const TITLE_ECHO = {
  emergency_visit: /^emergency visit on /i, hypoglycemia_event: /^hypoglycaemia event recorded on /i,
  missed_appointment: /^missed visit on /i, hba1c_plateaued: /^hba1c /i, engagement_dropped: /^sugar logs /i,
}
const cap = (t) => t.charAt(0).toUpperCase() + t.slice(1)
function reasonDetail(s) {
  if (s.key === 'overdue_screening') {   // "screening overdue: eye (115 days), foot (no record)"
    return s.short.replace(/^screening overdue: /i, '').split(', ').map((x) => cap(x
      .replace(/ \(no record\)$/, ' never done').replace(/ \((\d+) days?\)$/, ' $1 days overdue'))).join(' · ')
  }
  return cap(s.short.replace(TITLE_ECHO[s.key] ?? /^$/, ''))
}

// "Reason" column: a small button instead of a long sentence; the flagged checks show only when asked for.
function ReasonCell({ patient: p }) {
  const [open, setOpen] = useState(false)
  const fired = p.assessment.signals.filter((s) => s.fired)
    .sort((a, b) => (a.tier === 'hard' ? 0 : 1) - (b.tier === 'hard' ? 0 : 1))
  if (!fired.length) return <span className="text-muted">—</span>
  const id = `reason-${p.id}`
  return (
    <div onClick={(e) => e.stopPropagation()}>
      <button type="button" onClick={() => setOpen((o) => !o)} aria-expanded={open} aria-controls={id}
        className="inline-flex items-center gap-1 whitespace-nowrap rounded text-sm font-medium text-brand-700 hover:underline focus-visible:outline-2 focus-visible:outline-brand-600">
        {open ? 'Hide reason' : 'View reason'}{fired.length > 1 && !open ? ` (${fired.length})` : ''}
        <ChevronDown size={14} aria-hidden="true" className={open ? 'rotate-180' : ''} />
      </button>
      {open && (
        <ul id={id} className="mt-1.5 space-y-1 text-sm">
          {fired.map((s) => (
            <li key={s.key}>
              <span className="font-medium text-ink">{s.label}</span>
              <span className="text-muted"> · {reasonDetail(s)}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

function NotReviewedTitle({ n }) {
  const [open, setOpen] = useState(false)
  const wrap = useRef(null)
  useEffect(() => {
    if (!open) return undefined
    const outside = (e) => { if (!wrap.current?.contains(e.target)) setOpen(false) }
    const escape = (e) => { if (e.key === 'Escape') setOpen(false) }
    document.addEventListener('pointerdown', outside)
    document.addEventListener('keydown', escape)
    return () => { document.removeEventListener('pointerdown', outside); document.removeEventListener('keydown', escape) }
  }, [open])
  return (
    <span ref={wrap} className="relative inline-block">
      <button type="button" onClick={() => setOpen((o) => !o)} aria-expanded={open}
        className="inline-flex items-center gap-2 font-bold hover:text-brand-700">
        Not Reviewed <PendingCount n={n} />
      </button>
      {open && (
        <span role="status" className="absolute left-0 top-full z-20 mt-1.5 whitespace-nowrap rounded-md border border-line bg-surface px-2.5 py-1.5 text-sm font-medium text-ink shadow-sm">
          {n} pending report{n === 1 ? '' : 's'}
        </span>
      )}
    </span>
  )
}

// The number waiting, small and calm: soft amber when something is pending, soft green at zero.
function PendingCount({ n }) {
  return (
    <span className={`inline-flex h-5 min-w-5 items-center justify-center rounded-full px-1.5 text-xs font-semibold tnum ring-1 ${
      n ? 'bg-warn-50 text-warn-800 ring-warn-200' : 'bg-ok-50 text-ok-700 ring-ok-700/20'}`}>
      {n}<span className="sr-only"> pending</span>
    </span>
  )
}
