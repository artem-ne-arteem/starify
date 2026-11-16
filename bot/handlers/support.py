"""Чат поддержки: клиент пишет боту, сообщения уходят админам.
Админ отвечает РЕПЛАЕМ (свайп / «Ответить») на сообщение клиента — ответ уходит клиенту."""

import logging
from html import escape

from aiogram import Bot, F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.base import BaseStorage, StorageKey
from aiogram.types import CallbackQuery, Message, ReactionTypeEmoji

from ..keyboards import BTN_BUY, BTN_HELP, BTN_ORDERS, BTN_SUPPORT, support_admin_kb, support_exit_kb, support_reply_kb
from ..service import Shop

log = logging.getLogger(__name__)
router = Router()

WELCOME = ("💬 <b>Поддержка Starify</b>\n\n"
           "Напиши свой вопрос одним или несколькими сообщениями — можно с фото или скриншотом. "
           "Если вопрос по заказу, укажи его номер.\n"
           "Мы ответим прямо здесь, в боте.")


class Support(StatesGroup):
    chat = State()


async def open_support(target: Message, state: FSMContext, shop: Shop):
    if not shop.cfg.admin_ids:
        await target.answer("Поддержка временно недоступна.")
        return
    await state.set_state(Support.chat)
    await target.answer(WELCOME, reply_markup=support_exit_kb())


@router.message(F.text == BTN_SUPPORT)
async def support_btn(m: Message, state: FSMContext, shop: Shop):
    await open_support(m, state, shop)


@router.callback_query(F.data == "sup:open")
async def support_open_cb(c: CallbackQuery, state: FSMContext, shop: Shop):
    await open_support(c.message, state, shop)
    await c.answer()


@router.callback_query(F.data == "sup:exit")
async def support_exit(c: CallbackQuery, state: FSMContext):
    await state.clear()
    await c.message.edit_reply_markup(reply_markup=None)
    await c.message.answer("Чат с поддержкой завершён. Если что — жми «💬 Поддержка».")
    await c.answer()


MENU = {BTN_BUY, BTN_ORDERS, BTN_HELP, BTN_SUPPORT}


@router.message(StateFilter(Support.chat), ~F.text.startswith("/"), ~F.text.in_(MENU))
async def user_to_admins(m: Message, shop: Shop, bot: Bot):
    u = m.from_user
    who = f"@{escape(u.username)}" if u.username else escape(u.full_name)
    header = (f"💬 <b>Поддержка</b> · {who} · <code>{u.id}</code>\n"
              "<i>Ответь реплаем на это или следующее сообщение, чтобы написать клиенту.</i>")
    delivered = False
    for admin_id in shop.cfg.admin_ids:
        try:
            h = await bot.send_message(admin_id, header, reply_markup=support_admin_kb(u.id))
            c = await bot.copy_message(admin_id, m.chat.id, m.message_id)
            await shop.db.support_link(admin_id, h.message_id, u.id)
            await shop.db.support_link(admin_id, c.message_id, u.id)
            delivered = True
        except Exception:
            log.exception("support forward to %s failed", admin_id)
    if delivered:
        await m.react([ReactionTypeEmoji(emoji="👍")])
    else:
        await m.answer("Не удалось отправить сообщение, попробуй позже.")


# ---------- сторона админа ----------
async def support_reply_filter(m: Message, shop: Shop) -> dict | bool:
    if m.from_user.id not in shop.cfg.admin_ids or not m.reply_to_message:
        return False
    uid = await shop.db.support_user(m.chat.id, m.reply_to_message.message_id)
    return {"support_user_id": uid} if uid else False


@router.message(support_reply_filter)
async def admin_to_user(m: Message, bot: Bot, shop: Shop, support_user_id: int):
    try:
        if m.text:
            await bot.send_message(support_user_id,
                                   f"💬 <b>Поддержка:</b>\n{escape(m.text)}",
                                   reply_markup=support_reply_kb())
        else:
            await bot.send_message(support_user_id, "💬 <b>Поддержка:</b>")
            await bot.copy_message(support_user_id, m.chat.id, m.message_id,
                                   reply_markup=support_reply_kb())
    except Exception as e:
        await m.reply(f"❌ Не доставлено: {escape(str(e))}")
        return
    await m.react([ReactionTypeEmoji(emoji="👍")])


@router.callback_query(F.data.startswith("sup:close:"))
async def admin_close(c: CallbackQuery, bot: Bot, shop: Shop, fsm_storage: BaseStorage):
    if c.from_user.id not in shop.cfg.admin_ids:
        await c.answer("Нет доступа", show_alert=True)
        return
    uid = int(c.data.split(":")[2])
    key = StorageKey(bot_id=bot.id, chat_id=uid, user_id=uid)
    await fsm_storage.set_state(key, None)
    try:
        await bot.send_message(uid, "✅ Обращение закрыто. Если появятся вопросы — жми «💬 Поддержка».")
    except Exception:
        pass
    await c.message.edit_reply_markup(reply_markup=None)
    await c.answer("Обращение закрыто")
