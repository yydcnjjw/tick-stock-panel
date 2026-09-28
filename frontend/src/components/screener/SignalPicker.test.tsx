// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { SignalPicker } from './SignalPicker'

vi.mock('@tanstack/react-query', () => ({ useQuery: () => ({ data: { signals: [] } }) }))
vi.mock('@/lib/useSharedQueries', () => ({ useCustomSignalOptions: () => ({ chanUnavailableReason: null }) }))
let host: HTMLDivElement, root: Root
beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true })
  host = document.createElement('div'); document.body.append(host); root = createRoot(host)
})
afterEach(async () => { await act(async () => root.unmount()); host.remove() })

it('offers twelve new independent signals while legacy choices are removable only', async () => {
  const change = vi.fn()
  await act(async () => root.render(<SignalPicker signals={['signal_czsc_first_buy']} onChange={change} kind="entry" />))
  const buttons = [...host.querySelectorAll('button')]
  const native = buttons.filter(b => b.textContent?.startsWith('chan.py'))
  expect(native).toHaveLength(12)
  expect(native.every(b => !b.disabled)).toBe(true)
  expect(native.some(b => b.textContent?.includes('盘整背驰'))).toBe(true)
  expect(native.some(b => b.textContent?.includes('类二'))).toBe(true)
  const legacy = buttons.find(b => b.textContent?.startsWith('CZSC'))!
  expect(legacy.title).toContain('已停用')
  await act(async () => legacy.click())
  expect(change).toHaveBeenCalledWith([])
  await act(async () => root.render(<SignalPicker signals={[]} onChange={change} kind="entry" />))
  expect([...host.querySelectorAll('button')].some(b => b.textContent?.startsWith('CZSC'))).toBe(false)
})

it.each(['monitor', 'etf'] as const)('disables new signals in unsupported %s context', async value => {
  await act(async () => root.render(<SignalPicker signals={[]} onChange={vi.fn()} kind="entry"
    options={value === 'monitor' ? { context: 'monitor' } : { assetType: 'etf' }} />))
  const buttons = [...host.querySelectorAll('button')].filter(b => b.textContent?.startsWith('chan.py'))
  expect(buttons).toHaveLength(12)
  expect(buttons.every(b => b.disabled)).toBe(true)
})
