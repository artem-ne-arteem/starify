"""SQLite-хранилище заказов. Все смены статуса атомарные (UPDATE ... WHERE status=?),
чтобы админ и авто-доставка никогда не отправили звёзды дважды."""

import time
from dataclasses import dataclass

import aiosqlite

# Статусы заказа
NEW = "new"              # создан, ссылка на оплату ещё не получена
PENDING = "pending"      # ждём оплату
PAID = "paid"            # оплачен, ждём админа (идёт 5-минутный таймер)
MANUAL = "manual"        # админ взял в работу вручную (таймер остановлен)
PROCESSING = "processing"  # идёт автоотправка через Fragment
DELIVERED = "delivered"  # звёзды отправлены
UNCERTAIN = "uncertain"  # транзакция ушла, но подтверждения нет — проверить руками, НЕ повторять
FAILED = "failed"        # автоотправка не удалась, нужен админ
CANCELED = "canceled"    # оплата отменена / истекла

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    username TEXT,
    created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    recipient TEXT NOT NULL,
    stars INTEGER NOT NULL,
    amount_rub TEXT NOT NULL,
    method TEXT NOT NULL,
    tx_id TEXT,
    pay_url TEXT,
    status TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    paid_at INTEGER,
    delivered_at INTEGER,
    delivered_by TEXT,
    note TEXT
);
CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS admin_messages (
    order_id INTEGER NOT NULL,
    chat_id INTEGER NOT NULL,
    message_id INTEGER NOT NULL
);
"""


@dataclass
class Order:
    id: int
    user_id: int
    recipient: str
    stars: int
    amount_rub: str
    method: str
    tx_id: str | None
    pay_url: str | None
    status: str
    created_at: int
    paid_at: int | None
    delivered_at: int | None
    delivered_by: str | None
    note: str | None


class Database:
    def __init__(self, path: str):
        self.path = path
        self.conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        self.conn = await aiosqlite.connect(self.path)
        self.conn.row_factory = aiosqlite.Row
        await self.conn.executescript(SCHEMA)
        await self.conn.commit()

    async def close(self) -> None:
        if self.conn:
            await self.conn.close()

    # --- users ---
    async def upsert_user(self, user_id: int, username: str | None) -> None:
        await self.conn.execute(
            "INSERT INTO users(id, username, created_at) VALUES(?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET username=excluded.username",
            (user_id, username, int(time.time())),
        )
        await self.conn.commit()

    # --- settings ---
    async def get_setting(self, key: str) -> str | None:
        cur = await self.conn.execute("SELECT value FROM settings WHERE key=?", (key,))
        row = await cur.fetchone()
        return row["value"] if row else None

    async def set_setting(self, key: str, value: str) -> None:
        await self.conn.execute(
            "INSERT INTO settings(key, value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        await self.conn.commit()

    # --- orders ---
    async def create_order(self, user_id: int, recipient: str, stars: int,
                           amount_rub: str, method: str) -> int:
        cur = await self.conn.execute(
            "INSERT INTO orders(user_id, recipient, stars, amount_rub, method, status, created_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (user_id, recipient, stars, amount_rub, method, NEW, int(time.time())),
        )
        await self.conn.commit()
        return cur.lastrowid

    async def set_payment(self, order_id: int, tx_id: str, pay_url: str) -> None:
        await self.conn.execute(
            "UPDATE orders SET tx_id=?, pay_url=?, status=? WHERE id=? AND status=?",
            (tx_id, pay_url, PENDING, order_id, NEW),
        )
        await self.conn.commit()

    async def get_order(self, order_id: int) -> Order | None:
        cur = await self.conn.execute("SELECT * FROM orders WHERE id=?", (order_id,))
        row = await cur.fetchone()
        return Order(**dict(row)) if row else None

    async def user_orders(self, user_id: int, limit: int = 10) -> list[Order]:
        cur = await self.conn.execute(
            "SELECT * FROM orders WHERE user_id=? AND status!=? ORDER BY id DESC LIMIT ?",
            (user_id, NEW, limit),
        )
        return [Order(**dict(r)) for r in await cur.fetchall()]

    async def orders_by_status(self, *statuses: str) -> list[Order]:
        q = ",".join("?" * len(statuses))
        cur = await self.conn.execute(
            f"SELECT * FROM orders WHERE status IN ({q}) ORDER BY id", statuses
        )
        return [Order(**dict(r)) for r in await cur.fetchall()]

    async def transition(self, order_id: int, from_statuses: tuple[str, ...], to: str,
                         **fields) -> bool:
        """Атомарно меняет статус. Возвращает True, только если именно этот вызов сменил статус."""
        sets = ["status=?"] + [f"{k}=?" for k in fields]
        q = ",".join("?" * len(from_statuses))
        cur = await self.conn.execute(
            f"UPDATE orders SET {', '.join(sets)} WHERE id=? AND status IN ({q})",
            (to, *fields.values(), order_id, *from_statuses),
        )
        await self.conn.commit()
        return cur.rowcount == 1

    async def stats(self) -> dict:
        cur = await self.conn.execute(
            "SELECT COUNT(*) AS n, COALESCE(SUM(stars),0) AS stars, "
            "COALESCE(SUM(CAST(amount_rub AS REAL)),0) AS rub FROM orders "
            "WHERE status=? AND method!='test'",
            (DELIVERED,),
        )
        done = dict(await cur.fetchone())
        cur = await self.conn.execute("SELECT COUNT(*) AS n FROM users")
        users = (await cur.fetchone())["n"]
        return {"users": users, **done}

    # --- admin notification messages (чтобы обновлять их у всех админов) ---
    async def add_admin_message(self, order_id: int, chat_id: int, message_id: int) -> None:
        await self.conn.execute(
            "INSERT INTO admin_messages(order_id, chat_id, message_id) VALUES(?,?,?)",
            (order_id, chat_id, message_id),
        )
        await self.conn.commit()

    async def admin_messages(self, order_id: int) -> list[tuple[int, int]]:
        cur = await self.conn.execute(
            "SELECT chat_id, message_id FROM admin_messages WHERE order_id=?", (order_id,)
        )
        return [(r["chat_id"], r["message_id"]) for r in await cur.fetchall()]
