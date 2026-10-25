import os
import shutil

from .crypto import ensure_fernet_key
from .notifier import send_telegram_message
from .paths import ENV_EXAMPLE_FILE, ENV_FILE


def run_setup() -> None:
    print("=" * 60)
    print("      UPINAR Notifier setup")
    print("      Operator configuration for the Telegram bot")
    print("=" * 60)
    print()
    print("Students register later with /start inside Telegram.")
    print("This step only stores the bot token and encryption key in .env")
    print()

    if not os.path.exists(ENV_FILE) and os.path.exists(ENV_EXAMPLE_FILE):
        shutil.copy2(ENV_EXAMPLE_FILE, ENV_FILE)
        print("Created .env from .env.example")
        print()

    current = _read_env()
    token = _ask_required(
        "Telegram bot token (from BotFather)",
        current.get("TELEGRAM_BOT_TOKEN", ""),
        reject_placeholders=True,
    )
    interval_raw = input(
        f"Check interval in minutes [{current.get('CHECK_INTERVAL_MINUTES', '30')}]: "
    ).strip()
    interval = current.get("CHECK_INTERVAL_MINUTES", "30") or "30"
    if interval_raw:
        try:
            interval = str(max(1, int(interval_raw)))
        except ValueError:
            interval = "30"

    encryption_key = current.get("ENCRYPTION_KEY", "").strip()
    if not encryption_key:
        encryption_key = ensure_fernet_key("")
    os.environ["TELEGRAM_BOT_TOKEN"] = token
    os.environ["ENCRYPTION_KEY"] = encryption_key
    os.environ["CHECK_INTERVAL_MINUTES"] = interval
    _write_env(
        {
            "TELEGRAM_BOT_TOKEN": token,
            "ENCRYPTION_KEY": encryption_key,
            "CHECK_INTERVAL_MINUTES": interval,
        }
    )

    print()
    print("=" * 60)
    print("Saved settings to .env (gitignored).")
    print("Start the bot:  python -m upinar_notifier run")
    print("Then students send /start to your bot.")
    print("=" * 60)


def send_test_message(chat_id: str | None = None) -> bool:
    from .db import get_database
    from .settings import get_settings, reset_settings

    reset_settings()
    settings = get_settings()
    target = chat_id
    if not target:
        accounts = get_database().accounts.load_all()
        if not accounts:
            print("[FAIL] No registered students yet. A student must send /start first.")
            return False
        target = accounts[0].telegram_chat_id
    print(f"Sending test message to Telegram (Chat ID: {target})...")
    message = (
        "🎉 <b>تم الاتصال بنجاح مع بوت تيليجرام!</b>\n\n"
        "هذه رسالة تجريبية لتأكيد عمل إشعارات نظام مراقبة اليوبينار (UPINAR Notifier)."
    )
    ok = send_telegram_message(settings.telegram_bot_token, target, message)
    if ok:
        print("[OK] Test message sent successfully. Check Telegram.")
    else:
        print("[FAIL] Could not send message. Verify the token and that the student pressed Start.")
    return ok


def _read_env() -> dict[str, str]:
    values: dict[str, str] = {}
    if not os.path.exists(ENV_FILE):
        return values
    with open(ENV_FILE, "r", encoding="utf-8") as handle:
        for line in handle:
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, raw = stripped.split("=", 1)
            values[key.strip()] = raw.strip().strip('"').strip("'")
    return values


def _write_env(values: dict[str, str]) -> None:
    current = _read_env()
    current.update(values)
    lines = [
        "# UPINAR notifier secrets — never commit this file",
        f"TELEGRAM_BOT_TOKEN={current.get('TELEGRAM_BOT_TOKEN', '')}",
        f"ENCRYPTION_KEY={current.get('ENCRYPTION_KEY', '')}",
        f"CHECK_INTERVAL_MINUTES={current.get('CHECK_INTERVAL_MINUTES', '30')}",
        "",
    ]
    with open(ENV_FILE, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines))


def _ask_required(label: str, current: str, reject_placeholders: bool = False) -> str:
    placeholders = {"", "YOUR_TELEGRAM_BOT_TOKEN", "CHANGE_ME"}
    current_ok = current.strip() not in placeholders
    hint = f" [{current[:8]}...]" if current_ok and len(current) > 12 else (f" [{current}]" if current_ok else "")
    while True:
        value = input(f"{label}{hint}: ").strip()
        if not value and current_ok:
            return current.strip()
        if value and (not reject_placeholders or value not in placeholders):
            return value
        print("   This field is required. Enter the token from BotFather.")
