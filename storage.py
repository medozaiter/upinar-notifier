import json
import os
from typing import Set, Dict, Any

STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "seen_items.json")

def load_seen_items() -> Set[str]:
    """
    Loads the set of already recorded item IDs/URLs from disk.
    """
    if not os.path.exists(STATE_FILE):
        return set()
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return set(data.get("items", []))
    except Exception as e:
        print(f"[Storage Warning] Could not read seen items: {e}")
        return set()

def save_seen_items(items: Set[str]):
    """
    Saves the updated set of item IDs/URLs to disk.
    """
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump({"items": sorted(list(items))}, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[Storage Error] Could not save seen items: {e}")
