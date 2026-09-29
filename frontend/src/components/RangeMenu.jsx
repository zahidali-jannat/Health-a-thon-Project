import { Check, ChevronDown } from 'lucide-react'
import { useEffect, useId, useRef, useState } from 'react'

/*
 * A small button that opens a plain single-select dropdown under it.
 * options: [{ id, label, range, dot, disabled?, note?, group? }] - rows sharing a `group` get one small heading.
 * Picking a row closes the list and calls onChange(id). Outside click or Escape closes it with no change.
 */
export default function RangeMenu({ label, options, value, onChange, menuLabel = 'Reference range' }) {
  const [open, setOpen] = useState(false)
  const [focus, setFocus] = useState(0)
  const wrap = useRef(null)
  const button = useRef(null)
  const list = useRef(null)
  const id = useId()
  const enabled = options.flatMap((o, i) => (o.disabled ? [] : [i]))

  useEffect(() => {
    if (!open) return undefined
    list.current?.focus()
    const outside = (e) => { if (!wrap.current?.contains(e.target)) setOpen(false) }
    document.addEventListener('pointerdown', outside)
    return () => document.removeEventListener('pointerdown', outside)
  }, [open])

  const show = () => {
    const current = options.findIndex((o) => o.id === value && !o.disabled)
    setFocus(current >= 0 ? current : (enabled[0] ?? 0))
    setOpen(true)
  }
  const close = () => {
    setOpen(false)
    button.current?.focus()
  }
  const choose = (o) => {
    if (o.disabled) return
    onChange(o.id)
    close()
  }
  const onKeyDown = (e) => {
    const at = enabled.indexOf(focus)
    if (e.key === 'Escape') { e.preventDefault(); close() }
    else if (e.key === 'ArrowDown') { e.preventDefault(); setFocus(enabled[Math.min(enabled.length - 1, at + 1)] ?? focus) }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setFocus(enabled[Math.max(0, at - 1)] ?? focus) }
    else if (e.key === 'Home') { e.preventDefault(); setFocus(enabled[0] ?? focus) }
    else if (e.key === 'End') { e.preventDefault(); setFocus(enabled[enabled.length - 1] ?? focus) }
    else if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); choose(options[focus]) }
    else if (e.key === 'Tab') setOpen(false)
  }

  return (
    <div ref={wrap} className="relative inline-block">
      <button ref={button} type="button" aria-haspopup="listbox" aria-expanded={open} aria-controls={open ? id : undefined}
        onClick={() => (open ? close() : show())}
        className="inline-flex h-8 items-center gap-1.5 rounded-md border border-line-strong bg-surface px-2.5 text-sm font-medium text-ink hover:bg-subtle">
        {label}
        <ChevronDown size={14} aria-hidden="true" className={`text-muted ${open ? 'rotate-180' : ''}`} />
      </button>

      {open && (
        <ul ref={list} id={id} role="listbox" tabIndex={-1} aria-label={menuLabel} aria-activedescendant={`${id}-${focus}`}
          onKeyDown={onKeyDown}
          className="absolute left-0 top-full z-30 mt-1 w-[min(19rem,calc(100vw-2rem))] rounded-md border border-line-strong bg-surface py-1 text-sm shadow-sm focus:outline-none">
          {options.map((o, i) => {
            const heading = o.group && o.group !== options[i - 1]?.group
            const divider = i > 0 && o.group !== options[i - 1]?.group
            const selected = o.id === value
            return [
              divider && <li key={`d${i}`} role="presentation" className="my-1 border-t border-line" />,
              heading && <li key={`h${i}`} role="presentation" className="px-3 pb-0.5 pt-1.5 text-[11px] font-semibold uppercase tracking-wide text-muted">{o.group}</li>,
              <li key={o.id} id={`${id}-${i}`} role="option" aria-selected={selected} aria-disabled={o.disabled || undefined}
                onClick={() => choose(o)} onMouseEnter={() => !o.disabled && setFocus(i)}
                className={`flex items-center gap-2.5 px-3 py-2 ${i === focus && !o.disabled ? 'bg-subtle' : ''} ${
                  o.disabled ? 'cursor-not-allowed text-muted' : 'cursor-pointer text-ink'}`}>
                <span aria-hidden="true" className={`h-2.5 w-2.5 shrink-0 rounded-full ${o.disabled ? 'opacity-40' : ''}`} style={{ background: o.dot }} />
                <span className="min-w-0 flex-1">
                  <span className={selected ? 'font-semibold' : ''}>{o.label}</span>
                  {o.note && <span className="block text-xs text-muted">{o.note}</span>}
                </span>
                {o.range && <span className="shrink-0 text-muted tnum">{o.range}</span>}
                <Check size={14} aria-hidden="true" className={`shrink-0 text-brand-700 ${selected ? '' : 'invisible'}`} />
              </li>,
            ]
          })}
        </ul>
      )}
    </div>
  )
}
