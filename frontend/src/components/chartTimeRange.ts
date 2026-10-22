export interface ChartTimeRange { start: string; end: string }

/** A new object requests a viewport change; ordinary chart interaction remains uncontrolled. */
export type ChartViewport = { startValue: number; endValue: number } | { reset: true }

export function rangeInputValue(value: string, intraday: boolean, end = false): string {
  if (!value) return ''
  const normalized = value.replace(' ', 'T')
  return intraday ? normalized.length === 10 ? `${normalized}T${end ? '23:59' : '00:00'}` : normalized.slice(0, 16)
    : normalized.slice(0, 10)
}

export function resolveChartTimeRange(rows: readonly { date: string }[], range: ChartTimeRange, intraday: boolean) {
  const start = rangeInputValue(range.start, intraday)
  const end = rangeInputValue(range.end, intraday, true)
  if (!start || !end || start > end || !rows.length) return null
  let startIndex = -1
  let endIndex = -1
  for (let i = 0; i < rows.length; i++) {
    const label = rangeInputValue(rows[i].date, intraday)
    if (label >= start && label <= end) {
      if (startIndex < 0) startIndex = i
      endIndex = i
    }
  }
  if (startIndex < 0) return null
  const first = rangeInputValue(rows[0].date, intraday)
  const last = rangeInputValue(rows[rows.length - 1].date, intraday)
  const clipped = (range.start.length === 10 ? range.start < first.slice(0, 10) : start < first)
    || (range.end.length === 10 ? range.end > last.slice(0, 10) : end > last)
  return { startIndex, endIndex, clipped, actualStart: rows[startIndex].date, actualEnd: rows[endIndex].date }
}
