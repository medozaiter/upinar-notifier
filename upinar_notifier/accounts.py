import json
import os
from typing import List

from .models import StudentAccount
from .paths import CONFIG_EXAMPLE_FILE, CONFIG_FILE


class LocalJsonAccountStore:
    """Single-user store backed by gitignored config.json.

    SaaS replacement: load many accounts from a database instead of one JSON file.
    """

    def __init__(self, path: str = CONFIG_FILE):
        self.path = path

    def load_all(self) -> List[StudentAccount]:
        if not os.path.exists(self.path):
            raise FileNotFoundError(
                f"config.json not found at {self.path}. "
                f"Copy {CONFIG_EXAMPLE_FILE} to config.json or run: python -m upinar_notifier setup"
            )
        with open(self.path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        account = self._from_dict(data)
        if not account.is_complete():
            raise ValueError(
                "config.json still contains placeholders. Run setup and enter your own credentials."
            )
        return [account]

    def save(self, account: StudentAccount) -> None:
        student_id = str(account.student_id).strip()
        if len(student_id) > 32:
            raise ValueError("student_id too long")

        payload = {
            "student_id": student_id,
            "password": account.password,
            "telegram_bot_token": account.telegram_bot_token,
            "telegram_chat_id": account.telegram_chat_id,
            "check_interval_minutes": account.check_interval_minutes,
        }

        with open(self.path, "w", encoding="utf-8") as handle:
            json.dump(
                payload,
                handle,
                ensure_ascii=False,
                indent=2,
            )

    def load_raw(self) -> dict:
        if not os.path.exists(self.path):
            return {}
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                return json.load(handle)
        except Exception:
            return {}

    @staticmethod
    def _from_dict(data: dict) -> StudentAccount:
        return StudentAccount(
            student_id=str(data.get("student_id", "")).strip(),
            password=str(data.get("password", "")).strip(),
            telegram_bot_token=str(data.get("telegram_bot_token", "")).strip(),
            telegram_chat_id=str(data.get("telegram_chat_id", "")).strip(),
            check_interval_minutes=int(data.get("check_interval_minutes", 30) or 30),
        )
