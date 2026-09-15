import { useMemo, useState } from 'react'
import { useSearchParams, Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { RefreshCw } from 'lucide-react'
import { api } from '@/lib/api'
import { QK } from '@/lib/queryKeys'
import { cnSignal, CZSC_COVERAGE_REASON_LABELS } from '@/lib/signals'
import { StockFinancialSearch } from '@/components/financials/StockFinancialSearch'
import { EChartsCandlestick } from '@/components/EChartsCandlestick'
import type { ChartStructures } from '@/components/chartStructures'

const LAYERS = { fractals: '分型', strokes: '已完成笔', centers: '笔中枢', unfinished: '未完成结构', signals: '辅助信号' }
const INDICATORS = ['vol', 'macd']
const VOLUME_COMPARE = { enabled: false, days: 1 }

export default function CzscPage() {
  const [params, setParams] = useSearchParams()
  const symbol = params.get('symbol') ?? ''
  const [layers, setLayers] = useState<Record<keyof typeof LAYERS, boolean>>({ fractals: true, strokes: true, centers: true, unfinished: true, signals: true })
  const [selection, setSelection] = useState<{ symbol: string; date: string } | null>(null)
  const query = useQuery({ queryKey: QK.czscDaily(symbol), queryFn: () => api.czscDaily(symbol),
    enabled: !!symbol, staleTime: 0, refetchOnMount: 'always', refetchOnWindowFocus: false, retry: false,
  })
  const data = query.data
  const structures = useMemo<ChartStructures>(() => ({
    segments: [...(layers.strokes ? data?.strokes ?? [] : []),
      ...(layers.unfinished ? (data?.unfinished ?? []).map(s => ({ ...s, dashed: true })) : [])],
    boxes: layers.centers ? data?.centers : [],
    points: [...(layers.fractals ? (data?.fractals ?? []).map(f => ({ ...f, label: f.kind === 'top' ? '顶分型' : '底分型' })) : []),
      ...(layers.signals ? (data?.signals ?? []).map(s => ({ ...s,
        label: cnSignal(s.signal_id).replace('CZSC', '').replace('均线', '').replace('辅助', '辅'),
        description: cnSignal(s.signal_id),
        signal: true, above: s.signal_id.endsWith('_sell') })) : [])],
  }), [data, layers])
  const selectedDate = selection?.symbol === symbol ? selection.date : null
  const visibleEvents = (data?.signals ?? []).filter(e => !selectedDate || e.date === selectedDate).slice().reverse()

  return (
    <div className="mx-auto max-w-[1600px] space-y-4 p-3 pt-14 md:p-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div><h1 className="text-lg font-semibold text-foreground">CZSC 日线结构图</h1>
          <p className="mt-1 text-xs text-muted">已收盘日线 · 结构快照与首次触发日</p></div>
        <Link to="/watchlist" className="text-xs text-accent hover:underline">返回自选</Link>
      </div>
      <StockFinancialSearch onSelect={next => { setParams({ symbol: next }); setSelection(null) }} assetTypes="stock" />
      {!symbol && <div role="status" className="rounded-card border border-border p-8 text-center text-sm text-muted">搜索股票，查看日线结构与辅助信号。</div>}
      {symbol && <>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div><h2 className="text-base font-medium text-foreground">{data?.name ?? symbol} <span className="font-mono text-xs text-muted">{symbol}</span></h2>
            {data?.cutoff && <p className="mt-1 text-xs text-muted">行情截止 {data.cutoff} · 成交量：手 · 成交额：元</p>}</div>
          <button type="button" onClick={() => query.refetch()} disabled={query.isFetching}
            className="inline-flex items-center gap-1.5 rounded-btn border border-border px-3 py-1.5 text-xs disabled:opacity-50">
            <RefreshCw size={13} className={query.isFetching ? 'animate-spin' : ''} />{query.isFetching ? '刷新中' : '刷新'}</button>
        </div>
        {query.isPending && <div role="status" className="p-10 text-center text-muted">正在读取收盘日线并计算结构…</div>}
        {query.isError && <div role="alert" className="rounded-card border border-warning/30 bg-warning/10 p-3 text-sm text-warning">
          {data ? '刷新失败，当前保留上次结果。' : ''}{query.error.message}</div>}
        {data && data.status !== 'ready' && <div role="status" className="rounded-card border border-border p-6 text-sm text-muted">
          {data.reason}{data.status === 'empty' && <Link to="/data" className="ml-2 text-accent underline">前往数据管理</Link>}
          {data.status === 'unavailable' && <p className="mt-2">请在后端启用 CZSC 可选组件后刷新。</p>}
        </div>}
        {data?.status === 'ready' && <>
          <div className="rounded-card border border-border bg-surface">
            <div className="flex flex-wrap gap-x-4 gap-y-2 border-b border-border p-3">
              {(Object.entries(LAYERS) as [keyof typeof LAYERS, string][]).map(([key, label]) => (
                <label key={key} className="flex cursor-pointer items-center gap-1.5 text-xs text-secondary">
                  <input type="checkbox" checked={layers[key]} onChange={() => setLayers(prev => ({ ...prev, [key]: !prev[key] }))} />{label}
                </label>
              ))}
            </div>
            <div className="p-1 sm:p-3">
              {!!data.rows?.length && <EChartsCandlestick key={symbol} data={data.rows} structures={structures}
                stockInfo={{ name: data.name }} height={650} visibleBars={250} showMA={false}
                activeIndicators={INDICATORS} volumeCompare={VOLUME_COMPARE}
                onDateClick={day => setSelection({ symbol, date: day })} />}
              {!data.rows?.length && <p role="status" className="p-8 text-center text-muted">日线数据无效，暂无可绘制的 K 线。</p>}
            </div>
            <div className="flex flex-wrap gap-x-4 gap-y-1 border-t border-border px-3 py-2 text-[11px] text-muted">
              <span className="text-blue-500">实线：已完成笔</span><span className="text-amber-500">虚线：未完成结构，可能变化</span>
              <span className="text-violet-500">紫色区域：CZSC 笔中枢</span><span>拖动 / 滚轮缩放 · 点击日期查看信号</span>
            </div>
          </div>
          <p className="text-xs leading-6 text-muted">
            {data.price_basis} · 本地已发布日线 {data.input_start} 至 {data.input_end}，共 {data.input_count} 根（目标 {data.analysis_bars} 根）。
            {data.structure_start ? `当前保留结构始于 ${data.structure_start}。` : '当前样本尚未形成可展示的笔结构。'}
            结构是截至行情截止日的快照，端点日期不等于识别日期；辅助信号不代表严格缠论买卖点或实际成交。
            本地数据未提供逐根定版标识，尾日以盘后同步校正为准。
            {data.refreshed_at && `刷新于 ${new Date(data.refreshed_at).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false })}（北京时间）。`}
          </p>
          {!!data.invalid_dates?.length && <p role="status" className="text-xs text-warning">{data.invalid_dates.length} 根无效日线已中断结构；未跨缺口连接。日期：{data.invalid_dates.join('、')}</p>}
          <details className="rounded-card border border-border p-3 text-xs text-muted">
            <summary className="cursor-pointer">信号可计算性与口径 · CZSC {data.version}</summary>
            <p className="mt-2">首次可计算只建立基线。缩放不会重算信号；不同历史起点、数据修订或预热范围可能产生不同结果。</p>
            {data.coverage?.signals.map(s => <p className="mt-1" key={s.signal_id}>{cnSignal(s.signal_id)}：可计算 {s.ready_rows}，不可计算 {s.unavailable_rows}
              {Object.entries(s.reasons).map(([r, n]) => `；${CZSC_COVERAGE_REASON_LABELS[r] ?? r} ${n}`).join('')}</p>)}
          </details>
          <section className="rounded-card border border-border p-3">
            <div className="mb-2 flex items-center justify-between gap-2"><h3 className="text-sm text-foreground">辅助信号记录{selectedDate ? ` · ${selectedDate}` : ''}</h3>
              {selectedDate && <button type="button" onClick={() => setSelection(null)} className="text-xs text-accent">查看全部</button>}</div>
            {!visibleEvents.length && <p className="text-xs text-muted">{selectedDate ? '该日没有首次触发记录；可计算性见上方说明。' : '当前输入范围没有首次触发记录；可计算性见上方说明。'}</p>}
            <div className="max-h-64 space-y-1 overflow-auto">{visibleEvents.map(e => (
              <div key={`${e.date}-${e.signal_id}`} className="flex flex-wrap justify-between gap-x-4 gap-y-1 rounded bg-elevated px-2 py-1.5 text-xs">
                <span className="font-mono text-muted">{e.date}</span><span className="text-secondary">{cnSignal(e.signal_id)}</span><span className="font-mono text-foreground">识别日收盘 {e.price.toFixed(2)}</span>
              </div>
            ))}</div>
          </section>
        </>}
      </>}
    </div>
  )
}
