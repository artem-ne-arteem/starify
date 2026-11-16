from decimal import Decimal, InvalidOperation
from html import escape

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, Message

from .. import db as S
from ..keyboards import admin_order_kb, refund_confirm_kb
from ..service import Shop

router = Router()


def is_admin(user_id: int, shop: Shop) -> bool:
    return user_id in shop.cfg.admin_ids


@router.callback_query(F.data.startswith("adm:"))
async def admin_action(c: CallbackQuery, shop: Shop):
    if not is_admin(c.from_user.id, shop):
        await c.answer("Нет доступа", show_alert=True)
        return
    _, action, oid = c.data.split(":")
    order_id = int(oid)
    who = f"@{c.from_user.username}" if c.from_user.username else str(c.from_user.id)

    if action == "take":
        ok = await shop.db.transition(order_id, (S.PAID,), S.MANUAL, delivered_by=who)
        await shop.refresh_admin_messages(order_id)
        await c.answer("Заказ твой, таймер остановлен. Нажми «Отправил», когда закончишь."
                       if ok else "Заказ уже обработан", show_alert=True)

    elif action == "done":
        ok = await shop.mark_delivered(order_id, who, (S.PAID, S.MANUAL, S.FAILED, S.UNCERTAIN))
        await c.answer("Отмечено как выполнено ✅" if ok else "Заказ уже обработан",
                       show_alert=not ok)

    elif action == "refund":  # шаг 1: проверка и подтверждение
        o = await shop.db.get_order(order_id)
        ok, text = await shop.refund_preview(o)
        if not ok:
            await c.answer(text[:200], show_alert=True)
            return
        await c.message.edit_text(
            f"{shop.admin_text(o)}\n\n⚠️ <b>Отменить заказ и вернуть деньги?</b>\n{text}",
            reply_markup=refund_confirm_kb(order_id))
        await c.answer()

    elif action == "refundok":  # шаг 2: выполнить
        await c.answer("Запрашиваю возврат…")
        result = await shop.refund(order_id, who)
        o = await shop.db.get_order(order_id)
        try:
            await c.message.edit_text(shop.admin_text(o), reply_markup=admin_order_kb(o))
        except Exception:
            pass
        await c.message.answer(f"Заказ #{order_id}: {result}")

    elif action == "refundno":
        o = await shop.db.get_order(order_id)
        await c.message.edit_text(shop.admin_text(o), reply_markup=admin_order_kb(o))
        await c.answer("Ок, заказ не отменён")

    elif action == "auto":
        o = await shop.db.get_order(order_id)
        avail, why = shop.fragment.available
        if not avail and o.method != "test":
            await c.answer(f"Автодоставка недоступна: {why}", show_alert=True)
            return
        await c.answer("Отправляю через Fragment…")
        await shop.auto_deliver(order_id, by=f"авто ({who})",
                                from_statuses=(S.PAID, S.MANUAL, S.FAILED))


@router.message(Command("id"))
async def my_id(m: Message, shop: Shop):
    from ..config import VERSION
    await m.answer(
        f"Твой ID: <code>{m.from_user.id}</code>\n"
        f"Админ: {'да ✅' if is_admin(m.from_user.id, shop) else 'нет ❌'}\n"
        f"Версия бота: {VERSION}"
    )


@router.message(Command("testorder"))
async def test_order(m: Message, command: CommandObject, shop: Shop):
    """Фейковый оплаченный заказ: проверка уведомлений. Звёзды НЕ отправляются."""
    if not is_admin(m.from_user.id, shop):
        return
    stars, recipient = 50, m.from_user.username or "test_user"
    for arg in (command.args or "").split():
        if arg.isdigit():
            stars = int(arg)
        else:
            recipient = arg.lstrip("@")
    total = await shop.price_for(stars)
    order_id = await shop.db.create_order(m.from_user.id, recipient, stars, str(total), "test")
    await shop.db.set_payment(order_id, f"test-{order_id}", "")
    await m.answer(f"🧪 Создан тестовый заказ #{order_id}, имитирую оплату…\n"
                   "Звёзды по нему отправляться не будут.")
    await shop.on_paid(await shop.db.get_order(order_id))


@router.message(Command("admin"))
async def admin_help(m: Message, shop: Shop):
    if not is_admin(m.from_user.id, shop):
        return
    await m.answer(
        "🛠 <b>Админка Starify</b>\n"
        "/testorder [кол-во] [@username] — тестовый заказ (звёзды не отправляются)\n"
        "/stats — статистика\n"
        "/price 1.6 — изменить цену звезды (₽)\n"
        "/orders — заказы, ждущие действий\n"
        "/balance — баланс TON-кошелька для автодоставки\n\n"
        "💬 Поддержка: ответь реплаем на сообщение клиента — ответ уйдёт ему."
    )


@router.message(Command("stats"))
async def stats(m: Message, shop: Shop):
    if not is_admin(m.from_user.id, shop):
        return
    s = await shop.db.stats()
    await m.answer(
        f"👥 Пользователей: {s['users']}\n"
        f"✅ Выполнено заказов: {s['n']}\n"
        f"⭐ Продано звёзд: {s['stars']}\n"
        f"💰 Выручка: {s['rub']:.0f} ₽\n"
        f"Текущая цена: {await shop.star_price()} ₽/⭐"
    )


@router.message(Command("price"))
async def set_price(m: Message, command: CommandObject, shop: Shop):
    if not is_admin(m.from_user.id, shop):
        return
    try:
        price = Decimal((command.args or "").replace(",", "."))
        assert price > 0
    except (InvalidOperation, AssertionError):
        await m.answer(f"Текущая цена: {await shop.star_price()} ₽. Пример: /price 1.6")
        return
    await shop.db.set_setting("star_price_rub", str(price))
    await m.answer(f"Цена обновлена: {price} ₽ за звезду ✅")


@router.message(Command("orders"))
async def open_orders(m: Message, shop: Shop):
    if not is_admin(m.from_user.id, shop):
        return
    orders = await shop.db.orders_by_status(S.PAID, S.MANUAL, S.PROCESSING, S.FAILED,
                                            S.UNCERTAIN, S.REFUNDING)
    if not orders:
        await m.answer("Нет заказов, ждущих действий 👌")
        return
    for o in orders:
        await m.answer(shop.admin_text(o), reply_markup=admin_order_kb(o))


@router.message(Command("balance"))
async def balance(m: Message, shop: Shop):
    if not is_admin(m.from_user.id, shop):
        return
    avail, why = shop.fragment.available
    if not avail:
        await m.answer(f"Автодоставка недоступна: {escape(why)}")
        return
    try:
        await m.answer(f"💎 Баланс кошелька: {await shop.fragment.balance_ton()}")
    except Exception as e:
        await m.answer(f"Ошибка: {escape(str(e))}")
