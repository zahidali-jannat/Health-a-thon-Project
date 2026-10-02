import { Link } from 'react-router-dom'
import { formatDate, formatTime } from '../api.js'

/*
 * The Consultation Brief, rendered from its content (schema 1.0) - the same component for the composer's preview, the
 * doctor's screen and the printed page; the doctor dashboard will reuse it. Sections in the doctor's order:
 * identity, since last visit, key numbers, medicines (as recorded), tests, needs your attention, team note, footer.
 * The front card shows summaries; every detail is one click away in an expander on the same screen.
 * Never colour alone: every state is also a word ("Rising", "Attention", "Overdue").
 */
const clinicTime = (iso) => (iso ? new Date(iso).toLocaleString('en-GB', {
  timeZone: 'Asia/Kolkata', day: 'numeric', month: 'short', year: 'numeric', hour: 'numeric', minute: '2-digit', hour12: true,
}).replace(/\b(am|pm)\b/, (x) => x.toUpperCase()) : '—')      // "9:22 PM" - AM / PM always clear
const num = (v) => (v == null ? '—' : Number(v).toLocaleString('en-GB', { maximumFractionDigits: 2 }))
const withUnit = (v, unit) => `${num(v)}${unit === '%' ? ' %' : unit ? ` ${unit}` : ''}`
const STATE = { done: 'Done', 'awaiting verification': 'Awaiting verification', overdue: 'Overdue', 'not done': 'Not done', open: 'Open' }

function Section({ title, sub, children, className = '' }) {
  return (
    <section className={`border-t border-line px-5 py-3.5 print:break-inside-avoid print:px-0 print:py-2 ${className}`}>
      <h3 className="flex flex-wrap items-baseline gap-x-2">
        <span className="text-[17px] font-bold text-ink">{title}</span>
        {sub && <span className="text-[13px] font-normal text-muted">{sub}</span>}
      </h3>
      <div className="mt-2">{children}</div>
    </section>
  )
}

function More({ label, children }) {
  return (
    <details className="mt-1.5 print:hidden">
      <summary className="cursor-pointer text-sm font-medium text-brand-700 hover:underline">{label}</summary>
      <div className="mt-1.5">{children}</div>
    </details>
  )
}

function Rule({ text }) {
  return text ? <span className="block text-xs text-muted">Rule {text}</span> : null
}

// up to the last 6 verified values; the label says what it shows, the line only illustrates it
function Trend({ points, unit }) {
  if (!points || points.length < 2) return <span className="text-xs text-muted">—</span>
  const vals = points.map((p) => p.value)
  const lo = Math.min(...vals), hi = Math.max(...vals), span = hi - lo || 1
  const xy = points.map((p, i) => [4 + (i * 72) / (points.length - 1), 18 - ((p.value - lo) / span) * 14])
  return (
    <svg width="80" height="22" viewBox="0 0 80 22" role="img"
      aria-label={`Last ${points.length} values: ${points.map((p) => withUnit(p.value, unit)).join(', ')}`} className="overflow-visible">
      <polyline points={xy.map((p) => p.join(',')).join(' ')} fill="none" stroke="var(--color-muted)" strokeWidth="1.5" strokeLinejoin="round" />
      <circle cx={xy.at(-1)[0]} cy={xy.at(-1)[1]} r="2.5" fill="var(--color-ink)" />
    </svg>
  )
}

function SinceItem({ item: i }) {
  return (
    <li className="py-1.5">
      <span className="text-[15px] text-ink">
        {i.pinned && <span className="mr-1.5 text-xs font-semibold uppercase text-muted">Pinned</span>}
        {i.direction && <span className="mr-1.5 font-semibold">{i.direction === 'rising' ? 'Rising:' : 'Falling:'}</span>}
        {i.text}
      </span>
      <span className="flex flex-wrap gap-x-2 text-xs text-muted">
        <span className="tnum">{formatDate(i.date)}</span>
        {i.link && <Link to={i.link} className="text-brand-700 hover:underline print:hidden">Source</Link>}
        <span>· rule {i.rule}</span>
      </span>
    </li>
  )
}

function AttentionItem({ item: i }) {
  const attention = i.severity === 'Attention'
  return (
    <li className={`border-l-4 py-1.5 pl-3 ${attention ? 'border-l-alert-700' : 'border-l-line-strong'}`}>
      <span className={`mr-2 text-xs font-bold uppercase tracking-wide ${attention ? 'text-alert-800' : 'text-muted'}`}>{i.severity}</span>
      <span className="text-[15px] text-ink">{i.label}</span>
      {i.link && <Link to={i.link} className="ml-2 text-xs text-brand-700 hover:underline print:hidden">Open</Link>}
      <Rule text={i.rule_text} />
    </li>
  )
}

export default function ConsultationBrief({ content: c, banner = null }) {
  const id = c.identity
  const a = id.appointment
  const b = c.since_last_visit
  const k = c.key_numbers
  const m = c.medicines
  const att = c.attention
  return (
    <article className="consultation-brief rounded-lg border border-line bg-surface text-ink print:border-0" aria-label={`Consultation brief for ${id.name}`}>
      {banner}
      {/* A. identity strip */}
      <header className="flex flex-wrap items-start justify-between gap-3 px-5 py-4 print:px-0">
        <div>
          <h2 className="text-2xl font-bold tracking-tight">{id.name}</h2>
          <p className="text-[15px] text-muted tnum">
            {[id.age != null && `${id.age} y`, id.sex, id.patient_code].filter(Boolean).join(' · ')}
          </p>
        </div>
        <div className="text-right text-[15px]">
          <p className="font-semibold tnum">{formatDate(a.date)} · {formatTime(a.time)}</p>
          <p className="text-muted">{id.visit_type} · {a.doctor}</p>
        </div>
        <p className="w-full text-[15px]">
          <span className={`mr-2 font-bold ${id.priority.level === 'high_priority' ? 'text-alert-800' : 'text-ink'}`}>{id.priority.label}</span>
          <span>{id.priority.reason}</span>
          <span className="ml-2 text-xs text-muted">rule {id.priority.rule}</span>
        </p>
      </header>

      {/* B. since last visit */}
      <Section title="Since last visit" sub={b.since ? `(${formatDate(b.since)}${b.basis === 'snapshot' ? ', last consultation' : ''})` : ''}>
        {b.basis === 'none' ? <p className="text-[15px] text-muted">No earlier visit on file - nothing to compare with.</p> : (<>
          {b.items.length === 0 && <p className="text-[15px] text-muted">No ranked changes.</p>}
          <ol className="divide-y divide-line">{b.items.map((i) => <SinceItem key={i.key} item={i} />)}</ol>
          {b.more.length > 0 && <More label={`+${b.more.length} more`}><ol className="divide-y divide-line">{b.more.map((i) => <SinceItem key={i.key} item={i} />)}</ol></More>}
          {b.stable_line && <p className="mt-1 text-sm text-muted">{b.stable_line}</p>}
        </>)}
      </Section>

      {/* C. key numbers */}
      <Section title="Key numbers" sub="verified values only">
        {k.rows.length > 0 && (
          <table className="w-full text-[15px]">
            <thead><tr className="text-left text-xs uppercase tracking-wide text-muted">
              <th className="py-1 pr-3 font-semibold">Test</th><th className="py-1 pr-3 font-semibold">Latest</th>
              <th className="py-1 pr-3 font-semibold">Previous</th><th className="py-1 pr-3 font-semibold">Change</th>
              <th className="py-1 font-semibold print:hidden">Trend</th>
            </tr></thead>
            <tbody className="divide-y divide-line">
              {k.rows.map((r) => (
                <tr key={r.key} className="align-top">
                  <td className="py-1.5 pr-3">{r.label}</td>
                  <td className="py-1.5 pr-3 tnum"><strong>{withUnit(r.latest.value, r.unit)}</strong>
                    <span className="block text-xs text-muted">{formatDate(r.latest.date)}{r.mixed_labs && ` · ${r.latest.lab}`}</span></td>
                  <td className="py-1.5 pr-3 tnum">{r.previous ? <>{withUnit(r.previous.value, r.unit)}
                    <span className="block text-xs text-muted">{formatDate(r.previous.date)}{r.mixed_labs && ` · ${r.previous.lab}`}</span></> : '—'}</td>
                  <td className="py-1.5 pr-3 tnum">{r.change ? (r.change.direction === 'no change' ? 'No change'
                    : `${r.change.direction === 'rising' ? 'Rising' : 'Falling'} ${withUnit(Math.abs(r.change.value), r.unit)}`) : '—'}</td>
                  <td className="py-1.5 print:hidden"><Trend points={r.trend} unit={r.unit} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {k.rows.length === 0 && <p className="text-[15px] text-muted">No verified values on file.</p>}
        {k.not_on_file.length > 0 && <p className="mt-1.5 text-sm text-muted">Not on file: {k.not_on_file.join(', ')}</p>}
        {k.notice && <p className="mt-1 text-sm font-medium text-ink">Note: {k.notice}</p>}
      </Section>

      {/* D. medicines */}
      <Section title="Medicines" sub="as recorded">
        {m.items.length === 0 ? <p className="text-[15px] text-muted">No medicines on record.</p> : (<>
          <ul className="divide-y divide-line">
            {[...m.items].map((x) => <Medicine key={x.key} m={x} />)}
          </ul>
          {m.more.length > 0 && <More label={`+${m.more.length} more`}><ul className="divide-y divide-line">{m.more.map((x) => <Medicine key={x.key} m={x} />)}</ul></More>}
        </>)}
        {m.last_confirmed_on && (
          <p className="mt-1.5 text-sm text-muted">
            Last confirmed on {formatDate(m.last_confirmed_on)}
            {m.possibly_outdated && <strong className="ml-2 text-ink">Possibly outdated</strong>}
          </p>
        )}
      </Section>

      {/* E. tests */}
      <Section title="Tests" sub="ordered at the last visit">
        {c.tests.length === 0 ? <p className="text-[15px] text-muted">No tests ordered.</p> : (
          <ul className="divide-y divide-line text-[15px]">
            {c.tests.map((t) => (
              <li key={t.key} className="flex flex-wrap justify-between gap-2 py-1.5">
                <span>{t.name} <span className="text-xs text-muted tnum">due {formatDate(t.due_by)}</span></span>
                <span className={t.state === 'overdue' ? 'font-semibold text-alert-800' : t.state === 'done' ? 'text-ink' : 'text-muted'}>
                  {STATE[t.state]}{t.result && <span className="tnum"> · {withUnit(t.result.value, t.result.unit)}</span>}
                </span>
              </li>
            ))}
          </ul>
        )}
      </Section>

      {/* F. needs your attention */}
      <Section title="Needs your attention">
        {att.items.length === 0 ? <p className="text-[15px] text-muted">Nothing flagged by the rules.</p> : (
          <ul className="space-y-1">{att.items.map((i) => <AttentionItem key={i.key} item={i} />)}</ul>
        )}
        {att.more.length > 0 && <More label={`+${att.more.length} more`}><ul className="space-y-1">{att.more.map((i) => <AttentionItem key={i.key} item={i} />)}</ul></More>}
      </Section>

      {/* G. team note */}
      {c.team_note && (
        <Section title="Clinical team note">
          <p className="text-[15px]">{c.team_note.text}</p>
          <p className="text-xs text-muted">{c.team_note.by}{c.team_note.at && ` · ${clinicTime(c.team_note.at)}`}</p>
        </Section>
      )}

      {/* H. footer */}
      <footer className="flex flex-wrap gap-x-4 gap-y-1 border-t border-line px-5 py-2.5 text-xs text-muted print:px-0">
        <span>Data cut-off {clinicTime(c.footer.data_cutoff_at)}</span>
        <span className="font-semibold text-ink">Verified data only</span>
        {c.footer.awaiting_verification > 0 && (
          <Link to={c.footer.link} className="text-brand-700 hover:underline">{c.footer.awaiting_verification} item{c.footer.awaiting_verification === 1 ? '' : 's'} awaiting verification</Link>
        )}
      </footer>
    </article>
  )
}

function Medicine({ m }) {
  return (
    <li className="flex flex-wrap items-baseline justify-between gap-2 py-1.5 text-[15px]">
      <span>
        <span className="font-medium">{m.name}</span>
        {m.changed && <span className="ml-2 text-xs font-semibold uppercase tracking-wide text-ink">{m.changed}</span>}
        <span className="block text-xs text-muted">{m.dose} · {m.status}</span>
      </span>
      <span className="text-xs text-muted tnum">{m.source} · {formatDate(m.date)}</span>
    </li>
  )
}

export { clinicTime }
