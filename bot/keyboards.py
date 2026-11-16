from aiogram.enums import ButtonStyle
from aiogram.types import InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from . import db as S

# Цвета кнопок (Bot API 9.4+): PRIMARY — синий, SUCCESS — зелёный, DANGER — красный
BLUE, GREEN, RED = ButtonStyle.PRIMARY, ButtonStyle.SUCCESS, ButtonStyle.DANGER

PACKS = (50, 100, 250, 500, 1000, 2500)

BTN_BUY = "⭐ Купить звёзды"
BTN_ORDERS = "📦 Мои заказы"
BTN_HELP = "ℹ️ Помощь"
BTN_SUPPORT = "💬 Поддержка"


def main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=BTN_BUY, style=GREEN)],
                  [KeyboardButton(text=BTN_ORDERS), KeyboardButton(text=BTN_SUPPORT)],
                  [KeyboardButton(text=BTN_HELP)]],
        resize_keyboard=True,
    )


def recipient_kb(has_username: bool) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    if has_username:
        b.button(text="🙋 Себе", callback_data="rcpt:self", style=BLUE)
    b.button(text="🎁 Другу (ввести @username)", callback_data="rcpt:other", style=BLUE)
    b.adjust(1)
    return b.as_markup()


def packs_kb(prices: dict[int, str]) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for p in PACKS:
        b.button(text=f"{p} ⭐ — {prices[p]} ₽", callback_data=f"pack:{p}", style=BLUE)
    b.button(text="✏️ Своё количество", callback_data="pack:custom")
    b.button(text="« Назад", callback_data="back:recipient")
    b.adjust(2, 2, 2, 1, 1)
    return b.as_markup()


def pay_method_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="🏦 СБП", callback_data="pay:sbp", style=GREEN)
    b.button(text="💎 Криптовалюта", callback_data="pay:crypto", style=GREEN)
    b.button(text="« Назад", callback_data="back:packs")
    b.adjust(2, 1)
    return b.as_markup()


def payment_kb(order_id: int, url: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="💳 Оплатить", url=url, style=GREEN)
    b.button(text="🔄 Проверить оплату", callback_data=f"check:{order_id}", style=BLUE)
    b.adjust(1)
    return b.as_markup()


def admin_order_kb(o) -> InlineKeyboardMarkup | None:
    b = InlineKeyboardBuilder()
    if o.status == S.PAID:
        b.button(text="✋ Беру в работу", callback_data=f"adm:take:{o.id}", style=BLUE)
        b.button(text="⚡ Отправить автоматически", callback_data=f"adm:auto:{o.id}", style=GREEN)
        b.button(text="✅ Уже отправил вручную", callback_data=f"adm:done:{o.id}", style=GREEN)
    elif o.status in (S.MANUAL, S.FAILED, S.UNCERTAIN):
        b.button(text="✅ Отправил вручную", callback_data=f"adm:done:{o.id}", style=GREEN)
        if o.status != S.UNCERTAIN:
            b.button(text="⚡ Отправить автоматически", callback_data=f"adm:auto:{o.id}",
                     style=BLUE)
    else:
        return None
    if o.status in S.REFUNDABLE:
        b.button(text="↩️ Отменить и вернуть деньги", callback_data=f"adm:refund:{o.id}",
                 style=RED)
    b.adjust(1)
    return b.as_markup()


def refund_confirm_kb(order_id: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="↩️ Да, вернуть деньги", callback_data=f"adm:refundok:{order_id}", style=RED)
    b.button(text="« Не отменять", callback_data=f"adm:refundno:{order_id}")
    b.adjust(1)
    return b.as_markup()


# ---------- поддержка ----------
def support_exit_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="❌ Завершить чат", callback_data="sup:exit", style=RED)
    return b.as_markup()


def support_reply_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="✍️ Ответить", callback_data="sup:open", style=BLUE)
    return b.as_markup()


def support_admin_kb(user_id: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="✅ Закрыть обращение", callback_data=f"sup:close:{user_id}", style=GREEN)
    return b.as_markup()
