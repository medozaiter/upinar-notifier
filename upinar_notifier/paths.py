import os

PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(PACKAGE_DIR)
DATA_DIR = os.path.join(BASE_DIR, "data")
ENV_FILE = os.path.join(BASE_DIR, ".env")
ENV_EXAMPLE_FILE = os.path.join(BASE_DIR, ".env.example")
DATABASE_FILE = os.path.join(DATA_DIR, "upinar.sqlite3")
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")
CONFIG_EXAMPLE_FILE = os.path.join(BASE_DIR, "config.example.json")
LEGACY_SEEN_FILE = os.path.join(BASE_DIR, "seen_items.json")
LEGACY_SESSION_FILE = os.path.join(BASE_DIR, "session_cookies.json")


def ensure_data_dir() -> str:
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(os.path.join(DATA_DIR, "seen"), exist_ok=True)
    os.makedirs(os.path.join(DATA_DIR, "sessions"), exist_ok=True)
    return DATA_DIR


def seen_file_for(student_id: str) -> str:
    ensure_data_dir()
    safe_id = "".join(ch for ch in str(student_id) if ch.isalnum() or ch in ("-", "_"))
    return os.path.join(DATA_DIR, "seen", f"{safe_id}.json")


def session_file_for(student_id: str) -> str:
    ensure_data_dir()
    safe_id = "".join(ch for ch in str(student_id) if ch.isalnum() or ch in ("-", "_"))
    return os.path.join(DATA_DIR, "sessions", f"{safe_id}.json")
