import { Navigate } from 'react-router-dom'
import type { FrontendExtension } from '@/extensions/types'

const extension: FrontendExtension = {
  id: 'tickflow.spot-gold',
  apiVersion: 1,
  routes: [{ id: 'spot-gold', path: '/spot-gold', component: () => (
    <Navigate to="/analysis/spot_gold" replace />
  ) }],
}
export default extension
