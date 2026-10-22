// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import SpotGoldPage from './SpotGoldPage'
import { api, type SpotGoldChart } from '@/lib/api'

const fixture = vi.hoisted(() => ({ usable: true, ma: null as unknown }))
vi.mock('@/lib/storage', () => ({ storage: { chanMovingAverages: {
  get: () => fixture.ma, set: (value: unknown) => { fixture.ma = value },
} } }))
vi.mock('@/lib/useSharedQueries', () => ({
  useCapabilityMatrix: () => ({ isPending: false, isError: false, data: {
    capabilities: [{ id: 'spot_gold', usable: fixture.usable, effective: 'sina_gold' }],
  } }),
}))
vi.mock('@/lib/api', () => ({ api: { spotGoldChart: vi.fn(), refreshSpotGold: vi.fn() } }))
vi.mock('@/components/EChartsCandlestick', () => ({
  EChartsCandlestick: ({ data, structures, symbol, movingAverages, activeIndicators, viewport }: {
    data: { close: number }[]; structures: { segments: unknown[]; points?: unknown[] }; symbol: string;
    movingAverages: unknown; activeIndicators: string[]
    viewport: unknown
  }) => <div data-chart data-symbol={symbol} data-lines={structures.segments.length} data-points={structures.points?.length ?? 0}
    data-ma={JSON.stringify(movingAverages)} data-viewport={JSON.stringify(viewport)} data-count={data.length} data-indicators={JSON.stringify(activeIndicators)}>{data.at(-1)?.close}</div>,
}))

function snapshot(status: 'ready' | 'stale' = 'ready', close = 4190): SpotGoldChart {
  return {
    symbol: 'XAUUSD', name: '伦敦金现', status, reason: status === 'stale' ? '刷新失败，保留上次成功数据' : null,
    provider: 'sina_gold', source: '新浪伦敦金', source_url: 'https://finance.sina.com.cn/',
    price_basis: '美元/金衡盎司', timezone: 'Asia/Shanghai', refreshed_at: '2026-10-13T16:40:00+08:00',
    version: 'test', profile_id: 'test', notes: [],
    panels: (['1w', '1d', '4h', '1h'] as const).map(timeframe => ({
      timeframe, label: { '1w': '周线', '1d': '日线', '4h': '4 小时', '1h': '1 小时' }[timeframe],
      status: 'ready', cutoff: '2026-10-12', input_start: '2021-01-04', input_count: 1, source_count: 2,
      rows: [{ date: '2026-10-12', open: 4180, high: 4200, low: 4170, close, closed: false }],
      strokes: [{ start: '2026-10-12', end: '2026-10-12', start_price: 4180, end_price: close, confirmed: true }],
      unfinished: [], segments: [], centers: [], segment_centers: [],
      fractals: [{ date: '2026-10-12', price: 4200, kind: 'top' }],
    })),
  }
}

let host: HTMLDivElement, root: Root, client: QueryClient
beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true })
  fixture.usable = true
  fixture.ma = null
  vi.mocked(api.spotGoldChart).mockReset().mockResolvedValue(snapshot())
  vi.mocked(api.refreshSpotGold).mockReset().mockResolvedValue(snapshot())
  host = document.createElement('div'); document.body.append(host)
  root = createRoot(host)
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } })
})
afterEach(async () => { await act(async () => root.unmount()); client.clear(); host.remove() })
async function settle() {
  for (let i = 0; i < 5; i++) await act(async () => { await new Promise(resolve => setTimeout(resolve, 0)) })
}
async function render() {
  await act(async () => root.render(<MemoryRouter><QueryClientProvider client={client}><SpotGoldPage /></QueryClientProvider></MemoryRouter>))
  await settle()
}
function button(label: string) {
  return [...host.querySelectorAll('button')].find(b => b.textContent?.includes(label))!
}

it('locates dates across gold periods without refetching, hides empty intervals and resets', async () => {
  await render()
  const change = async (label: string, value: string) => {
    await act(async () => {
      const input = host.querySelector<HTMLInputElement>(`input[aria-label="${label}"]`)!
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, value)
      input.dispatchEvent(new Event('input', { bubbles: true }))
      input.dispatchEvent(new Event('change', { bubbles: true }))
    })
  }
  await change('区间开始', '2026-10-12'); await change('区间结束', '2026-10-12')
  await act(async () => button('应用').click())
  expect(host.querySelector('[data-chart]')?.getAttribute('data-viewport')).toBe('{"startValue":0,"endValue":0}')
  await act(async () => button('4 小时').click())
  expect(host.querySelector<HTMLInputElement>('input[aria-label="区间结束"]')?.value).toBe('2026-10-12T23:59')
  expect(host.querySelector('[data-chart]')?.getAttribute('data-count')).toBe('1')
  await change('区间开始', '2020-01-01T00:00'); await change('区间结束', '2020-01-02T00:00')
  await act(async () => button('应用').click())
  expect(host.querySelector('[data-chart]')).toBeNull()
  expect(host.textContent).toContain('该区间没有已加载的 K 线')
  await act(async () => button('重置').click())
  expect(host.querySelector('[data-chart]')).not.toBeNull()
  expect(api.spotGoldChart).toHaveBeenCalledTimes(1)
})

it('defaults to daily and switches a single chart while retaining layers without refetching', async () => {
  await render()
  expect(host.querySelectorAll('[data-chart]')).toHaveLength(1)
  expect(host.querySelector('[data-chart]')?.getAttribute('data-symbol')).toBe('XAUUSD:1d')
  expect(button('日线').getAttribute('aria-pressed')).toBe('true')
  const strokes = host.querySelector<HTMLInputElement>('input[aria-label="笔"]')!
  await act(async () => strokes.click())
  for (const [label, timeframe] of [['4 小时', '4h'], ['1 小时', '1h'], ['周线', '1w'], ['日线', '1d']]) {
    await act(async () => button(label).click())
    expect(host.querySelectorAll('[data-chart]')).toHaveLength(1)
    expect(host.querySelector('[data-chart]')?.getAttribute('data-symbol')).toBe(`XAUUSD:${timeframe}`)
    expect(host.querySelector('[data-chart]')?.getAttribute('data-lines')).toBe('0')
    expect(button(label).getAttribute('aria-pressed')).toBe('true')
    expect(host.querySelector('[role="group"][aria-label="K线周期"]')?.querySelectorAll('button[aria-pressed="true"]')).toHaveLength(1)
  }
  expect(api.spotGoldChart).toHaveBeenCalledTimes(1)
})

it('shows fractals, shared MA controls and price-only MACD without refetching on display changes', async () => {
  await render()
  const chart = () => host.querySelector('[data-chart]')!
  expect(JSON.parse(chart().getAttribute('data-ma')!)).toEqual({ type: 'SMA', periods: [5, 10, 20, 60], showCrosses: true })
  expect(JSON.parse(chart().getAttribute('data-indicators')!)).toEqual(['macd'])
  expect(chart().getAttribute('data-points')).toBe('1')
  await act(async () => host.querySelector<HTMLInputElement>('input[aria-label="分型"]')!.click())
  expect(chart().getAttribute('data-points')).toBe('0')
  await act(async () => button('EMA').click())
  await act(async () => host.querySelector<HTMLInputElement>('input[aria-label="EMA5"]')!.click())
  await act(async () => button('4 小时').click())
  expect(JSON.parse(chart().getAttribute('data-ma')!)).toEqual({ type: 'EMA', periods: [10, 20, 60], showCrosses: true })
  expect(host.textContent).toContain('成交量未提供')
  expect(api.spotGoldChart).toHaveBeenCalledTimes(1)
})

it('shows an empty selected period without substituting another chart', async () => {
  const data = snapshot()
  data.panels.find(panel => panel.timeframe === '4h')!.rows = []
  vi.mocked(api.spotGoldChart).mockResolvedValue(data)
  await render()
  await act(async () => button('4 小时').click())
  expect(host.querySelectorAll('[data-chart]')).toHaveLength(0)
  expect(host.textContent).toContain('该周期暂无可用 K 线')
  await act(async () => button('日线').click())
  expect(host.querySelectorAll('[data-chart]')).toHaveLength(1)
})

it('fails closed when the gold source is unavailable', async () => {
  fixture.usable = false
  await render()
  expect(api.spotGoldChart).not.toHaveBeenCalled()
  expect(host.textContent).toContain('数据源不可用')
  expect(host.querySelectorAll('[data-chart]')).toHaveLength(0)
})

it('retains the last chart during refresh and visibly marks a stale response', async () => {
  let resolve!: (data: SpotGoldChart) => void
  vi.mocked(api.refreshSpotGold).mockImplementation(() => new Promise(r => { resolve = r }))
  await render()
  await act(async () => button('4 小时').click())
  await act(async () => button('刷新数据').click())
  expect(host.querySelectorAll('[data-chart]')).toHaveLength(1)
  await act(async () => resolve(snapshot('stale')))
  await settle()
  expect(host.querySelector('[role="alert"]')?.textContent).toContain('刷新失败')
  expect(host.textContent).toContain('16:40')
  expect(host.querySelectorAll('[data-chart]')).toHaveLength(1)
  expect(host.querySelector('[data-chart]')?.getAttribute('data-symbol')).toBe('XAUUSD:4h')
})

it('shows initial errors and can recover using refresh', async () => {
  vi.mocked(api.spotGoldChart).mockRejectedValue(new Error('暂不可用'))
  await render()
  expect(host.textContent).toContain('暂不可用')
  expect(host.querySelectorAll('[data-chart]')).toHaveLength(0)
  await act(async () => button('刷新数据').click())
  await settle()
  expect(host.querySelectorAll('[data-chart]')).toHaveLength(1)
})
