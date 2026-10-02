import { Printer } from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'
import { api, formatDate, formatTime } from '../api.js'
import { useClinician } from '../auth.jsx'
import ConsultationBrief, { clinicTime } from '../components/ConsultationBrief.jsx'
import { AppShell, Badge, btn, ErrorBox, input } from '../components/ui.jsx'
import { BRIEF_STATUS } from './Briefs.jsx'

/*
 * The doctor's own dashboard - separate from the clinic team's workspace. At the top, what the clinic team sent:
 * "<clinic> sent a report about <patient>". Below it, today's queue (read-only) of patients whose brief was sent to me -
 * name, age, time, priority, status only.
 * "Call" asks for two identifiers (patient ID + date of birth or age) and shows only the name and ID until they match.
 * The previous patient's brief is cleared the moment the next one is called. Polls every 20 s (ETag) for the queue and
 * for verified data that arrived after the brief was sent.
 */
const POLL_MS = 20000

export default function DoctorQueue() {
  const { user } = useClinician()
  const [queue, setQueue] = useState(null)
  const [inbox, setInbox] = useState(null)
  const [error, setError] = useState('')
  const [current, setCurrent] = useState(null)        // { brief_id, name, patient_code, step: 'identity' | 'brief', ... }
  const loadQueue = useCallback(() => {
    api.doctorQueue().then((d) => setQueue(d.queue)).catch((e) => setError(e.message))
    api.doctorInbox().then(setInbox).catch((e) => setError(e.message))
  }, [])
  useEffect(() => {
    loadQueue()
    const t = setInterval(loadQueue, POLL_MS)
    return () => clearInterval(t)
  }, [loadQueue])

  const call = async (row) => {
    setCurrent(null)                                   // the previous patient's brief goes away immediately
    setError('')
    try {
      const r = await api.callPatient(row.brief_id)
      if (r.identity_confirmed) {
        setCurrent({ ...r, step: 'brief', data: await api.doctorBrief(row.brief_id) })
      } else {
        setCurrent({ ...r, step: 'identity' })
      }
      loadQueue()
    } catch (e) {
      setError(e.message)
      loadQueue()
    }
  }
  const waiting = (queue ?? []).filter((q) => q.status === 'sent')
  const next = waiting[0]

  return (
    <AppShell title="Doctor dashboard" breadcrumbs={[{ label: 'Dashboard' }]}
      subtitle={`${user.full_name} · reports from ${user.clinic_name}`}>
      <ErrorBox>{error}</ErrorBox>
      <Inbox data={inbox} current={current} onOpen={call} />
      <div className="grid gap-4 lg:grid-cols-[19rem_minmax(0,1fr)] lg:items-start print:block">
        <section aria-labelledby="queue-title" className="rounded-lg border border-line bg-surface print:hidden">
          <header className="flex items-center justify-between border-b border-line px-4 py-3">
            <h2 id="queue-title" className="text-base font-bold">Today's queue</h2>
            <button type="button" disabled={!next} onClick={() => call(next)} className={`${btn.primary} ${btn.sm}`}>Call next</button>
          </header>
          {queue && queue.length === 0 && <p className="px-4 py-4 text-sm text-muted">No briefs sent to you for today.</p>}
          <ul className="divide-y divide-line">
            {(queue ?? []).map((q) => {
              const [tone, label] = BRIEF_STATUS[q.status]
              const active = current?.brief_id === q.brief_id
              return (
                <li key={q.brief_id} className={`px-4 py-2.5 ${active ? 'bg-brand-50' : ''}`}>
                  <div className="flex items-baseline justify-between gap-2">
                    <span className="font-semibold tnum">{formatTime(q.time)}</span>
                    <Badge tone={tone}>{label}</Badge>
                  </div>
                  <p className="text-[15px] font-medium">{q.name}</p>
                  <p className="text-xs text-muted tnum">{[q.age != null && `${q.age} y`, q.sex, q.patient_code].filter(Boolean).join(' · ')}</p>
                  <div className="mt-1 flex items-center justify-between gap-2">
                    <span className={`text-xs ${q.priority_level === 'high_priority' ? 'font-bold text-alert-800' : 'text-muted'}`}>{q.priority}</span>
                    {q.status !== 'completed' && (
                      <button type="button" onClick={() => call(q)} className={`${btn.secondary} ${btn.sm}`}>{active ? 'Re-open' : 'Call'}</button>
                    )}
                  </div>
                </li>
              )
            })}
          </ul>
        </section>

        <div className="min-w-0">
          {!current && <p className="rounded-lg border border-dashed border-line px-4 py-10 text-center text-sm text-muted print:hidden">Call a patient to see their brief.</p>}
          {current?.step === 'identity' && (
            <IdentityStep key={current.brief_id} who={current} onConfirmed={async () => {
              setCurrent({ ...current, step: 'brief', data: await api.doctorBrief(current.brief_id) })
              loadQueue()
            }} />
          )}
          {current?.step === 'brief' && <BriefScreen key={current.brief_id} who={current} onChanged={loadQueue} />}
        </div>
      </div>
    </AppShell>
  )
}

function Inbox({ data, current, onOpen }) {
  const messages = data?.messages ?? []
  const fresh = messages.filter((m) => m.new).length
  return (
    <section aria-labelledby="inbox-title" className="mb-4 rounded-lg border border-line bg-surface print:hidden">
      <header className="flex items-baseline justify-between gap-2 border-b border-line px-4 py-3">
        <h2 id="inbox-title" className="text-base font-bold">From {data?.team ?? 'the clinic team'}</h2>
        <span className={`text-xs tnum ${fresh ? 'font-semibold text-brand-800' : 'text-muted'}`}>{fresh ? `${fresh} new` : 'Last 7 days'}</span>
      </header>
      {data && messages.length === 0 && (
        <p className="px-4 py-4 text-sm text-muted">No reports yet. When {data.team} sends you a patient's brief, it shows here.</p>
      )}
      <ul className="max-h-80 divide-y divide-line overflow-y-auto">
        {messages.map((m) => {
          const [tone, label] = BRIEF_STATUS[m.status]
          const active = current?.brief_id === m.brief_id
          return (
            <li key={m.brief_id} className={`flex flex-wrap items-center gap-x-3 gap-y-1.5 px-4 py-2.5 ${active ? 'bg-brand-50' : ''}`}>
              <span aria-hidden="true" className={`h-2 w-2 shrink-0 rounded-full ${m.new ? 'bg-brand-600' : 'bg-transparent'}`} />
              <div className="min-w-0 flex-1">
                <p className="text-[15px] text-ink">
                  <span className="font-semibold">{m.sent_by.team}</span> sent {m.version > 1 ? 'an updated report' : 'a report'} about{' '}
                  <span className="font-semibold">{m.patient}</span>
                </p>
                <p className="text-xs text-muted tnum">
                  {[m.patient_code, `appointment ${m.is_today ? 'today' : formatDate(m.date)} at ${formatTime(m.time)}`,
                    `sent ${clinicTime(m.sent_at)}${m.sent_by.name ? ` by ${m.sent_by.name}` : ''}`,
                    m.has_note && 'includes a note from the team'].filter(Boolean).join(' · ')}
                </p>
              </div>
              {m.new ? <Badge tone="info">New</Badge> : <Badge tone={tone}>{label}</Badge>}
              {m.is_today && m.status !== 'completed'
                ? <button type="button" onClick={() => onOpen(m)} className={`${btn.secondary} ${btn.sm}`}>{active ? 'Re-open' : 'Open'}</button>
                : !m.is_today && m.status !== 'completed' && <span className="text-xs text-muted">Opens on {formatDate(m.date)}</span>}
            </li>
          )
        })}
      </ul>
    </section>
  )
}

function IdentityStep({ who, onConfirmed }) {
  const [code, setCode] = useState('')
  const [useAge, setUseAge] = useState(false)
  const [dob, setDob] = useState('')
  const [age, setAge] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const first = useRef(null)
  useEffect(() => { first.current?.focus() }, [])
  const submit = async (e) => {
    e.preventDefault()
    setBusy(true)
    setError('')
    try {
      await api.confirmIdentity(who.brief_id, { patient_code: code, ...(useAge ? { age: Number(age) } : { date_of_birth: dob }) })
      await onConfirmed()
    } catch (e2) {
      setError(e2.message)
      setBusy(false)
    }
  }
  return (
    <form onSubmit={submit} className="max-w-lg rounded-lg border border-line bg-surface">
      <header className="border-b border-line px-5 py-4">
        <p className="text-xs font-semibold uppercase tracking-wide text-muted">Confirm the patient before opening the brief</p>
        <p className="mt-1 text-2xl font-bold">{who.name}</p>
        <p className="text-sm text-muted tnum">Patient ID {who.patient_code}</p>
      </header>
      <div className="space-y-3 px-5 py-4">
        <label className="block text-sm font-medium">Patient ID (ask the patient or check their card)
          <input ref={first} required value={code} onChange={(e) => setCode(e.target.value.toUpperCase())} maxLength={20} className={`${input} mt-1 uppercase tracking-wide`} /></label>
        {useAge ? (
          <label className="block text-sm font-medium">Age (years)
            <input required inputMode="numeric" value={age} onChange={(e) => setAge(e.target.value.replace(/\D/g, ''))} className={`${input} mt-1 w-28`} /></label>
        ) : (
          <label className="block text-sm font-medium">Date of birth
            <input required type="date" value={dob} onChange={(e) => setDob(e.target.value)} className={`${input} mt-1 w-48`} /></label>
        )}
        <button type="button" onClick={() => setUseAge((x) => !x)} className="text-sm text-brand-700 hover:underline">
          {useAge ? 'Use the date of birth instead' : 'No date of birth? Use the age instead'}</button>
        <ErrorBox>{error}</ErrorBox>
        <button type="submit" disabled={busy} className={btn.primary}>{busy ? 'Checking…' : 'Confirm identity and open brief'}</button>
      </div>
    </form>
  )
}

function BriefScreen({ who, onChanged }) {
  const [data, setData] = useState(who.data)
  const [updates, setUpdates] = useState(null)
  const [live, setLive] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const status = data.brief.status
  useEffect(() => {
    const check = () => api.briefUpdates(who.brief_id).then(setUpdates).catch(() => {})
    check()
    const t = setInterval(check, POLL_MS)
    return () => clearInterval(t)
  }, [who.brief_id])
  const act = async (fn) => {
    setBusy(true)
    setError('')
    try {
      await fn()
      setData(await api.doctorBrief(who.brief_id))
      onChanged()
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }
  const showing = live && updates?.live ? updates.live : data.content
  const banner = updates?.changed ? (
    <div role="status" className="border-b border-warn-200 bg-warn-50 px-5 py-3 text-sm text-warn-800 print:hidden">
      <p className="font-semibold">{live ? 'Showing the latest verified data - not the brief that was sent' : 'Updated since sent'}</p>
      {!live && <ul className="mt-1 list-disc pl-5">{updates.changes.map((c) => <li key={c}>{c}</li>)}</ul>}
      <button type="button" onClick={() => setLive((x) => !x)} className={`${btn.secondary} ${btn.sm} mt-2`}>
        {live ? 'Back to the sent brief' : 'Show the latest data'}</button>
    </div>
  ) : null

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2 print:hidden">
        {status === 'opened' && <button type="button" disabled={busy} onClick={() => act(() => api.acknowledgeBrief(who.brief_id))} className={btn.primary}>Acknowledge</button>}
        {['opened', 'acknowledged'].includes(status) && (
          <button type="button" disabled={busy} onClick={() => act(() => api.completeBrief(who.brief_id))} className={btn.secondary}>Finish consultation</button>
        )}
        <button type="button" onClick={() => window.print()} className={btn.secondary}><Printer size={15} aria-hidden="true" /> Print (A4)</button>
        <span className="text-xs text-muted">
          Sent by <strong className="font-semibold text-ink">{data.sent_by?.team}</strong>{data.sent_by?.name && ` (${data.sent_by.name})`}
          {' '}· {clinicTime(data.brief.sent_at)} · version {data.brief.version}</span>
      </div>
      <ErrorBox>{error}</ErrorBox>
      <ConsultationBrief content={showing} banner={banner} />
    </div>
  )
}
