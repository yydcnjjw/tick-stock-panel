// @vitest-environment jsdom
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { EChartsIntraday } from './EChartsIntraday'
import { EChartsMultiDayIntraday } from './EChartsMultiDayIntraday'

const chart = vi.hoisted(() => ({
  handlers: {} as Record<string, (event?: any) => void>,
  on: vi.fn(), off: vi.fn(), setOption: vi.fn(), clear: vi.fn(), resize: vi.fn(), dispose: vi.fn(),
  getZr: () => ({ on: vi.fn(), off: vi.fn() }),
}))
vi.mock('echarts', () => ({ init: () => chart }))
vi.mock('@/lib/theme', () => ({ useChartTheme: () => ({}) }))

const date = '2026-10-13'
const row = { datetime: `${date} 09:30:00`, open: 10, high: 10, low: 10, close: 10, volume: 100 }
let cleanup = async () => {}
beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true })
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} })
  chart.on.mockImplementation((name, callback) => { chart.handlers[name] = callback })
})
afterEach(async () => { await cleanup(); vi.unstubAllGlobals() })

function amountText(host: HTMLElement, label: string) {
  return Array.from(host.querySelectorAll('span'))
    .find(el => el.textContent === label)?.nextElementSibling?.textContent
}

for (const mode of ['single', 'multi'] as const) {
  it.each<[number | null, string]>([
    [0, '0'],
    [9999, '9999'],
    [10_000, '1万'],
    [100_000_000, '1.00亿'],
    [1_000_000_000, '10.00亿'],
    [779_302_288_847, '7793.02亿'],
    [null, '—'],
    [NaN, '—'],
    [Infinity, '—'],
  ])(`${mode} formats amount %s yuan as %s`, async (amount, expected) => {
    const host = document.createElement('div')
    const root = createRoot(host)
    cleanup = async () => { await act(async () => root.unmount()) }
    const data = [{ ...row, amount }]
    await act(async () => root.render(mode === 'single'
      ? <EChartsIntraday data={data} date={date} />
      : <EChartsMultiDayIntraday sessions={[{ date, prev_close: 10, rows: data }]} />))
    expect(amountText(host, mode === 'single' ? '累计额' : '额')).toBe(expected)
  })
}

it('formats the sum and hovered minute separately, then restores the sum', async () => {
  const host = document.createElement('div')
  const root = createRoot(host)
  cleanup = async () => { await act(async () => root.unmount()) }
  await act(async () => root.render(<EChartsIntraday date={date} data={[
    { ...row, amount: 100_000_000 },
    { ...row, datetime: `${date} 09:31:00`, amount: 900_000_000 },
  ]} />))
  expect(amountText(host, '累计额')).toBe('10.00亿')
  await act(async () => chart.handlers.updateAxisPointer({ axesInfo: [{ axisDim: 'x', value: 0 }] }))
  expect(amountText(host, '额')).toBe('1.00亿')
  await act(async () => chart.handlers.globalout())
  expect(amountText(host, '累计额')).toBe('10.00亿')
})
