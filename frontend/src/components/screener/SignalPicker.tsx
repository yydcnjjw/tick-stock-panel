import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { PenLine } from 'lucide-react'
import { api, type StrategyDetail } from '@/lib/api'
import { QK } from '@/lib/queryKeys'
import { CZSC_MONITOR_UNSUPPORTED, CZSC_SIGNAL_DEFINITIONS, SIGNAL_OPTIONS, cnSignal, czscStrategyUnsupportedReason, isCzscSignal, normalizeCzscSignalId } from '@/lib/signals'
import { useCustomSignalOptions } from '@/lib/useSharedQueries'

/** 展示与过滤可选项 (全部可选, 有默认值) */
interface SignalPickerOptions {
  /** 渲染尺寸: dialog = 选股弹窗紧凑样式; panel = 回测页设置抽屉样式 */
  variant?: 'dialog' | 'panel'
  builtinSignals?: { key: string; label: string }[]
  disabledSignals?: string[]
  disabledSignalHint?: string
  assetType?: 'stock' | 'etf' | 'index'
  context?: 'strategy' | 'backtest' | 'monitor'
  executionBackend?: StrategyDetail['execution_backend']
  /**
   * 是否按 kind 过滤自定义信号 (csg_*)。默认 true (选股/回测: 入场区只显示 entry)。
   * 监控规则页设 false: 报警语义是"命中即报", 不分入场出场, 自定义信号全部显示
   * (与内置信号一致: builtinSignals 透传时本就不按 kind 过滤)。
   */
  filterCustomByKind?: boolean
}

interface Props {
  /** 当前选中的信号 ID 列表 */
  signals: string[]
  /** 选中变化回调 */
  onChange: (next: string[]) => void
  /** 买点 / 卖点 — 决定自定义信号的过滤与配色主题 */
  kind: 'entry' | 'exit'
  options?: SignalPickerOptions
}

/**
 * 买卖触发器信号选择 — 选股页弹窗 / 回测页共用。
 *
 * - 内置信号 (signal_*): 全部展示
 * - 自定义信号 (csg_*): 按 kind 过滤 (entry / exit / both), 除非 filterCustomByKind=false
 * - entry 蓝色主题, exit 橙色主题; 自定义信号边框配色与内置一致, 右上角 PenLine 角标区分
 */
export function SignalPicker({ signals, onChange, kind, options }: Props) {
  const {
    variant = 'panel', builtinSignals, disabledSignals = [], disabledSignalHint,
    filterCustomByKind = true, assetType = 'stock', context = 'strategy', executionBackend,
  } = options ?? {}
  const customSignalsQuery = useQuery({ queryKey: QK.customSignals, queryFn: api.customSignalsList })
  const signalOptions = useCustomSignalOptions()
  const czscDisabledReason = [
    context === 'monitor' ? CZSC_MONITOR_UNSUPPORTED : null,
    assetType !== 'stock' ? 'CZSC 仅支持 A 股股票，不支持 ETF 或指数' : null,
    czscStrategyUnsupportedReason(executionBackend),
    signalOptions.czscUnavailableReason,
  ].filter(Boolean).join('；')

  const customOptions = useMemo(() => {
    const list = (customSignalsQuery.data?.signals ?? [])
      .filter(s => s.enabled && (!filterCustomByKind || s.kind === kind || s.kind === 'both'))
    const names: Record<string, string> = {}
    for (const cs of list) names[`csg_${cs.id}`] = cs.name
    return { list, names }
  }, [customSignalsQuery.data, kind, filterCustomByKind])

  const selectedIds = new Set(signals.map(normalizeCzscSignalId))
  const disabledIds = new Set(disabledSignals.map(normalizeCzscSignalId))
  const toggle = (sig: string) => {
    const next = selectedIds.has(sig) ? signals.filter(x => normalizeCzscSignalId(x) !== sig) : [...signals, sig]
    onChange(next)
  }

  // 配色: entry 蓝色 (accent), exit 橙色 (warning/amber)
  const isEntry = kind === 'entry'
  const active = isEntry
    ? 'border-accent/50 bg-accent/10 text-accent'
    : 'border-warning/50 bg-warning/10 text-warning'
  const idle = variant === 'dialog'
    ? 'border-border bg-base text-muted hover:border-accent/40'
    : 'border-border bg-base text-muted hover:border-accent/40'

  const btnCls = variant === 'dialog'
    ? 'rounded px-1.5 py-0.5 text-[10px] font-medium border transition-colors cursor-pointer'
    : 'rounded-btn border px-2.5 py-1.5 text-[11px] transition-colors cursor-pointer'
  const builtinOptions = (builtinSignals ?? SIGNAL_OPTIONS.map(key => ({ key, label: cnSignal(key) })))
    .map(option => ({ ...option, key: normalizeCzscSignalId(option.key) }))
  // 场景过滤后仍保留已选 CZSC，便于修复旧配置。
  const visibleBuiltinOptions = [...builtinOptions, ...CZSC_SIGNAL_DEFINITIONS
    .filter(sig => selectedIds.has(sig.id) && !builtinOptions.some(option => option.key === sig.id))
    .map(sig => ({ key: sig.id, label: sig.name }))]
  const showsCzsc = visibleBuiltinOptions.some(option => isCzscSignal(option.key))

  return (
    <div className="flex flex-wrap gap-1.5">
      {visibleBuiltinOptions.map(option => {
        const selected = selectedIds.has(option.key)
        const hint = isCzscSignal(option.key) && czscDisabledReason
          ? czscDisabledReason
          : disabledIds.has(option.key) ? disabledSignalHint : undefined
        const disabled = !selected && (disabledIds.has(option.key) || (isCzscSignal(option.key) && !!czscDisabledReason))
        return (
          <button
            key={option.key}
            type="button"
            disabled={disabled}
            aria-pressed={selected}
            title={hint ? `${hint}${selected ? '；点击移除' : ''}` : undefined}
            onClick={() => toggle(option.key)}
            className={`${btnCls} ${selected ? active : idle} disabled:cursor-not-allowed disabled:opacity-40`}
          >
            {option.label}
          </button>
        )
      })}
      {customOptions.list.map(cs => {
        const id = `csg_${cs.id}`
        return (
          <button
            key={id}
            type="button"
            onClick={() => toggle(id)}
            title="自定义信号"
            className={`${btnCls} relative ${signals.includes(id) ? active : idle}`}
          >
            {customOptions.names[id]}
            <span className="pointer-events-none absolute -top-1 -right-1 rounded-full border border-border bg-base p-px">
              <PenLine className="h-2 w-2 text-muted" />
            </span>
          </button>
        )
      })}
      {showsCzsc && (
        <div className={`w-full text-[10px] leading-4 ${czscDisabledReason ? 'text-warning' : 'text-muted'}`} role="status">
          {czscDisabledReason
            ? `${czscDisabledReason}。已选 CZSC 项仍可点击移除。`
            : 'CZSC 辅助信号仅在已收盘日线由不成立变为成立时触发；首次可计算只建基线，引用侧最早次交易日开盘成交，暂不支持盘中监控。'}
          {signalOptions.isError && (
            <button type="button" onClick={() => signalOptions.refetch()} className="ml-1 underline">重新检查</button>
          )}
        </div>
      )}
    </div>
  )
}
