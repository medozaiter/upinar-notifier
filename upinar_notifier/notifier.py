import html
import time
from typing import List

import requests

from .models import StudentAccount


class TelegramNotifier:
    def send(self, account: StudentAccount, text: str) -> bool:
        token = account.telegram_bot_token
        if not token:
            try:
                from .settings import get_settings

                token = get_settings().telegram_bot_token
            except Exception:
                token = ""
        return send_telegram_message(token, account.telegram_chat_id, text)


def chunk_text(text: str, max_chars: int = 3900) -> List[str]:
    """Split text into chunks that fit within Telegram's 4096 character limit."""
    if len(text) <= max_chars:
        return [text]

    chunks: List[str] = []
    lines = text.split("\n")
    current_chunk: List[str] = []
    current_length = 0

    for line in lines:
        line_len = len(line) + 1  # include newline
        if current_length + line_len > max_chars:
            if current_chunk:
                chunks.append("\n".join(current_chunk))
                current_chunk = []
                current_length = 0
            # If a single line itself is longer than max_chars
            while len(line) > max_chars:
                chunks.append(line[:max_chars])
                line = line[max_chars:]
            current_chunk.append(line)
            current_length = len(line)
        else:
            current_chunk.append(line)
            current_length += line_len

    if current_chunk:
        chunks.append("\n".join(current_chunk))

    return chunks


def send_telegram_message(bot_token: str, chat_id: str, text: str) -> bool:
    """Send a message (or chunked messages) to a Telegram chat safely."""
    if not bot_token or not chat_id or not text.strip():
        print("[Telegram] Bot token, chat_id or text is missing.")
        return False

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    chunks = chunk_text(text)
    overall_ok = True

    for chunk in chunks:
        sent = _send_single_chunk(url, chat_id, chunk)
        if not sent:
            overall_ok = False
        if len(chunks) > 1:
            time.sleep(0.3)

    return overall_ok


def _send_single_chunk(url: str, chat_id: str, chunk: str) -> bool:
    payload = {
        "chat_id": chat_id,
        "text": chunk,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }

    try:
        response = requests.post(url, json=payload, timeout=20)
        data = response.json()
        if data.get("ok"):
            return True

        description = data.get("description", "")
        # If user blocked bot, don't crash
        if "bot was blocked by the user" in description.lower():
            print(f"[Telegram Notice] Student {chat_id} has blocked the bot.")
            return False

        # If HTML parse error occurred, retry as plain text
        if "can't parse entities" in description.lower() or "parse" in description.lower():
            print("[Telegram Warning] HTML parsing failed; retrying as plain text...")
            plain_payload = {
                "chat_id": chat_id,
                "text": chunk,
                "disable_web_page_preview": False,
            }
            retry_res = requests.post(url, json=plain_payload, timeout=20)
            return retry_res.json().get("ok", False)

        print(f"[Telegram Error] HTTP {response.status_code}: {description}")
        return False
    except Exception as exc:
        print(f"[Telegram Exception] {exc}")
        return False


def format_item_alert(course_name: str, item_type: str, item_title: str, item_url: str) -> str:
    type_labels = {
        "assign": "📝 <b>واجب جديد (Assignment)</b>",
        "resource": "📄 <b>ملف / محاضرة جديدة (File)</b>",
        "folder": "📁 <b>مجلد جديد (Folder)</b>",
        "folder_file": "📄 <b>ملف داخل مجلد (Uploaded file)</b>",
        "forum": "📢 <b>منتدى / إعلانات (Forum)</b>",
        "forum_post": "📢 <b>إعلان جديد (Announcement)</b>",
        "quiz": "⏱️ <b>كويز / اختبار جديد (Quiz)</b>",
        "url": "🔗 <b>رابط / تسجيل محاضرة (Link/Recording)</b>",
        "page": "📄 <b>صفحة جديدة (Page)</b>",
        "book": "📘 <b>كتاب / مرجع جديد (Book)</b>",
    }
    type_emoji = type_labels.get(item_type, f"📌 <b>عنصر جديد ({item_type})</b>")

    safe_course = html.escape(course_name or "المساق")
    safe_title = html.escape(item_title or "عنصر بدون عنوان")
    safe_url = html.escape(item_url or "#")

    return (
        f"🔔 <b>تنبيه يوبينار جديد! (UPINAR Alert)</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📚 <b>المساق:</b> {safe_course}\n"
        f"{type_emoji}\n"
        f"📌 <b>العنوان:</b> {safe_title}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"👉 <a href='{safe_url}'>اضغط هنا لفتح العنصر مباشرة</a>"
    )
