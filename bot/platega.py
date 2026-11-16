"""Клиент Platega API (https://docs.platega.io)."""

from dataclasses import dataclass
from decimal import Decimal

import aiohttp

BASE_URL = "https://app.platega.io"


class PlategaError(Exception):
    pass


REFUND_REASONS = {
    "INSUFFICIENT_FUNDS": "на балансе Platega не хватает USDT для возврата",
    "PARTIAL_REFUND_EXISTS": "по платежу уже был частичный возврат",
    "REFUND_IN_PROGRESS": "возврат уже выполняется",
}


class PlategaRefundError(PlategaError):
    def __init__(self, data):
        code = (data or {}).get("code") or (data or {}).get("blockReason") or ""
        msg = (data or {}).get("message") or ""
        self.code = code
        super().__init__(REFUND_REASONS.get(code.split(":")[-1], f"{code} {msg}".strip()))


@dataclass
class Transaction:
    id: str
    status: str          # PENDING / CONFIRMED / CANCELED / CHARGEBACKED
    pay_url: str | None


class Platega:
    def __init__(self, merchant_id: str, secret: str):
        self.merchant_id = merchant_id
        self.secret = secret
        self._session: aiohttp.ClientSession | None = None

    @property
    def configured(self) -> bool:
        return bool(self.merchant_id and self.secret)

    async def _http(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                base_url=BASE_URL,
                headers={"X-MerchantId": self.merchant_id, "X-Secret": self.secret},
                timeout=aiohttp.ClientTimeout(total=30),
            )
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    async def create(self, *, method: int, amount: Decimal, description: str,
                     order_id: int, user_id: int, username: str | None,
                     return_url: str) -> Transaction:
        body = {
            "paymentMethod": method,
            "paymentDetails": {"amount": float(amount), "currency": "RUB"},
            "description": description,
            "return": return_url,
            "failedUrl": return_url,
            "payload": f"order-{order_id}",
            "orderId": str(order_id),
            "metadata": {
                "user_email": f"{user_id}@telegram.org",
                "userId": str(user_id),
                "userName": f"@{username}" if username else "",
            },
        }
        http = await self._http()
        async with http.post("/transaction/process", json=body) as r:
            data = await r.json(content_type=None)
            if r.status != 200:
                raise PlategaError(f"{r.status}: {data}")
        return Transaction(
            id=data["transactionId"],
            status=data.get("status", "PENDING"),
            pay_url=data.get("redirect") or data.get("url"),
        )

    async def refund_check(self, tx_id: str) -> dict:
        """Можно ли сделать полный возврат. Ничего не списывает.
        Поля: supported, totalDeductUsdt, blockReason."""
        http = await self._http()
        async with http.get(f"/transaction/{tx_id}/cancel-supported") as r:
            data = await r.json(content_type=None)
            if r.status != 200:
                raise PlategaRefundError(data)
        return data

    async def refund(self, tx_id: str) -> dict:
        """Полный возврат. Результат асинхронный: итог — статус платежа CHARGEBACKED.
        Поля: accepted, manualControlRequired, message, refundOperationId."""
        http = await self._http()
        async with http.post(f"/transaction/{tx_id}/cancel") as r:
            data = await r.json(content_type=None)
            if r.status != 200:
                raise PlategaRefundError(data)
        return data

    async def status(self, tx_id: str) -> str:
        http = await self._http()
        async with http.get(f"/transaction/{tx_id}") as r:
            data = await r.json(content_type=None)
            if r.status != 200:
                raise PlategaError(f"{r.status}: {data}")
        return data["status"]
