import { AlertTriangle, CheckCircle2, CircleHelp, FileText, Link2, OctagonAlert } from 'lucide-react'
import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { api, formatDate } from '../api.js'
import DocumentPreview from './DocumentPreview.jsx'
import RangeMenu from './RangeMenu.jsx'
import { Badge, TYPE, th, td } from './ui.jsx'

// ------------------------------------------------------------------ status styling (always icon + text)

const STATUS = {
  within: { tone: 'ok', icon: CheckCircle2 },
  normal: { tone: 'ok', icon: CheckCircle2 },
  above: { tone: 'warn', icon: AlertTriangle },
  below: { tone: 'warn', icon: AlertTriangle },
  abnormal: { tone: 'warn', icon: AlertTriangle },
  critical_high: { tone: 'alert', icon: OctagonAlert },
  critical_low: { tone: 'alert', icon: OctagonAlert },
  unavailable: { tone: 'muted', icon: CircleHelp },
}

const TONE = {
  ok: { chip: 'bg-ok-50 text-ok-700', dot: 'var(--color-brand-600)' },
  warn: { chip: 'bg-warn-50 text-warn-800', dot: 'var(--color-warn-700)' },
  alert: { chip: 'bg-alert-50 text-alert-800 font-bold', dot: 'var(--color-alert-700)' },
  muted: { chip: 'bg-page text-muted', dot: 'var(--color-muted)' },
}

const BADGE_TONE = { ok: 'ok', warn: 'warn', alert: 'alert', muted: 'neutral' }
function StatusChip({ status, label }) {
  const s = STATUS[status]
  return <Badge tone={BADGE_TONE[s.tone]} icon={s.icon}>{label}</Badge>
}

function formatValue(r) {
  if (r.value == null) return r.value_text
  return `${r.value}${r.unit === '%' ? ' %' : r.unit ? ` ${r.unit}` : ''}`
}

const TREND_TEXT_CLS = { toward: 'text-ok-700', within: 'text-ok-700', away: 'text-warn-800', same: 'text-muted' }

// ------------------------------------------------------------------ section

export default function LabResults({ patient }) {
  const [panels, setPanels] = useState(null)
  useEffect(() => {
    api.labs(patient.id).then(setPanels).catch(() => setPanels([]))
  }, [patient])

  if (!panels) return <p className="text-sm text-muted">Loading lab results…</p>
  if (panels.length === 0) return <p className="rounded-lg border border-line bg-surface px-4 py-6 text-sm text-muted">No lab results on record yet.</p>
  return (
    <section aria-labelledby="labs" className="space-y-4">
      <h2 id="labs" className="sr-only">Lab results</h2>
      {/* at-a-glance: latest value of every test, then the trend panels below */}
      <div className="overflow-x-auto rounded-lg border border-line bg-surface">
        <table className="w-full">
          <caption className="border-b border-line px-4 py-2.5 text-left">
            <span className={`block ${TYPE.section}`}>Latest results</span>
            <span className={`block font-normal ${TYPE.meta}`}>Compared with each report’s own range</span>
          </caption>
          <thead className="border-b border-line bg-subtle"><tr>
            <th className={th}>Test</th><th className={`${th} text-right`}>Latest</th><th className={th}>Reference</th>
            <th className={th}>Status</th><th className={th}>Trend</th><th className={th}>Date</th>
          </tr></thead>
          <tbody className="divide-y divide-line">
            {panels.map((p) => (
              <tr key={`${p.name}-${p.unit}`} className="hover:bg-subtle">
                <td className={td}><a href={`#lab-${slug(p)}`} className="font-medium hover:text-brand-700 hover:underline">{p.name}</a></td>
                <td className={`${td} whitespace-nowrap text-right font-semibold tnum`}>{formatValue(p.latest)}</td>
                <td className={`${td} tnum text-muted`}>{p.latest.reference?.text ?? 'Unavailable'}</td>
                <td className={td}>
                  <span className="flex flex-wrap gap-1">
                    {p.latest.status === 'unavailable' ? (!p.latest.stage && <span className="text-xs text-muted">—</span>)
                      : <StatusChip status={p.latest.status} label={p.latest.status_label} />}
                    {p.latest.stage && <StageChip stage={p.latest.stage} />}
                  </span>
                </td>
                <td className={`${td} text-xs ${TREND_TEXT_CLS[p.trend?.direction] ?? 'text-muted'}`}>{p.trend ? p.trend.text : '—'}</td>
                <td className={`${td} whitespace-nowrap text-muted tnum`}>{formatDate(p.latest.date)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        {panels.map((p) => <LabCard key={`${p.name}-${p.unit}`} panel={p} patientId={patient.id} />)}
      </div>
    </section>
  )
}

const slug = (p) => `${p.name}-${p.unit ?? ''}`.toLowerCase().replace(/[^a-z0-9]+/g, '-')

/*
 * Where a result came from. A value read from a patient-uploaded outside-lab report ALWAYS has a provenance:
 * "<patient> uploaded this <test> report on <date>. Reviewed by <clinician> on <date>." - and that line opens
 * the exact document it was read from.
 */
export function SourceLine({ result: r, patientId, className = '' }) {
  const [preview, setPreview] = useState(false)
  const prov = r.provenance
  if (prov?.document_id) {
    // always the value's CURRENT link - the graph data is read fresh from the server
    return (
      <>
        <button type="button" onClick={() => setPreview(true)}
          className={`group flex items-start gap-1.5 text-left text-sm text-ink ${className}`}>
          <Link2 size={15} aria-hidden="true" className="mt-0.5 shrink-0 text-brand-700" />
          <span className="underline decoration-brand-600/40 underline-offset-2 group-hover:text-brand-700 group-hover:decoration-brand-600">
            {prov.text}
          </span>
        </button>
        {preview && (
          <DocumentPreview patientId={patientId} onClose={() => setPreview(false)}
            url={prov.origin === 'document' ? api.documentUrl(patientId, prov.document_id) : undefined}
            title={prov.origin === 'document' ? `${prov.test_type} · uploaded ${formatDate(prov.uploaded_at)}` : undefined}
            report={{ id: prov.document_id, test_label: prov.test_type, upload_date: prov.uploaded_at, content_type: prov.content_type }} />
        )}
      </>
    )
  }
  if (prov) return <p className={`text-sm text-muted ${className}`}>{prov.text}</p>     // typed in, no report attached
  return (
    <p className={`flex flex-wrap items-start gap-x-1.5 text-sm text-muted ${className}`}>
      <span>{r.source === 'care_team_manual' ? `${r.asserted_by}.` : `From ${r.asserted_by} · ${r.source_label}.`}</span>
      {r.document_id && (
        <a href={api.documentUrl(patientId, r.document_id)} target="_blank" rel="noreferrer"
          className="inline-flex items-center gap-1 text-brand-700 hover:underline">
          <FileText size={13} aria-hidden="true" /> Open the report
        </a>
      )}
    </p>
  )
}

const menuRange = (t) => t.replace(/\s%/, '%')                               // "5.7–6.4 %" -> "5.7–6.4%"
const labelRange = (t) => menuRange(t).replace(/^([<>≥≤])\s/, '$1')         // "≥ 6.5 %"   -> "≥6.5%"

// The options of the "Reference Range" menu: the diabetes stages (HbA1c only) and the lab report's own range.
function rangeOptions(p, hasReportRange) {
  const stages = (p.stages ?? []).map((s) => ({
    id: s.key, label: s.label, range: menuRange(s.range_text), dot: STAGE_DOT[s.tone], group: 'Diabetes stage (ADA)',
  }))
  const printed = hasReportRange && p.reference_consistent ? menuRange(p.latest.reference.text) : null
  const age = p.age_reference && [{
    id: 'age', label: `Average for age ${p.age_reference.group}`, range: String(p.age_reference.average),
    dot: 'var(--color-ink)', group: 'Average for age', note: `Patient is ${p.age_reference.age} · ${p.age_reference.unit}`,
  }]
  return [...stages, ...(age || []), {
    id: 'report', label: 'Use Lab Report’s Own Range', range: printed, dot: 'var(--color-muted)', group: 'Lab report',
    disabled: !hasReportRange,
    note: !hasReportRange ? 'Not printed on these reports' : printed ? null : 'Differs between reports',
  }]
}

function LabCard({ panel: p, patientId }) {
  const latest = p.latest
  const numericCount = p.results.filter((r) => r.value != null).length
  const hasReportRange = p.results.some((r) => refBounds(r))
  // ONE range at a time: a diabetes stage, the average for the patient's age (eGFR - picked automatically from
  // their age, so it is the default), the lab report's own range, or none.
  const [range, setRange] = useState(p.age_reference ? 'age' : hasReportRange ? 'report' : null)
  const stage = p.stages?.find((s) => s.key === range)
  const label = stage ? `Reference Range: ${stage.label} (${labelRange(stage.range_text)})`
    : range === 'age' ? `Reference Range: Age ${p.age_reference.group} average (${p.age_reference.average})`
    : range === 'report' ? `Reference Range: Lab report (${p.reference_consistent ? labelRange(latest.reference.text) : 'varies'})`
      : 'Reference Range'

  return (
    <article id={`lab-${slug(p)}`} className="min-w-0 scroll-mt-16 rounded-lg border border-line bg-surface" aria-label={`${p.name} results`}>
      <div className="flex flex-wrap items-baseline justify-between gap-2 border-b border-line px-4 py-2.5">
        <h3 className={TYPE.section}>{p.name}</h3>
        <span className={TYPE.meta}>{formatDate(latest.date)} · {latest.asserted_by}</span>
      </div>
      <div className="px-4 py-3">
        <p className="flex flex-wrap items-baseline gap-x-3">
          <span className={`${TYPE.row} tnum`}>{formatValue(latest)}</span>
          {p.trend && <span className={`${TYPE.meta} tnum`}>{p.trend.change_text}</span>}
        </p>

        {p.kind === 'qualitative' ? (
          <>
            <p className={`mt-0.5 ${TYPE.body}`}>Reference: <strong className="font-semibold">{latest.reference?.text ?? '—'}</strong></p>
            <QualitativeHistory results={p.results} />
          </>
        ) : numericCount >= 2 ? (
          <>
            <div className="mt-2 flex flex-wrap items-center gap-2">
              <RangeMenu label={label} options={rangeOptions(p, hasReportRange)} value={range} onChange={setRange} />
              {!range && <span className={TYPE.meta}>No range shown</span>}
            </div>
            <LabChart panel={p} patientId={patientId} mode={range} />
            <ChartLegend panel={p} mode={range} />
          </>
        ) : (
          <>
            <p className={`mt-3 ${TYPE.meta}`}>One result so far — graph appears after the next</p>
            <SourceLine result={latest} patientId={patientId} className="mt-1" />
          </>
        )}

        {p.kind === 'numeric' && <ResultTable results={p.results} patientId={patientId} stages={Boolean(p.stages)} />}
      </div>
    </article>
  )
}

function QualitativeHistory({ results }) {
  return (
    <table className="mt-4 w-full text-left text-sm">
      <caption className="sr-only">Result history</caption>
      <thead className="text-muted">
        <tr className="border-b border-line">
          <th className="py-2 pr-3 font-semibold">Date</th>
          <th className="py-2 pr-3 font-semibold">Result</th>
          <th className="py-2 font-semibold">Status</th>
        </tr>
      </thead>
      <tbody>
        {[...results].reverse().map((r) => (
          <tr key={r.id} className="border-b border-line last:border-0">
            <td className="whitespace-nowrap py-2 pr-3 text-ink">{formatDate(r.date)}</td>
            <td className="py-2 pr-3 font-semibold text-ink">{r.value_text}</td>
            <td className="py-2"><StatusChip status={r.status} label={r.status_label} /></td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function ResultTable({ results, patientId, stages }) {
  return (
    <details className="mt-3">
      <summary className="cursor-pointer text-[13px] font-medium text-brand-700">Show as table</summary>
      <div className="overflow-x-auto">
        <table className="mt-2 w-full text-left text-sm tabular-nums">
          <thead className="text-muted">
            <tr className="border-b border-line">
              <th className="py-2 pr-3 font-semibold">Date</th>
              <th className="py-2 pr-3 font-semibold">Result</th>
              <th className="py-2 pr-3 font-semibold">Reference</th>
              <th className="py-2 pr-3 font-semibold">Status</th>
              {stages && <th className="py-2 pr-3 font-semibold">Diabetes stage</th>}
              <th className="py-2 font-semibold">Source</th>
            </tr>
          </thead>
          <tbody>
            {[...results].reverse().map((r) => (
              <tr key={r.id} className="border-b border-line last:border-0">
                <td className="whitespace-nowrap py-2 pr-3 text-ink">{formatDate(r.date)}</td>
                <td className="whitespace-nowrap py-2 pr-3 font-semibold text-ink">{formatValue(r)}</td>
                <td className="py-2 pr-3 text-ink">{r.reference?.text ?? <span className="text-muted">Unavailable</span>}</td>
                <td className="py-2 pr-3 text-ink">{r.status_label}</td>
                {stages && <td className="py-2 pr-3 text-ink">{r.stage ? `${r.stage.label} (${r.stage.range_text})` : '—'}</td>}
                <td className="py-2 text-muted">{r.provenance ? <SourceLine result={r} patientId={patientId} /> : r.asserted_by}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </details>
  )
}

// ------------------------------------------------------------------ chart

const H = 210
const M = { top: 18, right: 60, bottom: 30, left: 44 }
const BAND = 'var(--color-band)'
const BAND_EDGE = 'var(--color-band-edge)'
const GRID = 'var(--color-grid)'

function niceTicks(lo, hi, count = 4) {
  const raw = (hi - lo) / count
  const mag = 10 ** Math.floor(Math.log10(raw))
  const step = [1, 2, 2.5, 5, 10].map((f) => f * mag).find((s) => s >= raw)
  const ticks = []
  for (let v = Math.floor(lo / step) * step; v <= Math.ceil(hi / step) * step + step / 1e6; v += step) {
    ticks.push(Number(v.toFixed(6)))
  }
  return ticks
}

const time = (iso) => new Date(`${iso}T00:00:00`).getTime()
const shortDate = (iso) => new Date(`${iso}T00:00:00`).toLocaleDateString('en-GB', { month: 'short', year: '2-digit' })

function refBounds(r) {
  const ref = r.reference
  if (!ref || ref.kind === 'qualitative') return null
  if (ref.kind === 'range' && ref.low != null && ref.high != null) return { low: ref.low, high: ref.high, text: ref.text }
  if (ref.kind === 'upper' && ref.high != null) return { low: null, high: ref.high, text: ref.text }
  if (ref.kind === 'lower' && ref.low != null) return { low: ref.low, high: null, text: ref.text }
  return null
}

function useWidth() {
  const ref = useRef(null)
  const [width, setWidth] = useState(300)
  useLayoutEffect(() => {
    const ro = new ResizeObserver(([entry]) => setWidth(Math.max(260, entry.contentRect.width)))
    ro.observe(ref.current)
    return () => ro.disconnect()
  }, [])
  return [ref, width]
}

function LabChart({ panel, patientId, mode = null }) {
  const stage = panel.stages?.find((s) => s.key === mode)      // a diabetes stage band ...
  const reportMode = mode === 'report'                           // ... or the lab report's own range (never both)
  const ageRef = mode === 'age' ? panel.age_reference : null     // ... or the average for the patient's age (a line)
  const [wrapRef, W] = useWidth()
  const [active, setActive] = useState(null)
  const [picked, setPicked] = useState(null)     // clicked point: its source is shown under the chart
  const pts = panel.results.filter((r) => r.value != null).map((r) => ({ ...r, t: time(r.date), ref: refBounds(r) }))

  // the axis always includes the limits of whatever range is on show, so the band is never cut off
  const all = (stage ? [...pts.map((p) => p.value), stage.low, stage.high]
    : ageRef ? [...pts.map((p) => p.value), ageRef.average]
    : reportMode ? pts.flatMap((p) => [p.value, p.ref?.low, p.ref?.high])
      : pts.map((p) => p.value)).filter((v) => v != null)
  let lo = Math.min(...all)
  let hi = Math.max(...all)
  const pad = (hi - lo) * 0.15 || Math.abs(hi) * 0.1 || 1
  const ticks = niceTicks(lo - pad, hi + pad)
  lo = ticks[0]
  hi = ticks[ticks.length - 1]

  const t0 = pts[0].t
  const t1 = pts[pts.length - 1].t
  const plotW = W - M.left - M.right
  const plotH = H - M.top - M.bottom
  const x = (t) => M.left + ((t - t0) / (t1 - t0 || 1)) * plotW
  const y = (v) => M.top + ((hi - v) / (hi - lo)) * plotH

  // Reference band: each result's own range, shaded across its share of the timeline; identical neighbours merge.
  const segments = []
  pts.forEach((p, i) => {
    const x0 = i === 0 ? M.left : (x(pts[i - 1].t) + x(p.t)) / 2
    const x1 = i === pts.length - 1 ? W - M.right : (x(p.t) + x(pts[i + 1].t)) / 2
    const last = segments[segments.length - 1]
    if (last && last.ref?.text === p.ref?.text && last.ref && p.ref) last.x1 = x1
    else segments.push({ x0, x1, ref: p.ref })
  })

  const line = pts.map((p, i) => `${i ? 'L' : 'M'}${x(p.t).toFixed(1)},${y(p.value).toFixed(1)}`).join(' ')
  const showAllDates = pts.length <= 6 && plotW / pts.length >= 58
  const dateTicks = showAllDates ? pts : [pts[0], pts[pts.length - 1]]
  const lastPt = pts[pts.length - 1]
  const lastRef = segments[segments.length - 1].ref
  const places = Math.max(...pts.map((p) => (String(p.value).split('.')[1] ?? '').length))
  const num = (v) => v.toFixed(places)

  const nearest = (clientX, rect) => {
    const px = clientX - rect.left
    let best = 0
    pts.forEach((p, i) => { if (Math.abs(x(p.t) - px) < Math.abs(x(pts[best].t) - px)) best = i })
    return best
  }
  const a = active != null ? pts[active] : null

  return (
    <div ref={wrapRef} className="relative mt-4 min-w-0 overflow-hidden">
      <svg
        width={W} height={H} role="img" tabIndex={0}
        aria-label={`${panel.name} trend, ${pts.length} results. Latest ${formatValue(lastPt)} on ${formatDate(lastPt.date)}. Use left and right arrow keys to read each result.`}
        className="block cursor-pointer focus-visible:outline-2 focus-visible:outline-brand-600"
        onClick={(e) => setPicked(nearest(e.clientX, e.currentTarget.getBoundingClientRect()))}
        onPointerMove={(e) => setActive(nearest(e.clientX, e.currentTarget.getBoundingClientRect()))}
        onPointerLeave={() => setActive(null)}
        onFocus={() => setActive(pts.length - 1)}
        onBlur={() => setActive(null)}
        onKeyDown={(e) => {
          if (e.key === 'ArrowLeft') setActive((i) => Math.max(0, (i ?? pts.length) - 1))
          if (e.key === 'ArrowRight') setActive((i) => Math.min(pts.length - 1, (i ?? -1) + 1))
          if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setPicked(active ?? pts.length - 1) }
        }}
      >
        {/* the chosen diabetes-stage band (HbA1c) */}
        {[stage].filter(Boolean).map((s) => {
          const top = s.high != null ? Math.max(M.top, y(s.high)) : M.top
          const bottom = s.low != null ? Math.min(M.top + plotH, y(s.low)) : M.top + plotH
          if (bottom <= top) return null
          return (
            <g key={s.key}>
              <rect x={M.left} y={top} width={plotW} height={bottom - top} fill={STAGE_FILL[s.tone]} />
              <text x={M.left + 6} y={top + 13} fontSize="11" fontWeight="600" fill={STAGE_TEXT[s.tone]}>{s.label}</text>
              {/* its cut-off lines */}
              {[s.low, s.high].filter((v) => v != null && y(v) >= M.top && y(v) <= M.top + plotH).map((v) => (
                <g key={v}>
                  <line x1={M.left} x2={W - M.right} y1={y(v)} y2={y(v)} stroke={STAGE_TEXT[s.tone]} strokeOpacity="0.5" strokeDasharray="4 3" />
                  <text x={W - M.right + 6} y={y(v)} dy="0.32em" fontSize="11" fill="var(--color-muted)">{v}</text>
                </g>
              ))}
            </g>
          )
        })}

        {/* the average for the patient's age group - a line, not a normal/abnormal band */}
        {ageRef && (
          <g>
            <line x1={M.left} x2={W - M.right} y1={y(ageRef.average)} y2={y(ageRef.average)} stroke="var(--color-ink)"
              strokeOpacity="0.55" strokeWidth="1.5" strokeDasharray="6 4" />
            <text x={M.left + 6} y={y(ageRef.average) - 6} fontSize="11" fontWeight="600" fill="var(--color-muted)">
              Average for age {ageRef.group}
            </text>
            <text x={W - M.right + 6} y={y(ageRef.average)} dy="0.32em" fontSize="11" fill="var(--color-muted)">{ageRef.average}</text>
          </g>
        )}

        {/* reference band + boundary hairlines */}
        {reportMode && segments.map((s, i) => {
          if (!s.ref) return null
          const top = s.ref.high != null ? y(s.ref.high) : M.top
          const bottom = s.ref.low != null ? y(s.ref.low) : M.top + plotH
          return (
            <g key={i}>
              <rect x={s.x0} y={top} width={s.x1 - s.x0} height={Math.max(0, bottom - top)} fill={BAND} />
              {s.ref.high != null && <line x1={s.x0} x2={s.x1} y1={top} y2={top} stroke={BAND_EDGE} strokeWidth="1" />}
              {s.ref.low != null && <line x1={s.x0} x2={s.x1} y1={bottom} y2={bottom} stroke={BAND_EDGE} strokeWidth="1" />}
            </g>
          )
        })}

        {/* grid + y axis */}
        {ticks.map((v) => (
          <g key={v}>
            <line x1={M.left} x2={W - M.right} y1={y(v)} y2={y(v)} stroke={GRID} strokeWidth="1" />
            <text x={M.left - 8} y={y(v)} dy="0.32em" textAnchor="end" fontSize="12" fill="var(--color-muted)"
              style={{ fontVariantNumeric: 'tabular-nums' }}>{v}</text>
          </g>
        ))}

        {/* boundary values labelled in the right margin (latest range only) */}
        {reportMode && lastRef?.high != null && (
          <text x={W - M.right + 6} y={y(lastRef.high)} dy="0.32em" fontSize="11" fill="var(--color-muted)">
            {lastRef.low != null ? 'upper ' : ''}{num(lastRef.high)}
          </text>
        )}
        {reportMode && lastRef?.low != null && (
          <text x={W - M.right + 6} y={y(lastRef.low)} dy="0.32em" fontSize="11" fill="var(--color-muted)">
            {lastRef.high != null ? 'lower ' : ''}{num(lastRef.low)}
          </text>
        )}

        {/* x axis */}
        <line x1={M.left} x2={W - M.right} y1={M.top + plotH} y2={M.top + plotH} stroke="var(--color-axis)" strokeWidth="1" />
        {dateTicks.map((p, i) => (
          <text key={p.id} x={x(p.t)} y={H - 8} fontSize="12" fill="var(--color-muted)"
            textAnchor={dateTicks.length === 2 ? (i === 0 ? 'start' : 'end') : 'middle'}>
            {shortDate(p.date)}
          </text>
        ))}

        {/* crosshair */}
        {a && <line x1={x(a.t)} x2={x(a.t)} y1={M.top} y2={M.top + plotH} stroke="var(--color-crosshair)" strokeWidth="1" />}

        {/* trend line + result markers (colour = status; always also given in text) */}
        <path d={line} fill="none" stroke="var(--color-brand-600)" strokeWidth="2" strokeLinejoin="round" strokeLinecap="round" />
        {pts.map((p, i) => {
          // plain result colour; with the lab report's range shown, results outside it are marked
          const tone = reportMode ? TONE[STATUS[p.status].tone] : null
          const fill = tone && STATUS[p.status].tone !== 'muted' ? tone.dot : 'var(--color-brand-600)'
          return (
            <circle key={p.id} cx={x(p.t)} cy={y(p.value)} r={active === i || picked === i ? 6 : 4.5}
              fill={fill} stroke={picked === i ? 'var(--color-ink)' : 'var(--color-surface)'} strokeWidth="2" />
          )
        })}

        {/* direct label on the latest value only */}
        <text x={x(lastPt.t)} y={y(lastPt.value) - 12} textAnchor="end" fontSize="13" fontWeight="600" fill="var(--color-ink)">
          {num(lastPt.value)}
        </text>
      </svg>

      {a && (
        <div role="status" className="pointer-events-none absolute z-10 w-56 rounded-lg border border-line bg-surface p-3 text-sm shadow-sm"
          style={{ left: Math.min(Math.max(x(a.t) - 112, 0), W - 224), top: 0 }}>
          <p className="text-base font-semibold text-ink">{formatValue(a)}</p>
          <p className="text-muted">{formatDate(a.date)} · {a.asserted_by}</p>
          {a.other_unit && <p className="text-muted tnum">= {a.other_unit}</p>}
          {a.stage && <p className="mt-1 font-medium text-ink">Stage: {a.stage.label} <span className="font-normal text-muted">({a.stage.range_text})</span></p>}
          {a.reference?.text && <p className="mt-1 text-ink">Lab range: {a.reference.text}</p>}
          {a.status !== 'unavailable' && <p className="text-ink">{a.status_label}</p>}
          {a.provenance?.document_id && <p className="mt-1 text-xs text-brand-700">Linked to a report · click for its source</p>}
        </div>
      )}

      {/* Part E: the clicked point's source - for an outside-lab value, the reference line to its document */}
      <div className="mt-2 min-h-10 rounded-md border border-line bg-subtle px-3 py-2" aria-live="polite">
        {picked == null ? (
          <p className={TYPE.meta}>Click a point to see its source</p>
        ) : (
          <>
            <p className={`${TYPE.meta} font-semibold`}>
              {formatValue(pts[picked])} · {formatDate(pts[picked].date)}
              {pts[picked].stage && <span className="font-normal"> · {pts[picked].stage.label} stage</span>}
            </p>
            <SourceLine result={pts[picked]} patientId={patientId} className="mt-0.5" />
          </>
        )}
      </div>
    </div>
  )
}

// ------------------------------------------------------------------ HbA1c diabetes stages (ADA)

const STAGE_FILL = { ok: 'var(--color-ok-50)', warn: 'var(--color-warn-50)', alert: 'var(--color-alert-50)' }
const STAGE_TEXT = { ok: 'var(--color-ok-700)', warn: 'var(--color-warn-800)', alert: 'var(--color-alert-800)' }
const STAGE_ICON = { ok: CheckCircle2, warn: AlertTriangle, alert: OctagonAlert }
const STAGE_DOT = { ok: 'var(--color-ok-700)', warn: 'var(--color-warn-700)', alert: 'var(--color-alert-700)' }

function StageChip({ stage }) {
  return <Badge tone={stage.tone} icon={STAGE_ICON[stage.tone]}>{stage.label} range · {stage.range_text}</Badge>
}

function Item({ children, label }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <svg width="18" height="10" aria-hidden="true">{children}</svg>
      {label}
    </span>
  )
}

// Only what is on the graph right now.
function ChartLegend({ panel, mode }) {
  const stage = panel.stages?.find((s) => s.key === mode)
  const outside = mode === 'report' && panel.results.some((r) => r.value != null && ['warn', 'alert'].includes(STATUS[r.status].tone))
  return (
    <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted">
      <Item label="Patient result"><circle cx="9" cy="5" r="4" fill="var(--color-brand-600)" /></Item>
      <Item label="Trend"><line x1="1" x2="17" y1="5" y2="5" stroke="var(--color-brand-600)" strokeWidth="2" /></Item>
      {stage && (
        <Item label={`${stage.label}: ${stage.range_text} (${stage.alt_range_text})`}>
          <rect x="1" y="0" width="16" height="10" fill={STAGE_FILL[stage.tone]} stroke={STAGE_TEXT[stage.tone]} strokeWidth="1" />
        </Item>
      )}
      {mode === 'age' && panel.age_reference && (
        <Item label={panel.age_reference.text}>
          <line x1="1" x2="17" y1="5" y2="5" stroke="var(--color-ink)" strokeOpacity="0.55" strokeWidth="1.5" strokeDasharray="4 3" />
        </Item>
      )}
      {mode === 'report' && (
        <Item label={panel.reference_consistent ? `Lab report range: ${panel.latest.reference.text}` : 'Lab report range (as printed on each report)'}>
          <rect x="1" y="0" width="16" height="10" fill={BAND} stroke={BAND_EDGE} strokeWidth="1" />
        </Item>
      )}
      {outside && <Item label="Outside lab range"><circle cx="9" cy="5" r="4" fill="var(--color-warn-700)" /></Item>}
    </div>
  )
}
