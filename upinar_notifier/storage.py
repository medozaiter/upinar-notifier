import json
import os
import shutil
from typing import Optional, Set

from .paths import LEGACY_SEEN_FILE, LEGACY_SESSION_FILE, seen_file_for, session_file_for


class FileSeenStore:
    """Persists previously seen content keys per student.

    Local files today; a SaaS version can swap this for a per-user database table.
    """

    def load(self, student_id: str) -> Set[str]:
        path = seen_file_for(student_id)
        self._migrate_legacy(path)
        if not os.path.exists(path):
            return set()
        try:
            with open(path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            return set(data.get("items", []))
        except Exception as exc:
            print(f"[Storage Warning] Could not read seen items: {exc}")
            return set()

    def save(self, student_id: str, items: Set[str]) -> None:
        path = seen_file_for(student_id)
        try:
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(
                    {"student_id": student_id, "items": sorted(items)},
                    handle,
                    ensure_ascii=False,
                    indent=2,
                )
        except Exception as exc:
            print(f"[Storage Error] Could not save seen items: {exc}")

    @staticmethod
    def _migrate_legacy(target_path: str) -> None:
        if os.path.exists(target_path) or not os.path.exists(LEGACY_SEEN_FILE):
            return
        try:
            shutil.copy2(LEGACY_SEEN_FILE, target_path)
        except Exception:
            pass


class FileSessionStore:
    def load(self, student_id: str) -> Optional[dict]:
        path = session_file_for(student_id)
        self._migrate_legacy(path)
        if not os.path.exists(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as handle:
                return json.load(handle)
        except Exception:
            return None

    def save(self, student_id: str, cookies: dict) -> None:
        path = session_file_for(student_id)
        try:
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(cookies, handle, indent=2)
        except Exception as exc:
            print(f"[Warning] Could not save session cookies: {exc}")

    @staticmethod
    def _migrate_legacy(target_path: str) -> None:
        if os.path.exists(target_path) or not os.path.exists(LEGACY_SESSION_FILE):
            return
        try:
            shutil.copy2(LEGACY_SESSION_FILE, target_path)
        except Exception:
            pass
