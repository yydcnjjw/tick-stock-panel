// @vitest-environment jsdom
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { MemoryRouter, useNavigate } from 'react-router-dom'
import { expect, it } from 'vitest'
import { groupNavigation, NavigationGroup } from './NavigationGroup'

it('groups visible entries at their first position while retaining item order and legacy entries', () => {
  const index = { to: '/indices' }
  const gold = { to: '/analysis/gold', navGroup: '商品行情' }
  const table = { to: '/analysis/table', navGroup: '' }
  const silver = { to: '/analysis/silver', navGroup: ' 商品行情 ' }
  expect(groupNavigation([index, gold, table, silver])).toEqual([
    { kind: 'item', item: index },
    { kind: 'group', label: '商品行情', items: [gold, silver] },
    { kind: 'item', item: table },
  ])
  expect(groupNavigation([index, table])).toEqual([
    { kind: 'item', item: index }, { kind: 'item', item: table },
  ])
  expect(groupNavigation([])).toEqual([])
})

it('supports collapse, active navigation and accessible links in the icon rail', async () => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true })
  const host = document.createElement('div')
  document.body.append(host)
  const root = createRoot(host)
  const items = [{ to: '/analysis/gold', label: '伦敦金' }, { to: '/analysis/silver', label: '白银' }]
  let navigate: ReturnType<typeof useNavigate>
  function Harness({ railMode }: { railMode: boolean }) {
    navigate = useNavigate()
    return <NavigationGroup label="商品行情" items={items} railMode={railMode} />
  }
  try {
    await act(async () => root.render(<MemoryRouter initialEntries={['/analysis/gold']}><Harness railMode={false} /></MemoryRouter>))
    const toggle = host.querySelector('button')!
    expect(host.querySelector('a[aria-current="page"]')?.textContent).toBe('伦敦金')
    await act(async () => toggle.click())
    expect(toggle.getAttribute('aria-expanded')).toBe('false')
    expect(document.getElementById(toggle.getAttribute('aria-controls')!)?.hidden).toBe(true)
    await act(async () => navigate('/analysis/silver'))
    expect(toggle.getAttribute('aria-expanded')).toBe('true')
    expect(host.querySelector('a[aria-current="page"]')?.textContent).toBe('白银')
    await act(async () => root.render(<MemoryRouter><Harness railMode /></MemoryRouter>))
    expect(toggle.getAttribute('title')).toBe('商品行情')
    const link = host.querySelector('a[aria-label="伦敦金"]')!
    expect(link.getAttribute('href')).toBe('/analysis/gold')
    expect(link.getAttribute('title')).toBe('商品行情 → 伦敦金')
  } finally {
    await act(async () => root.unmount())
    host.remove()
  }
})
