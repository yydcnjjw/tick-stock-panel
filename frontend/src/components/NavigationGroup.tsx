import { useEffect, useId, useState } from 'react'
import { NavLink, useLocation } from 'react-router-dom'
import { BarChart3, ChevronDown, ChevronRight, FolderOpen } from 'lucide-react'
import { cn } from '@/lib/cn'

type GroupableItem = { to: string; navGroup?: string }
type NavigationEntry<T> = { kind: 'item'; item: T } | { kind: 'group'; label: string; items: T[] }

/** Call after sorting and visibility filtering so hidden pages never leave an empty group. */
export function groupNavigation<T extends GroupableItem>(items: T[]): NavigationEntry<T>[] {
  const entries: NavigationEntry<T>[] = []
  const groups = new Map<string, T[]>()
  for (const item of items) {
    const label = item.navGroup?.trim()
    if (!label) {
      entries.push({ kind: 'item', item })
      continue
    }
    let children = groups.get(label)
    if (!children) {
      children = []
      groups.set(label, children)
      entries.push({ kind: 'group', label, items: children })
    }
    children.push(item)
  }
  return entries
}

export function NavigationGroup({ label, items, railMode }: {
  label: string; items: { to: string; label: string }[]; railMode: boolean
}) {
  const { pathname } = useLocation()
  const active = items.some(item => item.to === pathname)
  const [expanded, setExpanded] = useState(true)
  const id = useId()
  useEffect(() => { if (active) setExpanded(true) }, [active, pathname])

  return <div>
    <button type="button" aria-label={label} aria-expanded={expanded} aria-controls={id}
      title={railMode ? label : undefined} onClick={() => setExpanded(value => !value)}
      className={cn('flex w-full items-center rounded-btn py-2 text-sm transition-colors',
        railMode ? 'justify-center' : 'gap-3 px-3',
        active ? 'bg-elevated font-medium text-foreground' : 'text-foreground/75 hover:bg-elevated/70 hover:text-foreground')}>
      <FolderOpen className={cn('h-4 w-4 shrink-0', active ? 'text-accent' : 'text-foreground/60')} />
      {!railMode && <><span className="min-w-0 flex-1 truncate text-left">{label}</span>
        {expanded ? <ChevronDown className="h-3.5 w-3.5 shrink-0 text-muted" /> : <ChevronRight className="h-3.5 w-3.5 shrink-0 text-muted" />}</>}
    </button>
    <div id={id} hidden={!expanded} className="mt-0.5 space-y-0.5">
      {items.map(item => <NavLink key={item.to} to={item.to} aria-label={item.label} title={railMode ? `${label} → ${item.label}` : undefined}
        className={({ isActive }) => cn('flex items-center rounded-btn py-1.5 text-xs transition-colors',
          railMode ? 'justify-center' : 'gap-2 pl-9 pr-3',
          isActive ? 'font-medium text-accent' : 'text-foreground/60 hover:bg-elevated/50 hover:text-foreground')}>
        {railMode ? <BarChart3 className="h-3.5 w-3.5" /> : <><span className="h-1.5 w-1.5 shrink-0 rounded-full bg-current opacity-60" /><span className="truncate">{item.label}</span></>}
      </NavLink>)}
    </div>
  </div>
}
