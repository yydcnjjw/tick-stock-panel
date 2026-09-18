// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { MemoryRouter, useLocation } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import CzscPage from './CzscPage'
import extension from './extension'

const fixture = vi.hoisted(() => ({ requests: [] as { symbol: string; timeframe: string; resolve: (data: any) => void; reject: (error: Error) => void }[], syncMinuteSingle: vi.fn() }))
vi.mock('@/lib/api', () => ({ api: { czscChart: (symbol: string, timeframe: string) => new Promise((resolve, reject) => fixture.requests.push({ symbol, timeframe, resolve, reject })), syncMinuteSingle: fixture.syncMinuteSingle } }))
vi.mock('@/components/financials/StockFinancialSearch', () => ({ StockFinancialSearch: ({ onSelect }: any) => <button onClick={() => onSelect('600001.SH')}>切股</button> }))
vi.mock('@/components/EChartsCandlestick', () => ({ EChartsCandlestick: ({ data, structures, onDateClick }: any) => <button data-chart onClick={() => onDateClick(data[0].date)}>{data[0].close}|笔{structures.segments.length}</button> }))

let host: HTMLDivElement, root: Root, client: QueryClient
beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true })
  fixture.requests.length = 0
  fixture.syncMinuteSingle.mockReset()
  host = document.createElement('div'); document.body.append(host); root = createRoot(host)
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } })
})
afterEach(async () => { await act(async () => root.unmount()); client.clear(); host.remove() })
function Location() { return <output data-url>{useLocation().search}</output> }
async function mount(entry = "/czsc?symbol=600000.SH") {
  await act(async () => root.render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[entry]}><CzscPage /><Location /></MemoryRouter></QueryClientProvider>))
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

it('isolates rapid period switches, persists the URL and retains period when changing stock', async () => {
  await mount()
  fixture.requests[0].resolve(ready('600000.SH', 10)); await tick()
  const choose = async (label: string) => {
    await act(async () => [...host.querySelectorAll('button')].find(b => b.textContent === label)!.click())
  }
  await choose('1分')
  expect(host.querySelector('[data-chart]')).toBeNull()
  await choose('30分')
  expect(fixture.requests.map(r => r.timeframe)).toEqual(['1d', '1m', '30m'])
  fixture.requests[2].resolve(ready('600000.SH', 30)); await tick()
  fixture.requests[1].resolve(ready('600000.SH', 1)); await tick()
  expect(host.querySelector('[data-chart]')?.textContent).toBe('30|笔1')
  expect(host.querySelector('[data-url]')?.textContent).toContain('timeframe=30m')
  await choose('切股')
  expect(fixture.requests[3].timeframe).toBe('30m')
  expect(host.querySelector('[data-chart]')).toBeNull()
})

it.each(['1m', '5m', '30m', '1w'])('loads a directly linked %s chart', async timeframe => {
  await mount(`/czsc?symbol=600000.SH&timeframe=${timeframe}`)
  expect(fixture.requests[0].timeframe).toBe(timeframe)
})

it('falls back to daily for an unknown URL period', async () => {
  await mount('/czsc?symbol=600000.SH&timeframe=3m')
  expect(fixture.requests[0].timeframe).toBe('1d')
  expect(host.textContent).toContain('周期无效')
})

it('shows limited coverage and a data management entry without triggering synchronization', async () => {
  await mount('/czsc?symbol=600000.SH&timeframe=30m')
  fixture.requests[0].resolve({ ...ready('600000.SH', 30), input_count: 40, analysis_bars: 1000,
    invalid_dates: ['2024-01-02T09:30'] }); await tick()
  expect(host.textContent).toContain('不足')
  expect(host.querySelector('a[href="/data"]')).not.toBeNull()
  expect(fixture.requests).toHaveLength(1)
  expect(fixture.syncMinuteSingle).not.toHaveBeenCalled()
})

it('synchronizes the selected stock and reloads the minute chart', async () => {
  fixture.syncMinuteSingle.mockResolvedValue({ status: 'ok', rows: 240 })
  await mount('/czsc?symbol=600000.SH&timeframe=30m')
  fixture.requests[0].resolve({ status: 'empty', reason: '暂无分钟数据' }); await tick()
  await act(async () => [...host.querySelectorAll('button')].find(b => b.textContent === '同步分钟数据')!.click())
  await tick()
  expect(fixture.syncMinuteSingle).toHaveBeenCalledWith('600000.SH', 30)
  expect(fixture.requests).toHaveLength(2)
  fixture.requests[1].resolve(ready('600000.SH', 30)); await tick()
  expect(host.querySelector('[data-chart]')?.textContent).toBe('30|笔1')
})

it('keeps valid chart data and displays a sync failure with a source settings link', async () => {
  fixture.syncMinuteSingle.mockRejectedValue(new Error('分钟数据源请求失败'))
  await mount('/czsc?symbol=600000.SH&timeframe=5m')
  fixture.requests[0].resolve(ready('600000.SH', 10)); await tick()
  await act(async () => [...host.querySelectorAll('button')].find(b => b.textContent === '同步分钟数据')!.click())
  await tick()
  expect(host.querySelector('[role=alert]')?.textContent).toContain('分钟数据源请求失败')
  expect(host.querySelector('[data-chart]')?.textContent).toBe('10|笔1')
  expect(host.querySelector('a[href="/settings?tab=data-sources"]')).not.toBeNull()
})

it('does not display a previous stock sync failure after changing stock', async () => {
  let rejectSync!: (error: Error) => void
  fixture.syncMinuteSingle.mockImplementation(() => new Promise((_, reject) => { rejectSync = reject }))
  await mount('/czsc?symbol=600000.SH&timeframe=30m')
  fixture.requests[0].resolve(ready('600000.SH', 10)); await tick()
  await act(async () => [...host.querySelectorAll('button')].find(b => b.textContent === '同步分钟数据')!.click())
  await act(async () => [...host.querySelectorAll('button')].find(b => b.textContent === '切股')!.click())
  rejectSync(new Error('上一只股票失败')); await tick()
  expect(host.textContent).not.toContain('上一只股票失败')
})
