"""Автоматическая покупка звёзд через Fragment (библиотека fragment-api-py).

Главное правило: если транзакция ушла, но результат неизвестен — НЕ повторяем,
а помечаем заказ как UNCERTAIN и зовём админа. Иначе можно заплатить дважды.
"""

import asyncio
import logging
from dataclasses import dataclass

from .config import Config

log = logging.getLogger(__name__)


@dataclass
class DeliveryResult:
    ok: bool
    uncertain: bool = False   # деньги могли уйти — не повторять автоматически
    info: str = ""


class FragmentDelivery:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._lock = asyncio.Lock()   # один кошелёк — одна транзакция за раз
        self._import_error: str | None = None
        try:
            from FragmentAPI import FragmentClient  # noqa: F401
        except Exception as e:  # библиотека не установлена
            self._import_error = str(e)

    @property
    def available(self) -> tuple[bool, str]:
        c = self.cfg
        if not c.fragment_enabled:
            return False, "автодоставка выключена (FRAGMENT_ENABLED=0)"
        if self._import_error:
            return False, f"не установлена fragment-api-py: {self._import_error}"
        if not (c.fragment_seed and c.fragment_api_key and c.fragment_cookies):
            return False, "не заполнены FRAGMENT_SEED / FRAGMENT_API_KEY / FRAGMENT_COOKIES"
        return True, ""

    def _client(self):
        from FragmentAPI import FragmentClient
        c = self.cfg
        return FragmentClient(
            cookies=c.fragment_cookies,
            seed=c.fragment_seed,
            api_key=c.fragment_api_key,
            api_provider=c.fragment_api_provider,
            wallet_version=c.fragment_wallet_version,
        )

    async def balance_ton(self) -> str:
        async with self._client() as client:
            w = await client.get_wallet()
            return f"{w.balance_nanoton / 1e9:.4f} TON"

    async def send_stars(self, username: str, amount: int) -> DeliveryResult:
        ok, why = self.available
        if not ok:
            return DeliveryResult(ok=False, info=why)

        async with self._lock:
            try:
                async with self._client() as client:
                    res = await client.purchase("stars", username, amount, payment_method="ton")
            except Exception as e:
                name = type(e).__name__
                log.exception("Fragment purchase failed")
                # До отправки транзакции (ошибка юзернейма, конфигурации, кук) — безопасно.
                if name in {"UserNotFoundError", "ConfigurationError", "ConfigError",
                            "CookieError", "WalletError", "PaidMessageLimitError"}:
                    return DeliveryResult(ok=False, info=f"{name}: {e}")
                # Всё остальное (BroadcastUncertainError, ConfirmationTimeout, сеть...) — неизвестно.
                return DeliveryResult(ok=False, uncertain=True, info=f"{name}: {e}")

        if getattr(res, "confirmed", False):
            return DeliveryResult(ok=True, info=f"tx {getattr(res, 'transaction_id', '')}")
        return DeliveryResult(
            ok=False, uncertain=True,
            info=f"не подтверждено: {getattr(res, 'confirmation_error', '')} "
                 f"(operation_id={getattr(res, 'operation_id', '')})",
        )
