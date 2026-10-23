import json
import os
import sys
import time
import re
import urllib.parse
from typing import Dict, List, Set

# Fix for Windows IPv6 DNS timeout
import socket
import urllib3.util.connection as urllib_conn
urllib_conn.allowed_gai_family = lambda: socket.AF_INET

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from bs4 import BeautifulSoup

# Ensure UTF-8 output on Windows console
if sys.stdout:
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except:
        pass

from telegram_notifier import send_telegram_message, format_item_alert
from storage import load_seen_items, save_seen_items

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")
SESSION_FILE = os.path.join(BASE_DIR, "session_cookies.json")

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
    'Accept-Language': 'ar,en-US;q=0.9,en;q=0.8',
}

def create_robust_session() -> requests.Session:
    """
    Creates a requests session with automatic retries for unstable connections.
    """
    s = requests.Session()
    s.headers.update(HEADERS)
    retries = Retry(
        total=3,
        backoff_factor=1,
        status_forcelist=[500, 502, 503, 504],
        raise_on_status=False
    )
    adapter = HTTPAdapter(max_retries=retries)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    return s

def load_config() -> dict:
    if not os.path.exists(CONFIG_FILE):
        print(f"[Error] Config file not found at {CONFIG_FILE}")
        sys.exit(1)
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_session(session: requests.Session):
    try:
        with open(SESSION_FILE, "w", encoding="utf-8") as f:
            cookies = requests.utils.dict_from_cookiejar(session.cookies)
            json.dump(cookies, f, indent=2)
    except Exception as e:
        print(f"[Warning] Could not save session cookies: {e}")

def load_session(session: requests.Session) -> bool:
    if not os.path.exists(SESSION_FILE):
        return False
    try:
        with open(SESSION_FILE, "r", encoding="utf-8") as f:
            cookies = json.load(f)
            session.cookies.update(cookies)
        return True
    except Exception:
        return False

def is_logged_in_upinar(session: requests.Session) -> bool:
    try:
        r = session.get("https://upinar.edu.ps/my/", timeout=60, allow_redirects=True)
        if "login" in r.url.lower():
            return False
        if "sesskey" in r.text or "logout.php" in r.text or "user-menu" in r.text or "my-courses" in r.text:
            return True
        return False
    except Exception:
        return False

def login_flow(session: requests.Session, student_id: str, password: str) -> bool:
    """
    1. Authenticate to https://students.up.edu.ps/login via Inertia.js API
    2. Obtain dynamic Moodle OAuth2 login link with sesskey from UPINAR
    3. Trigger OAuth2 SSO redirect
    """
    print("[1/3] Connecting to Student Portal (students.up.edu.ps)...")
    try:
        r_get = session.get("https://students.up.edu.ps/login", timeout=60)
        if r_get.status_code != 200:
            print(f"[Error] Failed to load portal login page (HTTP {r_get.status_code})")
            return False

        match_version = re.search(r'version["\']\s*:\s*["\']([a-zA-Z0-9]+)["\']', r_get.text)
        inertia_version = match_version.group(1) if match_version else ""

        xsrf_cookie = session.cookies.get("XSRF-TOKEN", "")
        xsrf_token = urllib.parse.unquote(xsrf_cookie) if xsrf_cookie else ""

        post_headers = {
            'X-Inertia': 'true',
            'X-Inertia-Version': inertia_version,
            'X-XSRF-TOKEN': xsrf_token,
            'Content-Type': 'application/json',
            'Referer': 'https://students.up.edu.ps/login',
            'Origin': 'https://students.up.edu.ps',
        }

        payload = {
            "name": str(student_id).strip(),
            "password": str(password).strip(),
            "remember": "on"
        }

        print("[2/3] Authenticating student credentials...")
        r_post = session.post(
            "https://students.up.edu.ps/login",
            json=payload,
            headers=post_headers,
            timeout=60,
            allow_redirects=False
        )

        if r_post.status_code not in [200, 302, 409]:
            print(f"[Error] Portal login returned HTTP {r_post.status_code}")
            return False

        print(f"[OK] Portal login successful! (HTTP {r_post.status_code})")

        # Step 3: Fetch UPINAR login page to get the valid OAuth2 link with sesskey
        print("[3/3] Synchronizing with UPINAR (SSO)...")
        r_moodle_login = session.get("https://upinar.edu.ps/login/index.php", timeout=60)
        soup = BeautifulSoup(r_moodle_login.text, 'html.parser')

        oauth_link = None
        for a in soup.find_all('a', href=True):
            if "auth/oauth2" in a['href']:
                oauth_link = a['href']
                break

        if not oauth_link:
            m = re.search(r'https?://upinar\.edu\.ps/auth/oauth2/login\.php\?[^"\']+', r_moodle_login.text)
            if m:
                oauth_link = m.group(0)

        if not oauth_link:
            oauth_link = "https://upinar.edu.ps/auth/oauth2/login.php?id=1&wantsurl=https%3A%2F%2Fupinar.edu.ps%2F"

        # Execute SSO redirect
        r_sso = session.get(oauth_link, timeout=75, allow_redirects=True)

        if is_logged_in_upinar(session) or "login" not in r_sso.url.lower():
            print("[SUCCESS] Fully logged into UPINAR via SSO!")
            save_session(session)
            return True
        else:
            print(f"[Error] Failed to complete UPINAR login. Final URL: {r_sso.url}")
            return False

    except Exception as e:
        print(f"[Exception in login_flow]: {e}")
        return False

def get_enrolled_courses(session: requests.Session) -> List[Dict[str, str]]:
    """
    Extracts all enrolled courses from UPINAR using Moodle's internal timeline service API
    with fallback to HTML link discovery.
    """
    print("[Courses] Scanning all enrolled courses via Moodle API...")
    courses = []
    found_cids = set()

    try:
        r_my = session.get("https://upinar.edu.ps/my/", timeout=40)
        sess_m = re.search(r'"sesskey":"([^"]+)"', r_my.text)
        sesskey = sess_m.group(1) if sess_m else ""

        if sesskey:
            service_url = f"https://upinar.edu.ps/lib/ajax/service.php?sesskey={sesskey}&info=core_course_get_enrolled_courses_by_timeline_classification"
            payload = [{
                "index": 0,
                "methodname": "core_course_get_enrolled_courses_by_timeline_classification",
                "args": {"offset": 0, "limit": 0, "classification": "all", "sort": "fullname"}
            }]
            r_svc = session.post(service_url, json=payload, timeout=30)
            svc_data = r_svc.json()
            if isinstance(svc_data, list) and len(svc_data) > 0 and not svc_data[0].get("error"):
                api_courses = svc_data[0].get("data", {}).get("courses", [])
                for c in api_courses:
                    cid = str(c["id"])
                    if cid == "1" or cid in found_cids:
                        continue
                    found_cids.add(cid)
                    courses.append({
                        "id": cid,
                        "title": c.get("fullname", f"Course #{cid}").strip(),
                        "url": f"https://upinar.edu.ps/course/view.php?id={cid}"
                    })
    except Exception as e:
        print(f"[Courses API Notice] {e}")

    # Fallback to HTML if API returned nothing
    if not courses:
        try:
            r = session.get("https://upinar.edu.ps/my/", timeout=40)
            soup = BeautifulSoup(r.text, 'html.parser')
            for a in soup.find_all('a', href=re.compile(r'/course/view\.php\?id=\d+')):
                m = re.search(r'id=(\d+)', a['href'])
                if not m:
                    continue
                cid = m.group(1)
                if cid == "1" or cid in found_cids:
                    continue
                found_cids.add(cid)
                courses.append({
                    "id": cid,
                    "title": f"Course #{cid}",
                    "url": f"https://upinar.edu.ps/course/view.php?id={cid}"
                })
        except:
            pass

    print(f"[Courses] Successfully found {len(courses)} enrolled courses across your semester.")
    for idx, c in enumerate(courses, 1):
        print(f"   {idx}. {c['title']} (ID: {c['id']})")
    return courses

def scan_course_content(session: requests.Session, course: Dict[str, str]) -> List[Dict[str, str]]:
    """
    Scrapes all activities, resources, assignments, etc. from a course page
    and resolves the official course name.
    """
    items = []
    course_url = course["url"]
    cid = course["id"]

    try:
        r = session.get(course_url, timeout=60)
        soup = BeautifulSoup(r.text, 'html.parser')

        # 1. Resolve Official Course Title directly from the course page header or breadcrumb
        official_title = ""
        h1 = soup.find('h1')
        if h1:
            official_title = h1.get_text(strip=True)

        breadcrumb = soup.find('nav', {'aria-label': re.compile(r'(breadcrumb|navigation)', re.I)})
        if breadcrumb:
            crumbs = [c.get_text(strip=True) for c in breadcrumb.find_all('li')]
            if len(crumbs) >= 2:
                candidate = crumbs[-1]
                if candidate and len(candidate) > len(official_title) and "تسجيل الدخول" not in candidate:
                    official_title = candidate

        if official_title and "تسجيل الدخول" not in official_title:
            course["title"] = official_title
        else:
            official_title = course["title"]

        # 2. Extract Activities
        links = soup.find_all('a', href=re.compile(r'/mod/([a-zA-Z0-9_]+)/view\.php\?id=(\d+)'))
        seen_keys = set()

        for a in links:
            href = a.get('href', '')
            match = re.search(r'/mod/([a-zA-Z0-9_]+)/view\.php\?id=(\d+)', href)
            if not match:
                continue

            mod_type = match.group(1).lower()
            mod_id = match.group(2)
            unique_key = f"{cid}_{mod_type}_{mod_id}"

            if unique_key in seen_keys:
                continue
            seen_keys.add(unique_key)

            # Clean screen-reader hidden tags (<span class="accesshide">File</span>)
            for hide in a.find_all(class_=re.compile(r'(accesshide|sr-only|badge)')):
                hide.decompose()

            instance = a.find(class_=re.compile(r'(instancename|activityname)'))
            if instance:
                title_text = instance.get_text(strip=True)
            else:
                title_text = a.get_text(strip=True)

            # Clean any trailing or leading type names
            title_text = re.sub(r'^(Assignment|File|Folder|Quiz|URL|Forum)\s*', '', title_text, flags=re.IGNORECASE)
            title_text = re.sub(r'(File|URL|Assignment|Quiz|Forum)$', '', title_text, flags=re.IGNORECASE)
            title_text = re.sub(r'\s+', ' ', title_text).strip()

            if not title_text:
                title_text = f"{mod_type.capitalize()} #{mod_id}"

            items.append({
                "unique_key": unique_key,
                "course_name": official_title,
                "mod_type": mod_type,
                "mod_id": mod_id,
                "title": title_text,
                "url": f"https://upinar.edu.ps/mod/{mod_type}/view.php?id={mod_id}"
            })

    except Exception as e:
        print(f"[Scan Error] Course ID {cid}: {e}")

    return items

def check_upinar_now(is_first_run: bool = False):
    config = load_config()
    student_id = config.get("student_id")
    password = config.get("password")
    bot_token = config.get("telegram_bot_token")
    chat_id = config.get("telegram_chat_id")

    if not password or password == "YOUR_PORTAL_PASSWORD":
        print("[Notice] Please set your credentials in config.json.")
        return

    session = create_robust_session()

    logged_in = False
    if load_session(session):
        print("[Auth] Checking previous session validity...")
        if is_logged_in_upinar(session):
            print("[Auth] Previous session is valid! Skipping login.")
            logged_in = True

    if not logged_in:
        print("[Auth] Performing fresh SSO login...")
        success = login_flow(session, student_id, password)
        if not success:
            print("[Error] Login flow failed. Will retry on next interval.")
            return

    courses = get_enrolled_courses(session)
    if not courses:
        print("[Notice] No courses detected. Session might need refresh next run.")
        return

    seen_items = load_seen_items()
    first_time = (len(seen_items) == 0) or is_first_run

    all_scanned_keys = set()
    new_items = []

    for course in courses:
        items = scan_course_content(session, course)
        print(f"   -> Course: {course['title']} ({len(items)} items)")
        for it in items:
            all_scanned_keys.add(it["unique_key"])
            if it["unique_key"] not in seen_items:
                new_items.append(it)

    if first_time:
        print(f"\n[Baseline Scan] Indexed {len(all_scanned_keys)} items across {len(courses)} courses!")
        save_seen_items(all_scanned_keys)

        initial_msg = (
            f"✅ <b>تم تفعيل نظام متابعة اليوبينار بنجاح!</b>\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"🎓 <b>جامعة فلسطين - UPINAR</b>\n"
            f"📚 <b>عدد المساقات المفهرسة:</b> {len(courses)} مساقاً مسجلاً\n"
            f"📦 <b>إجمالي المحتويات الحالية:</b> {len(all_scanned_keys)} ملف وواجب تم حصرهم\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"🚀 <i>النظام الآن يراقب جميع مساقاتك الـ {len(courses)} بدقة، وسيصلك إشعار فوري فور إضافة أي واجب أو محاضرة جديدة!</i>"
        )
        send_telegram_message(bot_token, chat_id, initial_msg)
        return

    if new_items:
        print(f"\n[ALERT] Found {len(new_items)} new items!")
        for it in new_items:
            print(f"--> Sending notification: {it['course_name']} | {it['title']}")
            msg = format_item_alert(
                course_name=it["course_name"],
                item_type=it["mod_type"],
                item_title=it["title"],
                item_url=it["url"]
            )
            send_telegram_message(bot_token, chat_id, msg)
            seen_items.add(it["unique_key"])
            time.sleep(1)

        save_seen_items(seen_items)
    else:
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] [OK] Everything is up to date. No new items.")

def start_daemon():
    config = load_config()
    interval = config.get("check_interval_minutes", 30)

    print("=" * 60)
    print("      UPINAR Notifier - University of Palestine")
    print(f"      Monitoring interval: Every {interval} minutes")
    print("=" * 60)
    print("Press Ctrl+C to stop at any time.\n")

    while True:
        try:
            check_upinar_now()
        except Exception as e:
            print(f"[Error in checking cycle]: {e}")

        print(f"\nSleeping for {interval} minutes until next check...\n")
        time.sleep(interval * 60)

if __name__ == "__main__":
    if "--once" in sys.argv:
        check_upinar_now()
    else:
        start_daemon()
