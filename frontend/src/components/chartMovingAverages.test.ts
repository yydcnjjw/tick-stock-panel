import { expect, it } from 'vitest'
import { movingAverageCrosses } from './chartMovingAverages'

const rows = (values: (number | null)[]) => values.map((delta, i) => ({
  date: `day-${i}`, low: 8, high: 12,
  ma5: delta == null ? null : 10 + delta, ma10: 10,
  ema5: delta == null ? null : 10 - delta, ema10: 10,
}))

it('waits through equality, ignores touches and initial baselines, and detects both directions', () => {
  const data = rows([0, 0, -1, 0, -1, 0, 0, 1, 1, 0, -1])
  expect(movingAverageCrosses(data, 'SMA').map(p => [p.date, p.direction])).toEqual([
    ['day-7', 'golden'], ['day-10', 'death'],
  ])
  expect(movingAverageCrosses(data, 'EMA').map(p => [p.date, p.direction])).toEqual([
    ['day-7', 'death'], ['day-10', 'golden'],
  ])
})

it('resets the baseline at absent or invalid values, without bridging a warmup or gap', () => {
  const data = rows([-1, null, 1, Number.NaN, -1, Number.POSITIVE_INFINITY, 1, -1])
  expect(movingAverageCrosses(data, 'SMA').map(p => p.date)).toEqual(['day-7'])
  expect(movingAverageCrosses([{ date: 'old-response', low: 8, high: 12 }], 'SMA')).toEqual([])
  expect(movingAverageCrosses([], 'EMA')).toEqual([])
})

it('uses exact values and is prefix stable, without rounding or visible-window baselines', () => {
  const data = rows([-0.00001, 0.00001, -1, 1])
  const all = movingAverageCrosses(data, 'SMA')
  expect(all[0]).toMatchObject({ date: 'day-1', direction: 'golden', short: 10.00001, long: 10 })
  expect(movingAverageCrosses(data.slice(0, 3), 'SMA')).toEqual(all.slice(0, 2))
})
