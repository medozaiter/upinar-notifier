import html
import re
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
    """Send a message (or chunked messages) to a Telegram chat safely with rate-limit retries."""
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
            time.sleep(0.8)

    return overall_ok


def _send_single_chunk(url: str, chat_id: str, chunk: str, max_retries: int = 3) -> bool:
    payload = {
        "chat_id": chat_id,
        "text": chunk,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }

    for attempt in range(max_retries):
        try:
            response = requests.post(url, json=payload, timeout=25)
            data = response.json()
            if data.get("ok"):
                return True

            description = str(data.get("description", ""))
            status_code = response.status_code

            # If user blocked bot, don't retry
            if "bot was blocked by the user" in description.lower():
                print(f"[Telegram Notice] Student {chat_id} has blocked the bot.")
                return False

            # Rate limit handling (HTTP 429 Too Many Requests)
            if status_code == 429 or "too many requests" in description.lower():
                retry_after = data.get("parameters", {}).get("retry_after", 3)
                print(f"[Telegram RateLimit] Chat {chat_id} rate-limited. Waiting {retry_after}s (attempt {attempt + 1}/{max_retries})...")
                time.sleep(retry_after + 1)
                continue

            # If HTML parsing or malformed entities / URLs failed, strip tags and send as clean plaintext
            if status_code == 400 or "parse" in description.lower() or "url" in description.lower():
                print(f"[Telegram Warning] Entity/URL error: '{description}'. Retrying as clean plaintext...")
                clean_text = re.sub(r"<[^>]+>", "", chunk)
                plain_payload = {
                    "chat_id": chat_id,
                    "text": clean_text,
                    "disable_web_page_preview": True,
                }
                retry_res = requests.post(url, json=plain_payload, timeout=25)
                retry_data = retry_res.json()
                if retry_data.get("ok"):
                    return True
                print(f"[Telegram Plaintext Retry Failed] {retry_data.get('description')}")

            print(f"[Telegram Error] HTTP {status_code}: {description}")
            time.sleep(1.5)
        except Exception as exc:
            print(f"[Telegram Exception] {exc} (attempt {attempt + 1}/{max_retries})")
            time.sleep(2.0)

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
    raw_url = (item_url or "").strip()

    if raw_url.startswith("http://") or raw_url.startswith("https://"):
        safe_url = html.escape(raw_url)
        url_line = f"👉 <a href='{safe_url}'>اضغط هنا لفتح العنصر مباشرة</a>"
    else:
        url_line = "👉 <i>العنصر متاح على صفحة المساق في اليوبينار</i>"

    return (
        f"🔔 <b>تنبيه يوبينار جديد! (UPINAR Alert)</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📚 <b>المساق:</b> {safe_course}\n"
        f"{type_emoji}\n"
        f"📌 <b>العنوان:</b> {safe_title}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"{url_line}"
    )
