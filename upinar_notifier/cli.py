import sys

from .net import configure_stdout
from .report import generate_full_inventory
from .runtime import scan_all_active
from .service import install_autostart, start_service, status_service, stop_service, uninstall_autostart
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
        if "--service" in args:
            from .paths import ensure_data_dir
            from .service import LOG_FILE

            ensure_data_dir()
            log_fp = open(LOG_FILE, "a", encoding="utf-8", buffering=1)
            sys.stdout = log_fp
            sys.stderr = log_fp

        start_bot()
        return
    if command in ("start", "background", "daemon"):
        start_service()
        return
    if command in ("stop", "kill"):
        stop_service()
        return
    if command in ("status", "ps"):
        status_service()
        return
    if command == "autostart":
        if "--disable" in args or "disable" in args or "remove" in args:
            uninstall_autostart()
        else:
            install_autostart()
        return

    print(f"Unknown command: {command}")
    print_help()
    sys.exit(1)


def print_help() -> None:
    print(
        """UPINAR Notifier

Commands:
  python -m upinar_notifier start           Start the bot silently in the background (Windows / Linux)
  python -m upinar_notifier status          Check if the background bot is running and show logs
  python -m upinar_notifier stop            Stop the background bot
  python -m upinar_notifier autostart       Enable automatic startup with Windows boot
  python -m upinar_notifier autostart --disable   Disable automatic startup
  python -m upinar_notifier run             Start the bot in the current foreground console
  python -m upinar_notifier setup           Save TELEGRAM_BOT_TOKEN to .env
  python -m upinar_notifier once            Scan all registered students once
  python -m upinar_notifier report          Send a full inventory to the first student
"""
    )


def run_once() -> None:
    scan_all_active()


def start_bot() -> None:
    from .bot import run_bot

    run_bot()
