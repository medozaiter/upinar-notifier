import time

from .auth import MoodleAuth
from .db import get_database
from .notifier import TelegramNotifier
from .scanner import ScanService

_SCANNER: ScanService | None = None


def get_scan_service() -> ScanService:
    global _SCANNER
    if _SCANNER is None:
        db = get_database()
        _SCANNER = ScanService(
            auth=MoodleAuth(session_store=db.sessions),
            seen_store=db.seen,
            notifier=TelegramNotifier(),
        )
    return _SCANNER


def scan_all_active() -> None:
    db = get_database()
    scanner = get_scan_service()
    accounts = db.accounts.load_all()
    if not accounts:
        print("[Monitor] No registered students yet.")
        return

    print(f"[Monitor] Starting periodic cycle for {len(accounts)} active student(s)...")
    for index, account in enumerate(accounts, 1):
        print(f"[{index}/{len(accounts)}] Scanning student ID: {account.student_id}...")
        try:
            scanner.scan_account(account, force_inventory=False)
            db.accounts.mark_scan(account.student_id)
        except Exception as exc:
            print(f"[Error] Scan failed for {account.student_id}: {exc}")
            db.accounts.mark_scan(account.student_id, error=str(exc))

        # Polite delay between student scans to avoid firewall / rate limits from university servers
        if index < len(accounts):
            time.sleep(2.5)

    print("[Monitor] Finished scanning all active students.")
