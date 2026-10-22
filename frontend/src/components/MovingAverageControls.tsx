import { MA_PERIODS, type MovingAverageOptions } from './chartMovingAverages'
import { storage } from '@/lib/storage'

const DEFAULT_MA: MovingAverageOptions = { type: 'SMA', periods: MA_PERIODS, showCrosses: true }

export function loadChanMovingAverages(): MovingAverageOptions {
  const saved = storage.chanMovingAverages.get(null)
  if (!saved || typeof saved !== 'object' || Array.isArray(saved)) return DEFAULT_MA
  const value = saved as Partial<MovingAverageOptions>
  return {
    type: value.type === 'EMA' ? 'EMA' : 'SMA',
    periods: Array.isArray(value.periods) ? MA_PERIODS.filter(n => value.periods!.includes(n)) : MA_PERIODS,
    showCrosses: typeof value.showCrosses === 'boolean' ? value.showCrosses : true,
  }
}

export function MovingAverageControls({ value, onChange }: {
  value: MovingAverageOptions; onChange: (next: MovingAverageOptions) => void
}) {
  return <div role="group" aria-label="均线设置" className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-border p-3">
    <span className="text-xs text-muted">均线</span>
    <div className="flex gap-1">
      {(['SMA', 'EMA'] as const).map(type => <button key={type} type="button" aria-pressed={value.type === type}
        title={type === 'SMA' ? '简单移动平均线' : '指数移动平均线'} onClick={() => onChange({ ...value, type })}
        className={`rounded-btn border px-2 py-1 text-xs ${value.type === type ? 'border-accent bg-accent/10 text-accent' : 'border-border text-secondary hover:bg-elevated'}`}>
        {type}
      </button>)}
    </div>
    {MA_PERIODS.map(period => <label key={period} className="flex cursor-pointer items-center gap-1.5 text-xs text-secondary">
      <input type="checkbox" aria-label={`${value.type}${period}`} checked={value.periods.includes(period)} className="accent-accent"
        onChange={() => onChange({ ...value, periods: MA_PERIODS.filter(n => n === period ? !value.periods.includes(n) : value.periods.includes(n)) })} />
      {value.type}{period}
    </label>)}
    <label className="flex cursor-pointer items-center gap-1.5 text-xs text-secondary">
      <input type="checkbox" aria-label="5↔10 交叉" checked={value.showCrosses} className="accent-accent"
        onChange={() => onChange({ ...value, showCrosses: !value.showCrosses })} />5↔10 交叉
    </label>
    <span className="text-[11px] text-muted">满 N 根显示 · 5、10 均显示时标注交叉</span>
  </div>
}
