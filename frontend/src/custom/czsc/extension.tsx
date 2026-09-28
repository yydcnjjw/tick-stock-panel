import { lazy, Suspense } from 'react'
import { Link } from 'react-router-dom'
import type { FrontendExtension, FrontendSlotContextMap } from '@/extensions/types'

const Page = lazy(() => import('./CzscPage'))

function StockEntry(context: FrontendSlotContextMap[keyof FrontendSlotContextMap]) {
  if (!('symbol' in context)) return null
  const { symbol } = context
  return <div className="border-t border-border px-4 py-2">
    <Link to={`/czsc?symbol=${encodeURIComponent(symbol)}`} className="text-xs text-accent hover:underline">查看 chan.py 多周期结构图 →</Link>
  </div>
}

const extension: FrontendExtension = {
  id: 'tickflow.czsc',
  apiVersion: 1,
  routes: [{ id: 'czsc', path: '/czsc', component: () => (
    <Suspense fallback={<div role="status" className="p-6 text-muted">正在加载 chan.py 图表…</div>}><Page /></Suspense>
  ) }],
  slots: [{ name: 'stock-preview.footer', id: 'czsc-entry', component: StockEntry }],
}

export default extension
