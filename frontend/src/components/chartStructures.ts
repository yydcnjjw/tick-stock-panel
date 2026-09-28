import { format } from 'echarts'

export interface ChartSegment {
  start: string
  end: string
  start_price: number
  end_price: number
  color?: string
  width?: number
  dashed?: boolean
}
export interface ChartBox {
  color?: string
  borderColor?: string
  start: string
  end: string
  low: number
  high: number
}
export interface ChartPoint {
  date: string
  price: number
  label: string
  description?: string
  signal?: boolean
  candidate?: boolean
  above?: boolean
}
export interface ChartStructures {
  segments?: ChartSegment[]
  boxes?: ChartBox[]
  points?: ChartPoint[]
}

/** 结构使用真实日期和价格坐标；不依赖 DOM 或图表像素位置。 */
export function structureSeries(layers: ChartStructures | undefined, dates: Map<string, number>): any[] {
  if (!layers) return []
  const series: any[] = []
  const segments = (layers.segments ?? []).filter(s => dates.has(s.start) && dates.has(s.end)
    && Number.isFinite(s.start_price) && Number.isFinite(s.end_price))
  if (segments.length) series.push({
    id: 'structure-segments', name: '笔结构', type: 'custom', silent: true, clip: true,
    animation: false, z: 5,
    encode: { x: [0, 2], y: [1, 3] },
    data: segments.map(s => [dates.get(s.start), s.start_price, dates.get(s.end), s.end_price, s.dashed ? 1 : 0, s.color ?? (s.dashed ? '#F59E0B' : '#3B82F6'), s.width ?? 1.7]),
    renderItem: (_params: unknown, api: any) => {
      const a = api.coord([api.value(0), api.value(1)])
      const b = api.coord([api.value(2), api.value(3)])
      return { type: 'line', shape: { x1: a[0], y1: a[1], x2: b[0], y2: b[1] },
        style: { stroke: api.value(5), lineWidth: api.value(6),
          lineDash: api.value(4) ? [5, 4] : undefined } }
    },
  })
  const boxes = (layers.boxes ?? []).filter(b => dates.has(b.start) && dates.has(b.end)
    && Number.isFinite(b.low) && Number.isFinite(b.high) && b.low <= b.high)
  if (boxes.length) series.push({
    id: 'structure-centers', name: '结构中枢', type: 'line', data: [], silent: true, animation: false,
    markArea: { silent: true, animation: false, itemStyle: { color: 'rgba(139,92,246,0.13)',
      borderColor: 'rgba(139,92,246,0.65)', borderWidth: 1 },
      label: { show: false },
      data: boxes.map(b => [{ xAxis: b.start, yAxis: b.low, itemStyle: { color: b.color ?? 'rgba(139,92,246,0.13)', borderColor: b.borderColor ?? 'rgba(139,92,246,0.65)' } }, { xAxis: b.end, yAxis: b.high }]),
    },
  })
  const points = (layers.points ?? []).filter(p => dates.has(p.date) && Number.isFinite(p.price))
  if (points.length) series.push({
    id: 'structure-points', name: '结构与辅助信号', type: 'scatter', animation: false, z: 8,
    labelLayout: { hideOverlap: true },
    tooltip: { trigger: 'item', backgroundColor: '#1F2937', borderWidth: 0,
      textStyle: { fontSize: 12, color: '#F9FAFB' },
      formatter: (p: any) => format.encodeHTML(p.data.name),
    },
    data: points.map(p => ({ value: [dates.get(p.date), p.price],
      date: p.date,
      name: `${p.date} · ${p.description ?? p.label} · ${p.price.toFixed(2)}`,
      symbol: p.candidate ? 'emptyTriangle' : p.signal ? 'pin' : 'diamond', symbolSize: p.signal ? 25 : 6,
      symbolRotate: p.signal && !p.above ? 180 : 0,
      symbolOffset: p.signal ? [0, p.above ? '-20%' : '20%'] : [0, 0],
      itemStyle: { color: p.signal ? (p.above ? '#2D9B65' : '#C74040') : '#8B5CF6' },
      label: { show: p.signal ?? false, formatter: p.label, position: p.above ? 'top' : 'bottom',
        color: p.above ? '#2D9B65' : '#C74040', fontSize: 10 },
    })),
  })
  return series
}
