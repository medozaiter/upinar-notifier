import os
from dataclasses import dataclass

from dotenv import load_dotenv

from .crypto import ensure_fernet_key, normalize_fernet_secret
from .paths import BASE_DIR, ENV_FILE, PACKAGE_DIR


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str
    encryption_key: str
    check_interval_minutes: int
    database_path: str
    telegram_proxy: str | None = None


_SETTINGS: Settings | None = None


def load_env() -> None:
    load_dotenv(ENV_FILE)
    load_dotenv(os.path.join(BASE_DIR, ".env"))
    load_dotenv(os.path.join(PACKAGE_DIR, ".env"))
    load_dotenv(os.path.join(os.getcwd(), ".env"))


def get_settings() -> Settings:
    global _SETTINGS
    if _SETTINGS is not None:
        return _SETTINGS

    load_env()
    token = str(os.getenv("TELEGRAM_BOT_TOKEN", "")).strip()
    if not token or token in {"YOUR_TELEGRAM_BOT_TOKEN", "CHANGE_ME"}:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN is missing. Copy .env.example to .env "
            "or run: python -m upinar_notifier setup"
        )

    encryption_key = ensure_fernet_key(os.getenv("ENCRYPTION_KEY", ""))
    interval_raw = os.getenv("CHECK_INTERVAL_MINUTES", "30")
    try:
        interval = max(1, int(interval_raw))
    except ValueError:
        interval = 30

    from .paths import DATABASE_FILE

    proxy = (
        os.getenv("TELEGRAM_PROXY")
        or os.getenv("HTTPS_PROXY")
        or os.getenv("HTTP_PROXY")
        or ""
    ).strip() or None

    _SETTINGS = Settings(
        telegram_bot_token=token,
        encryption_key=normalize_fernet_secret(encryption_key),
        check_interval_minutes=interval,
        database_path=os.getenv("DATABASE_PATH", DATABASE_FILE).strip() or DATABASE_FILE,
        telegram_proxy=proxy,
    )
    return _SETTINGS


def reset_settings() -> None:
    global _SETTINGS
    _SETTINGS = None
