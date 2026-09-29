import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api.js'
import Modal from './Modal.jsx'
import { btn, ErrorBox, Field, input, PatientCredentials, Tabs } from './ui.jsx'

export default function AddPatientDialog({ onClose }) {
  const [tab, setTab] = useState('existing')
  return (
    <Modal title="Add patient" onClose={onClose}>
      {(close) => (
        <>
          <div className="px-4 pt-2">
            <Tabs label="Patient type" value={tab} onChange={setTab}
              tabs={[{ id: 'existing', label: 'Existing patient' }, { id: 'new', label: 'New patient' }]} />
          </div>
          {tab === 'existing' ? <LinkExisting onCancel={close} /> : <CreateNew onCancel={close} />}
        </>
      )}
    </Modal>
  )
}

function Footer({ busy, label, busyLabel, onCancel }) {
  return (
    <div className="flex justify-end gap-2 border-t border-line bg-subtle px-4 py-3">
      <button type="button" onClick={onCancel} className={btn.secondary}>Cancel</button>
      <button type="submit" disabled={busy} className={btn.primary}>{busy ? busyLabel : label}</button>
    </div>
  )
}

function LinkExisting({ onCancel }) {
  const navigate = useNavigate()
  const [code, setCode] = useState('')
  const [phone, setPhone] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const submit = async (e) => {
    e.preventDefault()
    setBusy(true)
    setError('')
    try {
      const linked = await api.linkPatient(code, phone)
      navigate(`/care-team/patients/${linked.id}`)
    } catch (err) {
      setError(err.message)
      setBusy(false)
    }
  }

  return (
    <form onSubmit={submit}>
      <div className="grid gap-3 px-4 py-4">
        <p className="text-sm text-muted">
          For a patient who already has a Patient ID — registered by the hospital or by themselves in the patient app.
        </p>
        <Field label="Patient ID" htmlFor="link-code" required>
          <input id="link-code" value={code} onChange={(e) => { setCode(e.target.value.toUpperCase()); setError('') }} required
            maxLength={20} placeholder="P-1001" className={`${input} max-w-48 uppercase tracking-wide`} />
        </Field>
        <Field label="Patient’s mobile number" htmlFor="link-phone" required hint="Both must match the patient’s record. This keeps records private.">
          <input id="link-phone" value={phone} onChange={(e) => { setPhone(e.target.value); setError('') }} required inputMode="tel"
            maxLength={20} placeholder="10 digits" className={`${input} max-w-64`} />
        </Field>
        <ErrorBox>{error}</ErrorBox>
      </div>
      <Footer busy={busy} label="Add to my patients" busyLabel="Checking…" onCancel={onCancel} />
    </form>
  )
}

function CreateNew({ onCancel }) {
  const navigate = useNavigate()
  const [created, setCreated] = useState(null)
  const [f, setF] = useState({ full_name: '', phone: '', date_of_birth: '', sex: '', abha_number: '', email: '' })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const set = (k) => (e) => setF((s) => ({ ...s, [k]: e.target.value }))

  const submit = async (e) => {
    e.preventDefault()
    setBusy(true)
    setError('')
    try {
      const body = Object.fromEntries(Object.entries(f).filter(([, v]) => v !== ''))
      setCreated(await api.createPatient(body))
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  if (created) {
    return (
      <div>
        <div className="px-4 py-4"><PatientCredentials title="Patient created" credentials={created} /></div>
        <div className="flex justify-end border-t border-line bg-subtle px-4 py-3">
          <button type="button" onClick={() => navigate(`/care-team/patients/${created.id}`)} className={btn.primary}>Open patient record</button>
        </div>
      </div>
    )
  }

  return (
    <form onSubmit={submit}>
      <div className="grid gap-3 px-4 py-4 sm:grid-cols-2">
        <Field label="Full name" htmlFor="np-name" required className="sm:col-span-2">
          <input id="np-name" value={f.full_name} onChange={set('full_name')} required maxLength={120} className={input} />
        </Field>
        <Field label="Mobile number" htmlFor="np-phone" required>
          <input id="np-phone" value={f.phone} onChange={set('phone')} required inputMode="tel" maxLength={20} placeholder="10 digits" className={input} />
        </Field>
        <Field label="Date of birth" htmlFor="np-dob">
          <input id="np-dob" type="date" value={f.date_of_birth} onChange={set('date_of_birth')} className={input} />
        </Field>
        <Field label="Sex" htmlFor="np-sex">
          <select id="np-sex" value={f.sex} onChange={set('sex')} className={input}>
            <option value="">—</option><option value="F">Female</option><option value="M">Male</option><option value="O">Other</option>
          </select>
        </Field>
        <Field label="ABHA number" htmlFor="np-abha" hint="Optional · 14 digits">
          <input id="np-abha" value={f.abha_number} onChange={set('abha_number')} inputMode="numeric" maxLength={17} className={input} />
        </Field>
        <Field label="Email" htmlFor="np-email" hint="Optional" className="sm:col-span-2">
          <input id="np-email" type="email" value={f.email} onChange={set('email')} maxLength={200} className={input} />
        </Field>
        <p className="text-xs text-muted sm:col-span-2">A unique Patient ID and a temporary password are created for the patient app.</p>
        <div className="sm:col-span-2"><ErrorBox>{error}</ErrorBox></div>
      </div>
      <Footer busy={busy} label="Create patient" busyLabel="Creating…" onCancel={onCancel} />
    </form>
  )
}
