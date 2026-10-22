import { useMemo, useState } from 'react'
import { rangeInputValue, resolveChartTimeRange, type ChartTimeRange, type ChartViewport } from './chartTimeRange'

const NO_ROWS: { date: string }[] = []

/** Keep this hook in the page so changing a chart's symbol/period key preserves the selection. */
export function useChartTimeRange(rows: readonly { date: string }[] = NO_ROWS, intraday = false) {
  const [draft, setDraft] = useState<ChartTimeRange>({ start: '', end: '' })
  const [request, setRequest] = useState<{ range: ChartTimeRange | null; revision: number }>({ range: null, revision: 0 })
  const start = rangeInputValue(draft.start, intraday)
  const end = rangeInputValue(draft.end, intraday, true)
  const error = start && end && start > end ? '开始时间不能晚于结束时间。' : ''
  const resolved = useMemo(() => request.range ? resolveChartTimeRange(rows, request.range, intraday) : null,
    [rows, request.range, intraday])
  const startIndex = resolved?.startIndex
  const endIndex = resolved?.endIndex
  const viewport = useMemo<ChartViewport>(() => startIndex !== undefined && endIndex !== undefined
    ? { startValue: startIndex, endValue: endIndex } : { reset: true }, [startIndex, endIndex, request.revision])
  return {
    draft, setDraft, start, end, error, intraday, rows, resolved, viewport,
    applied: request.range,
    empty: request.range !== null && resolved === null,
    apply: () => {
      if (start && end && !error && rows.length) setRequest(previous => ({ range: { start, end }, revision: previous.revision + 1 }))
    },
    reset: () => {
      setDraft({ start: '', end: '' })
      setRequest(previous => ({ range: null, revision: previous.revision + 1 }))
    },
  }
}

const display = (date: string) => date.replace('T', ' ')

export function ChartTimeRangeControls({ selection }: { selection: ReturnType<typeof useChartTimeRange> }) {
  const { draft, setDraft, start, end, error, intraday, rows, resolved, applied, empty, apply, reset } = selection
  return <div className="min-w-0 space-y-2 border-b border-border p-3">
    <p className="text-xs text-secondary">时间区间（仅定位） · 北京时间</p>
    <form noValidate onSubmit={event => { event.preventDefault(); apply() }} className="flex flex-wrap items-end gap-2">
      {(['start', 'end'] as const).map(key => <label key={key} className="flex min-w-0 flex-col gap-1 text-xs text-muted">
        {key === 'start' ? '开始' : '结束'}
        <input aria-label={key === 'start' ? '区间开始' : '区间结束'} type={intraday ? 'datetime-local' : 'date'}
          step={intraday ? 60 : undefined} value={key === 'start' ? start : end} disabled={!rows.length}
          onChange={event => setDraft({ ...draft, [key]: event.target.value })}
          className="min-w-0 max-w-full rounded-btn border border-border bg-surface px-2 py-1.5 text-foreground [color-scheme:light] dark:[color-scheme:dark] disabled:opacity-50" />
      </label>)}
      <button type="submit" disabled={!start || !end || !!error || !rows.length}
        className="rounded-btn border border-accent/40 px-3 py-1.5 text-xs text-accent disabled:opacity-50">应用</button>
      <button type="button" onClick={reset} className="rounded-btn border border-border px-3 py-1.5 text-xs text-secondary">重置</button>
    </form>
    {error && <p role="alert" className="text-xs text-warning">{error}</p>}
    <p className="break-words text-[11px] text-muted">{rows.length
      ? `可选历史：${display(rows[0].date)} 至 ${display(rows[rows.length - 1].date)}（${rows.length} 根）`
      : '暂无已加载的 K 线。'}</p>
    {applied && <p className="break-words text-[11px] text-muted">已应用：{display(applied.start)} 至 {display(applied.end)}；拖动或缩放后可再次应用定位。</p>}
    {resolved && <p role="status" className={`text-xs ${resolved.clipped ? 'text-warning' : 'text-muted'}`}>
      {resolved.clipped ? '所选区间部分超出已加载历史，已显示交集。' : '已定位。'}实际定位范围：{display(resolved.actualStart)} 至 {display(resolved.actualEnd)}
    </p>}
    {empty && <p role="status" className="text-xs text-warning">该区间没有已加载的 K 线，请调整区间或点击“重置”恢复默认视图。</p>}
  </div>
}
