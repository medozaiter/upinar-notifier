import json
import os
import sys
import re
import urllib.parse
from typing import Dict, List

# Fix for Windows IPv6 DNS timeout
import socket
import urllib3.util.connection as urllib_conn
urllib_conn.allowed_gai_family = lambda: socket.AF_INET

import requests
from bs4 import BeautifulSoup

# Ensure UTF-8 output on Windows console
if sys.stdout:
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except:
        pass

from telegram_notifier import send_telegram_message
from upinar_engine import get_enrolled_courses, create_robust_session, login_flow, is_logged_in_upinar, load_session

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")

def load_config() -> dict:
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def get_authenticated_session() -> requests.Session:
    s = create_robust_session()
    if load_session(s) and is_logged_in_upinar(s):
        print("[Auth] Using valid saved session.")
        return s

    print("[Auth] Performing fresh login via Portal SSO...")
    cfg = load_config()
    login_flow(s, cfg["student_id"], cfg["password"])
    return s

def generate_full_inventory():
    cfg = load_config()
    bot_token = cfg["telegram_bot_token"]
    chat_id = cfg["telegram_chat_id"]

    s = get_authenticated_session()

    courses = get_enrolled_courses(s)
    if not courses:
        print("[Report Notice] No courses found.")
        return

    # Intro message to Telegram
    send_telegram_message(
        bot_token, chat_id,
        f"📊 <b>تقرير الجرد الشامل لمساقات اليوبينار</b>\n"
        f"🎓 جامعة فلسطين - UPINAR\n"
        f"📚 تم العثور على <b>{len(courses)} مساقات نشطة</b> في حسابك\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"جاري إرسال تفاصيل المحاضرات والواجبات والشباتر لكل مساق..."
    )

    for course in courses:
        cid = course["id"]
        course_url = course["url"]
        course_title = course["title"]
        print(f"[Report] Inspecting course: {course_title} (ID: {cid})...")
        try:
            r = s.get(course_url, timeout=45)
            soup = BeautifulSoup(r.text, 'html.parser')

            assignments = []
            quizzes = []
            files_chapters = []
            recordings_urls = []
            forums = []

            seen_ids = set()
            for a in soup.find_all('a', href=re.compile(r'/mod/([a-zA-Z0-9_]+)/view\.php\?id=\d+')):
                href = a['href']
                m = re.search(r'/mod/([a-zA-Z0-9_]+)/view\.php\?id=(\d+)', href)
                if not m:
                    continue
                mtype, mid = m.group(1).lower(), m.group(2)
                if mid in seen_ids:
                    continue
                seen_ids.add(mid)

                for hide in a.find_all(class_=re.compile(r'(accesshide|sr-only|badge)')):
                    hide.decompose()

                instance = a.find(class_=re.compile(r'(instancename|activityname)'))
                title_text = instance.get_text(strip=True) if instance else a.get_text(strip=True)
                title_text = re.sub(r'^(Assignment|File|Folder|Quiz|URL|Forum)\s*', '', title_text, flags=re.IGNORECASE)
                title_text = re.sub(r'(File|URL|Assignment|Quiz|Forum)$', '', title_text, flags=re.IGNORECASE).strip()

                if not title_text:
                    continue

                item_obj = {"title": title_text, "url": f"https://upinar.edu.ps/mod/{mtype}/view.php?id={mid}"}

                if mtype == "assign":
                    assignments.append(item_obj)
                elif mtype == "quiz":
                    quizzes.append(item_obj)
                elif mtype in ["resource", "folder", "book"]:
                    files_chapters.append(item_obj)
                elif mtype == "url":
                    recordings_urls.append(item_obj)
                elif mtype == "forum":
                    forums.append(item_obj)

            # Skip empty courses if they have 0 items
            if not assignments and not quizzes and not files_chapters and not recordings_urls:
                continue

            msg = (
                f"📚 <b>{course_title}</b>\n"
                f"🔗 <a href='{course_url}'>رابط صفحة المساق</a>\n"
                f"━━━━━━━━━━━━━━━━━━\n"
            )

            if assignments:
                msg += "📝 <b>الواجبات والتاسكات (Assignments):</b>\n"
                for it in assignments:
                    msg += f"• <a href='{it['url']}'>{it['title']}</a>\n"
                msg += "\n"

            if quizzes:
                msg += "⏱️ <b>الكويزات والاختبارات (Quizzes):</b>\n"
                for it in quizzes:
                    msg += f"• <a href='{it['url']}'>{it['title']}</a>\n"
                msg += "\n"

            if files_chapters:
                msg += f"📄 <b>المحاضرات والشباتر والملفات ({len(files_chapters)}):</b>\n"
                for it in files_chapters[:12]:
                    msg += f"• <a href='{it['url']}'>{it['title']}</a>\n"
                if len(files_chapters) > 12:
                    msg += f"<i>...والمزيد في صفحة المساق ({len(files_chapters) - 12} ملف إضافي)</i>\n"
                msg += "\n"

            if recordings_urls:
                msg += f"🎥 <b>تسجيلات وروابط المحاضرات ({len(recordings_urls)}):</b>\n"
                for it in recordings_urls[:6]:
                    msg += f"• <a href='{it['url']}'>{it['title']}</a>\n"
                if len(recordings_urls) > 6:
                    msg += f"<i>...والمزيد في صفحة المساق</i>\n"
                msg += "\n"

            print(f"[Report] Sending report for: {course_title}")
            send_telegram_message(bot_token, chat_id, msg)
            time.sleep(1.2)

        except Exception as e:
            print(f"[Report Error] Course ID {cid}: {e}")

    print("\n✅ [Report] Finished sending full detailed inventory to Telegram!")

if __name__ == "__main__":
    generate_full_inventory()
