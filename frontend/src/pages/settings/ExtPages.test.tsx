// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { SettingsExtPagesPanel } from './ExtPages'
import { AnalysisDetail } from '../AnalysisDetail'
import { api, type AnalysisMenu } from '@/lib/api'
import { QK } from '@/lib/queryKeys'

vi.mock('@/lib/api', () => ({ api: {
  analysisMenus: vi.fn(), extDataList: vi.fn(), analysisMenuSave: vi.fn(), analysisMenuDelete: vi.fn(), analysisMenu: vi.fn(),
} }))
vi.mock('@/custom/spot-gold/SpotGoldPage', () => ({ default: ({ title }: { title: string }) => <h1 data-gold>{title}</h1> }))
vi.mock('@/components/ExtDimensionAnalysis', () => ({ ExtDimensionAnalysis: ({ menu }: { menu: AnalysisMenu }) => <p data-table>{menu.label}</p> }))

const gold = (): AnalysisMenu => ({ id: 'gold', label: '黄金观察', icon: 'chart', template: 'spot_gold', data_source: 'spot_gold',
  nav_group: '商品行情', group_columns: [], detail_columns: [], order: 7, visible: false })
let host: HTMLDivElement, root: Root, client: QueryClient, menus: AnalysisMenu[]
beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true })
  vi.resetAllMocks()
  menus = []
  vi.mocked(api.analysisMenus).mockImplementation(async () => ({ items: menus }))
  vi.mocked(api.extDataList).mockResolvedValue({ items: [] })
  vi.mocked(api.analysisMenuSave).mockImplementation(async (id, body) => {
    const result = { id, ...body }
    menus = [...menus.filter(m => m.id !== id), result]
    return result
  })
  host = document.createElement('div'); document.body.append(host)
  root = createRoot(host)
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } })
})
afterEach(async () => { await act(async () => root.unmount()); client.clear(); host.remove() })
async function settle() {
  for (let i = 0; i < 6; i++) await act(async () => { await new Promise(r => setTimeout(r, 0)) })
}
async function render(detail = false) {
  await act(async () => root.render(<MemoryRouter initialEntries={['/analysis/gold']}><QueryClientProvider client={client}>
    {detail ? <Routes><Route path="/analysis/:menuId" element={<AnalysisDetail />} /></Routes> : <SettingsExtPagesPanel />}
  </QueryClientProvider></MemoryRouter>))
  await settle()
}
async function click(text: string) {
  const button = [...host.querySelectorAll('button')].find(b => b.textContent?.trim() === text)!
  await act(async () => button.click()); await settle()
}
async function input(placeholder: string, value: string) {
  const node = host.querySelector<HTMLInputElement>(`input[placeholder="${placeholder}"]`)!
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(node, value)
    node.dispatchEvent(new Event('input', { bubbles: true }))
  })
}

it('creates a four-period extension without requiring tabular data', async () => {
  await render(); await click('新建页面')
  await input('如 concept_hot', 'gold'); await input('如 概念热度', '伦敦金')
  const select = [...host.querySelectorAll('select')].find(s => s.querySelector('option[value="spot_gold"]'))!
  await act(async () => { select.value = 'spot_gold'; select.dispatchEvent(new Event('change', { bubbles: true })) })
  expect(host.textContent).not.toContain('列表列配置')
  await click('保存')
  expect(api.analysisMenuSave).toHaveBeenCalledWith('gold', expect.objectContaining({
    template: 'spot_gold', data_source: 'spot_gold', label: '伦敦金', nav_group: '商品行情', group_columns: [], detail_columns: [],
  }))
  expect(host.querySelector('a[href="/analysis/gold"]')).not.toBeNull()
  expect(host.textContent).toContain('伦敦金四周期图')
})

it('edits a gold extension while preserving visibility/order and updates its cached title', async () => {
  menus = [gold()]
  client.setQueryData(QK.analysisMenu('gold'), gold())
  await render()
  await act(async () => host.querySelector<HTMLButtonElement>('button[title="编辑"]')!.click())
  await input('如 概念热度', '黄金多周期')
  await click('保存')
  expect(api.analysisMenuSave).toHaveBeenCalledWith('gold', expect.objectContaining({ label: '黄金多周期', order: 7, visible: false, template: 'spot_gold', nav_group: '商品行情' }))
  expect(client.getQueryData<AnalysisMenu>(QK.analysisMenu('gold'))?.label).toBe('黄金多周期')
})

it('can change or clear an extension navigation group', async () => {
  menus = [gold()]
  await render()
  for (const group of ['贵金属', '']) {
    await act(async () => host.querySelector<HTMLButtonElement>('button[title="编辑"]')!.click())
    await input('留空为一级菜单，如 商品行情', group)
    await click('保存')
    expect(api.analysisMenuSave).toHaveBeenLastCalledWith('gold', expect.objectContaining({ nav_group: group || null }))
    expect(client.getQueryData<AnalysisMenu>(QK.analysisMenu('gold'))?.nav_group).toBe(group || null)
  }
})

it('dispatches chart templates and uses the saved page title', async () => {
  vi.mocked(api.analysisMenu).mockResolvedValue(gold())
  await render(true)
  expect(host.querySelector('[data-gold]')?.textContent).toBe('黄金观察')
  expect(host.querySelector('[data-table]')).toBeNull()
  expect(api.extDataList).not.toHaveBeenCalled()
})

it('keeps existing table pages on the table renderer', async () => {
  vi.mocked(api.analysisMenu).mockResolvedValue({ ...gold(), template: 'table', data_source: 'existing' })
  await render(true)
  expect(host.querySelector('[data-table]')?.textContent).toBe('黄金观察')
  expect(host.querySelector('[data-gold]')).toBeNull()
})

it('shows missing pages explicitly instead of falling back to unrelated data', async () => {
  vi.mocked(api.analysisMenu).mockRejectedValue(new Error('not found'))
  await render(true)
  expect(host.querySelector('[role="alert"]')?.textContent).toContain('找不到')
  expect(host.querySelector('[data-table], [data-gold]')).toBeNull()
})
