import { useEffect, useId, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { api } from '../api.js'

/*
 * A test's short name (HbA1c, FPG, KFT…). Its full name and why it is done appear only on hover - or keyboard focus,
 * or a long press on a phone. One list for the patient app and the care team: GET /api/report-tests.
 */
let loading = null
export function useReportTests() {
  const [data, setData] = useState(null)
  useEffect(() => {
    loading ??= api.reportTests().catch(() => { loading = null; return null })
    let live = true
    loading.then((d) => live && setData(d))
    return () => { live = false }
  }, [])
  return data
}

export function findTest(data, name) {
  if (!data || !name) return null
  const key = data.aliases?.[name] ?? name
  const low = String(key).toLowerCase()
  if (low === 'prescription') return { key: 'prescription', ...data.prescription }
  return data.tests.find((t) => t.key === low || t.short.toLowerCase() === low) ?? null
}

export default function TestName({ name, fallback, className = '' }) {
  const data = useReportTests()
  const t = findTest(data, name)
  if (!t) return <span className={className}>{fallback ?? name}</span>
  return <Tip short={t.short} full={t.full} why={t.why} className={className} />
}

export function Tip({ short, full, why, className = '' }) {
  const [pos, setPos] = useState(null)              // where the box is drawn (fixed, above everything) - null = closed
  const id = useId()
  const anchor = useRef(null)
  const press = useRef(null)
  const show = () => {
    const r = anchor.current?.getBoundingClientRect()
    if (!r) return
    const width = Math.min(256, window.innerWidth - 16)
    setPos({ top: r.bottom + 6, left: Math.max(8, Math.min(r.left, window.innerWidth - width - 8)), width })
  }
  const hide = () => setPos(null)
  useEffect(() => {                                  // a scroll or resize moves the anchor: just close
    if (!pos) return undefined
    window.addEventListener('scroll', hide, true)
    window.addEventListener('resize', hide)
    return () => { window.removeEventListener('scroll', hide, true); window.removeEventListener('resize', hide) }
  }, [pos])
  const startPress = (e) => {
    if (e.pointerType !== 'touch') return
    press.current = setTimeout(show, 450)
  }
  const endPress = () => {
    clearTimeout(press.current)
    if (pos) setTimeout(hide, 1800)
  }
  return (
    <span className={`inline-block ${className}`} onMouseEnter={show} onMouseLeave={hide}
      onPointerDown={startPress} onPointerUp={endPress} onPointerCancel={endPress}>
      <span ref={anchor} tabIndex={0} aria-describedby={id} onFocus={show} onBlur={hide}
        onContextMenu={(e) => e.preventDefault()}
        className="cursor-help underline decoration-dotted decoration-1 underline-offset-[3px] outline-none focus-visible:rounded-sm focus-visible:ring-2 focus-visible:ring-brand-100">
        {short}
      </span>
      {createPortal(
        <span id={id} role="tooltip" style={pos ? { top: pos.top, left: pos.left, width: pos.width } : undefined}
          className={`${pos ? 'fixed' : 'hidden'} z-[60] rounded-md border border-line bg-surface px-3 py-2 text-left text-sm font-normal normal-case tracking-normal text-ink shadow-lg`}>
          <span className="block font-semibold">{full}</span>
          <span className="mt-0.5 block text-muted">{why}</span>
        </span>, document.body)}
    </span>
  )
}
