import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ExternalLink, RefreshCw } from 'lucide-react'
import { EChartsCandlestick, type ChartMarker } from '@/components/EChartsCandlestick'
import { ChartTimeRangeControls, useChartTimeRange } from '@/components/ChartTimeRangeControls'
import type { ChartStructures } from '@/components/chartStructures'
import type { MovingAverageOptions } from '@/components/chartMovingAverages'
import { loadChanMovingAverages, MovingAverageControls } from '@/components/MovingAverageControls'
import { api, type GoldTimeframe, type SpotGoldPanel } from '@/lib/api'
import { QK } from '@/lib/queryKeys'
import { useCapabilityMatrix } from '@/lib/useSharedQueries'
import { storage } from '@/lib/storage'

const LAYERS = { fractals: '分型', strokes: '已完成笔', centers: '笔中枢', segments: '线段', segmentCenters: '线段中枢', unfinished: '未完成结构' }
type Layers = Record<keyof typeof LAYERS, boolean>
const PERIODS: Record<GoldTimeframe, string> = { '1w': '周线', '1d': '日线', '4h': '4 小时', '1h': '1 小时' }
const VISIBLE: Record<GoldTimeframe, number> = { '1w': 100, '1d': 140, '4h': 160, '1h': 240 }
const INDICATORS = ['macd']
const NO_VOLUME_COMPARE = { enabled: false, days: 1 }

function GoldPanel({ panel, layers, onLayersChange, movingAverages, onMovingAveragesChange, timeSelection }: {
  panel: SpotGoldPanel; layers: Layers; onLayersChange: (next: Layers) => void;
  movingAverages: MovingAverageOptions; onMovingAveragesChange: (next: MovingAverageOptions) => void
  timeSelection: ReturnType<typeof useChartTimeRange>
}) {
  const structures = useMemo<ChartStructures>(() => ({
    segments: [
      ...(layers.strokes ? panel.strokes : []),
      ...(layers.strokes && layers.unfinished ? panel.unfinished.map(s => ({ ...s, dashed: true })) : []),
      ...(layers.segments ? panel.segments.filter(s => layers.unfinished || s.confirmed)
        .map(s => ({ ...s, dashed: !s.confirmed, color: '#14B8A6', width: 3 })) : []),
    ],
    boxes: [
      ...(layers.centers ? panel.centers.filter(s => layers.unfinished || s.confirmed)
        .map(s => ({ ...s, dashed: !s.confirmed })) : []),
      ...(layers.segmentCenters ? panel.segment_centers.filter(s => layers.unfinished || s.confirmed)
        .map(s => ({ ...s, dashed: !s.confirmed, color: 'rgba(20,184,166,0.12)', borderColor: '#14B8A6' })) : []),
    ],
    points: layers.fractals ? (panel.fractals ?? []).map(f => ({ ...f, label: f.kind === 'top' ? '顶分型' : '底分型' })) : [],
  }), [panel, layers])
  const markers = useMemo<ChartMarker[]>(() => panel.rows.filter(r => !r.closed).map(r => ({
    date: r.date, kind: 'neutral', above: true, label: '未收盘', color: '#D97706',
  })), [panel.rows])

  return <section aria-label={`${panel.label}图`} className="min-w-0 overflow-hidden rounded-card border border-border bg-surface">
    <div role="group" aria-label="缠论图层" className="flex flex-wrap gap-x-4 gap-y-2 border-b border-border p-3">
      {(Object.entries(LAYERS) as [keyof Layers, string][]).map(([key, label]) => <label key={key} className="flex cursor-pointer items-center gap-1.5 text-xs text-secondary">
        <input type="checkbox" aria-label={key === 'strokes' ? '笔' : label} checked={layers[key]}
          onChange={() => onLayersChange({ ...layers, [key]: !layers[key] })} className="accent-accent" />{label}
      </label>)}
    </div>
    <MovingAverageControls value={movingAverages} onChange={onMovingAveragesChange} />
    <ChartTimeRangeControls selection={timeSelection} />
    <div className="p-1 sm:p-3">
      {panel.rows.length && !timeSelection.empty ? <EChartsCandlestick data={panel.rows} structures={structures} markers={markers}
        symbol={`XAUUSD:${panel.timeframe}`} height={650} visibleBars={VISIBLE[panel.timeframe]}
        stockInfo={{ name: '伦敦金现' }} movingAverages={movingAverages}
        viewport={timeSelection.viewport}
        activeIndicators={INDICATORS} volumeCompare={NO_VOLUME_COMPARE} />
        : !panel.rows.length && <p role="status" className="p-8 text-center text-sm text-muted">该周期暂无可用 K 线。</p>}
    </div>
    <div className="flex flex-wrap gap-x-4 gap-y-1 border-t border-border px-3 py-2 text-[11px] text-muted">
      <span className="text-blue-500">蓝线：笔 · 青线：线段</span>
      <span className="text-amber-500">虚线：未完成结构，可能变化 · ● 未收盘 K 线</span>
      <span className="text-violet-500">紫色区域：笔中枢 · 青色区域：线段中枢</span>
      <span>均线箭头：金色金叉 · 青色死叉 · 悬停查看数值，仅辅助观察</span>
      <span>拖动 / 滚轮缩放</span>
    </div>
    <div className="flex flex-wrap gap-x-4 gap-y-1 border-t border-border px-3 py-2 text-[11px] text-muted">
      <span>{panel.timeframe.endsWith('h') ? '北京时间 · 结束标签' : panel.timeframe === '1w' ? '横轴为周起日' : '横轴为供应商交易日期'}</span>
      <span>已收盘 {panel.input_count} 根</span>
      <span>历史起点 {panel.input_start?.replace('T', ' ') ?? '—'}</span>
    </div>
  </section>
}

export default function SpotGoldPage({ title = '伦敦金现' }: { title?: string }) {
  const client = useQueryClient()
  const matrix = useCapabilityMatrix()
  const capability = matrix.data?.capabilities.find(c => c.id === 'spot_gold')
  const provider = capability?.effective ?? ''
  const usable = capability?.usable === true
  const [timeframe, setTimeframe] = useState<GoldTimeframe>('1d')
  const [layers, setLayers] = useState<Layers>({ fractals: true, strokes: true, segments: true, centers: true, segmentCenters: true, unfinished: true })
  const [movingAverages, setMovingAverages] = useState(loadChanMovingAverages)
  const updateMovingAverages = (next: MovingAverageOptions) => {
    setMovingAverages(next)
    storage.chanMovingAverages.set(next)
  }
  const query = useQuery({
    queryKey: QK.spotGoldChart(provider),
    queryFn: async () => {
      const value = await api.spotGoldChart()
      if (value.provider !== provider) throw new Error('黄金数据源已变更，请重新打开页面。')
      return value
    },
    enabled: usable, staleTime: 60_000, refetchOnWindowFocus: false, retry: false,
  })
  const refresh = useMutation({
    mutationFn: api.refreshSpotGold,
    onSuccess: value => client.setQueryData(QK.spotGoldChart(value.provider), value),
  })
  const data = usable ? query.data : undefined
  const selectedPanel = data?.panels.find(panel => panel.timeframe === timeframe)
  const timeSelection = useChartTimeRange(selectedPanel?.rows, timeframe.endsWith('h'))
  const error = refresh.error ?? query.error
  const fetching = query.isFetching || refresh.isPending
  const last = data?.panels.find(p => p.timeframe === '1h')?.rows.at(-1)
  const refreshed = data && new Date(data.refreshed_at).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false })

  return <div className="mx-auto max-w-[1600px] space-y-4 p-3 pt-14 md:p-6">
    <header className="flex flex-wrap items-start justify-between gap-4">
      <div className="min-w-0">
        <h1 className="text-lg font-semibold text-foreground">{title} · {PERIODS[timeframe]}结构图</h1>
        <p className="mt-1 text-xs text-muted">chan.py 结构快照 · 均线与 MACD 辅助观察</p>
      </div>
      <Link to="/settings?tab=ext-pages" className="text-xs text-accent hover:underline">管理扩展页面</Link>
    </header>
    <div role="group" aria-label="K线周期" className="flex flex-wrap gap-2">
      {(Object.entries(PERIODS) as [GoldTimeframe, string][]).map(([value, label]) => <button key={value} type="button"
        aria-pressed={timeframe === value} onClick={() => setTimeframe(value)}
        className={`rounded-btn border px-3 py-1.5 text-xs ${timeframe === value ? 'border-accent bg-accent/10 text-accent' : 'border-border text-secondary hover:bg-elevated'}`}>
        {label}
      </button>)}
    </div>
    <div className="flex flex-wrap items-center justify-between gap-3">
      <div className="min-w-0">
        <h2 className="flex flex-wrap items-center gap-2 text-base font-medium text-foreground">
          伦敦金现 <span className="font-mono text-xs text-muted">XAU/USD</span>
          <span className="rounded bg-elevated px-1.5 py-0.5 text-[10px] text-secondary">现货 / CFD</span>
          {last && <span className="font-mono text-sm text-foreground">{last.close.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</span>}
        </h2>
        <p className="mt-1 text-xs text-muted">结构截至 {selectedPanel?.cutoff?.replace('T', ' ') ?? '暂无已收盘数据'} · 美元/金衡盎司</p>
        <p className="mt-1 text-[11px] text-muted">小时 K 线参考价 · 成交量未提供</p>
      </div>
      <button type="button" disabled={!usable || fetching} onClick={() => refresh.mutate()}
        className="inline-flex items-center gap-1.5 rounded-btn border border-border px-3 py-1.5 text-xs text-secondary disabled:cursor-not-allowed disabled:opacity-50">
        <RefreshCw size={13} className={fetching ? 'animate-spin' : ''} />{fetching ? '正在刷新' : '刷新数据'}
      </button>
    </div>

    {matrix.isPending && <p role="status" className="text-sm text-muted">正在检查黄金数据源…</p>}
    {!matrix.isPending && !usable && <div role="alert" className="rounded-lg border border-border bg-card p-4 text-sm text-muted">
      {matrix.isError ? '暂时无法确认黄金数据源状态。' : '伦敦金数据源不可用。'}
      <Link to="/settings?tab=data-sources" className="ml-2 text-accent underline">检查数据源设置</Link>
    </div>}
    {usable && fetching && !data && <p role="status" className="py-8 text-center text-sm text-muted">正在获取黄金 K 线并计算四周期结构…</p>}
    {usable && (error || data?.status === 'stale') && <div role="alert" className="rounded-lg border border-warning/40 bg-warning/5 p-3 text-sm text-warning">
      {error ? `刷新失败：${error.message}${data ? '；当前保留上次成功图表。' : ''}` : data?.reason}
    </div>}

    {data && <>
      {selectedPanel ? <GoldPanel key={selectedPanel.timeframe} panel={selectedPanel} layers={layers} onLayersChange={setLayers}
        movingAverages={movingAverages} onMovingAveragesChange={updateMovingAverages} timeSelection={timeSelection} />
        : <p role="status" className="p-8 text-sm text-muted">该周期暂无可用 K 线。</p>}
      <div className="flex flex-wrap items-center gap-x-5 gap-y-2 text-xs text-muted">
        <a href={data.source_url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 hover:text-accent">{data.source}<ExternalLink size={12} /></a>
        <span>抓取于 {refreshed}（北京）</span>
        <span>{data.price_basis}</span>
      </div>
      <p className="text-xs leading-relaxed text-muted">未收盘 K 线带有标记，仅供观察。各周期独立计算，软件结构尚不构成严格递归级别或买卖点确认。</p>
      <details className="rounded-lg border border-border p-3 text-xs text-muted">
        <summary className="cursor-pointer">数据范围与计算口径</summary>
        <div className="mt-3 space-y-2">{data.notes.map(note => <p key={note}>{note}</p>)}
          <p className="break-all">chan.py {data.version} · {data.profile_id}</p>
        </div>
      </details>
    </>}
  </div>
}
