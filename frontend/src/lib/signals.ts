/**
 * 信号/字段显示名 — 展示侧统一查表。
 *
 * 日线信号布尔列 (csg_* 与迁移自内置、沿用 signal_* 列名的定义) 全部由
 * /api/custom-signals 的用户定义驱动, 中文名经 cnSignal 第二参数
 * (useCustomSignalNames 的列名→命名映射) 传入; 此处不再内置信号清单。
 * 信号 ID 语义与后端 backtest/strategy.py:_build_signal_mask 对齐。
 */

export type SignalKind = 'entry' | 'exit' | 'both'

export interface BuiltinSignalDefinition {
  id: string
  name: string
  kind: SignalKind
  category: string
  description: string
}

export const CZSC_SIGNAL_EXPLANATION = 'CZSC 1.0.1 原生辅助判定，仅用于 A 股股票已收盘日线。各信号从不成立变为成立时触发一次，持续成立不重复触发；首次可计算时只建立基线。信号记录在识别日，不回填笔端点。辅助名称不代表严格缠论买卖点确认。'
export const CZSC_MONITOR_UNSUPPORTED = 'CZSC 日线辅助信号暂不支持盘中监控'

export const CZSC_SIGNAL_DEFINITIONS: BuiltinSignalDefinition[] = [
  { id: 'signal_czsc_first_buy', name: 'CZSC一买辅助', kind: 'entry', category: 'CZSC', description: 'V221126：CZSC 原生一买辅助判定，基于笔结构识别候选形态。' },
  { id: 'signal_czsc_first_sell', name: 'CZSC一卖辅助', kind: 'exit', category: 'CZSC', description: 'V221126：CZSC 原生一卖辅助判定，基于笔结构识别候选形态。' },
  { id: 'signal_czsc_second_buy', name: 'CZSC均线二买辅助', kind: 'entry', category: 'CZSC', description: 'V230320，固定 SMA21：结合笔结构与均线的原生二买辅助判定。' },
  { id: 'signal_czsc_second_sell', name: 'CZSC均线二卖辅助', kind: 'exit', category: 'CZSC', description: 'V230320，固定 SMA21：结合笔结构与均线的原生二卖辅助判定。' },
  { id: 'signal_czsc_third_buy', name: 'CZSC均线三买辅助', kind: 'entry', category: 'CZSC', description: 'V230318，固定 SMA34：结合笔结构与均线的原生三买辅助判定。' },
  { id: 'signal_czsc_third_sell', name: 'CZSC均线三卖辅助', kind: 'exit', category: 'CZSC', description: 'V230318，固定 SMA34：结合笔结构与均线的原生三卖辅助判定。' },
]

export const normalizeCzscSignalId = (id: string) => id.startsWith('czsc_') ? `signal_${id}` : id
export const isCzscSignal = (id: string) => normalizeCzscSignalId(id).startsWith('signal_czsc_')

export function czscStrategyUnsupportedReason(executionBackend?: string): string | null {
  if (!executionBackend || executionBackend === 'polars_expr') return null
  const label: Record<string, string> = {
    matrix_native: '矩阵策略', composite: '叠加策略', minute_filter: '分钟策略', python_history_legacy: '历史 Python 策略',
  }
  return `CZSC 首版仅支持普通日线策略，${label[executionBackend] ?? '当前策略类型'}不支持 CZSC 外部触发器`
}

export const CZSC_COVERAGE_REASON_LABELS: Record<string, string> = {
  insufficient_structure: '笔结构不足',
  missing_data: '数据缺失',
  unclosed_bar: '日线尚未收盘',
  baseline: '建立初始基线（不触发）',
}

export const MONITOR_INTRADAY_SIGNAL_LABELS: Record<string, string> = {
  signal_intraday_avg_cross_up: '分时价格上穿均价',
  signal_intraday_avg_cross_down: '分时价格下穿均价',
  signal_intraday_zero_cross_up: '分时价格上穿0轴',
  signal_intraday_zero_cross_down: '分时价格下穿0轴',
}

export const MONITOR_INTRADAY_SIGNAL_OPTIONS = Object.keys(MONITOR_INTRADAY_SIGNAL_LABELS)

/** 盘中信号 → 中文标签 (日线信号标签由用户定义提供, 不在此表) */
export const SIGNAL_LABELS: Record<string, string> = {
  ...MONITOR_INTRADAY_SIGNAL_LABELS,
  ...Object.fromEntries(CZSC_SIGNAL_DEFINITIONS.map(signal => [signal.id, signal.name])),
}

/** 常用技术指标/字段 → 中文 (阈值条件展示用, 与后端 ENRICHED_COLUMNS 对齐) */
const FIELD_LABELS: Record<string, string> = {
  close: '收盘价', open: '开盘价', high: '最高价', low: '最低价',
  change_pct: '涨跌幅', change_amount: '涨跌额', amplitude: '振幅',
  turnover_rate: '换手率', volume: '成交量', amount: '成交额',
  ma5: 'MA5', ma10: 'MA10', ma20: 'MA20', ma30: 'MA30', ma60: 'MA60',
  ema5: 'EMA5', ema10: 'EMA10', ema20: 'EMA20',
  macd_dif: 'MACD-DIF', macd_dea: 'MACD-DEA', macd_hist: 'MACD柱',
  boll_upper: '布林上轨', boll_lower: '布林下轨',
  kdj_k: 'KDJ-K', kdj_d: 'KDJ-D', kdj_j: 'KDJ-J',
  rsi_6: 'RSI6', rsi_14: 'RSI14', rsi_24: 'RSI24',
  vol_ratio_5d: '5日量比', vol_ratio_20d: '20日量比',
  vol_ma5: '5日均量', vol_ma10: '10日均量',
  high_60d: '60日最高', low_60d: '60日最低',
  momentum_5d: '5日动量', momentum_20d: '20日动量', momentum_60d: '60日动量',
  atr_14: 'ATR14', annual_vol_20d: '20日年化波动',
  consecutive_limit_ups: '连板数', consecutive_limit_downs: '跌停连板',
  limit_up_price: '涨停判定价', limit_down_price: '跌停判定价',
}

/**
 * 信号/字段 ID → 中文显示名。
 * 自定义信号列 (csg_/signal_*) 查传入的命名映射; 盘中信号查 SIGNAL_LABELS;
 * 技术指标查 FIELD_LABELS; 都找不到则原样返回。
 */
export function cnSignal(name: string, customNames?: Record<string, string>): string {
  if (customNames && name in customNames) return customNames[name]
  return SIGNAL_LABELS[normalizeCzscSignalId(name)] ?? FIELD_LABELS[name] ?? name
}
