// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { MemoryRouter, useLocation } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import CzscPage from './CzscPage'
import extension from './extension'
import { QK } from '@/lib/queryKeys'

const fixture = vi.hoisted(() => ({ requests: [] as { symbol: string; timeframe: string; assetType?: string; resolve: (data: any) => void; reject: (error: Error) => void }[], syncCzscMinute: vi.fn(), provider: 'exchange_minute' as string | undefined, usable: true }))
vi.mock('@/lib/api', () => ({ api: { czscChart: (symbol: string, timeframe: string, assetType?: string) => new Promise((resolve, reject) => fixture.requests.push({ symbol, timeframe, assetType, resolve, reject })), syncCzscMinute: fixture.syncCzscMinute } }))
vi.mock('@/lib/useSharedQueries', () => ({ useCapabilityMatrix: () => ({ data: { capabilities: [{ id: 'czsc_minute', effective: fixture.provider, effective_display: fixture.provider, usable: fixture.usable }] } }) }))
vi.mock('@/components/financials/StockFinancialSearch', () => ({ StockFinancialSearch: ({ onSelect, assetTypes }: any) => <div data-search-assets={assetTypes}><button onClick={() => onSelect('600001.SH')}>切股</button><button onClick={() => onSelect('000300.SH', '沪深300', 'index')}>切指数</button></div> }))
vi.mock('@/components/EChartsCandlestick', () => ({ EChartsCandlestick: ({ data, structures, onDateClick }: any) => <button data-chart data-structures={JSON.stringify(structures)} onClick={() => onDateClick(data[0].date)}>{data[0].close}|笔{structures.segments.length}</button> }))

let host: HTMLDivElement, root: Root, client: QueryClient
beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true })
  fixture.requests.length = 0
  fixture.provider = 'exchange_minute'; fixture.usable = true
  fixture.syncCzscMinute.mockReset()
  host = document.createElement('div'); document.body.append(host); root = createRoot(host)
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } })
})
afterEach(async () => { await act(async () => root.unmount()); client.clear(); host.remove() })
function Location() { return <output data-url>{useLocation().search}</output> }
async function mount(entry = "/czsc?symbol=600000.SH") {
  await act(async () => root.render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[entry]}><CzscPage /><Location /></MemoryRouter></QueryClientProvider>))
}
const tick = async () => { await act(async () => { await new Promise(resolve => setTimeout(resolve, 10)) }) }
const ready = (symbol: string, close: number) => ({ minute_provider: fixture.provider, symbol, name: symbol, status: 'ready', cutoff: '2024-01-02', version: '1.0.1', rows: [{ date: '2024-01-02', open: close, high: close, low: close, close, volume: 10 }], strokes: [{ start: '2024-01-01', end: '2024-01-02', start_price: close - 1, end_price: close }], signals: [] })

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

it('separates native endpoint candidates from confirmed events and segment layers', async () => {
  await mount()
  fixture.requests[0].resolve({ ...ready('600000.SH', 10), engine: 'chan.py',
    segments: [{ start: '2024-01-01', end: '2024-01-02', start_price: 9, end_price: 10, confirmed: false, dashed: true }],
    segment_centers: [{ start: '2024-01-01', end: '2024-01-02', low: 9, high: 10 }],
    bsp_points: [{ level: 'seg', date: '2024-01-01', price: 9, types: ['1p'], is_buy: true, status: 'candidate', first_seen_at: '2024-01-02', confirmed_at: null }],
    signals: [{ signal_id: 'signal_chan_bi_2_buy', date: '2024-01-04', price: 11,
      endpoint_at: '2024-01-01', first_seen_at: '2024-01-02', confirmed_at: '2024-01-04', event_id: 'unique-point' }],
  }); await tick()
  const structures = () => JSON.parse(host.querySelector('[data-chart]')!.getAttribute('data-structures')!)
  expect(structures().segments).toHaveLength(2)
  expect(structures().segments[1]).toMatchObject({ color: '#14B8A6', dashed: true })
  expect(structures().points[0]).toMatchObject({ date: '2024-01-01', candidate: true })
  expect(structures().points[1]).toMatchObject({ date: '2024-01-04', signal: true })
  expect(host.textContent).toContain('确认 2024-01-04 · 端点 2024-01-01 · 首见 2024-01-02')
  const candidates = [...host.querySelectorAll('label')].find(el => el.textContent === '买卖点端点（含候选）')!
  await act(async () => candidates.querySelector('input')!.click())
  expect(structures().points).toHaveLength(1)
  expect(structures().points[0].date).toBe('2024-01-04')
  expect(fixture.requests).toHaveLength(1)
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
  expect(fixture.syncCzscMinute).not.toHaveBeenCalled()
})

it('synchronizes the selected stock and reloads the minute chart', async () => {
  const invalidate = vi.spyOn(client, 'invalidateQueries')
  fixture.syncCzscMinute.mockResolvedValue({ status: 'ok', rows: 240 })
  await mount('/czsc?symbol=600000.SH&timeframe=30m')
  fixture.requests[0].resolve({ status: 'empty', minute_provider: fixture.provider, reason: '暂无分钟数据' }); await tick()
  await act(async () => [...host.querySelectorAll('button')].find(b => b.textContent === '同步分钟数据')!.click())
  await tick()
  expect(fixture.syncCzscMinute).toHaveBeenCalledWith('600000.SH', 'exchange_minute', 30)
  expect(invalidate.mock.calls.map(([arg]) => arg?.queryKey)).toEqual(
    ['1m', '5m', '30m'].map(period => QK.czscChart('600000.SH', period as '1m' | '5m' | '30m', 'exchange_minute')),
  )
  expect(fixture.requests).toHaveLength(2)
  fixture.requests[1].resolve(ready('600000.SH', 30)); await tick()
  expect(host.querySelector('[data-chart]')?.textContent).toBe('30|笔1')
})

it('switches source without showing the previous source chart or late sync error', async () => {
  let rejectSync!: (error: Error) => void
  fixture.syncCzscMinute.mockImplementation(() => new Promise((_, reject) => { rejectSync = reject }))
  await mount('/czsc?symbol=600000.SH&timeframe=30m')
  fixture.requests[0].resolve(ready('600000.SH', 10)); await tick()
  await act(async () => [...host.querySelectorAll('button')].find(b => b.textContent === '同步分钟数据')!.click())
  fixture.provider = 'stocksdk'
  await act(async () => host.querySelector('input')!.click())
  expect(host.querySelector('[data-chart]')).toBeNull()
  expect(fixture.requests).toHaveLength(2)
  fixture.requests[1].resolve(ready('600000.SH', 20)); await tick()
  rejectSync(new Error('旧数据源同步失败')); await tick()
  expect(host.textContent).not.toContain('旧数据源同步失败')
  expect(host.querySelector('[data-chart]')?.textContent).toBe('20|笔1')
})

it('disables sync when the CZSC route is unavailable', async () => {
  fixture.usable = false
  await mount('/czsc?symbol=600000.SH&timeframe=30m')
  fixture.requests[0].resolve({ minute_provider: fixture.provider, status: 'empty', reason: '暂无分钟数据' }); await tick()
  const button = [...host.querySelectorAll('button')].find(b => b.textContent === '同步分钟数据')!
  expect(button.disabled).toBe(true)
  expect(host.textContent).toContain('当前来源不可用')
  expect(fixture.syncCzscMinute).not.toHaveBeenCalled()
})

it('keeps valid chart data and displays a sync failure with a source settings link', async () => {
  fixture.syncCzscMinute.mockRejectedValue(new Error('分钟数据源请求失败'))
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
  fixture.syncCzscMinute.mockImplementation(() => new Promise((_, reject) => { rejectSync = reject }))
  await mount('/czsc?symbol=600000.SH&timeframe=30m')
  fixture.requests[0].resolve(ready('600000.SH', 10)); await tick()
  await act(async () => [...host.querySelectorAll('button')].find(b => b.textContent === '同步分钟数据')!.click())
  await act(async () => [...host.querySelectorAll('button')].find(b => b.textContent === '切股')!.click())
  rejectSync(new Error('上一只股票失败')); await tick()
  expect(host.textContent).not.toContain('上一只股票失败')
})


it('opens an index weekly chart with typed URL and only enables supported periods', async () => {
  await mount('/czsc?symbol=000300.SH&asset_type=index&timeframe=1w')
  expect(fixture.requests[0]).toMatchObject({ symbol: '000300.SH', timeframe: '1w', assetType: 'index' })
  expect(host.querySelector('[data-search-assets]')?.getAttribute('data-search-assets')).toBe('stock,index')
  for (const label of ['1分', '5分', '30分']) {
    expect([...host.querySelectorAll('button')].find(b => b.textContent === label)?.disabled).toBe(true)
  }
  fixture.requests[0].resolve({ ...ready('000300.SH', 3500), asset_type: 'index' }); await tick()
  expect(host.querySelector('a[href="/indices?symbol=000300.SH"]')).not.toBeNull()
  await act(async () => [...host.querySelectorAll('button')].find(b => b.textContent === '日线')!.click())
  expect(host.querySelector('[data-url]')?.textContent).toContain('asset_type=index')
  expect(fixture.requests[1]).toMatchObject({ assetType: 'index', timeframe: '1d' })
})

it('switches a stock minute chart to index daily and ignores the old response', async () => {
  await mount('/czsc?symbol=000001.SZ&timeframe=30m')
  await act(async () => [...host.querySelectorAll('button')].find(b => b.textContent === '切指数')!.click())
  expect(fixture.requests[1]).toMatchObject({ symbol: '000300.SH', assetType: 'index', timeframe: '1d' })
  fixture.requests[1].resolve({ ...ready('000300.SH', 3500), asset_type: 'index' }); await tick()
  fixture.requests[0].resolve(ready('000001.SZ', 10)); await tick()
  expect(host.querySelector('[data-chart]')?.textContent).toBe('3500|笔1')
  expect(fixture.syncCzscMinute).not.toHaveBeenCalled()
  expect([...host.querySelectorAll('button')].some(b => b.textContent === '同步分钟数据')).toBe(false)
  await act(async () => [...host.querySelectorAll('button')].find(b => b.textContent === '切股')!.click())
  expect(host.querySelector('[data-chart]')).toBeNull()
  expect(fixture.requests[2]).toMatchObject({ symbol: '600001.SH', timeframe: '1d' })
  expect([...host.querySelectorAll('button')].find(b => b.textContent === '30分')?.disabled).toBe(false)
})

it.each([true, false])('explains unsupported index minutes on an untyped old link (minute route available: %s)', async available => {
  if (!available) fixture.provider = undefined
  await mount('/czsc?symbol=000300.SH&timeframe=30m')
  expect([...host.querySelectorAll('button')].find(b => b.textContent === '同步分钟数据')?.disabled).toBe(true)
  expect(fixture.requests).toHaveLength(1)
  fixture.requests[0].resolve({ asset_type: 'index', symbol: '000300.SH', status: 'unavailable',
    unavailable_reason: 'unsupported_timeframe', reason: '指数 CZSC 图表仅支持日线和周线' }); await tick()
  expect(host.textContent).toContain('仅支持日线和周线')
  expect(host.textContent).not.toContain('启用 CZSC 可选组件')
  expect(host.textContent).not.toContain('数据源已变更')
  expect(host.textContent).not.toContain('同步分钟数据')
  expect(fixture.syncCzscMinute).not.toHaveBeenCalled()
})

it('shows an index without history and links to data management without syncing', async () => {
  await mount('/czsc?symbol=000832.SH&asset_type=index')
  fixture.requests[0].resolve({ asset_type: 'index', symbol: '000832.SH', status: 'empty', reason: '本地暂无已结束行情' }); await tick()
  expect(host.querySelector('a[href="/data"]')).not.toBeNull()
  expect(host.querySelector('[data-chart]')).toBeNull()
  expect(fixture.syncCzscMinute).not.toHaveBeenCalled()
})


it('rejects an invalid asset URL without requesting or manually refreshing a different asset', async () => {
  await mount('/czsc?symbol=000300.SH&asset_type=etf')
  expect(host.textContent).toContain('资产类型无效')
  expect(fixture.requests).toHaveLength(0)
  expect([...host.querySelectorAll('button')].find(b => b.textContent === '刷新')?.disabled).toBe(true)
})
