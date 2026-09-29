import { useState } from 'react'
import { api, todayIso } from '../api.js'
import { btn, ErrorBox, Field, input } from './ui.jsx'

// Type in the values of an uploaded lab report exactly as printed (test, result, unit, reference range).
// Saving them also confirms the report's file. Used on the patient record and on the lab-report review screen.
const COMMON_TESTS = ['HbA1c', 'Fasting glucose', 'Hemoglobin', 'eGFR', 'Creatinine', 'LDL cholesterol', 'Urine protein', 'Triglycerides']
const COMMON_UNITS = ['%', 'mg/dL', 'g/dL', 'mL/min/1.73m²', 'mmol/L']
const emptyRow = () => ({ test_name: '', value: '', unit: '', reference: '' })

export default function EnterResults({ report, patientId, onCancel, onSaved }) {
  const [date, setDate] = useState(report.report_date.slice(0, 10))
  const [rows, setRows] = useState([emptyRow()])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const setCell = (i, k) => (e) => setRows((rs) => rs.map((row, j) => (j === i ? { ...row, [k]: e.target.value } : row)))

  const save = async (e) => {
    e.preventDefault()
    const results = rows.filter((row) => row.test_name.trim() || row.value.trim())
    if (!results.length) {
      setError('Enter at least one test and its result.')
      return
    }
    setBusy(true)
    setError('')
    try {
      await api.enterReportResults(patientId, report.id, { report_date: date, results })
      onSaved()
    } catch (err) {
      setError(err.message)
      setBusy(false)
    }
  }

  return (
    <form onSubmit={save} className="rounded-md border border-line bg-surface p-3">
      <p className="text-xs text-muted">Copy the values exactly as printed on the report. Leave the reference empty if none is printed.</p>
      <Field label="Date of the test" htmlFor={`rd-${report.id}`} required className="mt-2">
        <input id={`rd-${report.id}`} type="date" value={date} max={todayIso()} onChange={(e) => setDate(e.target.value)} required className={`${input} max-w-44`} />
      </Field>
      <datalist id="uc2-tests">{COMMON_TESTS.map((t) => <option key={t} value={t} />)}</datalist>
      <datalist id="uc2-units">{COMMON_UNITS.map((u) => <option key={u} value={u} />)}</datalist>
      <div className="mt-3 hidden grid-cols-[1.4fr_0.8fr_0.8fr_1.2fr] gap-2 text-[11px] font-semibold uppercase tracking-wide text-muted sm:grid">
        <span>Test</span><span>Result</span><span>Unit</span><span>Reference (as printed)</span>
      </div>
      <div className="mt-1 space-y-2">
        {rows.map((row, i) => (
          <div key={i} className="grid grid-cols-2 gap-2 sm:grid-cols-[1.4fr_0.8fr_0.8fr_1.2fr]">
            <input aria-label={`Test ${i + 1}`} list="uc2-tests" value={row.test_name} onChange={setCell(i, 'test_name')} placeholder="e.g. HbA1c" className={input} />
            <input aria-label={`Result ${i + 1}`} value={row.value} onChange={setCell(i, 'value')} placeholder="Result" className={`${input} tnum`} />
            <input aria-label={`Unit ${i + 1}`} list="uc2-units" value={row.unit} onChange={setCell(i, 'unit')} placeholder="Unit" className={input} />
            <input aria-label={`Reference ${i + 1}`} value={row.reference} onChange={setCell(i, 'reference')} placeholder="e.g. < 5.7" className={input} />
          </div>
        ))}
      </div>
      <button type="button" onClick={() => setRows((rs) => [...rs, emptyRow()])} className={`${btn.ghost} mt-1 -ml-2.5`}>+ Add another test</button>
      <div className="mt-2"><ErrorBox>{error}</ErrorBox></div>
      <div className="mt-3 flex gap-2">
        <button type="submit" disabled={busy} className={btn.primary}>{busy ? 'Saving…' : 'Save results'}</button>
        <button type="button" onClick={onCancel} className={btn.secondary}>Cancel</button>
      </div>
    </form>
  )
}

