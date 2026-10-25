import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from typing import List, Optional, Set

from .accounts import LocalJsonAccountStore
from .crypto import decrypt_text, encrypt_text
from .models import StudentAccount
from .paths import DATABASE_FILE, ensure_data_dir
from .storage import FileSeenStore, FileSessionStore

SCHEMA = """
CREATE TABLE IF NOT EXISTS students (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_chat_id TEXT NOT NULL UNIQUE
    CHECK(length(telegram_chat_id) <= 64),
    telegram_user_id INTEGER,
    student_id TEXT NOT NULL UNIQUE
    CHECK(length(student_id) <= 32),
    password_encrypted TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_scan_at TEXT,
    last_error TEXT
);

CREATE TABLE IF NOT EXISTS seen_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id TEXT NOT NULL,
    unique_key TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    UNIQUE(student_id, unique_key)
);

CREATE TABLE IF NOT EXISTS sessions (
    student_id TEXT PRIMARY KEY,
    cookies_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_seen_student ON seen_items(student_id);
CREATE INDEX IF NOT EXISTS idx_students_active ON students(is_active);
"""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Database:
    def __init__(self, path: str | None = None):
        ensure_data_dir()
        self.path = path or DATABASE_FILE
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA journal_mode = WAL")
        self.conn.executescript(SCHEMA)
        self.conn.commit()
        self.accounts = SqliteAccountStore(self)
        self.seen = SqliteSeenStore(self)
        self.sessions = SqliteSessionStore(self)

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            cursor = self.conn.execute(sql, params)
            self.conn.commit()
            return cursor

    def query(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            cursor = self.conn.execute(sql, params)
            return cursor.fetchall()

    def migrate_legacy(self) -> None:
        self.accounts.migrate_config_json()
        self.seen.migrate_legacy_files()
        self.sessions.migrate_legacy_files()


_DB: Database | None = None


def get_database() -> Database:
    global _DB
    if _DB is None:
        from .settings import get_settings

        _DB = Database(get_settings().database_path)
        _DB.migrate_legacy()
    return _DB


class SqliteAccountStore:
    def __init__(self, db: Database):
        self.db = db

    def load_all(self) -> List[StudentAccount]:
        rows = self.db.query("SELECT * FROM students WHERE is_active = 1 ORDER BY id")
        return [self._to_account(row) for row in rows]

    def load_by_chat_id(self, chat_id: str) -> Optional[StudentAccount]:
        rows = self.db.query("SELECT * FROM students WHERE telegram_chat_id = ?", (str(chat_id),))
        return self._to_account(rows[0]) if rows else None

    def get_row_by_chat_id(self, chat_id: str) -> Optional[sqlite3.Row]:
        rows = self.db.query("SELECT * FROM students WHERE telegram_chat_id = ?", (str(chat_id),))
        return rows[0] if rows else None

    def upsert(
        self,
        *,
        telegram_chat_id: str,
        telegram_user_id: int | None,
        student_id: str,
        password: str,
    ) -> StudentAccount:
        from .settings import get_settings

        settings = get_settings()
        encrypted = encrypt_text(password, settings.encryption_key)
        now = utc_now()
        chat_id = str(telegram_chat_id).strip()
        student_id = str(student_id).strip()
        if not student_id:
            raise ValueError("student_id is required")

        if len(student_id) > 32:
            raise ValueError("student_id too long")

        if len(chat_id) > 64:
            raise ValueError("chat_id too long")

        with self.db._lock:
            existing_chat = self.db.conn.execute(
                "SELECT id FROM students WHERE telegram_chat_id = ?",
                (chat_id,),
            ).fetchone()
            existing_student = self.db.conn.execute(
                "SELECT id FROM students WHERE student_id = ?",
                (student_id,),
            ).fetchone()

            if existing_student and (not existing_chat or existing_student["id"] != existing_chat["id"]):
                self.db.conn.execute("DELETE FROM students WHERE student_id = ?", (student_id,))
            if existing_chat:
                self.db.conn.execute("DELETE FROM students WHERE telegram_chat_id = ?", (chat_id,))

            self.db.conn.execute(
                """
                INSERT INTO students (
                    telegram_chat_id, telegram_user_id, student_id, password_encrypted,
                    is_active, created_at, updated_at, last_error
                ) VALUES (?, ?, ?, ?, 1, ?, ?, NULL)
                """,
                (chat_id, telegram_user_id, student_id, encrypted, now, now),
            )
            self.db.conn.commit()

        account = self.load_by_chat_id(chat_id)
        if account is None:
            raise RuntimeError("Failed to persist student account.")
        return account

    def set_active(self, chat_id: str, active: bool) -> None:
        self.db.execute(
            "UPDATE students SET is_active = ?, updated_at = ? WHERE telegram_chat_id = ?",
            (1 if active else 0, utc_now(), str(chat_id)),
        )

    def mark_scan(self, student_id: str, error: str | None = None) -> None:
        self.db.execute(
            "UPDATE students SET last_scan_at = ?, last_error = ?, updated_at = ? WHERE student_id = ?",
            (utc_now(), error, utc_now(), student_id),
        )

    def save(self, account: StudentAccount) -> None:
        self.upsert(
            telegram_chat_id=account.telegram_chat_id,
            telegram_user_id=None,
            student_id=account.student_id,
            password=account.password,
        )

    def migrate_config_json(self) -> None:
        if self.db.query("SELECT id FROM students LIMIT 1"):
            return
        try:
            store = LocalJsonAccountStore()
            if not os.path.exists(store.path):
                return
            accounts = store.load_all()
        except Exception:
            return
        for account in accounts:
            try:
                self.upsert(
                    telegram_chat_id=account.telegram_chat_id,
                    telegram_user_id=None,
                    student_id=account.student_id,
                    password=account.password,
                )
                print(f"[Migrate] Imported student {account.student_id} from config.json")
            except Exception as exc:
                print(f"[Migrate] Could not import config.json: {exc}")

    def _to_account(self, row: sqlite3.Row) -> StudentAccount:
        from .settings import get_settings

        settings = get_settings()
        password = decrypt_text(row["password_encrypted"], settings.encryption_key)
        return StudentAccount(
            student_id=row["student_id"],
            password=password,
            telegram_chat_id=row["telegram_chat_id"],
            telegram_bot_token=settings.telegram_bot_token,
            check_interval_minutes=settings.check_interval_minutes,
            is_active=bool(row["is_active"]),
        )


class SqliteSeenStore:
    def __init__(self, db: Database):
        self.db = db

    def load(self, student_id: str) -> Set[str]:
        rows = self.db.query(
            "SELECT unique_key FROM seen_items WHERE student_id = ?",
            (student_id,),
        )
        return {row["unique_key"] for row in rows}

    def count(self, student_id: str) -> int:
        rows = self.db.query(
            "SELECT count(*) as cnt FROM seen_items WHERE student_id = ?",
            (student_id,),
        )
        return rows[0]["cnt"] if rows else 0

    def save(self, student_id: str, items: Set[str]) -> None:
        now = utc_now()
        with self.db._lock:
            self.db.conn.execute("DELETE FROM seen_items WHERE student_id = ?", (student_id,))
            self.db.conn.executemany(
                "INSERT INTO seen_items (student_id, unique_key, first_seen_at) VALUES (?, ?, ?)",
                [(student_id, key, now) for key in items],
            )
            self.db.conn.commit()

    def migrate_legacy_files(self) -> None:
        file_store = FileSeenStore()
        seen_dir = os.path.join(os.path.dirname(self.db.path), "seen")
        candidates: list[str] = []
        if os.path.isdir(seen_dir):
            for name in os.listdir(seen_dir):
                if name.endswith(".json"):
                    candidates.append(os.path.splitext(name)[0])
        for student_id in candidates:
            if self.load(student_id):
                continue
            keys = file_store.load(student_id)
            if keys:
                self.save(student_id, keys)
                print(f"[Migrate] Imported {len(keys)} seen keys for {student_id}")


class SqliteSessionStore:
    def __init__(self, db: Database):
        self.db = db

    def load(self, student_id: str) -> Optional[dict]:
        rows = self.db.query(
            "SELECT cookies_json FROM sessions WHERE student_id = ?",
            (student_id,),
        )

        if not rows:
            return None

        try:
            from .settings import get_settings

            settings = get_settings()

            decrypted = decrypt_text(
                rows[0]["cookies_json"],
                settings.encryption_key,
            )

            return json.loads(decrypted)

        except Exception as exc:
            print(
                f"[Session Warning] "
                f"Could not decrypt session cookies: {exc}"
            )
            return None

    def save(self, student_id: str, cookies: dict) -> None:
        from .settings import get_settings

        settings = get_settings()

        encrypted = encrypt_text(
            json.dumps(cookies),
            settings.encryption_key,
        )

        self.db.execute(
            """
            INSERT INTO sessions (
                student_id,
                cookies_json,
                updated_at
            )
            VALUES (?, ?, ?)
            ON CONFLICT(student_id)
            DO UPDATE SET
                cookies_json = excluded.cookies_json,
                updated_at = excluded.updated_at
            """,
            (
                student_id,
                encrypted,
                utc_now(),
            ),
        )

    def migrate_legacy_files(self) -> None:
        file_store = FileSessionStore()

        sessions_dir = os.path.join(
            os.path.dirname(self.db.path),
            "sessions"
        )

        if not os.path.isdir(sessions_dir):
            return

        for name in os.listdir(sessions_dir):

            if not name.endswith(".json"):
                continue

            student_id = os.path.splitext(name)[0]

            if self.load(student_id):
                continue

            cookies = file_store.load(student_id)

            if cookies:
                self.save(student_id, cookies)

                print(
                    f"[Migrate] Imported session cookies "
                    f"for {student_id}"
                )