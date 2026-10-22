import { expect, it } from 'vitest'
import { resolveChartTimeRange } from './chartTimeRange'

const rows = ['2026-10-12T09:35', '2026-10-12T10:00', '2026-10-13T09:35'].map(date => ({ date }))

it('includes endpoints and never mutates or slices the input', () => {
  const before = JSON.stringify(rows)
  expect(resolveChartTimeRange(rows, { start: rows[0].date, end: rows[1].date }, true)).toMatchObject({ startIndex: 0, endIndex: 1, clipped: false })
  expect(JSON.stringify(rows)).toBe(before)
})

it('treats dates as whole days for intraday charts and matches daily/weekly labels by date', () => {
  expect(resolveChartTimeRange(rows, { start: '2026-10-12', end: '2026-10-12' }, true)).toMatchObject({ startIndex: 0, endIndex: 1 })
  expect(resolveChartTimeRange([{ date: '2026-10-12' }], { start: rows[0].date, end: rows[1].date }, false)).toMatchObject({ startIndex: 0, endIndex: 0 })
  expect(resolveChartTimeRange([{ date: '2026-10-12' }], { start: '2026-10-14', end: '2026-10-16' }, false)).toBeNull()
})

it('clips partial overlap and returns no window for gaps or no overlap', () => {
  expect(resolveChartTimeRange(rows, { start: '2020-01-01', end: rows[1].date }, true)).toMatchObject({ startIndex: 0, endIndex: 1, clipped: true })
  expect(resolveChartTimeRange(rows, { start: '2026-10-14', end: '2026-10-16' }, true)).toBeNull()
  expect(resolveChartTimeRange(rows, { start: '2026-10-12T11:00', end: '2026-10-12T12:00' }, true)).toBeNull()
  expect(resolveChartTimeRange([], { start: '2020-01-01', end: '2030-01-01' }, true)).toBeNull()
})
