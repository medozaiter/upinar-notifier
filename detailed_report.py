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

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")
SESSION_FILE = os.path.join(BASE_DIR, "session_cookies.json")

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
    'Accept-Language': 'ar,en-US;q=0.9,en;q=0.8',
}

def load_config() -> dict:
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def get_authenticated_session() -> requests.Session:
    """
    Returns an active session, performing SSO login if needed.
    """
    s = requests.Session()
    s.headers.update(HEADERS)
    
    # Try saved cookies
    if os.path.exists(SESSION_FILE):
        try:
            with open(SESSION_FILE, "r", encoding="utf-8") as f:
                s.cookies.update(json.load(f))
            r = s.get("https://upinar.edu.ps/my/", timeout=40)
            if "login" not in r.url.lower() and ("sesskey" in r.text or "user-menu" in r.text):
                print("[Auth] Using valid saved session.")
                return s
        except:
            pass

    # Fresh login
    print("[Auth] Performing fresh login via Portal SSO...")
    cfg = load_config()
    from upinar_engine import login_flow
    login_flow(s, cfg["student_id"], cfg["password"])
    return s

def generate_full_inventory():
    cfg = load_config()
    bot_token = cfg["telegram_bot_token"]
    chat_id = cfg["telegram_chat_id"]

    s = get_authenticated_session()

    print("[Report] Scanning dashboard for courses...")
    r_my = s.get("https://upinar.edu.ps/my/", timeout=45)
    soup_my = BeautifulSoup(r_my.text, 'html.parser')

    # Find all course links
    found_cids = set()
    courses_to_scan = []

    for a in soup_my.find_all('a', href=re.compile(r'/course/view\.php\?id=\d+')):
        m = re.search(r'id=(\d+)', a['href'])
        if not m:
            continue
        cid = m.group(1)
        if cid == "1" or cid in found_cids:
            continue
        found_cids.add(cid)
        courses_to_scan.append(cid)

    print(f"[Report] Found {len(courses_to_scan)} course IDs to inspect: {courses_to_scan}")

    # Intro message to Telegram
    send_telegram_message(
        bot_token, chat_id,
        f"📊 <b>تقرير الجرد الشامل لمساقات اليوبينار</b>\n"
        f"🎓 جامعة فلسطين - UPINAR\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"جاري إرسال تفاصيل المحاضرات والواجبات والشباتر لكل مساق..."
    )

    for cid in sorted(courses_to_scan):
        course_url = f"https://upinar.edu.ps/course/view.php?id={cid}"
        print(f"[Report] Inspecting course ID {cid}...")
        try:
            r = s.get(course_url, timeout=45)
            soup = BeautifulSoup(r.text, 'html.parser')

            # Real Course Title
            h1 = soup.find('h1')
            course_title = h1.get_text(strip=True) if h1 else f"Course #{cid}"

            # If title is just frontpage or generic, check breadcrumb
            breadcrumb = soup.find('nav', {'aria-label': re.compile(r'(breadcrumb|navigation)', re.I)})
            if breadcrumb:
                crumbs = [c.get_text(strip=True) for c in breadcrumb.find_all('li')]
                if len(crumbs) >= 3:
                    course_title = crumbs[-1]

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

                # Clean Title
                instance = a.find(class_=re.compile(r'(instancename|activityname)'))
                title_text = instance.get_text(strip=True) if instance else a.get_text(strip=True)
                title_text = re.sub(r'^(Assignment|File|Folder|Quiz|URL|Forum)\s*', '', title_text, flags=re.IGNORECASE).strip()

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

            # Skip courses that have 0 materials (empty containers)
            if not assignments and not quizzes and not files_chapters and not recordings_urls:
                continue

            # Build Telegram Message for this course
            msg = (
                f"📚 <b>{course_title}</b>\n"
                f"🔗 <a href='{course_url}'>رابط صفحة المساق</a>\n"
                f"━━━━━━━━━━━━━━━━━━\n"
            )

            # 1. Assignments
            if assignments:
                msg += "📝 <b>الواجبات والتاسكات (Assignments):</b>\n"
                for it in assignments:
                    msg += f"• <a href='{it['url']}'>{it['title']}</a>\n"
                msg += "\n"

            # 2. Quizzes
            if quizzes:
                msg += "⏱️ <b>الكويزات والاختبارات (Quizzes):</b>\n"
                for it in quizzes:
                    msg += f"• <a href='{it['url']}'>{it['title']}</a>\n"
                msg += "\n"

            # 3. Chapters & Files
            if files_chapters:
                msg += f"📄 <b>المحاضرات والشباتر والملفات ({len(files_chapters)}):</b>\n"
                for it in files_chapters[:12]:  # limit if too long
                    msg += f"• <a href='{it['url']}'>{it['title']}</a>\n"
                if len(files_chapters) > 12:
                    msg += f"<i>...والمزيد في صفحة المساق ({len(files_chapters) - 12} ملف إضافي)</i>\n"
                msg += "\n"

            # 4. Recordings & Links
            if recordings_urls:
                msg += f"🎥 <b>تسجيلات وروابط المحاضرات ({len(recordings_urls)}):</b>\n"
                for it in recordings_urls[:6]:
                    msg += f"• <a href='{it['url']}'>{it['title']}</a>\n"
                if len(recordings_urls) > 6:
                    msg += f"<i>...والمزيد في صفحة المساق</i>\n"
                msg += "\n"

            print(f"[Report] Sending report for: {course_title}")
            send_telegram_message(bot_token, chat_id, msg)
            import time
            time.sleep(1.5)

        except Exception as e:
            print(f"[Report Error] Course ID {cid}: {e}")

    print("\n✅ [Report] Finished sending full detailed inventory to Telegram!")

if __name__ == "__main__":
    generate_full_inventory()
