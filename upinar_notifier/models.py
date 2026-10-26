import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Protocol, Set


def clean_student_id(raw_id: str) -> str:
    """Normalizes Eastern Arabic/Persian digits to standard ASCII digits and strips non-digits."""
    if not raw_id:
        return ""
    trans = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
    cleaned = str(raw_id).translate(trans)
    return re.sub(r"\D", "", cleaned)



PLACEHOLDER_VALUES = {
    "YOUR_STUDENT_ID",
    "YOUR_PORTAL_PASSWORD",
    "YOUR_TELEGRAM_BOT_TOKEN",
    "YOUR_TELEGRAM_CHAT_ID",
}


@dataclass
class StudentAccount:
    student_id: str
    password: str
    telegram_chat_id: str
    telegram_bot_token: str = ""
    check_interval_minutes: int = 30
    is_active: bool = True

    def __post_init__(self):
        if self.student_id:
            self.student_id = clean_student_id(self.student_id)

    def is_complete(self) -> bool:
        fields = [
            self.student_id,
            self.password,
            self.telegram_chat_id,
        ]

        if not all(
            value and str(value).strip() not in PLACEHOLDER_VALUES
            for value in fields
        ):
            return False

        if len(str(self.student_id)) > 32:
            return False

        if len(str(self.password)) > 128:
            return False

        if len(str(self.telegram_chat_id)) > 64:
            return False

        return True


@dataclass
class Course:
    id: str
    title: str
    url: str


@dataclass
class ContentItem:
    unique_key: str
    course_id: str
    course_name: str
    category: str
    mod_type: str
    title: str
    url: str
    extra: Dict[str, str] = field(default_factory=dict)
    created_at: str = ""


@dataclass
class ScanResult:
    courses: List[Course]
    items: List[ContentItem]
    new_items: List[ContentItem]
    is_baseline: bool
    seen_keys: Set[str]


class AccountStore(Protocol):
    def load_all(self) -> List[StudentAccount]:
        ...

    def save(self, account: StudentAccount) -> None:
        ...


class SeenStore(Protocol):
    def load(self, student_id: str) -> Set[str]:
        ...

    def save(self, student_id: str, items: Set[str]) -> None:
        ...


class SessionStore(Protocol):
    def load(self, student_id: str) -> Optional[dict]:
        ...

    def save(self, student_id: str, cookies: dict) -> None:
        ...


class Notifier(Protocol):
    def send(self, account: StudentAccount, text: str) -> bool:
        ...
