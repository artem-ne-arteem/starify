import json
import os
from dataclasses import dataclass, field
from decimal import Decimal

from dotenv import load_dotenv

load_dotenv()


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


@dataclass(frozen=True)
class Config:
    bot_token: str
    admin_ids: tuple[int, ...]
    star_price_rub: Decimal
    min_stars: int
    max_stars: int

    platega_merchant_id: str
    platega_secret: str
    platega_return_url: str
    platega_method_sbp: int
    platega_method_crypto: int

    admin_timeout_sec: int

    fragment_enabled: bool
    fragment_seed: str
    fragment_api_key: str
    fragment_api_provider: str
    fragment_wallet_version: str
    fragment_cookies: dict = field(default_factory=dict)

    db_path: str = "starify.db"


def load_config() -> Config:
    token = _env("BOT_TOKEN")
    if not token:
        raise SystemExit("BOT_TOKEN не задан в .env")

    admins = tuple(int(x) for x in _env("ADMIN_IDS").replace(" ", "").split(",") if x)

    cookies_raw = _env("FRAGMENT_COOKIES")
    cookies = json.loads(cookies_raw) if cookies_raw else {}

    return Config(
        bot_token=token,
        admin_ids=admins,
        star_price_rub=Decimal(_env("STAR_PRICE_RUB", "1.5")),
        min_stars=int(_env("MIN_STARS", "50")),
        max_stars=int(_env("MAX_STARS", "100000")),
        platega_merchant_id=_env("PLATEGA_MERCHANT_ID"),
        platega_secret=_env("PLATEGA_SECRET"),
        platega_return_url=_env("PLATEGA_RETURN_URL", "https://t.me"),
        platega_method_sbp=int(_env("PLATEGA_METHOD_SBP", "2")),
        platega_method_crypto=int(_env("PLATEGA_METHOD_CRYPTO", "13")),
        admin_timeout_sec=int(_env("ADMIN_TIMEOUT_SEC", "300")),
        fragment_enabled=_env("FRAGMENT_ENABLED", "0") == "1",
        fragment_seed=_env("FRAGMENT_SEED"),
        fragment_api_key=_env("FRAGMENT_API_KEY"),
        fragment_api_provider=_env("FRAGMENT_API_PROVIDER", "tonapi"),
        fragment_wallet_version=_env("FRAGMENT_WALLET_VERSION", "V5R1"),
        fragment_cookies=cookies,
    )
