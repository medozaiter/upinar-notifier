import json
import re
import urllib.parse
from typing import Optional, Tuple

import requests
from bs4 import BeautifulSoup

from .models import SessionStore, StudentAccount
from .net import create_robust_session
from .storage import FileSessionStore

PORTAL_LOGIN = "https://students.up.edu.ps/login"
PORTAL_DASHBOARD = "https://students.up.edu.ps/dashboard"
UPINAR_MY = "https://upinar.edu.ps/my/"
UPINAR_LOGIN = "https://upinar.edu.ps/login/index.php"

CHROME_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "ar,en-US;q=0.9,en;q=0.8",
}


class MoodleAuth:
    def __init__(self, session_store: Optional[SessionStore] = None):
        self.session_store = session_store or FileSessionStore()
        self.last_error: str = ""

    def login_with_account(self, account: StudentAccount) -> bool:
        session = create_robust_session()
        ok, error_msg = self.login_with_feedback(session, account)
        self.last_error = error_msg
        return ok

    def get_session(self, account: StudentAccount) -> Optional[requests.Session]:
        session = create_robust_session()
        cookies = self.session_store.load(account.student_id)
        if cookies:
            session.cookies.update(cookies)
            print(f"[Auth] Checking previous session validity for {account.student_id}...")
            if self.is_logged_in(session):
                print("[Auth] Previous session is valid! Skipping login.")
                return session

        print(f"[Auth] Performing fresh login for {account.student_id}...")
        ok, _ = self.login_with_feedback(session, account)
        if ok:
            return session
        print(f"[Error] Login flow failed for {account.student_id}: {self.last_error}")
        return None

    def is_logged_in(self, session: requests.Session) -> bool:
        try:
            response = session.get(UPINAR_MY, timeout=30, allow_redirects=True)
            if "login" in response.url.lower():
                return False
            body = response.text
            return any(marker in body for marker in ("sesskey", "logout.php", "user-menu", "my-courses"))
        except Exception:
            return False

    def login(self, session: requests.Session, account: StudentAccount) -> bool:
        ok, _ = self.login_with_feedback(session, account)
        return ok

    def login_with_feedback(self, session: requests.Session, account: StudentAccount) -> Tuple[bool, str]:
        student_id = str(account.student_id).strip()
        password = str(account.password).strip()

        if not student_id or not password:
            return False, "الرقم الجامعي أو كلمة المرور فارغة."

        if len(student_id) > 32 or len(password) > 128:
            return False, "طول الرقم الجامعي أو كلمة المرور غير صالح."

        # Step Sequence:
        # 1. Login to https://students.up.edu.ps/login
        # 2. Land on https://students.up.edu.ps/dashboard
        # 3. Open https://upinar.edu.ps/login/index.php
        # 4. Follow "Log in with بوابة الطالب" SSO to https://upinar.edu.ps/my/
        print(f"[Auth 1/2] Logging into Student Portal for {student_id}...")
        ok_portal, portal_err = self._login_portal_and_sso(session, student_id, password)
        if ok_portal:
            self.session_store.save(
                account.student_id,
                requests.utils.dict_from_cookiejar(session.cookies),
            )
            return True, ""

        print(f"[Auth 1/2 Failed]: {portal_err}")

        # Fallback Method 2: Direct UPINAR Moodle form login
        print(f"[Auth 2/2] Attempting direct UPINAR Moodle login for {student_id}...")
        ok_moodle, moodle_err = self._login_via_direct_moodle(session, student_id, password)
        if ok_moodle:
            self.session_store.save(
                account.student_id,
                requests.utils.dict_from_cookiejar(session.cookies),
            )
            return True, ""

        print(f"[Auth 2/2 Failed]: {moodle_err}")

        final_err = portal_err or moodle_err or "الرقم الجامعي أو كلمة المرور غير صحيحة."
        return False, final_err

    def _login_portal_and_sso(self, session: requests.Session, student_id: str, password: str) -> Tuple[bool, str]:
        try:
            # 1. Fetch portal login page
            print("[Portal Step 1/4] GET https://students.up.edu.ps/login...")
            session.headers.update(CHROME_HEADERS)
            r_get = session.get(PORTAL_LOGIN, timeout=30)
            if r_get.status_code != 200:
                return False, f"تعذر تحميل صفحة بوابة الطالب (HTTP {r_get.status_code})"

            # Extract Inertia version and XSRF token
            inertia_version = ""
            soup = BeautifulSoup(r_get.text, "html.parser")
            app_elem = soup.find(attrs={"data-page": True})
            if app_elem and app_elem.get("data-page"):
                try:
                    data = json.loads(app_elem["data-page"])
                    inertia_version = data.get("version", "")
                except Exception:
                    pass

            if not inertia_version:
                match = re.search(r'(?:version|&quot;version&quot;)["\':\s]+([a-zA-Z0-9]+)', r_get.text)
                if match:
                    inertia_version = match.group(1)

            xsrf_cookie = session.cookies.get("XSRF-TOKEN", "")
            xsrf_token = urllib.parse.unquote(xsrf_cookie) if xsrf_cookie else ""

            post_headers = {
                "Accept": "text/html, application/xhtml+xml",
                "Content-Type": "application/json",
                "Origin": "https://students.up.edu.ps",
                "Referer": PORTAL_LOGIN,
                "Sec-Ch-Ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
                "Sec-Ch-Ua-Mobile": "?0",
                "Sec-Ch-Ua-Platform": '"Windows"',
                "Sec-Fetch-Dest": "empty",
                "Sec-Fetch-Mode": "cors",
                "Sec-Fetch-Site": "same-origin",
                "X-Inertia": "true",
                "X-Inertia-Version": inertia_version,
                "X-XSRF-TOKEN": xsrf_token,
            }
            payload = {
                "name": student_id,
                "password": password,
                "remember": "on",
            }

            # 2. Submit credentials to Portal
            print("[Portal Step 2/4] Submitting student credentials...")
            r_post = session.post(
                PORTAL_LOGIN,
                json=payload,
                headers=post_headers,
                timeout=35,
                allow_redirects=False,
            )
            print(f"[Portal Step 2/4] Response HTTP {r_post.status_code}")

            if r_post.status_code == 302:
                redirect_url = r_post.headers.get("location", "")
                print(f"[Portal Step 2/4] Redirect Location: {redirect_url}")

                # If redirected back to /login, credentials were wrong
                if redirect_url.rstrip("/").endswith("/login"):
                    r_err = session.get(redirect_url, timeout=25)
                    reason = _extract_inertia_error(r_err.text)
                    return False, reason or "الرقم الجامعي أو كلمة المرور غير صحيحة في بوابة الطالب."

                # Follow redirect to dashboard to complete portal session
                r_dash = session.get(redirect_url, timeout=30, allow_redirects=True)
                print(f"[Portal Step 2/4] Reached: {r_dash.url} (HTTP {r_dash.status_code})")

            elif r_post.status_code == 200:
                reason = _extract_inertia_error(r_post.text)
                if reason:
                    return False, reason
            else:
                return False, f"استجابة غير متوقعة من بوابة الطالب (HTTP {r_post.status_code})"

            # 3. Now that we are logged in to the portal, open UPINAR login page
            print("[Portal Step 3/4] Opening UPINAR login page...")
            r_moodle = session.get(UPINAR_LOGIN, timeout=30)
            soup_m = BeautifulSoup(r_moodle.text, "html.parser")

            oauth_link = None
            for a in soup_m.find_all("a", href=True):
                if "auth/oauth2" in a["href"]:
                    oauth_link = a["href"]
                    break

            if not oauth_link:
                return False, "لم يتم العثور على رابط تسجيل الدخول ببوابة الطالب داخل اليوبينار."

            # 4. Follow "Log in with بوابة الطالب" SSO link
            # Since the session already has Portal cookies, it authorizes and redirects to upinar.edu.ps/my/
            print(f"[Portal Step 4/4] Following SSO link: {oauth_link}...")
            r_sso = session.get(oauth_link, timeout=45, allow_redirects=True)
            print(f"[Portal Step 4/4] SSO finished at: {r_sso.url}")

            if self.is_logged_in(session):
                print("[SUCCESS] Fully logged into UPINAR via Portal SSO!")
                return True, ""

            return False, f"تم الدخول لبوابة الطالب بنجاح، ولكن تعذر إكمال الربط مع اليوبينار (الرابط: {r_sso.url})"

        except Exception as exc:
            print(f"[Portal SSO Exception]: {exc}")
            return False, f"انتهت مهلة الاتصال بالجامعة: {exc}"

    def _login_via_direct_moodle(self, session: requests.Session, student_id: str, password: str) -> Tuple[bool, str]:
        try:
            r_get = session.get(UPINAR_LOGIN, timeout=30)
            if r_get.status_code != 200:
                return False, f"تعذر فتح صفحة الموديل (HTTP {r_get.status_code})"

            soup = BeautifulSoup(r_get.text, "html.parser")
            token_input = soup.find("input", {"name": "logintoken"})
            logintoken = token_input.get("value", "") if token_input else ""

            post_data = {
                "anchor": "",
                "logintoken": logintoken,
                "username": student_id,
                "password": password,
            }
            headers = {
                "Origin": "https://upinar.edu.ps",
                "Referer": UPINAR_LOGIN,
            }

            r_post = session.post(
                UPINAR_LOGIN,
                data=post_data,
                headers=headers,
                timeout=35,
                allow_redirects=True,
            )

            if self.is_logged_in(session):
                print("[SUCCESS] Fully logged into UPINAR via direct login form!")
                return True, ""

            soup_post = BeautifulSoup(r_post.text, "html.parser")
            err_el = soup_post.find(class_="alert") or soup_post.find(id="loginerrormessage")
            moodle_err = err_el.get_text(strip=True) if err_el else "بيانات الدخول المباشر للموديل غير صحيحة."
            return False, moodle_err

        except Exception as exc:
            print(f"[Moodle Direct Login Exception]: {exc}")
            return False, f"خطأ في الاتصال بالموديل: {exc}"


def _extract_inertia_error(html_text: str) -> str:
    try:
        soup = BeautifulSoup(html_text, "html.parser")
        app_elem = soup.find(attrs={"data-page": True})
        if app_elem and app_elem.get("data-page"):
            data = json.loads(app_elem["data-page"])
            errors = data.get("props", {}).get("errors", {})
            if isinstance(errors, dict) and errors:
                return " - ".join(str(v) for v in errors.values())
    except Exception:
        pass
    return ""
