import { useCallback, useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api, formatDate, formatTime, todayIso } from '../api.js'
import Modal from '../components/Modal.jsx'
import { AppShell, Badge, btn, ErrorBox, Field, input, Notice, Panel, td, th } from '../components/ui.jsx'

/*
 * Consultation Briefs - the clinical team's list for a day: one row per appointment with a readiness checklist and the
 * brief's status. "Prepare" drafts the brief from verified data; the composer is where it is checked and sent.
 */
export const BRIEF_STATUS = {
  draft: ['neutral', 'Draft'], sent: ['info', 'Sent'], in_consultation: ['info', 'Patient called'], opened: ['info', 'Opened'],
  acknowledged: ['ok', 'Acknowledged'], completed: ['ok', 'Completed'], superseded: ['neutral', 'Superseded'], cancelled: ['neutral', 'Cancelled'],
}

export default function Briefs() {
  const navigate = useNavigate()
  const [day, setDay] = useState(todayIso())
  const [data, setData] = useState(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(null)
  const [bulk, setBulk] = useState(null)
  const [notice, setNotice] = useState('')
  const load = useCallback(() => {
    api.briefsForDay(day).then(setData).catch((e) => setError(e.message))
  }, [day])
  useEffect(() => {
    load()
    const t = setInterval(load, 20000)                     // statuses (sent, opened, acknowledged) stay current
    return () => clearInterval(t)
  }, [load])

  const prepare = async (row) => {
    if (row.brief) return navigate(`/care-team/briefs/${row.brief.id}`)
    setBusy(row.appointment_id)
    try {
      const v = await api.draftBrief(row.appointment_id)
      navigate(`/care-team/briefs/${v.brief.id}`)
    } catch (e) {
      setError(e.message)
      setBusy(null)
    }
  }
  const openBulk = async () => {
    setError('')
    try {
      setBulk({ list: (await api.sendReady()).ready, busy: false })
    } catch (e) {
      setError(e.message)
    }
  }
  const sendBulk = async () => {
    setBulk((b) => ({ ...b, busy: true }))
    try {
      const r = await api.sendReady(bulk.list.map((x) => x.brief_id))
      setBulk(null)
      setNotice(`${r.sent.length} brief${r.sent.length === 1 ? '' : 's'} sent${r.skipped.length ? ` · ${r.skipped.length} skipped` : ''}.`)
      load()
    } catch (e) {
      setError(e.message)
      setBulk((b) => ({ ...b, busy: false }))
    }
  }
  const rows = data?.appointments ?? []
  const isToday = data && data.date === data.today

  return (
    <AppShell title="Consultation briefs" breadcrumbs={[{ label: 'Briefs' }]}
      subtitle="Prepare a short brief for each patient before the doctor calls them in. Verified data only."
      actions={isToday && <button type="button" onClick={openBulk} className={btn.primary}>Send all ready</button>}>
      <div className="mb-4 flex flex-wrap items-end gap-3">
        <Field label="Day" htmlFor="brief-day" className="w-44">
          <input id="brief-day" type="date" value={day} onChange={(e) => e.target.value && setDay(e.target.value)} className={input} />
        </Field>
        {!isToday && data && <button type="button" onClick={() => setDay(data.today)} className={`${btn.ghost} mb-0.5`}>Back to today</button>}
      </div>
      <ErrorBox>{error}</ErrorBox>
      <Notice>{notice}</Notice>

      <Panel title={`${data ? formatDate(data.date) : '…'}${isToday ? ' · today' : ''}`} bodyClass=""
        description={data && `${rows.length} appointment${rows.length === 1 ? '' : 's'}`}>
        {data && rows.length === 0 && <p className="px-4 py-5 text-sm text-muted">No appointments booked for this day.</p>}
        {rows.length > 0 && (
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead className="border-b border-line bg-subtle"><tr>
                <th className={th}>Time</th><th className={th}>Patient</th><th className={th}>Doctor</th><th className={th}>Ready?</th>
                <th className={th}>Brief</th><th className={th}><span className="sr-only">Action</span></th>
              </tr></thead>
              <tbody className="divide-y divide-line">
                {rows.map((r) => {
                  const [tone, label] = r.brief ? BRIEF_STATUS[r.brief.status] : ['neutral', 'Not prepared']
                  return (
                    <tr key={r.appointment_id} className="align-top hover:bg-subtle">
                      <td className={`${td} whitespace-nowrap font-semibold tnum`}>{formatTime(r.start_time)}</td>
                      <td className={td}><Link to={`/care-team/patients/${r.patient_id}`} className="font-medium hover:text-brand-700 hover:underline">{r.full_name}</Link>
                        <span className="block text-xs text-muted tnum">{r.patient_code}</span>
                        {r.appointment_status !== 'confirmed' && <span className="block text-xs font-semibold text-alert-800">Needs a new time</span>}</td>
                      <td className={`${td} text-muted`}>{r.doctor_name}</td>
                      <td className={td}>
                        <ul className="space-y-0.5 text-xs">
                          <li>{r.vitals_today ? 'Vitals recorded today' : <span className="font-semibold">No vitals today</span>}</li>
                          <li>{r.awaiting_verification ? <span className="font-semibold">{r.awaiting_verification} awaiting verification</span> : 'Nothing awaiting verification'}</li>
                          <li>{r.medicines_last_confirmed ? <>Medicines confirmed {r.medicines_age_days} days ago{r.medicines_outdated && <strong> · possibly outdated</strong>}</> : 'No medicines on record'}</li>
                        </ul>
                      </td>
                      <td className={td}><Badge tone={tone}>{label}</Badge>{r.brief && <span className="block text-xs text-muted">version {r.brief.version}</span>}</td>
                      <td className={`${td} text-right`}>
                        <button type="button" disabled={busy === r.appointment_id} onClick={() => prepare(r)}
                          className={`${r.brief ? btn.secondary : btn.primary} ${btn.sm}`}>{r.brief ? 'Open' : busy === r.appointment_id ? 'Preparing…' : 'Prepare brief'}</button>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </Panel>

      {bulk && (
        <Modal title="Send all ready briefs" onClose={() => setBulk(null)}
          footer={(close) => (<>
            <button type="button" onClick={close} className={btn.secondary}>Cancel</button>
            <button type="button" disabled={bulk.busy || bulk.list.length === 0} onClick={sendBulk} className={btn.primary}>
              {bulk.busy ? 'Sending…' : `Send ${bulk.list.length} brief${bulk.list.length === 1 ? '' : 's'}`}</button>
          </>)}>
          <div className="px-4 py-3">
            {bulk.list.length === 0 ? <p className="text-sm text-muted">No drafts are ready to send. Prepare a brief first.</p> : (
              <table className="w-full">
                <thead><tr><th className={th}>Time</th><th className={th}>Patient</th><th className={th}>Doctor</th></tr></thead>
                <tbody className="divide-y divide-line">
                  {bulk.list.map((x) => (
                    <tr key={x.brief_id}><td className={`${td} font-semibold tnum`}>{formatTime(x.time)}</td>
                      <td className={td}>{x.patient} <span className="text-xs text-muted tnum">{x.patient_code}</span></td><td className={td}>{x.doctor}</td></tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </Modal>
      )}
    </AppShell>
  )
}
