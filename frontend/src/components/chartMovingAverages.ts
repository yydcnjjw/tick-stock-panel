import { format } from 'echarts'

export const MA_PERIODS = [5, 10, 20, 60] as const
export type MovingAveragePeriod = typeof MA_PERIODS[number]
export type MovingAverageType = 'SMA' | 'EMA'
export type MovingAverageValues = Partial<Record<`ma${MovingAveragePeriod}` | `ema${MovingAveragePeriod}`, number | null>>
export interface MovingAverageOptions {
  type: MovingAverageType
  periods: readonly MovingAveragePeriod[]
  showCrosses: boolean
}
type Bar = MovingAverageValues & { date: string; high: number; low: number }

const COLORS = { 5: '#A1A1AA', 10: '#3B82F6', 20: '#F97316', 60: '#8B5CF6' }

export function movingAverageLines(options?: MovingAverageOptions) {
  return (options?.periods ?? MA_PERIODS).map(period => ({
    key: `${options?.type === 'EMA' ? 'ema' : 'ma'}${period}` as keyof MovingAverageValues,
    name: `${options?.type ?? 'MA'}${period}`,
    color: COLORS[period],
  }))
}

/** Equality preserves the last strict relation; unavailable values reset the baseline. */
export function movingAverageCrosses(data: Bar[], type: MovingAverageType) {
  const points: { date: string; price: number; direction: 'golden' | 'death'; short: number; long: number }[] = []
  let previous = 0
  for (const bar of data) {
    const short = type === 'EMA' ? bar.ema5 : bar.ma5
    const long = type === 'EMA' ? bar.ema10 : bar.ma10
    if (short == null || long == null || !Number.isFinite(short) || !Number.isFinite(long)) {
      previous = 0
      continue
    }
    const relation = Math.sign(short - long)
    if (!relation) continue
    if (previous && relation !== previous) {
      points.push({ date: bar.date, price: relation > 0 ? bar.low : bar.high,
        direction: relation > 0 ? 'golden' : 'death', short, long })
    }
    previous = relation
  }
  return points
}

/** Absent options preserve the ordinary chart's four smooth SMA lines. */
export function movingAverageSeries(data: Bar[], dates: Map<string, number>, options?: MovingAverageOptions): any[] {
  const lines = movingAverageLines(options)
  if (!lines.some(line => data.some(row => row[line.key] != null))) return []
  const series: any[] = lines.map(line => ({
    name: line.name, type: 'line',
    data: data.map(row => row[line.key] != null && Number.isFinite(row[line.key]) ? row[line.key] : '-'),
    smooth: !options, connectNulls: false, symbol: 'none', animation: false, silent: true,
    lineStyle: { width: 1, color: line.color }, itemStyle: { color: line.color },
  }))
  if (options?.showCrosses && options.periods.includes(5) && options.periods.includes(10)) {
    const points = movingAverageCrosses(data, options.type)
    series.push({
      id: 'ma-crosses', name: '均线交叉', type: 'scatter', animation: false, z: 9,
      tooltip: { trigger: 'item', confine: true, backgroundColor: '#1F2937', borderWidth: 0,
        extraCssText: 'max-width:280px;white-space:normal;overflow-wrap:anywhere;',
        textStyle: { fontSize: 12, color: '#F9FAFB' },
        formatter: (p: { data: { name: string } }) => format.encodeHTML(p.data.name),
      },
      data: points.map(point => ({
        date: point.date, value: [dates.get(point.date), point.price],
        name: `${point.date} · ${options.type} 5↔10 ${point.direction === 'golden' ? '金叉' : '死叉'} · ${options.type}5:${point.short.toFixed(6)} · ${options.type}10:${point.long.toFixed(6)}`,
        symbol: 'arrow', symbolSize: 10, symbolRotate: point.direction === 'golden' ? 0 : 180,
        symbolOffset: [0, point.direction === 'golden' ? '70%' : '-70%'],
        itemStyle: { color: point.direction === 'golden' ? '#D97706' : '#0891B2' },
      })),
    })
  }
  return series
}
