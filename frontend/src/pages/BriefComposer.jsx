import { useCallback, useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { api, formatDate, formatTime } from '../api.js'
import ConsultationBrief, { clinicTime } from '../components/ConsultationBrief.jsx'
import Modal from '../components/Modal.jsx'
import { AppShell, Badge, btn, ErrorBox, Notice, Panel } from '../components/ui.jsx'
import { BRIEF_STATUS } from './Briefs.jsx'

/*
 * Brief composer: on the left, the brief exactly as the doctor will see it; on the right, hide / pin a line, the team
 * note (280 characters), completeness warnings and Send. A sent brief is read-only - "Prepare a new version" starts the
 * next one, which keeps the team's choices.
 */
const NOTE_MAX = 280
const SECTION = { since_last_visit: 'Since last visit', attention: 'Needs your attention', medicines: 'Medicines', stable: 'No meaningful change' }

export default function BriefComposer() {
  const { briefId } = useParams()
  const navigate = useNavigate()
  const [view, setView] = useState(null)
  const [note, setNote] = useState('')
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState(false)
  const [confirming, setConfirming] = useState(false)
  const [early, setEarly] = useState(false)
  const load = useCallback(async () => {
    try {
      const v = await api.brief(briefId)
      setView(v)
      setNote(v.brief.team_note || '')
      setError('')
    } catch (e) {
      setError(e.message)
    }
  }, [briefId])
  useEffect(() => {
    load()
  }, [load])
  useEffect(() => {                                   // after sending: follow sent -> opened -> acknowledged
    if (!view || view.brief.status === 'draft') return undefined
    const t = setInterval(() => api.brief(briefId).then(setView).catch(() => {}), 20000)
    return () => clearInterval(t)
  }, [view, briefId])

  const run = async (fn, message) => {
    setBusy(true)
    setError('')
    try {
      const r = await fn()
      if (r?.brief) setView(r)
      if (message) setNotice(message)
      return r
    } catch (e) {
      setError(e.message)
      if (e.code === 'stale') load()
      return null
    } finally {
      setBusy(false)
    }
  }
  const edit = (body, message) => run(() => api.editBrief(briefId, { row_version: view.brief.row_version, ...body }), message)

  if (!view) {
    return <AppShell title="Consultation brief" breadcrumbs={[{ label: 'Briefs', to: '/care-team/briefs' }, { label: '…' }]}><ErrorBox>{error}</ErrorBox></AppShell>
  }
  const b = view.brief
  const a = view.appointment
  const draft = b.status === 'draft'
  const ident = view.content.identity
  const [tone, label] = BRIEF_STATUS[b.status]
  const isToday = view.warnings.every((w) => w !== 'The appointment is not today')
  const blocked = a.status !== 'confirmed'

  return (
    <AppShell title={`Brief · ${ident.name}`} breadcrumbs={[{ label: 'Briefs', to: '/care-team/briefs' }, { label: ident.name }]}
      subtitle={<span className="tnum">{formatDate(a.date)} · {formatTime(a.start_time)} · {a.doctor_name}</span>}>
      <ErrorBox>{error}</ErrorBox>
      <Notice>{notice}</Notice>
      <div className="mt-2 grid gap-4 xl:grid-cols-[minmax(0,1fr)_22rem] xl:items-start">
        <div>
          <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted">Preview - exactly what the doctor will see</p>
          <ConsultationBrief content={view.content} />
        </div>

        <div className="space-y-4 xl:sticky xl:top-16">
          <Panel title="Status" bodyClass="px-4 py-3">
            <p className="flex items-center gap-2 text-sm"><Badge tone={tone}>{label}</Badge> <span className="text-muted">version {b.version}</span></p>
            <ul className="mt-2 space-y-0.5 text-xs text-muted tnum">
              {b.sent_at && <li>Sent {clinicTime(b.sent_at)}</li>}
              {b.opened_at && <li>Opened by the doctor {clinicTime(b.opened_at)}</li>}
              {b.acknowledged_at && <li>Acknowledged {clinicTime(b.acknowledged_at)}</li>}
              {b.completed_at && <li>Consultation completed {clinicTime(b.completed_at)}</li>}
            </ul>
            {!draft && !['completed', 'superseded'].includes(b.status) && (
              <button type="button" disabled={busy} onClick={async () => { const r = await run(() => api.draftBrief(b.appointment_id)); if (r) navigate(`/care-team/briefs/${r.brief.id}`) }}
                className={`${btn.secondary} ${btn.sm} mt-3`}>Prepare a new version</button>
            )}
            {b.status === 'superseded' && <button type="button" onClick={() => navigate(`/care-team/briefs/${b.superseded_by}`)} className={`${btn.ghost} ${btn.sm} mt-2`}>Open the current version</button>}
          </Panel>

          {view.warnings.length > 0 && (
            <Panel title="Before sending" bodyClass="px-4 py-3">
              <ul className="space-y-1 text-sm">{view.warnings.map((w) => <li key={w} className="text-warn-800">{w}</li>)}</ul>
            </Panel>
          )}

          {draft && (<>
            <Panel title="Team note" description={`Shown with your name · up to ${NOTE_MAX} characters`} bodyClass="px-4 py-3">
              <textarea aria-label="Team note" value={note} maxLength={NOTE_MAX} rows={3} onChange={(e) => setNote(e.target.value)}
                className="block w-full rounded-md border border-line-strong bg-surface px-2.5 py-2 text-sm text-ink focus:border-brand-600 focus:outline-none focus:ring-2 focus:ring-brand-100" />
              <div className="mt-1.5 flex items-center justify-between">
                <span className={`text-xs tnum ${note.length >= NOTE_MAX ? 'font-semibold text-alert-800' : 'text-muted'}`}>{note.length} / {NOTE_MAX}</span>
                <button type="button" disabled={busy || note === (b.team_note || '')} onClick={() => edit({ note }, 'Note saved.')} className={`${btn.secondary} ${btn.sm}`}>Save note</button>
              </div>
            </Panel>

            <Panel title="Lines in the brief" description="Hide a line or pin it to the top of its section" bodyClass="">
              <ul className="max-h-96 divide-y divide-line overflow-y-auto">
                {view.candidates.map((c) => (
                  <li key={c.key} className={`px-4 py-2 ${c.hidden ? 'opacity-60' : ''}`}>
                    <p className="text-xs text-muted">{SECTION[c.section]}{c.rule && ` · rule ${c.rule}`}</p>
                    <p className={`text-sm ${c.hidden ? 'line-through' : ''}`}>{c.text}</p>
                    <div className="mt-1 flex gap-1.5">
                      <button type="button" disabled={busy} onClick={() => edit(c.hidden ? { unhide: [c.key] } : { hide: [c.key] })}
                        className={`${btn.secondary} ${btn.sm}`}>{c.hidden ? 'Show' : 'Hide'}</button>
                      {c.section !== 'stable' && (
                        <button type="button" disabled={busy || c.hidden} onClick={() => edit(c.pinned ? { unpin: [c.key] } : { pin: [c.key] })}
                          className={`${btn.secondary} ${btn.sm}`}>{c.pinned ? 'Unpin' : 'Pin'}</button>
                      )}
                    </div>
                  </li>
                ))}
                {view.candidates.length === 0 && <li className="px-4 py-3 text-sm text-muted">No lines to adjust.</li>}
              </ul>
            </Panel>

            <div className="flex flex-col gap-2">
              <button type="button" disabled={busy} onClick={() => run(() => api.draftBrief(b.appointment_id), 'Brief rebuilt from the latest verified data.')}
                className={btn.secondary}>Refresh from latest data</button>
              <button type="button" disabled={busy || blocked} onClick={() => { setEarly(false); setConfirming(true) }} className={btn.primary}>Send to doctor</button>
              {blocked && <p className="text-xs text-alert-800">The appointment needs a new time or was cancelled - it cannot be sent.</p>}
            </div>
          </>)}
        </div>
      </div>

      {confirming && (
        <Modal title="Send this brief?" onClose={() => setConfirming(false)}
          footer={(close) => (<>
            <button type="button" onClick={close} className={btn.secondary}>Cancel</button>
            <button type="button" disabled={busy || (!isToday && !early)} onClick={async () => {
              const r = await run(() => api.sendBrief(b.id, early), 'Brief sent to the doctor.')
              if (r) { setConfirming(false); load() }
            }} className={btn.primary}>{busy ? 'Sending…' : 'Send brief'}</button>
          </>)}>
          <dl className="grid grid-cols-[8rem_1fr] gap-y-2 px-4 py-4 text-sm">
            <dt className="text-muted">Patient</dt><dd className="font-semibold">{ident.name}</dd>
            <dt className="text-muted">Patient ID</dt><dd className="font-semibold tnum">{ident.patient_code}</dd>
            <dt className="text-muted">Doctor</dt><dd className="font-semibold">{a.doctor_name}</dd>
            <dt className="text-muted">Appointment</dt>
            <dd className="font-semibold tnum">{formatDate(a.date)} · <span className="text-lg">{formatTime(a.start_time).replace(/ (AM|PM)$/, '')} <strong>{formatTime(a.start_time).slice(-2)}</strong></span></dd>
          </dl>
          {!isToday && (
            <label className="mx-4 mb-4 flex items-start gap-2 rounded border border-warn-200 bg-warn-50 px-3 py-2 text-sm text-warn-800">
              <input type="checkbox" checked={early} onChange={(e) => setEarly(e.target.checked)} className="mt-0.5 accent-brand-600" />
              The appointment is not today - send early anyway
            </label>
          )}
        </Modal>
      )}
    </AppShell>
  )
}
