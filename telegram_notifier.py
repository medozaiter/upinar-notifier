import json
import os
import subprocess
import requests

def send_telegram_message(bot_token: str, chat_id: str, text: str) -> bool:
    """
    Sends a formatted message to Telegram using curl.exe for maximum reliability on Windows.
    """
    if not bot_token or not chat_id:
        print("[Telegram] Bot token or chat_id is missing.")
        return False

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    
    # Try via curl.exe first (bypasses Windows Python IPv6 DNS bugs)
    try:
        cmd = [
            "curl.exe", "-s", "-X", "POST", url,
            "-d", f"chat_id={chat_id}",
            "--data-urlencode", f"text={text}",
            "-d", "parse_mode=HTML",
            "-d", "disable_web_page_preview=false"
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        if '"ok":true' in result.stdout:
            return True
        else:
            print(f"[Telegram Notice] curl response: {result.stdout}")
    except Exception as e:
        print(f"[Telegram Notice] curl failed, trying requests: {e}")

    # Fallback to requests
    try:
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": False
        }
        r = requests.post(url, json=payload, timeout=15)
        return r.json().get("ok", False)
    except Exception as e:
        print(f"[Telegram Error] {e}")
        return False

def format_item_alert(course_name: str, item_type: str, item_title: str, item_url: str) -> str:
    """
    Formats an alert message for a newly discovered item on UPINAR.
    """
    type_emoji = {
        "assign": "📝 <b>واجب جديد (Assignment)</b>",
        "resource": "📄 <b>ملف / محاضرة جديدة (File)</b>",
        "folder": "📁 <b>مجلد جديد (Folder)</b>",
        "forum": "📢 <b>إعلان / منتدى جديد (Announcement)</b>",
        "quiz": "⏱️ <b>كويز / اختبار جديد (Quiz)</b>",
        "url": "🔗 <b>رابط جديد (Link)</b>",
    }.get(item_type, f"📌 <b>عنصر جديد ({item_type})</b>")

    message = (
        f"🔔 <b>تنبيه يوبينار جديد! (UPINAR Alert)</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📚 <b>المساق:</b> {course_name}\n"
        f"{type_emoji}\n"
        f"📌 <b>العنوان:</b> {item_title}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"👉 <a href='{item_url}'>اضغط هنا لفتح العنصر مباشرة</a>"
    )
    return message
