// @vitest-environment jsdom
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { EChartsCandlestick, type OHLC } from './EChartsCandlestick'
import type { MovingAverageOptions } from './chartMovingAverages'

const chart = vi.hoisted(() => ({
  handlers: {} as Record<string, (event?: any) => void>,
  setOption: vi.fn(),
  on: vi.fn(),
  off: vi.fn(),
  dispatchAction: vi.fn(),
  resize: vi.fn(),
  dispose: vi.fn(),
  getZr: () => ({ on: vi.fn(), off: vi.fn() }),
  getOption: () => ({ dataZoom: [{ start: 0, end: 100 }] }),
  containPixel: () => false,
  convertFromPixel: () => [0, 0],
}))

vi.mock('echarts', async importOriginal => ({ ...await importOriginal<typeof import('echarts')>(), init: () => chart }))

// 超过 COMPACT_THRESHOLD(60): 切标的后 dataZoom 一响就会走紧凑态转换, 进而重写 markPoint
const DAYS = Array.from({ length: 62 }, (_, i) =>
  new Date(Date.UTC(2024, 0, 1 + i)).toISOString().slice(0, 10))
const rows = (base: number): OHLC[] =>
  DAYS.map(date => ({ date, open: base, high: base + 1, low: base - 1, close: base, volume: 1 }))

let host: HTMLDivElement
let root: ReturnType<typeof createRoot>

beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true })
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} })
  chart.handlers = {}
  chart.setOption.mockClear()
  chart.dispatchAction.mockClear()
  chart.on.mockImplementation((name: string, callback: (event?: any) => void) => {
    chart.handlers[name] = callback
  })
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
})

afterEach(async () => {
  await act(async () => root.unmount())
  host.remove()
  vi.unstubAllGlobals()
})

/** 最后一次 setOption 里 K 线的 markPoint (买卖箭头来源) */
function lastMarkPoint(): string {
  const option = chart.setOption.mock.calls.at(-1)?.[0] as any
  const k = option?.series?.find((s: any) => s.name === 'K')
  return JSON.stringify(k?.markPoint ?? null)
}

/** 模拟竖虚线命中某根 K 线 (鼠标在数据区内移动时 echarts 派发的事件) */
async function hoverCandle(index: number) {
  await act(async () => { chart.handlers.updateAxisPointer?.({ axesInfo: [{ value: index }] }) })
}

it('applies exact candle indices without slicing structures, preserves free zoom, and can reapply or reset', async () => {
  const data = rows(10)
  const structures = { segments: [{ start: DAYS[0], end: DAYS[50], start_price: 9, end_price: 11 }] }
  const viewport = { startValue: 10, endValue: 20 }
  const render = async (request: typeof viewport | { reset: true }, showMA = true) => {
    await act(async () => root.render(<EChartsCandlestick data={data} structures={structures} viewport={request}
      visibleBars={10} symbol="test" showMA={showMA} />))
  }
  await render(viewport)
  expect(chart.dispatchAction).toHaveBeenLastCalledWith({ type: 'dataZoom', startValue: 10, endValue: 20 })
  const option = chart.setOption.mock.calls.find(call => call[0]?.xAxis)?.[0] as any
  expect(option.xAxis[0].data).toHaveLength(data.length)
  expect(option.series.some((series: any) => series.type === 'custom')).toBe(true)
  await act(async () => chart.handlers.dataZoom())
  await render(viewport, false)
  expect(chart.dispatchAction).toHaveBeenLastCalledWith({ type: 'dataZoom', start: 0, end: 100 })
  await render({ ...viewport }, false)
  expect(chart.dispatchAction).toHaveBeenLastCalledWith({ type: 'dataZoom', startValue: 10, endValue: 20 })
  await render({ reset: true })
  expect(chart.dispatchAction.mock.calls.at(-1)?.[0]).toMatchObject({ type: 'dataZoom', end: 100 })
  expect(chart.dispatchAction.mock.calls.at(-1)?.[0].start).toBeGreaterThan(80)
})

it('切标的后响应 dataZoom 仍用当前标的的买卖标记, 不回退到上一只', async () => {
  const render = async (symbol: string, data: OHLC[], markerLabel: string, markerDate: string) => {
    await act(async () => root.render(
      <EChartsCandlestick
        data={data}
        markers={[{ date: markerDate, kind: 'buy', label: markerLabel }]}
        symbol={symbol}
        height={400}
        showInfoBar={false}
        visibleBars="all"
        activeIndicators={['vol']}
      />,
    ))
  }

  await render('600000', rows(10), 'A-BUY', DAYS[10])
  await render('000001', rows(100), 'B-SELL', DAYS[20])

  // 切换标的后图表实例被复用, dataZoom 监听仍是最初注册的那个; 首次 setOption 已是当前标的
  expect(lastMarkPoint()).toContain('B-SELL')

  await act(async () => { chart.handlers.dataZoom?.() })

  expect(lastMarkPoint()).toContain('B-SELL')
  expect(lastMarkPoint()).not.toContain('A-BUY')
})

it('切股后鼠标未离开图表, 竖虚线重新命中即恢复「至今/周期」', async () => {
  const render = async (symbol: string) => {
    await act(async () => root.render(
      <EChartsCandlestick data={rows(10)} symbol={symbol} height={400} visibleBars="all" />,
    ))
  }

  await render('600000')
  const surface = host.firstElementChild as HTMLElement
  await act(async () => { surface.dispatchEvent(new MouseEvent('mouseenter')) })
  await hoverCandle(30)
  expect(host.textContent).toContain('至今')

  // 切股: 上一只的悬停上下文作废, 清掉「至今/周期」
  await render('000001')
  expect(host.textContent).not.toContain('至今')

  // 鼠标没离开图表区 (只是切股), 竖虚线重新命中即恢复, 不靠 mouseenter
  await hoverCandle(20)
  expect(host.textContent).toContain('至今')
  expect(host.textContent).toContain('周期')

  // 移出图表区 → 重新收起
  await act(async () => { surface.dispatchEvent(new MouseEvent('mouseleave')) })
  expect(host.textContent).not.toContain('至今')
})

it('switches precise SMA/EMA lines, values and crossing markers while preserving ordinary chart defaults', async () => {
  const data = rows(10).map((row, i) => ({ ...row,
    ma5: i < 30 ? 9 : 11, ma10: 10, ma20: 10, ma60: 10,
    ema5: i < 30 ? 11 : 9, ema10: 10, ema20: 10, ema60: 10,
  }))
  const render = async (movingAverages?: MovingAverageOptions, showMA = true) => {
    await act(async () => root.render(<EChartsCandlestick data={data} movingAverages={movingAverages} showMA={showMA} visibleBars="all" />))
  }
  const series = () => chart.setOption.mock.calls.findLast(([option]) => option.series?.some((s: any) => s.name === 'K'))![0].series
  await render()
  expect(series().find((s: any) => s.name === 'MA5').smooth).toBe(true)
  expect(series().some((s: any) => s.id === 'ma-crosses')).toBe(false)
  expect(host.textContent).toContain('MA5:11.00')
  const options: MovingAverageOptions = { type: 'SMA', periods: [5, 10, 20, 60], showCrosses: true }
  await render(options)
  expect(series().find((s: any) => s.name === 'SMA5')).toMatchObject({ smooth: false, connectNulls: false })
  let crosses = series().find((s: any) => s.id === 'ma-crosses')
  expect(crosses.data).toHaveLength(1)
  expect(crosses.data[0]).toMatchObject({ date: DAYS[30], value: [30, 9], symbol: 'arrow' })
  expect(crosses.tooltip.formatter({ data: crosses.data[0] })).toContain('SMA 5↔10 金叉')
  const values = crosses.data
  await act(async () => chart.handlers.dataZoom?.())
  expect(series().find((s: any) => s.id === 'ma-crosses').data).toBe(values)
  await render({ ...options, type: 'EMA' })
  expect(series().some((s: any) => s.name === 'SMA5')).toBe(false)
  expect(host.textContent).toContain('EMA5:9.00')
  crosses = series().find((s: any) => s.id === 'ma-crosses')
  expect(crosses.tooltip.formatter({ data: crosses.data[0] })).toContain('EMA 5↔10 死叉')
  for (const periods of [[10, 20, 60], [5, 20, 60], []] as const) {
    await render({ ...options, periods })
    expect(series().some((s: any) => s.id === 'ma-crosses')).toBe(false)
    expect(series().filter((s: any) => /^SMA/.test(s.name))).toHaveLength(periods.length)
  }
  await render({ ...options, showCrosses: false })
  expect(series().some((s: any) => s.id === 'ma-crosses')).toBe(false)
  expect(series().filter((s: any) => /^SMA/.test(s.name))).toHaveLength(4)
  await render(options, false)
  expect(series().filter((s: any) => /^SMA/.test(s.name) || s.id === 'ma-crosses')).toHaveLength(0)
})

it('shows warmup as unavailable and never joins MA lines through a null gap', async () => {
  const data = rows(10).slice(0, 3).map((row, i) => ({ ...row, ma5: i === 1 ? null : 10 }))
  await act(async () => root.render(<EChartsCandlestick data={data} movingAverages={{ type: 'SMA', periods: [5, 10], showCrosses: true }} />))
  const option = chart.setOption.mock.calls.at(-1)![0]
  expect(option.series.find((s: any) => s.name === 'SMA5')).toMatchObject({ data: [10, '-', 10], connectNulls: false })
  expect(host.textContent).toContain('SMA10:—')
  expect(option.series.find((s: any) => s.id === 'ma-crosses').data).toEqual([])
})
