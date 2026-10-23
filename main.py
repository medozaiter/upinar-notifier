import json
import os
import sys
import time
import re
from typing import Dict, List, Set, Tuple
from playwright.sync_api import sync_playwright, Page, BrowserContext

from telegram_notifier import send_telegram_message, format_item_alert
from storage import load_seen_items, save_seen_items

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")
STORAGE_STATE_FILE = os.path.join(BASE_DIR, "auth_state.json")

def load_config() -> dict:
    if not os.path.exists(CONFIG_FILE):
        print(f"[Error] Config file not found at {CONFIG_FILE}")
        sys.exit(1)
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def is_logged_into_upinar(page: Page) -> bool:
    """
    Checks if the current page in UPINAR is authenticated.
    """
    try:
        page.goto("https://upinar.edu.ps/my/", timeout=30000, wait_until="domcontentloaded")
        time.sleep(2)
        # If redirected to login page, not logged in
        if "login" in page.url.lower():
            return False
        # If user-menu or my courses is present
        if page.locator(".usermenu, #page-my-index, .my-courses, nav").count() > 0:
            return True
        return False
    except Exception as e:
        print(f"[Check Auth] Exception: {e}")
        return False

def login_student_portal(context: BrowserContext, config: dict) -> bool:
    """
    Performs login to the University of Palestine student portal,
    then logs into UPINAR via SSO.
    """
    page = context.new_page()
    print("[Login] 1. Opening student portal (https://students.up.edu.ps/login)...")
    try:
        page.goto("https://students.up.edu.ps/login", timeout=45000, wait_until="networkidle")
        time.sleep(2)
        
        # Fill Student ID (look for text or number inputs)
        student_id_input = page.locator('input[type="text"], input[type="number"], input[name="username"], input[placeholder*="الجامعي"]').first
        student_id_input.fill(str(config.get("student_id", "")))
        
        # Fill Password
        password_input = page.locator('input[type="password"]').first
        password_input.fill(str(config.get("password", "")))
        
        # Click login button
        login_btn = page.locator('button:has-text("تسجيل الدخول"), input[type="submit"]').first
        login_btn.click()
        
        # Wait for navigation to dashboard
        print("[Login] Waiting for portal dashboard...")
        page.wait_for_url("**/dashboard**", timeout=30000)
        time.sleep(2)
        print("[Login] ✅ Successfully logged in to student portal.")

        # Step 2: Now navigate to UPINAR OAuth2 or UPINAR login
        print("[Login] 2. Connecting to UPINAR via بوابة الطالب...")
        page.goto("https://upinar.edu.ps/login/index.php", timeout=30000, wait_until="domcontentloaded")
        time.sleep(2)

        # Click the "Log in with بوابة الطالب" button
        portal_sso_btn = page.locator('text="Log in with بوابة الطالب", a:has-text("بوابة الطالب"), a[href*="auth/oauth2"]').first
        if portal_sso_btn.count() > 0:
            portal_sso_btn.click()
        else:
            # Fallback direct OAuth URL for id=1
            page.goto("https://upinar.edu.ps/auth/oauth2/login.php?id=1&wantsurl=https%3A%2F%2Fupinar.edu.ps%2F", timeout=30000)

        # Wait for UPINAR dashboard / my page
        time.sleep(4)
        print(f"[Login] Current URL after SSO: {page.url}")

        # Save session cookies
        context.storage_state(path=STORAGE_STATE_FILE)
        print("[Login] ✅ Authentication session saved successfully.")
        page.close()
        return True

    except Exception as e:
        print(f"[Login Error] Failed during login flow: {e}")
        try:
            page.screenshot(path=os.path.join(BASE_DIR, "login_error.png"))
            print(f"[Debug] Screenshot saved to login_error.png")
        except:
            pass
        page.close()
        return False

def get_enrolled_courses(page: Page) -> List[Dict[str, str]]:
    """
    Navigates to my courses page and extracts course links and names.
    """
    courses = []
    print("[Courses] Fetching enrolled courses...")
    try:
        page.goto("https://upinar.edu.ps/my/courses.php", timeout=30000, wait_until="domcontentloaded")
        time.sleep(3)
        
        # Also try https://upinar.edu.ps/my/ if courses.php is not direct
        if "courses.php" not in page.url and page.locator('a[href*="/course/view.php?id="]').count() == 0:
            page.goto("https://upinar.edu.ps/my/", timeout=30000, wait_until="domcontentloaded")
            time.sleep(3)

        # Find all course links
        course_elements = page.locator('a[href*="/course/view.php?id="]').all()
        seen_urls = set()

        for el in course_elements:
            url = el.get_attribute("href")
            if not url or url in seen_urls:
                continue
            
            # Clean URL to standard form: https://upinar.edu.ps/course/view.php?id=XYZ
            match = re.search(r'course/view\.php\?id=(\d+)', url)
            if not match:
                continue
            
            clean_url = f"https://upinar.edu.ps/course/view.php?id={match.group(1)}"
            if clean_url in seen_urls:
                continue

            # Extract title
            title = el.inner_text().strip()
            # If empty, try child text or aria-label
            if not title:
                title = el.get_attribute("aria-label") or el.get_attribute("title") or ""
            title = re.sub(r'\s+', ' ', title).strip()

            if title and len(title) > 2:
                seen_urls.add(clean_url)
                courses.append({
                    "id": match.group(1),
                    "title": title,
                    "url": clean_url
                })

        print(f"[Courses] Found {len(courses)} enrolled courses.")
    except Exception as e:
        print(f"[Courses Error] {e}")

    return courses

def scan_course_content(page: Page, course: Dict[str, str]) -> List[Dict[str, str]]:
    """
    Scans a single course page for activities, files, assignments, etc.
    """
    items = []
    course_url = course["url"]
    course_title = course["title"]
    
    try:
        page.goto(course_url, timeout=30000, wait_until="domcontentloaded")
        time.sleep(2)

        # Moodle activities pattern: /mod/{module_name}/view.php?id={id}
        # Common modules: assign, resource, folder, quiz, forum, url, page, book
        link_elements = page.locator('a[href*="/mod/"]').all()
        
        seen_in_course = set()
        for el in link_elements:
            href = el.get_attribute("href")
            if not href:
                continue
            
            # Match module type and cmid (course module id)
            mod_match = re.search(r'/mod/([a-zA-Z0-9_]+)/view\.php\?id=(\d+)', href)
            if not mod_match:
                continue

            mod_type = mod_match.group(1).lower()
            mod_id = mod_match.group(2)
            unique_key = f"{course['id']}_{mod_type}_{mod_id}"

            if unique_key in seen_in_course:
                continue
            seen_in_course.add(unique_key)

            # Get clean title
            # In Moodle 4, instancename or activityname contains the title
            title_text = ""
            try:
                instance_el = el.locator('.instancename, .activityname').first
                if instance_el.count() > 0:
                    title_text = instance_el.inner_text().strip()
            except:
                pass

            if not title_text:
                title_text = el.inner_text().strip()
            
            # Clean up text (remove Moodle badges like "File", "Assignment", etc.)
            title_text = re.sub(r'^(Assignment|File|Folder|Quiz|URL|Forum)\s*', '', title_text, flags=re.IGNORECASE)
            title_text = re.sub(r'\s+', ' ', title_text).strip()

            if not title_text:
                title_text = f"{mod_type.capitalize()} #{mod_id}"

            items.append({
                "unique_key": unique_key,
                "course_name": course_title,
                "mod_type": mod_type,
                "mod_id": mod_id,
                "title": title_text,
                "url": f"https://upinar.edu.ps/mod/{mod_type}/view.php?id={mod_id}"
            })

    except Exception as e:
        print(f"[Scan Error] Course {course_title}: {e}")

    return items

def run_check(is_first_run: bool = False):
    """
    Main checking routine.
    """
    config = load_config()
    seen_items = load_seen_items()
    first_time = (len(seen_items) == 0) or is_first_run

    bot_token = config.get("telegram_bot_token", "")
    chat_id = config.get("telegram_chat_id", "")
    headless = config.get("headless", True)

    with sync_playwright() as p:
        # Launch browser
        browser = p.chromium.launch(headless=headless)
        
        # Load storage state if exists
        context_kwargs = {}
        if os.path.exists(STORAGE_STATE_FILE):
            context_kwargs["storage_state"] = STORAGE_STATE_FILE

        context = browser.new_context(**context_kwargs)
        page = context.new_page()

        # Check authentication
        print("[UPINAR] Verifying session...")
        if not is_logged_into_upinar(page):
            print("[UPINAR] Session expired or not logged in. Re-authenticating...")
            page.close()
            success = login_student_portal(context, config)
            if not success:
                print("[UPINAR Error] Authentication failed. Aborting check.")
                browser.close()
                return
            page = context.new_page()

        # 1. Fetch courses
        courses = get_enrolled_courses(page)
        if not courses:
            print("[UPINAR Warning] No courses found. Please check selectors or page structure.")
            browser.close()
            return

        all_scanned_keys = set()
        new_items_found = []

        # 2. Scan each course
        for course in courses:
            print(f"[Scan] Checking course: {course['title']}...")
            course_items = scan_course_content(page, course)
            
            for item in course_items:
                all_scanned_keys.add(item["unique_key"])
                if item["unique_key"] not in seen_items:
                    new_items_found.append(item)

        browser.close()

        # 3. Handle results
        if first_time:
            print(f"\n[Inventory] Initial baseline scan completed!")
            print(f"[Inventory] Indexed {len(all_scanned_keys)} existing items across {len(courses)} courses.")
            save_seen_items(all_scanned_keys)
            
            # Send initial confirmation message via Telegram
            msg = (
                f"✅ <b>تم تفعيل نظام متابعة اليوبينار بنجاح!</b>\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"📚 <b>عدد المساقات المسجلة:</b> {len(courses)}\n"
                f"📦 <b>إجمالي المحتويات الحالية:</b> {len(all_scanned_keys)} ملف وواجب\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"🚀 <i>النظام الآن يراقب المنصة، وسيصلك إشعار فوري عند إضافة أي محاضرة، سلايد، أو واجب جديد.</i>"
            )
            send_telegram_message(bot_token, chat_id, msg)
            return

        # Subsequent runs
        if new_items_found:
            print(f"\n🔥 [ALERT] Found {len(new_items_found)} new items!")
            for item in new_items_found:
                alert_text = format_item_alert(
                    course_name=item["course_name"],
                    item_type=item["mod_type"],
                    item_title=item["title"],
                    item_url=item["url"]
                )
                print(f"--> Notifying: {item['course_name']} - {item['title']}")
                send_telegram_message(bot_token, chat_id, alert_text)
                seen_items.add(item["unique_key"])
                time.sleep(1) # slight delay between messages
            
            save_seen_items(seen_items)
        else:
            print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] ✅ No new items. Everything is up to date.")

def main():
    config = load_config()
    interval_minutes = config.get("check_interval_minutes", 30)

    # Check command-line arguments
    if "--once" in sys.argv:
        print("[Runner] Running single check...")
        run_check()
        return

    print(f"🚀 UPINAR Monitor started! Checking every {interval_minutes} minutes. (Press Ctrl+C to stop)")
    while True:
        try:
            run_check()
        except Exception as e:
            print(f"[Error in loop] {e}")
        
        print(f"\nSleeping for {interval_minutes} minutes...\n")
        time.sleep(interval_minutes * 60)

if __name__ == "__main__":
    main()
