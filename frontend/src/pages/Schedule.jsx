import { Send } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, formatDate, formatDateTime, formatTime, todayIso } from '../api.js'
import BlockTimeForm from '../components/BlockTime.jsx'
import { AppShell, Badge, btn, ErrorBox, Field, input, Panel, td, th } from '../components/ui.jsx'

const SLOT = {
  available: ['neutral', 'Available'],
  booked: ['info', 'Booked'],
  blocked: ['neutral', 'Blocked'],
  needs_reschedule: ['warn', 'Needs a new time'],
}

// A doctor's day for the care team: every slot and its state, marking the doctor away, and the
// "please rebook" messages that follow. Blocking and sending are separate steps on purpose.
export default function Schedule() {
  const [doctors, setDoctors] = useState(null)
  const [doctorId, setDoctorId] = useState('')
  const [day, setDay] = useState(todayIso())
  const [data, setData] = useState(null)
  const [notices, setNotices] = useState(null)
  const [error, setError] = useState('')

  useEffect(() => {
    api.doctors().then((list) => {
      setDoctors(list)
      setDoctorId((id) => id || list[0]?.id || '')
    }).catch((e) => setError(e.message))
  }, [])

  const load = useCallback(async () => {
    if (!doctorId || !day) return
    try {
      const [d, n] = await Promise.all([api.doctorSchedule(doctorId, day), api.rescheduleNotices(doctorId)])
      setData(d)
      setNotices(n)
      setError('')
    } catch (e) {
      setError(e.message)
    }
  }, [doctorId, day])
  useEffect(() => {
    load()
  }, [load])

  const slots = data?.slots ?? []
  const count = (f) => slots.filter(f).length
  const booked = count((s) => s.status === 'booked')

  return (
    <AppShell title="Schedule" breadcrumbs={[{ label: 'Schedule' }]}>
      {doctors && doctors.length === 0 ? (
        <Panel title="No doctors take appointments yet">
          <p className="px-4 py-4 text-sm text-muted">
            A doctor sets their hours on their own dashboard, in Settings → Consultation hours.
          </p>
        </Panel>
      ) : (<>
        <div className="mb-4 flex flex-wrap items-end gap-3">
          <Field label="Doctor" htmlFor="sch-doctor" className="w-64">
            <select id="sch-doctor" value={doctorId} onChange={(e) => { setData(null); setDoctorId(e.target.value) }} className={input}>
              {doctors?.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
            </select>
          </Field>
          <Field label="Date" htmlFor="sch-day" className="w-44">
            <input id="sch-day" type="date" value={day} onChange={(e) => { if (e.target.value) { setData(null); setDay(e.target.value) } }} className={input} />
          </Field>
        </div>
        <ErrorBox>{error}</ErrorBox>

        <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_22rem] xl:items-start">
          <Panel bodyClass="" title={`${formatDate(day)}`}
            description={data && `${slots.length} slots · ${booked} booked · ${count((s) => s.status === 'blocked')} blocked · ${count((s) => s.status === 'available')} free`}>
            {data?.unavailable.length > 0 && (
              <ul className="border-b border-line bg-subtle px-4 py-2 text-sm">
                {data.unavailable.map((u) => (
                  <li key={u.id} className="text-ink">
                    <span className="font-medium tnum">
                      Away {u.start.date === u.end.date ? `${formatTime(u.start.time)} – ${formatTime(u.end.time)}`
                        : `${formatDate(u.start.date)}, ${formatTime(u.start.time)} – ${formatDate(u.end.date)}, ${formatTime(u.end.time)}`}
                    </span>
                    <span className="text-muted">{u.reason ? ` · ${u.reason}` : ''} · marked by {u.created_by_name}</span>
                  </li>
                ))}
              </ul>
            )}
            {!data ? <p className="px-4 py-4 text-sm text-muted">Loading…</p>
              : slots.length === 0 ? <p className="px-4 py-4 text-sm text-muted">No slots on this day.</p> : (
                <table className="w-full">
                  <thead className="border-b border-line bg-subtle"><tr>
                    <th className={th}>Time</th><th className={th}>Status</th><th className={th}>Patient</th>
                  </tr></thead>
                  <tbody className="divide-y divide-line">
                    {slots.map((s) => {
                      const [tone, label] = SLOT[s.appointment_status === 'needs_reschedule' ? 'needs_reschedule' : s.status]
                      return (
                        <tr key={s.id}>
                          <td className={`${td} whitespace-nowrap tnum`}>{formatTime(s.start_time)} – {formatTime(s.end_time)}</td>
                          <td className={td}><Badge tone={tone}>{label}</Badge></td>
                          <td className={td}>
                            {s.full_name ? (
                              <Link to={`/care-team/patients/${s.patient_id}`} className="font-medium hover:text-brand-700 hover:underline">
                                {s.full_name} <span className="font-normal text-muted tnum">{s.patient_code}</span>
                              </Link>
                            ) : s.appointment_id ? <span className="text-muted">Another care team’s patient</span> : null}
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              )}
          </Panel>

          <div className="space-y-4">
            {data && <BlockTimeForm key={`${data.doctor.id}-${day}`} doctor={data.doctor} day={day} onDone={load} />}
            <RebookMessages doctorId={doctorId} notices={notices} onChanged={load} />
          </div>
        </div>
      </>)}
    </AppShell>
  )
}

// Appointments that need a new time, each with its drafted message. Nothing reaches a patient until Send.
function RebookMessages({ doctorId, notices, onChanged }) {
  const [busy, setBusy] = useState(null)
  const [error, setError] = useState('')
  const drafts = notices?.filter((n) => n.can_send) ?? []

  const act = async (key, fn) => {
    setBusy(key)
    setError('')
    try {
      await fn()
      onChanged()
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(null)
    }
  }

  return (
    <Panel bodyClass="" title={`Needs a new time${notices ? ` (${notices.length})` : ''}`}
      actions={drafts.length > 1 && (
        <button type="button" disabled={busy !== null} onClick={() => act('all', () => api.sendAllNotices(doctorId))} className={`${btn.secondary} ${btn.sm}`}>
          <Send size={13} aria-hidden="true" /> Send all ({drafts.length})
        </button>
      )}>
      {error && <div className="px-4 pt-3"><ErrorBox>{error}</ErrorBox></div>}
      {notices?.length > 0 && (
        <ul className="divide-y divide-line">
          {notices.map((n) => (
            <li key={n.id} className="px-4 py-3">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span className="text-sm font-semibold text-ink">{n.full_name ?? 'Another care team’s patient'}</span>
                {n.status === 'sent' ? <Badge tone="ok">Sent</Badge>
                  : n.can_send ? (
                    <button type="button" disabled={busy !== null} onClick={() => act(n.id, () => api.sendNotice(n.id))} className={`${btn.primary} ${btn.sm}`}>
                      <Send size={13} aria-hidden="true" /> {busy === n.id ? 'Sending…' : 'Send'}
                    </button>
                  ) : <Badge>Draft</Badge>}
              </div>
              <p className="text-xs text-muted tnum">Was {formatDate(n.date)} · {formatTime(n.start_time)}{n.sent_at ? ` · sent ${formatDateTime(n.sent_at)}` : ''}</p>
              {n.message && <p className="mt-1.5 rounded-md border border-line bg-subtle px-2.5 py-2 text-sm text-ink">{n.message}</p>}
            </li>
          ))}
        </ul>
      )}
    </Panel>
  )
}
