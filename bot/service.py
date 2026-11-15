"""Бизнес-логика: цены, оплата, уведомления, доставка. Общая для хендлеров и фоновых задач."""

import logging
import time
from decimal import ROUND_CEILING, Decimal
from html import escape

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest

from . import db as S
from .config import Config
from .db import Database, Order
from .fragment import FragmentDelivery
from .keyboards import admin_order_kb
from .platega import Platega

log = logging.getLogger(__name__)

STATUS_TEXT = {
    S.NEW: "🆕 создан",
    S.PENDING: "⏳ ждёт оплаты",
    S.PAID: "💰 оплачен, отправляем",
    S.MANUAL: "👨‍💻 отправляем вручную",
    S.PROCESSING: "🚀 отправляем",
    S.DELIVERED: "✅ выполнен",
    S.UNCERTAIN: "🔎 проверяется",
    S.FAILED: "⚠️ задержка, админ в курсе",
    S.CANCELED: "❌ отменён",
}

METHOD_TEXT = {"sbp": "СБП", "crypto": "Крипта"}


class Shop:
    def __init__(self, bot: Bot, cfg: Config, db: Database, platega: Platega,
                 fragment: FragmentDelivery):
        self.bot, self.cfg, self.db, self.platega, self.fragment = bot, cfg, db, platega, fragment

    # ---------- цены ----------
    async def star_price(self) -> Decimal:
        v = await self.db.get_setting("star_price_rub")
        return Decimal(v) if v else self.cfg.star_price_rub

    async def price_for(self, stars: int) -> Decimal:
        total = await self.star_price() * stars
        return total.quantize(Decimal("1"), rounding=ROUND_CEILING)  # округляем вверх до рубля

    # ---------- тексты ----------
    @staticmethod
    def order_text(o: Order) -> str:
        return (
            f"<b>Заказ #{o.id}</b>\n"
            f"Получатель: @{escape(o.recipient)}\n"
            f"Звёзд: <b>{o.stars} ⭐</b>\n"
            f"Сумма: <b>{o.amount_rub} ₽</b> ({METHOD_TEXT.get(o.method, o.method)})\n"
            f"Статус: {STATUS_TEXT.get(o.status, o.status)}"
        )

    def admin_text(self, o: Order) -> str:
        extra = ""
        if o.status == S.PAID and o.paid_at:
            left = max(0, o.paid_at + self.cfg.admin_timeout_sec - int(time.time()))
            extra = f"\n\n⏱ Автоотправка через ~{left // 60} мин {left % 60} с, если не ответишь."
        if o.delivered_by:
            extra += f"\nВыполнил: {escape(o.delivered_by)}"
        if o.note:
            extra += f"\nℹ️ {escape(o.note)}"
        return f"{self.order_text(o)}\nКлиент: <code>{o.user_id}</code>{extra}"

    # ---------- уведомления админов ----------
    async def notify_admins_paid(self, o: Order) -> None:
        for admin_id in self.cfg.admin_ids:
            try:
                m = await self.bot.send_message(admin_id, "🔔 Новый оплаченный заказ!\n\n"
                                                + self.admin_text(o),
                                                reply_markup=admin_order_kb(o))
                await self.db.add_admin_message(o.id, admin_id, m.message_id)
            except Exception:
                log.exception("Не удалось уведомить админа %s", admin_id)

    async def refresh_admin_messages(self, order_id: int) -> None:
        o = await self.db.get_order(order_id)
        for chat_id, msg_id in await self.db.admin_messages(order_id):
            try:
                await self.bot.edit_message_text(self.admin_text(o), chat_id=chat_id,
                                                 message_id=msg_id, reply_markup=admin_order_kb(o))
            except TelegramBadRequest:
                pass

    async def alert_admins(self, text: str) -> None:
        for admin_id in self.cfg.admin_ids:
            try:
                await self.bot.send_message(admin_id, text)
            except Exception:
                log.exception("alert failed")

    # ---------- оплата ----------
    async def on_paid(self, o: Order) -> None:
        if not await self.db.transition(o.id, (S.PENDING,), S.PAID, paid_at=int(time.time())):
            return  # уже обработан
        o = await self.db.get_order(o.id)
        await self.bot.send_message(
            o.user_id,
            f"✅ Оплата заказа #{o.id} получена!\n"
            f"Отправляем {o.stars} ⭐ на @{escape(o.recipient)}, обычно это занимает до 5 минут.",
        )
        if self.cfg.admin_ids:
            await self.notify_admins_paid(o)
        else:
            await self.auto_deliver(o.id, by="авто (нет админов)")

    async def on_canceled(self, o: Order) -> None:
        if await self.db.transition(o.id, (S.PENDING,), S.CANCELED):
            await self.bot.send_message(o.user_id, f"❌ Оплата заказа #{o.id} отменена или истекла.")

    # ---------- доставка ----------
    async def mark_delivered(self, order_id: int, by: str, from_statuses: tuple[str, ...]) -> bool:
        ok = await self.db.transition(order_id, from_statuses, S.DELIVERED,
                                      delivered_at=int(time.time()), delivered_by=by)
        if ok:
            o = await self.db.get_order(order_id)
            await self.bot.send_message(
                o.user_id,
                f"🎉 Заказ #{o.id} выполнен! {o.stars} ⭐ отправлены на @{escape(o.recipient)}.\n"
                "Спасибо, что выбрали Starify!",
            )
            await self.refresh_admin_messages(order_id)
        return ok

    async def auto_deliver(self, order_id: int, by: str,
                           from_statuses: tuple[str, ...] = (S.PAID,)) -> None:
        # Захватываем заказ — если админ успел нажать кнопку, сюда не попадём
        if not await self.db.transition(order_id, from_statuses, S.PROCESSING, delivered_by=by):
            return
        await self.refresh_admin_messages(order_id)
        o = await self.db.get_order(order_id)

        res = await self.fragment.send_stars(o.recipient, o.stars)
        if res.ok:
            await self.db.transition(order_id, (S.PROCESSING,), S.PROCESSING, note=res.info)
            await self.mark_delivered(order_id, by, (S.PROCESSING,))
            return

        status = S.UNCERTAIN if res.uncertain else S.FAILED
        await self.db.transition(order_id, (S.PROCESSING,), status, note=res.info)
        await self.refresh_admin_messages(order_id)
        hint = ("Транзакция могла пройти — проверь кошелёк/Fragment и НЕ отправляй повторно, "
                "пока не убедишься." if res.uncertain else "Отправь звёзды вручную.")
        await self.alert_admins(
            f"⚠️ Автоотправка заказа #{order_id} не удалась.\n{escape(res.info)}\n\n{hint}"
        )
