import { describe, expect, it } from 'vitest'
import type { BacktestProgress } from './api'
import { describeBacktestProgress } from './backtestTask'

describe('backtest progress display', () => {
  it('shows stock preparation separately before switching to legacy simulation progress', () => {
    const messages: BacktestProgress[] = [
      { phase: 'czsc_signals', completed: 0, total: 200 },
      { phase: 'czsc_signals', completed: 50, total: 200 },
      { phase: 'czsc_signals', completed: 200, total: 200 },
      { day: 1, total: 41, date: '2026-04-16', equity: 100000 },
    ]
    expect(messages.map(describeBacktestProgress)).toEqual([
      { label: '准备 CZSC 信号 · 0/200 只股票', percent: 0 },
      { label: '准备 CZSC 信号 · 50/200 只股票', percent: 25 },
      { label: '准备 CZSC 信号 · 200/200 只股票', percent: 100 },
      { label: '回测中 · 第 1/41 天 (2026-04-16)', percent: 100 / 41 },
    ])
  })

  it('accepts explicit simulation phase and clamps progress to its own phase', () => {
    expect(describeBacktestProgress({
      phase: 'simulation', day: 5, total: 5, date: '2026-04-22', equity: 101000,
    })?.percent).toBe(100)
    expect(describeBacktestProgress({ phase: 'czsc_signals', completed: 6, total: 5 })?.percent).toBe(100)
  })

  it('never renders a non-finite percentage for empty or malformed SSE messages', () => {
    expect(describeBacktestProgress(null)).toBeNull()
    for (const message of [
      {}, { phase: 'czsc_signals', completed: 0, total: 0 },
      { phase: 'czsc_signals', total: 100 },
      { day: 1, total: 0, date: '2026-04-16' },
      { phase: 'future_phase', completed: 1, total: 10 },
    ]) {
      expect(describeBacktestProgress(message as BacktestProgress)).toBeNull()
    }
  })
})
