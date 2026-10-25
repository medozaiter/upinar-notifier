import sys

from .net import configure_stdout
from .report import generate_full_inventory
from .runtime import scan_all_active
from .setup_wizard import run_setup, send_test_message


def main(argv: list[str] | None = None) -> None:
    configure_stdout()
    args = list(sys.argv[1:] if argv is None else argv)
    command = args[0] if args else "run"

    if command in ("-h", "--help", "help"):
        print_help()
        return
    if command == "setup":
        run_setup()
        return
    if command in ("test-telegram", "test"):
        send_test_message()
        return
    if command == "report":
        generate_full_inventory()
        return
    if command == "once" or "--once" in args:
        run_once()
        return
    if command == "run":
        start_bot()
        return

    print(f"Unknown command: {command}")
    print_help()
    sys.exit(1)


def print_help() -> None:
    print(
        """UPINAR Notifier

Commands:
  python -m upinar_notifier setup           Save TELEGRAM_BOT_TOKEN to .env
  python -m upinar_notifier run             Start the Telegram bot + 30-minute monitor
  python -m upinar_notifier once            Scan all registered students once
  python -m upinar_notifier report          Send a full inventory to the first student
  python -m upinar_notifier test-telegram   Send a Telegram test message
"""
    )


def run_once() -> None:
    scan_all_active()


def start_bot() -> None:
    from .bot import run_bot

    run_bot()
