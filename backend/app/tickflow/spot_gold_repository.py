"""Read boundary for the separate spot_gold dataset; never writes A-share K lines."""
from app.data_providers import custom as custom_sources
from app.data_providers.spot_gold import GoldHistory


class SpotGoldRepository:
    def __init__(self, provider: str):
        self.provider = provider

    def get_snapshot(self) -> GoldHistory:
        name = self.provider
        if not custom_sources.provider_has_dataset(name, 'spot_gold'):
            raise ValueError('伦敦金数据源不可用，请在数据源设置中检查插件状态')
        provider = custom_sources.get_provider(name)
        get_history = getattr(provider, 'get_spot_gold', None)
        if not callable(get_history):
            raise ValueError('伦敦金数据源未实现历史 K 线接口')
        history = get_history()
        if history.provider != name:
            raise ValueError('伦敦金数据源身份不一致')
        return history
