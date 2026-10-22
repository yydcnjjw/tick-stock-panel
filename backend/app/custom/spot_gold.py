"""L2 spot-gold chart routes; no background polling or A-share data mutation."""
import logging
from functools import lru_cache

from fastapi import APIRouter, HTTPException

from app.extensions import BACKEND_EXTENSION_API_VERSION, BackendExtensionRegistrar
from app.services import preferences
from app.services.spot_gold import SpotGoldService
from app.tickflow.spot_gold_repository import SpotGoldRepository

EXTENSION_ID = 'tickflow.spot-gold'
EXTENSION_API_VERSION = BACKEND_EXTENSION_API_VERSION
logger = logging.getLogger(__name__)
router = APIRouter(prefix='/api/custom/spot-gold', tags=['spot-gold'])


@lru_cache(maxsize=8)
def get_service(provider: str) -> SpotGoldService:
    return SpotGoldService(SpotGoldRepository(provider))


def _chart(*, refresh=False):
    try:
        service = get_service(preferences.get_spot_gold_data_provider())
        return service.get_chart(refresh=refresh)
    except Exception as exc:
        logger.warning('spot-gold chart unavailable: %s', type(exc).__name__)
        raise HTTPException(503, '伦敦金图表暂不可用，请检查数据源或稍后重试。') from exc


@router.get('/chart')
def get_chart():
    return _chart()


@router.post('/refresh')
def refresh_chart():
    return _chart(refresh=True)


def setup(registrar: BackendExtensionRegistrar) -> None:
    registrar.include_router(router)
