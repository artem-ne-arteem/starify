import logging
import re
from html import escape

from aiogram import F, Router
from aiogram.filters import CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from .. import db as S
from ..keyboards import (BTN_BUY, BTN_HELP, BTN_ORDERS, PACKS, main_menu, packs_kb,
                         pay_method_kb, payment_kb, recipient_kb)
from ..platega import PlategaError
from ..service import STATUS_TEXT, Shop

log = logging.getLogger(__name__)
router = Router()

USERNAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{3,31}$")


class Buy(StatesGroup):
    username = State()
    amount = State()


def parse_username(text: str) -> str | None:
    t = text.strip()
    t = re.sub(r"^(https?://)?(t\.me|telegram\.me)/", "", t)
    t = t.lstrip("@")
    return t if USERNAME_RE.match(t) else None


@router.message(CommandStart())
async def start(m: Message, state: FSMContext, shop: Shop):
    await state.clear()
    await shop.db.upsert_user(m.from_user.id, m.from_user.username)
    price = await shop.star_price()
    await m.answer(
        f"👋 Привет, {escape(m.from_user.first_name)}!\n\n"
        "<b>Starify</b> — быстрое пополнение Telegram Stars ⭐\n"
        f"Курс: <b>{price} ₽</b> за звезду. Оплата через СБП или криптовалюту.\n\n"
        "Нажми «Купить звёзды», чтобы начать.",
        reply_markup=main_menu(),
    )


@router.message(F.text == BTN_HELP)
async def help_(m: Message):
    await m.answer(
        "ℹ️ <b>Как это работает</b>\n"
        "1. Выбираешь, кому отправить звёзды и сколько.\n"
        "2. Оплачиваешь через СБП или криптой.\n"
        "3. Звёзды приходят на аккаунт получателя в течение ~5 минут.\n\n"
        "Звёзды отправляются по @username, поэтому у получателя должен быть юзернейм.\n\n"
        "Остались вопросы? Жми «💬 Поддержка» — ответим прямо здесь."
    )


@router.message(F.text == BTN_ORDERS)
async def my_orders(m: Message, shop: Shop):
    orders = await shop.db.user_orders(m.from_user.id)
    if not orders:
        await m.answer("У тебя пока нет заказов.")
        return
    lines = [f"#{o.id} · {o.stars} ⭐ → @{escape(o.recipient)} · {o.amount_rub} ₽ · "
             f"{STATUS_TEXT.get(o.status, o.status)}" for o in orders]
    await m.answer("📦 <b>Твои заказы</b>\n\n" + "\n".join(lines))


# ---------- шаг 1: получатель ----------
@router.message(F.text == BTN_BUY)
async def buy(m: Message, state: FSMContext):
    await state.clear()
    await m.answer("Кому отправить звёзды?", reply_markup=recipient_kb(bool(m.from_user.username)))


@router.callback_query(F.data == "back:recipient")
async def back_recipient(c: CallbackQuery, state: FSMContext):
    await state.clear()
    await c.message.edit_text("Кому отправить звёзды?",
                              reply_markup=recipient_kb(bool(c.from_user.username)))


@router.callback_query(F.data == "rcpt:self")
async def rcpt_self(c: CallbackQuery, state: FSMContext, shop: Shop):
    await state.update_data(recipient=c.from_user.username)
    await show_packs(c.message, state, shop, edit=True)
    await c.answer()


@router.callback_query(F.data == "rcpt:other")
async def rcpt_other(c: CallbackQuery, state: FSMContext):
    await state.set_state(Buy.username)
    await c.message.edit_text("Отправь @username получателя (например, @durov):")
    await c.answer()


@router.message(Buy.username)
async def got_username(m: Message, state: FSMContext, shop: Shop):
    u = parse_username(m.text or "")
    if not u:
        await m.answer("Не похоже на юзернейм. Пример: @durov. Попробуй ещё раз:")
        return
    await state.update_data(recipient=u)
    await state.set_state(None)
    await show_packs(m, state, shop, edit=False)


# ---------- шаг 2: количество ----------
async def show_packs(msg: Message, state: FSMContext, shop: Shop, edit: bool):
    data = await state.get_data()
    prices = {p: str(await shop.price_for(p)) for p in PACKS}
    text = f"Получатель: @{escape(data['recipient'])}\n\nСколько звёзд отправить?"
    if edit:
        await msg.edit_text(text, reply_markup=packs_kb(prices))
    else:
        await msg.answer(text, reply_markup=packs_kb(prices))


@router.callback_query(F.data == "back:packs")
async def back_packs(c: CallbackQuery, state: FSMContext, shop: Shop):
    if not (await state.get_data()).get("recipient"):
        await c.answer("Начни заново: «Купить звёзды»", show_alert=True)
        return
    await show_packs(c.message, state, shop, edit=True)
    await c.answer()


@router.callback_query(F.data.startswith("pack:"))
async def pack(c: CallbackQuery, state: FSMContext, shop: Shop):
    if not (await state.get_data()).get("recipient"):
        await c.answer("Начни заново: «Купить звёзды»", show_alert=True)
        return
    val = c.data.split(":")[1]
    if val == "custom":
        await state.set_state(Buy.amount)
        await c.message.edit_text(
            f"Введи количество звёзд (от {shop.cfg.min_stars} до {shop.cfg.max_stars}):")
    else:
        await state.update_data(stars=int(val))
        await show_confirm(c.message, state, shop, edit=True)
    await c.answer()


@router.message(Buy.amount)
async def got_amount(m: Message, state: FSMContext, shop: Shop):
    txt = (m.text or "").strip()
    if not txt.isdigit() or not shop.cfg.min_stars <= int(txt) <= shop.cfg.max_stars:
        await m.answer(f"Нужно целое число от {shop.cfg.min_stars} до {shop.cfg.max_stars}:")
        return
    await state.update_data(stars=int(txt))
    await state.set_state(None)
    await show_confirm(m, state, shop, edit=False)


# ---------- шаг 3: способ оплаты ----------
async def show_confirm(msg: Message, state: FSMContext, shop: Shop, edit: bool):
    d = await state.get_data()
    total = await shop.price_for(d["stars"])
    text = (f"Получатель: @{escape(d['recipient'])}\n"
            f"Звёзд: <b>{d['stars']} ⭐</b>\n"
            f"К оплате: <b>{total} ₽</b>\n\nВыбери способ оплаты:")
    if edit:
        await msg.edit_text(text, reply_markup=pay_method_kb())
    else:
        await msg.answer(text, reply_markup=pay_method_kb())


@router.callback_query(F.data.startswith("pay:"))
async def pay(c: CallbackQuery, state: FSMContext, shop: Shop):
    d = await state.get_data()
    if not d.get("recipient") or not d.get("stars"):
        await c.answer("Начни заново: «Купить звёзды»", show_alert=True)
        return
    if not shop.platega.configured:
        await c.answer("Оплата временно недоступна, попробуй позже.", show_alert=True)
        return

    method = c.data.split(":")[1]
    code = shop.cfg.platega_method_sbp if method == "sbp" else shop.cfg.platega_method_crypto
    total = await shop.price_for(d["stars"])
    order_id = await shop.db.create_order(c.from_user.id, d["recipient"], d["stars"],
                                          str(total), method)
    try:
        tx = await shop.platega.create(
            method=code, amount=total, order_id=order_id,
            description=f"Starify: {d['stars']} звёзд для @{d['recipient']} (заказ #{order_id})",
            user_id=c.from_user.id, username=c.from_user.username,
            return_url=shop.cfg.platega_return_url,
        )
    except (PlategaError, Exception) as e:
        log.exception("Platega create failed")
        await shop.db.transition(order_id, (S.NEW,), S.CANCELED, note=str(e)[:500])
        await c.answer("Не удалось создать платёж, попробуй позже или выбери другой способ.",
                       show_alert=True)
        return

    await shop.db.set_payment(order_id, tx.id, tx.pay_url)
    await state.clear()
    await c.message.edit_text(
        f"🧾 <b>Заказ #{order_id}</b>\n"
        f"{d['stars']} ⭐ → @{escape(d['recipient'])}\n"
        f"К оплате: <b>{total} ₽</b>\n\n"
        "Нажми «Оплатить». Ссылка действует 15 минут.\n"
        "Оплата проверится автоматически, звёзды придут сами.",
        reply_markup=payment_kb(order_id, tx.pay_url),
    )
    await c.answer()


@router.callback_query(F.data.startswith("check:"))
async def check(c: CallbackQuery, shop: Shop):
    order_id = int(c.data.split(":")[1])
    o = await shop.db.get_order(order_id)
    if not o or o.user_id != c.from_user.id:
        await c.answer("Заказ не найден", show_alert=True)
        return
    if o.status == S.PENDING and o.tx_id:
        try:
            st = await shop.platega.status(o.tx_id)
        except Exception:
            await c.answer("Не удалось проверить, попробуй через минуту.", show_alert=True)
            return
        if st == "CONFIRMED":
            await shop.on_paid(o)
        elif st in ("CANCELED", "CHARGEBACKED"):
            await shop.on_canceled(o)
        o = await shop.db.get_order(order_id)
    await c.answer(f"Статус: {STATUS_TEXT.get(o.status, o.status)}", show_alert=True)


@router.message(StateFilter(None))
async def fallback(m: Message):
    await m.answer("Выбери действие в меню 👇\nЕсть вопрос — жми «💬 Поддержка».",
                   reply_markup=main_menu())
