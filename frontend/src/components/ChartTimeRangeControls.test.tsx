// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, it } from 'vitest'
import { ChartTimeRangeControls, useChartTimeRange } from './ChartTimeRangeControls'

const rows = ['2026-10-12T09:35', '2026-10-12T10:00', '2026-10-13T09:35'].map(date => ({ date }))
function Harness({ intraday = true, data = rows }: { intraday?: boolean; data?: { date: string }[] }) {
  const selection = useChartTimeRange(data, intraday)
  return <><ChartTimeRangeControls selection={selection} />
    {!selection.empty && <output data-chart data-count={data.length}>{JSON.stringify(selection.viewport)}</output>}</>
}
let host: HTMLDivElement, root: Root
beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true })
  host = document.createElement('div'); document.body.append(host); root = createRoot(host)
})
afterEach(async () => { await act(async () => root.unmount()); host.remove() })
const render = async (intraday = true, data = rows) => { await act(async () => root.render(<Harness intraday={intraday} data={data} />)) }
async function change(label: string, value: string) {
  const input = host.querySelector<HTMLInputElement>(`input[aria-label="${label}"]`)!
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, value)
    input.dispatchEvent(new Event('input', { bubbles: true }))
    input.dispatchEvent(new Event('change', { bubbles: true }))
  })
}
const click = async (label: string) => { await act(async () => [...host.querySelectorAll('button')].find(b => b.textContent === label)!.click()) }

it('only applies on request, validates reversal and clips while retaining full chart data', async () => {
  await render()
  await change('区间开始', '2026-10-12T10:00'); await change('区间结束', '2026-10-12T09:35')
  expect(host.textContent).toContain('开始时间不能晚于结束时间')
  expect(host.querySelector('[data-chart]')?.textContent).toBe('{"reset":true}')
  expect(host.querySelector<HTMLButtonElement>('button[type=submit]')?.disabled).toBe(true)
  await change('区间开始', '2026-01-01T00:00'); await change('区间结束', '2026-10-12T10:00')
  expect(host.querySelector('[data-chart]')?.textContent).toBe('{"reset":true}')
  await click('应用')
  expect(host.querySelector('[data-chart]')?.textContent).toBe('{"startValue":0,"endValue":1}')
  expect(host.querySelector('[data-chart]')?.getAttribute('data-count')).toBe('3')
  expect(host.textContent).toContain('已显示交集')
})

it('keeps selection across period/data changes, shows empty intervals, and resets without persistence', async () => {
  await render(false)
  await change('区间开始', '2026-10-12'); await change('区间结束', '2026-10-12'); await click('应用')
  await render(true)
  expect(host.querySelector<HTMLInputElement>('input[aria-label="区间结束"]')?.value).toBe('2026-10-12T23:59')
  expect(host.querySelector('[data-chart]')?.textContent).toBe('{"startValue":0,"endValue":1}')
  await render(true, [{ date: '2026-01-01T09:35' }])
  expect(host.querySelector('[data-chart]')).toBeNull()
  expect(host.textContent).toContain('该区间没有已加载的 K 线')
  await click('重置')
  expect(host.querySelector('[data-chart]')?.textContent).toBe('{"reset":true}')
  expect(host.querySelector<HTMLInputElement>('input[aria-label="区间开始"]')?.value).toBe('')
  await act(async () => root.unmount()); root = createRoot(host)
  await render()
  expect(host.querySelector<HTMLInputElement>('input[aria-label="区间开始"]')?.value).toBe('')
})
