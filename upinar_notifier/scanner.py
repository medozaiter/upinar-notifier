import threading
import time
from collections import defaultdict
from typing import List, Optional

from .auth import MoodleAuth
from .models import ContentItem, ScanResult, SeenStore, StudentAccount
from .moodle_client import MoodleClient
from .notifier import TelegramNotifier, format_item_alert
from .storage import FileSeenStore


class ScanService:
    """Per-account scan: discover courses, diff against stored keys, notify on new items,

    and generate initial organized course inventories.
    """

    def __init__(
        self,
        auth: Optional[MoodleAuth] = None,
        client: Optional[MoodleClient] = None,
        seen_store: Optional[SeenStore] = None,
        notifier: Optional[TelegramNotifier] = None,
    ):
        self.auth = auth or MoodleAuth()
        self.client = client or MoodleClient()
        self.seen_store = seen_store or FileSeenStore()
        self.notifier = notifier or TelegramNotifier()
        self._locks: dict[str, threading.Lock] = defaultdict(threading.Lock)
        self._lock_guard = threading.Lock()

    def scan_account(
        self,
        account: StudentAccount,
        force_inventory: bool = False,
    ) -> Optional[ScanResult]:
        with self._lock_guard:
            lock = self._locks[account.student_id]
        with lock:
            return self._scan_account_unlocked(account, force_inventory=force_inventory)

    def _scan_account_unlocked(
        self,
        account: StudentAccount,
        force_inventory: bool = False,
    ) -> Optional[ScanResult]:
        session = self.auth.get_session(account)
        if session is None:
            print(f"[Auth Error] Could not authenticate student {account.student_id}")
            return None

        courses = self.client.get_enrolled_courses(session)
        if not courses:
            print(f"[Notice] No courses detected for student {account.student_id}.")
            return None

        seen_items = self.seen_store.load(account.student_id)
        is_baseline = len(seen_items) == 0 or force_inventory
        all_items: List[ContentItem] = []
        new_items: List[ContentItem] = []
        all_keys = set()

        for course in courses:
            items = self.client.scan_course(session, course)
            print(f"   -> Course: {course.title} ({len(items)} items)")
            for item in items:
                all_items.append(item)
                all_keys.add(item.unique_key)
                if item.unique_key not in seen_items:
                    new_items.append(item)

        # First time registration or explicit inventory request:
        if is_baseline:
            # If not initial baseline (student was registered before) and there are new items, send individual alerts!
            if len(seen_items) > 0 and new_items:
                print(f"[Inventory Scan] Found {len(new_items)} new items during report for {account.student_id}. Sending alerts...")
                for item in new_items:
                    print(f"--> Sending instant alert: {item.course_name} | {item.title}")
                    msg = format_item_alert(
                        course_name=item.course_name,
                        item_type=item.mod_type,
                        item_title=item.title,
                        item_url=item.url,
                    )
                    self.notifier.send(account, msg)
                    time.sleep(1.5)

            print(
                f"\n[Inventory Scan] Indexed {len(all_keys)} items across {len(courses)} courses for {account.student_id}."
            )
            # Persist existing items so future periodic runs only notify about genuine additions
            self.seen_store.save(account.student_id, all_keys)

            # Send clean, organized inventory to student via Telegram
            from .report import send_student_inventory

            try:
                send_student_inventory(account, courses, all_items, notifier=self.notifier)
            except Exception as exc:
                print(f"[Report Error] Could not send initial inventory to {account.student_id}: {exc}")

            return ScanResult(
                courses=courses,
                items=all_items,
                new_items=new_items if len(seen_items) > 0 else [],
                is_baseline=True,
                seen_keys=all_keys,
            )

        # Subsequent periodic scan:
        if not new_items:
            print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] [OK] {account.student_id}: Up to date. No new items.")
            return ScanResult(
                courses=courses,
                items=all_items,
                new_items=[],
                is_baseline=False,
                seen_keys=seen_items,
            )

        print(f"\n[ALERT] Found {len(new_items)} new items for student {account.student_id}!")
        notified_keys = set()
        for item in new_items:
            print(f"--> Sending notification: {item.course_name} | {item.title}")
            message = format_item_alert(
                course_name=item.course_name,
                item_type=item.mod_type,
                item_title=item.title,
                item_url=item.url,
            )
            sent = self.notifier.send(account, message)
            if sent:
                notified_keys.add(item.unique_key)
                time.sleep(1.5)

        # Only remember items that were successfully delivered
        if notified_keys:
            seen_items.update(notified_keys)
            self.seen_store.save(account.student_id, seen_items)

        return ScanResult(
            courses=courses,
            items=all_items,
            new_items=new_items,
            is_baseline=False,
            seen_keys=seen_items,
        )
