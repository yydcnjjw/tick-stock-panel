import { describe, expect, it } from 'vitest'
import { structureSeries } from './chartStructures'

describe('price structures', () => {
  const dates = new Map(['2024-01-01', '2024-01-02', '2024-01-03'].map((d, i) => [d, i]))
  it('adds no series without layers and rejects missing dates', () => {
    expect(structureSeries(undefined, dates)).toEqual([])
    expect(structureSeries({ segments: [{ start: 'missing', end: '2024-01-03', start_price: 10, end_price: 12 }] }, dates)).toEqual([])
  })
  it('preserves exact stroke endpoints, dashed status and center price bounds', () => {
    const series = structureSeries({
      segments: [{ start: '2024-01-01', end: '2024-01-03', start_price: 10, end_price: 12, dashed: true }],
      boxes: [{ start: '2024-01-01', end: '2024-01-03', low: 10.5, high: 11.5 }],
      points: [{ date: '2024-01-02', price: 13, label: '顶' }],
    }, dates)
    expect(series[0].data).toEqual([[0, 10, 2, 12, 1, '#F59E0B', 1.7]])
    expect(series[1].markArea.data[0][0]).toMatchObject({ xAxis: '2024-01-01', yAxis: 10.5 })
    expect(series[1].markArea.data[0][1]).toMatchObject({ xAxis: '2024-01-03', yAxis: 11.5 })
    expect(series[2].data[0].value).toEqual([1, 13])
  })
})
