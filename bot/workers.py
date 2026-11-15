"""Фоновые задачи: опрос статусов оплаты и автодоставка по таймауту админа."""

import asyncio
import logging
import time

from . import db as S
from .service import Shop

log = logging.getLogger(__name__)

PAYMENT_POLL_SEC = 10
PENDING_TTL_SEC = 60 * 60   # через час неоплаченный заказ считаем отменённым


async def payment_poller(shop: Shop) -> None:
    while True:
        try:
            for o in await shop.db.orders_by_status(S.PENDING):
                if not o.tx_id or o.method == "test":
                    continue
                try:
                    st = await shop.platega.status(o.tx_id)
                except Exception as e:
                    log.warning("status #%s: %s", o.id, e)
                    continue
                if st == "CONFIRMED":
                    await shop.on_paid(o)
                elif st in ("CANCELED", "CHARGEBACKED"):
                    await shop.on_canceled(o)
                elif time.time() - o.created_at > PENDING_TTL_SEC:
                    await shop.on_canceled(o)
        except Exception:
            log.exception("payment_poller")
        await asyncio.sleep(PAYMENT_POLL_SEC)


async def admin_timeout_watcher(shop: Shop) -> None:
    """Если админ не отреагировал за ADMIN_TIMEOUT_SEC — бот отправляет звёзды сам."""
    while True:
        try:
            now = time.time()
            for o in await shop.db.orders_by_status(S.PAID):
                if o.paid_at and now - o.paid_at >= shop.cfg.admin_timeout_sec:
                    if o.method == "test":
                        await shop.auto_deliver(o.id, by="авто (таймаут)")
                        continue
                    avail, why = shop.fragment.available
                    if not avail:
                        # Автодоставка не настроена — переводим в FAILED и напоминаем админам
                        if await shop.db.transition(o.id, (S.PAID,), S.FAILED,
                                                    note=f"таймаут, автодоставка недоступна: {why}"):
                            await shop.refresh_admin_messages(o.id)
                            await shop.alert_admins(
                                f"⏰ Заказ #{o.id} ждёт уже {shop.cfg.admin_timeout_sec // 60} мин, "
                                f"а автодоставка недоступна ({why}). Отправь звёзды вручную!")
                        continue
                    log.info("Admin timeout, auto delivering #%s", o.id)
                    await shop.auto_deliver(o.id, by="авто (таймаут)")
        except Exception:
            log.exception("admin_timeout_watcher")
        await asyncio.sleep(5)


async def recover_after_restart(shop: Shop) -> None:
    """Если бот упал посреди автоотправки, мы не знаем, ушли ли деньги — отдаём на проверку."""
    for o in await shop.db.orders_by_status(S.PROCESSING):
        await shop.db.transition(o.id, (S.PROCESSING,), S.UNCERTAIN,
                                 note="бот перезапустился во время отправки")
        await shop.alert_admins(
            f"🔎 Заказ #{o.id}: бот перезапустился во время автоотправки. "
            "Проверь Fragment/кошелёк и отметь вручную.")
