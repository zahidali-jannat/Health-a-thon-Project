import { Download } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api, formatDate } from '../api.js'
import { btn, ErrorBox, Panel } from './ui.jsx'

/*
 * The patient's test orders as FHIR R4-shaped resources, generated on request from the record (nothing stored).
 * Only verified results appear as Observations. Each resource: a plain summary, with its raw JSON on demand.
 */
const TYPES = [['ServiceRequest', 'Service requests (orders)'], ['Observation', 'Observations (verified results)'],
  ['DocumentReference', 'Document references (uploaded reports)']]

const summary = {
  ServiceRequest: (r) => [r.code?.text, `status ${r.status}`, r.priority, r.occurrencePeriod?.end && `due by ${formatDate(r.occurrencePeriod.end)}`,
    r.requester?.display && `requested by ${r.requester.display}`],
  Observation: (r) => [r.code?.text, r.valueQuantity && `${r.valueQuantity.value} ${r.valueQuantity.unit ?? ''}`.trim(),
    r.effectiveDateTime && `test ${formatDate(r.effectiveDateTime)}`, r.performer?.[0]?.display, `status ${r.status}`],
  DocumentReference: (r) => [r.content?.[0]?.attachment?.title, r.description, `status ${r.status}`, r.docStatus && `document ${r.docStatus}`],
}

function Resource({ resource: r }) {
  const [raw, setRaw] = useState(false)
  const codes = r.code?.coding?.map((c) => `${c.system?.includes('loinc') ? 'LOINC' : c.system} ${c.code}`) ?? []
  return (
    <li className="px-4 py-2.5">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <p className="text-sm text-ink">{summary[r.resourceType](r).filter(Boolean).join(' · ')}</p>
        <button type="button" aria-expanded={raw} onClick={() => setRaw((x) => !x)} className={`${btn.ghost} ${btn.sm}`}>{raw ? 'Hide JSON' : 'Raw JSON'}</button>
      </div>
      <p className="text-xs text-muted tnum">{r.resourceType}/{r.id}{codes.length ? ` · ${codes.join(', ')}` : ' · no LOINC code'}</p>
      {raw && <pre className="mt-2 max-h-80 overflow-auto rounded border border-line bg-subtle p-3 text-xs leading-5 text-ink">{JSON.stringify(r, null, 2)}</pre>}
    </li>
  )
}

export default function FhirView({ patient }) {
  const [data, setData] = useState(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  useEffect(() => {
    Promise.all(TYPES.map(([t]) => api.fhir(patient.id, t)))
      .then((bundles) => setData(Object.fromEntries(TYPES.map(([t], i) => [t, bundles[i].entry.map((e) => e.resource)]))))
      .catch((e) => setError(e.message))
  }, [patient.id])

  const download = async () => {
    setBusy(true)
    try {
      const bundle = await api.fhir(patient.id, 'Bundle')
      const url = URL.createObjectURL(new Blob([JSON.stringify(bundle, null, 2)], { type: 'application/fhir+json' }))
      const a = Object.assign(document.createElement('a'), { href: url, download: `${patient.patient_code}-test-orders-fhir-bundle.json` })
      a.click()
      URL.revokeObjectURL(url)
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="max-w-2xl text-sm text-muted">FHIR R4-shaped data generated from this record on request. It is not an ABDM or national-standard certified export.
          Uploads waiting for review are not shown as Observations.</p>
        <button type="button" onClick={download} disabled={busy} className={btn.secondary}><Download size={15} aria-hidden="true" /> Download bundle</button>
      </div>
      <ErrorBox>{error}</ErrorBox>
      {!data && !error && <p className="text-sm text-muted">Loading…</p>}
      {data && TYPES.map(([t, label]) => (
        <Panel key={t} title={`${label} (${data[t].length})`} bodyClass="">
          {data[t].length === 0 ? <p className="px-4 py-3 text-sm text-muted">None.</p>
            : <ul className="divide-y divide-line">{data[t].map((r) => <Resource key={r.id} resource={r} />)}</ul>}
        </Panel>
      ))}
    </div>
  )
}
