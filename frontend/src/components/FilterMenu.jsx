import { Check, ChevronDown, ListFilter } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { btn } from './ui.jsx'

// One button showing the current filter and its count; the choices open under it and apply on click.
// options: [{ id, label }]; counts (optional): { [id]: number }; name: what is filtered, e.g. "patients".
export default function FilterMenu({ name, options, value, counts, onChange }) {
  const [open, setOpen] = useState(false)
  const [alignRight, setAlignRight] = useState(false)     // opens leftwards when there's no room to the right
  const box = useRef(null)
  const current = options.find((o) => o.id === value) ?? options[0]
  useEffect(() => {
    if (!open) return undefined
    const away = (e) => { if (!box.current?.contains(e.target)) setOpen(false) }
    const esc = (e) => { if (e.key === 'Escape') { setOpen(false); box.current?.querySelector('button')?.focus() } }
    document.addEventListener('mousedown', away)
    document.addEventListener('keydown', esc)
    return () => { document.removeEventListener('mousedown', away); document.removeEventListener('keydown', esc) }
  }, [open])
  return (
    <div ref={box} className="relative">
      <button type="button" aria-haspopup="menu" aria-expanded={open}
        onClick={() => { setAlignRight(box.current.getBoundingClientRect().left + 208 > window.innerWidth - 8); setOpen((o) => !o) }}
        aria-label={`Filter ${name}: ${current.label}`} className={`${btn.secondary} gap-2`}>
        <ListFilter size={15} aria-hidden="true" className="text-muted" />
        {current.label} {counts?.[current.id] != null && <span className="tnum text-muted">{counts[current.id]}</span>}
        <ChevronDown size={15} aria-hidden="true" className={`text-muted ${open ? 'rotate-180' : ''}`} />
      </button>
      {open && (
        <ul role="menu" aria-label={`Filter ${name}`} className={`absolute ${alignRight ? 'right-0' : 'left-0'} z-20 mt-1 w-52 rounded-md border border-line bg-surface py-1 shadow-sm`}>
          {options.map((o) => (
            <li key={o.id} role="none">
              <button type="button" role="menuitemradio" aria-checked={o.id === value}
                onClick={() => { onChange(o.id); setOpen(false) }}
                className={`flex w-full items-center gap-2 px-3 py-1.5 text-left text-sm hover:bg-subtle ${o.id === value ? 'font-semibold text-brand-800' : 'text-ink'}`}>
                <Check size={14} aria-hidden="true" className={o.id === value ? '' : 'invisible'} />
                <span className="flex-1">{o.label}</span>
                {counts?.[o.id] != null && <span className="tnum text-muted">{counts[o.id]}</span>}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
