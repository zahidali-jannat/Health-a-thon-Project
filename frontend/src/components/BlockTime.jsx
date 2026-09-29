import { AlertTriangle, X } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { api, formatTime } from '../api.js'
import { btn, ErrorBox, Field, input, Notice, Panel } from './ui.jsx'

/*
 * "Mark doctor unavailable": 12-hour times with an explicit AM / PM (no default), a live summary of what will be
 * blocked, then a confirmation pop-up built ONLY from the server's preview - the same plan the confirm call checks
 * again (a booking made meanwhile stops the save). All times are clinic time (Asia/Kolkata).
 */
const HOURS = Array.from({ length: 12 }, (_, i) => String(i + 1))
const MINUTES = Array.from({ length: 12 }, (_, i) => String(i * 5).padStart(2, '0'))

// 12-hour parts -> "HH:MM" (24 h), or null while something is missing
const to24 = ({ h, m, ap }) => {
  if (!h || !ap) return null
  const hour = (Number(h) % 12) + (ap === 'PM' ? 12 : 0)
  return `${String(hour).padStart(2, '0')}:${m}`
}
const from24 = (hm) => {
  const [H, M] = hm.split(':').map(Number)
  return { h: String(H % 12 || 12), m: String(M).padStart(2, '0'), ap: H < 12 ? 'AM' : 'PM' }
}
const day = (iso, opts) => new Date(`${iso}T00:00:00`).toLocaleDateString('en-GB', opts)
const shortDay = (iso) => day(iso, { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric' })
const longDay = (iso) => day(iso, { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' })
const minutesBetween = (d1, t1, d2, t2) => Math.round((new Date(`${d2}T${t2}:00`) - new Date(`${d1}T${t1}:00`)) / 60000)
const shortDuration = (n) => {
  const d = Math.floor(n / 1440), h = Math.floor((n % 1440) / 60), m = n % 60
  return [d && `${d} d`, h && `${h} h`, m && `${m} min`].filter(Boolean).join(' ') || '0 min'
}

// "2:00 PM" with the AM / PM in bold - never shortened to "p"
function Clock({ hm }) {
  const [t, ap] = formatTime(hm).split(' ')
  return <span className="tnum">{t} <strong className="font-bold">{ap}</strong></span>
}

function TimeField({ id, label, value, onChange, disabled }) {
  const hm = to24(value)
  return (
    <fieldset disabled={disabled} className="min-w-0">
      <legend className="mb-1 block text-[13px] font-medium text-ink">{label}</legend>
      <div className="flex items-center gap-1.5">
        <select id={`${id}-h`} aria-label={`${label} hour`} value={value.h} onChange={(e) => onChange({ ...value, h: e.target.value })}
          className={`${input} w-16! px-1.5! tnum`}>
          <option value="">–</option>
          {HOURS.map((h) => <option key={h} value={h}>{h}</option>)}
        </select>
        <span aria-hidden="true" className="text-muted">:</span>
        <select aria-label={`${label} minutes`} value={value.m} onChange={(e) => onChange({ ...value, m: e.target.value })}
          className={`${input} w-16! px-1.5! tnum`}>
          {MINUTES.map((m) => <option key={m} value={m}>{m}</option>)}
        </select>
        <div role="radiogroup" aria-label={`${label} AM or PM`} className="flex overflow-hidden rounded-md border border-line-strong">
          {['AM', 'PM'].map((ap) => (
            <button key={ap} type="button" role="radio" aria-checked={value.ap === ap} onClick={() => onChange({ ...value, ap })}
              className={`h-9 px-2.5 text-sm font-semibold ${value.ap === ap ? 'bg-brand-600 text-white' : 'bg-surface text-muted hover:text-ink'}`}>
              {ap}
            </button>
          ))}
        </div>
      </div>
      <p className="mt-1 text-xs text-muted tnum">{hm ? `24-hour: ${hm}` : value.h && !value.ap ? 'Choose AM or PM' : ' '}</p>
    </fieldset>
  )
}

const blank = { h: '', m: '00', ap: '' }

export default function BlockTimeForm({ doctor, day: initialDay, onDone }) {
  const [f, setF] = useState({ startDate: initialDay, endDate: initialDay, multiDay: false, fullDay: false,
    start: blank, end: blank, reason: '' })
  const [plan, setPlan] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [done, setDone] = useState('')
  const set = (patch) => { setDone(''); setF((x) => ({ ...x, ...patch })) }

  const block = () => ({
    start_date: f.startDate, end_date: f.multiDay ? f.endDate : f.startDate, full_day: f.fullDay,
    start_time: f.fullDay ? null : to24(f.start), end_time: f.fullDay ? null : to24(f.end), reason: f.reason || null,
  })
  const who = doctor.name.toLowerCase().startsWith('dr') ? doctor.name : `Dr. ${doctor.name}`

  // the live line under the form
  const summary = useMemo(() => {
    const endDate = f.multiDay ? f.endDate : f.startDate
    if (f.fullDay) {
      const n = minutesBetween(f.startDate, doctor.working_start_time, endDate, doctor.working_end_time)
      return { text: `Blocking ${who}: ${shortDay(f.startDate)}${endDate !== f.startDate ? ` to ${shortDay(endDate)}` : ''}, full day `
        + `(${formatTime(doctor.working_start_time)} to ${formatTime(doctor.working_end_time)}, ${shortDuration(n)})`, bad: n <= 0 }
    }
    const s = to24(f.start), e = to24(f.end)
    if (!s || !e) return { text: 'Choose the start and end times, including AM or PM.', bad: false, missing: true }
    const n = minutesBetween(f.startDate, s, endDate, e)
    if (n <= 0) return { text: 'The end is before the start. Check AM/PM.', bad: true }
    const endPart = endDate === f.startDate ? formatTime(e) : `${shortDay(endDate)}, ${formatTime(e)}`
    return { text: `Blocking ${who}: ${shortDay(f.startDate)}, ${formatTime(s)} to ${endPart} (${shortDuration(n)})`, bad: false }
  }, [f, doctor, who])

  const openPreview = async (e) => {
    e?.preventDefault()
    setBusy(true)
    setError('')
    try {
      setPlan({ data: await api.previewDoctorAway(doctor.id, block()), shown: [], changes: [] })
    } catch (e2) {
      setError(e2.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Panel title="Mark doctor unavailable" description={who}>
      <form onSubmit={openPreview} className="space-y-3 px-4 py-3">
        <div className="grid grid-cols-2 gap-3">
          <Field label="Date" htmlFor="away-date">
            <input id="away-date" type="date" required value={f.startDate}
              onChange={(e) => e.target.value && set({ startDate: e.target.value, endDate: f.multiDay ? f.endDate : e.target.value })} className={input} />
          </Field>
          {f.multiDay && (
            <Field label="Until" htmlFor="away-until">
              <input id="away-until" type="date" required min={f.startDate} value={f.endDate}
                onChange={(e) => e.target.value && set({ endDate: e.target.value })} className={input} />
            </Field>
          )}
        </div>
        <div className="flex flex-wrap gap-x-4 gap-y-1 text-sm text-ink">
          <label className="flex items-center gap-1.5"><input type="checkbox" checked={f.fullDay} onChange={(e) => set({ fullDay: e.target.checked })} className="accent-brand-600" /> Full day</label>
          <label className="flex items-center gap-1.5"><input type="checkbox" checked={f.multiDay} onChange={(e) => set({ multiDay: e.target.checked, endDate: f.startDate })} className="accent-brand-600" /> Ends on another day</label>
        </div>
        {!f.fullDay && (
          <div className="grid gap-3">
            <TimeField id="away-from" label="From" value={f.start} onChange={(v) => set({ start: v })} />
            <TimeField id="away-to" label={f.multiDay ? 'To (on the “Until” date)' : 'To'} value={f.end} onChange={(v) => set({ end: v })} />
          </div>
        )}
        <Field label="Reason (optional)" htmlFor="away-reason">
          <input id="away-reason" maxLength={200} value={f.reason} onChange={(e) => set({ reason: e.target.value })} className={input} />
        </Field>
        <p aria-live="polite" className={`rounded border px-2.5 py-2 text-sm tnum ${summary.bad ? 'border-alert-200 bg-alert-50 text-alert-800' : 'border-line bg-subtle text-ink'}`}>
          {summary.text}
        </p>
        <ErrorBox>{error}</ErrorBox>
        <Notice>{done}</Notice>
        <button type="submit" disabled={busy || summary.missing} className={btn.primary}>{busy ? 'Checking…' : 'Block time'}</button>
      </form>

      {plan && (
        <ConfirmBlock doctor={doctor} plan={plan.data} shown={plan.shown} changes={plan.changes}
          onEdit={() => setPlan(null)}
          onFix={async (fix, change) => {
            const next = { ...f, start: fix.start_time ? from24(fix.start_time) : f.start, end: fix.end_time ? from24(fix.end_time) : f.end }
            setF(next)
            const shown = [...new Set([...plan.shown, ...plan.data.warnings.map((w) => w.code), ...plan.data.errors.map((x) => x.code)])]
            const b = { ...block(), start_time: to24(next.start), end_time: to24(next.end) }
            setPlan({ data: await api.previewDoctorAway(doctor.id, b), shown, changes: [...plan.changes, change] })
          }}
          onChanged={(fresh) => setPlan((p) => ({ ...p, data: fresh }))}
          onSaved={(r) => {
            setPlan(null)
            setF((x) => ({ ...x, start: blank, end: blank, reason: '', fullDay: false }))
            setDone(`Blocked ${shortDay(r.start.date)}, ${formatTime(r.start.time)} to `
              + `${r.end.date !== r.start.date ? `${shortDay(r.end.date)}, ` : ''}${formatTime(r.end.time)} · `
              + `${r.blocked} slot${r.blocked === 1 ? '' : 's'} blocked · ${r.needs_reschedule} appointment${r.needs_reschedule === 1 ? '' : 's'} to rebook`)
            onDone()
          }} />
      )}
    </Panel>
  )
}

// ------------------------------------------------------------------ the confirmation pop-up

function ConfirmBlock({ doctor, plan, shown, changes, onEdit, onFix, onChanged, onSaved }) {
  const ref = useRef(null)
  const editBtn = useRef(null)
  const [checked, setChecked] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [stale, setStale] = useState(false)
  useEffect(() => {
    ref.current.showModal()
    editBtn.current?.focus()                  // the safe choice has focus - Enter never confirms by accident
  }, [])
  useEffect(() => { setChecked(false) }, [plan])   // any change to the plan needs a fresh check

  const block = {
    start_date: plan.start?.date, end_date: plan.end?.date, full_day: plan.full_day, reason: plan.reason,
    start_time: plan.full_day ? null : plan.start?.time, end_time: plan.full_day ? null : plan.end?.time,
  }
  const ok = plan.errors.length === 0
  const n = plan.affected?.length ?? 0
  const sameDay = plan.start && plan.start.date === plan.end.date
  const range = plan.start && (sameDay ? `${formatTime(plan.start.time)} – ${formatTime(plan.end.time)}`
    : `${shortDay(plan.start.date)} ${formatTime(plan.start.time)} – ${shortDay(plan.end.date)} ${formatTime(plan.end.time)}`)

  const confirm = async () => {
    setBusy(true)
    setError('')
    try {
      const warnings = [...new Set([...shown, ...plan.warnings.map((w) => w.code)])]
      onSaved(await api.confirmDoctorAway(doctor.id, { ...block, fingerprint: plan.fingerprint, checked, warnings_shown: warnings, changes }))
    } catch (e) {
      if (e.code === 'schedule_changed' && e.data?.preview) {
        setStale(true)                         // show what is true now; the user checks again
        onChanged(e.data.preview)
      } else {
        setError(e.message)
      }
    } finally {
      setBusy(false)
    }
  }
  const fix = (f, what) => {
    setStale(false)
    const parts = []
    if (f.start_time) parts.push(`start ${formatTime(plan.start.time)} → ${formatTime(f.start_time)}`)
    if (f.end_time) parts.push(`end ${formatTime(plan.end.time)} → ${formatTime(f.end_time)}`)
    onFix(f, `${what}: ${parts.join(', ')}`)
  }

  return (
    <dialog ref={ref} aria-labelledby="confirm-block-title" onClose={(e) => { if (e.target === ref.current) onEdit() }}
      onKeyDown={(e) => { if (e.key === 'Enter' && e.target.dataset.confirm === undefined) e.preventDefault() }}
      style={{ width: 'min(520px, calc(100vw - 2rem))' }}
      className="fade-in m-auto rounded-lg border border-line-strong bg-surface p-0 text-ink shadow-lg backdrop:bg-ink/30">
      <header className="flex items-center justify-between border-b border-line px-5 py-3">
        <h2 id="confirm-block-title" className="text-base font-bold text-ink">Confirm doctor unavailable time</h2>
        <button type="button" onClick={() => ref.current.close()} aria-label="Cancel and close" className="rounded p-1.5 text-muted hover:bg-subtle">
          <X size={18} aria-hidden="true" />
        </button>
      </header>

      <div className="max-h-[72vh] space-y-4 overflow-y-auto px-5 py-4">
        {stale && <p role="alert" className="rounded border border-warn-200 bg-warn-50 px-3 py-2 text-sm font-semibold text-warn-800">The schedule changed - please review again.</p>}

        <div className="flex items-center gap-3">
          <span aria-hidden="true" className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-brand-50 text-sm font-bold text-brand-800">{plan.doctor.initials}</span>
          <span>
            <span className="block text-sm font-semibold text-ink">{plan.doctor.name}</span>
            <span className="block text-xs text-muted tnum">Working hours {formatTime(plan.doctor.working_start_time)} – {formatTime(plan.doctor.working_end_time)}</span>
          </span>
        </div>

        {plan.start && (
          <div>
            <p className="text-sm text-ink">{sameDay ? longDay(plan.start.date) : `${shortDay(plan.start.date)} → ${shortDay(plan.end.date)}`}</p>
            <p className="mt-1 text-3xl font-semibold tracking-tight text-ink"><Clock hm={plan.start.time} /> <span className="px-1 text-muted">→</span> <Clock hm={plan.end.time} /></p>
            <p className="text-sm text-muted tnum">({plan.start.time} → {plan.end.time}) · Asia/Kolkata</p>
            {ok && <p className="mt-2 text-xl font-semibold text-ink">{plan.duration_text}</p>}
          </div>
        )}

        {ok && plan.days?.length > 0 && <Timeline days={plan.days} more={plan.more_days} />}

        {ok && (
          <div className="text-sm text-ink">
            {n === 0 ? <p>No booked appointments are affected.</p> : (<>
              <p><strong className={plan.highlight_affected ? 'text-base font-bold' : 'font-semibold'}>{n} booked appointment{n === 1 ? '' : 's'}</strong> will move to Needs rescheduling:</p>
              <ul className="mt-1 divide-y divide-line rounded border border-line">
                {plan.affected.map((a) => (
                  <li key={a.appointment_id} className="flex justify-between gap-3 px-3 py-1.5">
                    <span>{a.patient_name ?? 'Another care team’s patient'}</span>
                    <span className="text-muted tnum">{!sameDay && `${shortDay(a.date)}, `}{formatTime(a.start_time)}</span>
                  </li>
                ))}
              </ul>
            </>)}
            <p className="mt-1.5">{plan.free_slots} free slot{plan.free_slots === 1 ? '' : 's'} will be blocked.</p>
            <p className="mt-1.5 text-muted">Reason: <span className="text-ink">{plan.reason || 'none given'}</span></p>
          </div>
        )}

        {plan.warnings.length > 0 && (
          <div className="space-y-2 rounded border border-warn-200 bg-warn-50 px-3 py-2.5 text-sm text-warn-800">
            {plan.warnings.map((w) => (
              <div key={w.code} className="flex items-start gap-2">
                <AlertTriangle size={15} aria-hidden="true" className="mt-0.5 shrink-0" />
                <div>
                  <p>{w.message}</p>
                  {w.fix && <button type="button" onClick={() => fix(w.fix, 'AM/PM fixed from the warning')} className={`${btn.secondary} ${btn.sm} mt-1.5`}>{w.fix.label}</button>}
                </div>
              </div>
            ))}
          </div>
        )}
        {plan.errors.length > 0 && (
          <div role="alert" className="space-y-2 rounded border border-alert-200 bg-alert-50 px-3 py-2.5 text-sm text-alert-800">
            {plan.errors.map((x) => (
              <div key={x.code}>
                <p className="font-semibold">{x.message}</p>
                {x.fix && <button type="button" onClick={() => fix(x.fix, 'end AM/PM swapped from the error')} className={`${btn.secondary} ${btn.sm} mt-1.5`}>{x.fix.label}</button>}
              </div>
            ))}
          </div>
        )}
        {error && <ErrorBox>{error}</ErrorBox>}

        {ok && (
          <label className="flex items-start gap-2 rounded border border-line px-3 py-2 text-sm font-medium text-ink">
            <input type="checkbox" checked={checked} onChange={(e) => setChecked(e.target.checked)} className="mt-0.5 accent-brand-600" />
            I have checked the doctor, date and time (AM/PM)
          </label>
        )}
      </div>

      <footer className="flex flex-col-reverse gap-2 border-t border-line bg-subtle px-5 py-3 sm:flex-row sm:justify-end">
        <button ref={editBtn} type="button" onClick={() => ref.current.close()} className={btn.secondary}>Edit time</button>
        {ok && (
          <button type="button" data-confirm disabled={!checked || busy} onClick={confirm} className={`${btn.primary} tnum`}>
            {busy ? 'Blocking…' : `Block ${range}${n ? ` (moves ${n} appointment${n === 1 ? '' : 's'})` : ''}`}
          </button>
        )}
      </footer>
    </dialog>
  )
}

// One row per day, midnight to midnight: working hours shaded, the block hatched on top, appointments as ticks.
function Timeline({ days, more }) {
  const W = 100
  const x = (min) => (min / 1440) * W
  return (
    <figure aria-label="The block on each day's timeline" className="space-y-1.5">
      {days.map((d) => (
        <div key={d.date} className="flex items-center gap-2">
          <span className="w-16 shrink-0 text-xs text-muted tnum">{day(d.date, { weekday: 'short', day: 'numeric', month: 'short' })}</span>
          <svg viewBox={`0 0 ${W} 10`} preserveAspectRatio="none" className="h-5 w-full overflow-visible" role="img"
            aria-label={`Blocked ${formatTime(`${String(Math.floor(d.block[0] / 60)).padStart(2, '0')}:${String(d.block[0] % 60).padStart(2, '0')}`)} to ${d.block[1] === 1440 ? 'midnight' : formatTime(`${String(Math.floor(d.block[1] / 60)).padStart(2, '0')}:${String(d.block[1] % 60).padStart(2, '0')}`)}`}>
            <defs>
              <pattern id="hatch" width="1.6" height="10" patternUnits="userSpaceOnUse" patternTransform="skewX(-35)">
                <rect width="0.6" height="10" fill="var(--color-ink)" opacity="0.55" />
              </pattern>
            </defs>
            <rect x="0" y="0" width={W} height="10" fill="var(--color-subtle)" stroke="var(--color-line-strong)" strokeWidth="0.15" />
            <rect x={x(d.working[0])} y="0" width={x(d.working[1] - d.working[0])} height="10" fill="var(--color-brand-100)" />
            <rect x={x(d.block[0])} y="0" width={Math.max(0.4, x(d.block[1] - d.block[0]))} height="10" fill="url(#hatch)"
              stroke="var(--color-ink)" strokeWidth="0.25" />
            {d.appointments.map((a, i) => (
              <rect key={i} x={x(a.minute) - 0.2} y={a.affected ? -1.5 : 1.5} width="0.4" height={a.affected ? 13 : 7}
                fill={a.affected ? 'var(--color-ink)' : 'var(--color-muted)'} />
            ))}
          </svg>
        </div>
      ))}
      <div className="flex pl-[4.5rem] text-[10px] text-muted">
        {['12 AM', '6 AM', '12 PM', '6 PM', '12 AM'].map((l, i) => (
          <span key={i} className={`flex-1 ${i === 4 ? 'flex-none' : ''}`}>{l}</span>
        ))}
      </div>
      {more > 0 && <figcaption className="text-xs text-muted">+{more} more day{more === 1 ? '' : 's'}</figcaption>}
      <figcaption className="flex flex-wrap gap-x-3 text-[11px] text-muted">
        <span><span className="mr-1 inline-block h-2 w-3 align-middle" style={{ background: 'var(--color-brand-100)' }} />Working hours</span>
        <span><span className="mr-1 inline-block h-2 w-3 border border-ink align-middle" style={{ background: 'repeating-linear-gradient(-55deg, var(--color-ink) 0 1px, transparent 1px 3px)' }} />Blocked</span>
        <span><span className="mr-1 inline-block h-2.5 w-0.5 bg-ink align-middle" />Appointment</span>
      </figcaption>
    </figure>
  )
}
