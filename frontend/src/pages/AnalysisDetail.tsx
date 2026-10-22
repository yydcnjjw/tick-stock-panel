import { lazy, Suspense } from 'react'
import { Link, useParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { ExtDimensionAnalysis } from '@/components/ExtDimensionAnalysis'
import { api } from '@/lib/api'
import { QK } from '@/lib/queryKeys'

const SpotGoldPage = lazy(() => import('@/custom/spot-gold/SpotGoldPage'))
const loading = <p role="status" className="p-6 text-sm text-muted">正在加载扩展页面…</p>

export function AnalysisDetail() {
  const { menuId } = useParams()
  const query = useQuery({
    queryKey: QK.analysisMenu(menuId ?? ''), queryFn: () => api.analysisMenu(menuId!),
    enabled: !!menuId, retry: false,
  })
  if (query.isPending && menuId) return loading
  const menu = query.data
  if (!menu || query.isError) return <div role="alert" className="p-6 text-sm text-muted">
    找不到或无法加载此扩展页面。<Link to="/settings?tab=ext-pages" className="ml-2 text-accent underline">管理扩展页面</Link>
  </div>
  if (menu.template === 'spot_gold') {
    if (menu.data_source !== 'spot_gold') return <p role="alert" className="p-6 text-sm text-warning">伦敦金页面的数据集配置无效。</p>
    return <Suspense fallback={loading}><SpotGoldPage title={menu.label} /></Suspense>
  }
  return <ExtDimensionAnalysis key={menu.id} menuId={menu.id} menu={menu} />
}
