// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import CzscPage from './CzscPage'
import extension from './extension'

const fixture = vi.hoisted(() => ({ requests: [] as { symbol: string; resolve: (data: any) => void; reject: (error: Error) => void }[] }))
vi.mock('@/lib/api', () => ({ api: { czscDaily: (symbol: string) => new Promise((resolve, reject) => fixture.requests.push({ symbol, resolve, reject })) } }))
vi.mock('@/components/financials/StockFinancialSearch', () => ({ StockFinancialSearch: ({ onSelect }: any) => <button onClick={() => onSelect('600001.SH')}>切股</button> }))
vi.mock('@/components/EChartsCandlestick', () => ({ EChartsCandlestick: ({ data, structures, onDateClick }: any) => <button data-chart onClick={() => onDateClick(data[0].date)}>{data[0].close}|笔{structures.segments.length}</button> }))

let host: HTMLDivElement, root: Root, client: QueryClient
beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true })
  fixture.requests.length = 0
  host = document.createElement('div'); document.body.append(host); root = createRoot(host)
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } })
})
afterEach(async () => { await act(async () => root.unmount()); client.clear(); host.remove() })
async function mount() {
  await act(async () => root.render(<QueryClientProvider client={client}><MemoryRouter initialEntries={['/czsc?symbol=600000.SH']}><CzscPage /></MemoryRouter></QueryClientProvider>))
}
const tick = async () => { await act(async () => { await new Promise(resolve => setTimeout(resolve, 10)) }) }
const ready = (symbol: string, close: number) => ({ symbol, name: symbol, status: 'ready', cutoff: '2024-01-02', version: '1.0.1', rows: [{ date: '2024-01-02', open: close, high: close, low: close, close, volume: 10 }], strokes: [{ start: '2024-01-01', end: '2024-01-02', start_price: close - 1, end_price: close }], signals: [] })

it('ignores late responses for a previous symbol and layer toggles do not refetch', async () => {
  await mount()
  expect(host.textContent).toContain('正在读取')
  await act(async () => host.querySelector('button')!.click())
  expect(fixture.requests.map(r => r.symbol)).toEqual(['600000.SH', '600001.SH'])
  fixture.requests[1].resolve(ready('600001.SH', 20)); await tick()
  fixture.requests[0].resolve(ready('600000.SH', 10)); await tick()
  expect(host.querySelector('[data-chart]')?.textContent).toBe('20|笔1')
  const label = [...host.querySelectorAll('label')].find(el => el.textContent === '已完成笔')!
  await act(async () => label.querySelector('input')!.click())
  expect(host.querySelector('[data-chart]')?.textContent).toBe('20|笔0')
  expect(fixture.requests).toHaveLength(2)
})

it('retains the same chart on refresh error and displays the error', async () => {
  await mount(); fixture.requests[0].resolve(ready('600000.SH', 10)); await tick()
  const chart = host.querySelector('[data-chart]')
  await act(async () => [...host.querySelectorAll('button')].find(b => b.textContent === '刷新')!.click())
  fixture.requests[1].reject(new Error('网络失败')); await tick()
  expect(host.querySelector('[data-chart]')).toBe(chart)
  expect(host.querySelector('[role=alert]')?.textContent).toContain('保留上次结果')
})

it.each(['empty', 'unavailable'])('shows %s without a chart', async status => {
  await mount(); fixture.requests[0].resolve({ symbol: '600000.SH', status, reason: '可定位原因' }); await tick()
  expect(host.textContent).toContain('可定位原因')
  expect(host.querySelector('[data-chart]')).toBeNull()
})

it('registers a static route and footer, isolates a conflicting route', async () => {
  for (const conflict of [false, true]) {
    vi.resetModules()
    const registry = await import('@/extensions/registry')
    await registry.loadFrontendExtensions({ good: async () => ({ default: extension }) })
    registry.finalizeFrontendExtensions(new Set(conflict ? ['/czsc'] : []))
    expect(registry.getFrontendExtensionRoutes().map(r => r.path)).toEqual(conflict ? [] : ['/czsc'])
    expect(registry.getFrontendSlotRegistrations('stock-preview.footer')).toHaveLength(conflict ? 0 : 1)
  }
})
