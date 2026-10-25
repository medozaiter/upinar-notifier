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
UPINAR_MY = "https://upinar.edu.ps/my/"
UPINAR_LOGIN = "https://upinar.edu.ps/login/index.php"
OAUTH_FALLBACK = "https://upinar.edu.ps/auth/oauth2/login.php?id=1&wantsurl=https%3A%2F%2Fupinar.edu.ps%2F"


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
            response = session.get(UPINAR_MY, timeout=45, allow_redirects=True)
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

        # Attempt Method 1: Student Portal SSO (Inertia.js -> UPINAR OAuth)
        print(f"[Auth 1/2] Attempting Student Portal SSO for {student_id}...")
        ok_portal, portal_err = self._login_via_portal_sso(session, student_id, password)
        if ok_portal:
            self.session_store.save(
                account.student_id,
                requests.utils.dict_from_cookiejar(session.cookies),
            )
            return True, ""

        print(f"[Auth 1/2 Result] Portal SSO failed: {portal_err}")

        # Attempt Method 2: Direct UPINAR Moodle Login
        print(f"[Auth 2/2] Attempting direct UPINAR Moodle login for {student_id}...")
        ok_moodle, moodle_err = self._login_via_direct_moodle(session, student_id, password)
        if ok_moodle:
            self.session_store.save(
                account.student_id,
                requests.utils.dict_from_cookiejar(session.cookies),
            )
            return True, ""

        print(f"[Auth 2/2 Result] Direct Moodle login failed: {moodle_err}")

        final_err = portal_err or moodle_err or "الرقم الجامعي أو كلمة المرور غير صحيحة."
        return False, final_err

    def _login_via_portal_sso(self, session: requests.Session, student_id: str, password: str) -> Tuple[bool, str]:
        try:
            # 1. Start from UPINAR login page to initialize OAuth session
            print("[Portal SSO] Fetching Upinar login to acquire OAuth session parameters...")
            r_moodle = session.get(UPINAR_LOGIN, timeout=40)
            soup_m = BeautifulSoup(r_moodle.text, "html.parser")
            oauth_link = None
            for a in soup_m.find_all("a", href=True):
                if "auth/oauth2" in a["href"]:
                    oauth_link = a["href"]
                    break

            if not oauth_link:
                oauth_link = OAUTH_FALLBACK

            # 2. Call OAuth link to trigger redirect to portal authorize
            r_oauth = session.get(oauth_link, allow_redirects=False, timeout=40)
            authorize_url = r_oauth.headers.get("Location") or PORTAL_LOGIN
            print(f"[Portal SSO] Authorize URL: {authorize_url}")

            # 3. Visit the authorize URL which redirects to /login and sets intended URL
            r_get = session.get(authorize_url, allow_redirects=True, timeout=40)
            print(f"[Portal SSO] Landed at: {r_get.url} (HTTP {r_get.status_code})")

            # Extract Inertia version reliably
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
                "X-Inertia": "true",
                "X-Inertia-Version": inertia_version,
                "X-XSRF-TOKEN": xsrf_token,
                "Content-Type": "application/json",
                "Referer": r_get.url,
                "Origin": "https://students.up.edu.ps",
                "Accept": "text/html, application/xhtml+xml",
            }
            payload = {
                "name": student_id,
                "password": password,
                "remember": "on",
            }

            print("[Portal SSO] Submitting credentials to students.up.edu.ps/login...")
            r_post = session.post(
                PORTAL_LOGIN,
                json=payload,
                headers=post_headers,
                timeout=45,
                allow_redirects=False,
            )
            print(f"[Portal SSO] POST status: {r_post.status_code}")

            if r_post.status_code == 302:
                redirect_url = r_post.headers.get("location", "")
                print(f"[Portal SSO] Redirect Location: {redirect_url}")

                # If redirected back to /login, login was rejected by portal
                if redirect_url.rstrip("/").endswith("/login"):
                    # Inspect rejection reason from login page
                    r_err_page = session.get(redirect_url, timeout=30)
                    reason = _extract_inertia_error(r_err_page.text)
                    print(f"[Portal SSO Rejection Reason]: {reason}")
                    return False, reason or "بيانات الاعتماد غير متطابقة مع سجلات بوابة الطالب."

                # If redirected to authorize URL or dashboard, follow the redirect chain
                r_follow = session.get(redirect_url, allow_redirects=True, timeout=60)
                print(f"[Portal SSO] Final chain landing URL: {r_follow.url}")

                # Verify if session is now logged in to UPINAR
                if self.is_logged_in(session):
                    print("[SUCCESS] Fully logged into UPINAR via Portal SSO chain!")
                    return True, ""

                # If landing is still on students portal, visit Upinar OAuth one more time now that we have cookies
                r_sso_final = session.get(oauth_link, allow_redirects=True, timeout=60)
                if self.is_logged_in(session):
                    print("[SUCCESS] Fully logged into UPINAR via final OAuth step!")
                    return True, ""

                return False, f"تم الدخول للبوابة ولكن تعذر الانتقال لليوبينار (الرابط: {r_sso_final.url})"

            elif r_post.status_code == 200:
                # Some Inertia versions return 200 with props
                reason = _extract_inertia_error(r_post.text)
                if reason:
                    return False, reason

            return False, f"استجابة غير متوقعة من بوابة الطالب (HTTP {r_post.status_code})"

        except Exception as exc:
            print(f"[Portal SSO Exception]: {exc}")
            return False, f"انتهت مهلة الاتصال ببوابة الطالب: {exc}"

    def _login_via_direct_moodle(self, session: requests.Session, student_id: str, password: str) -> Tuple[bool, str]:
        try:
            r_get = session.get(UPINAR_LOGIN, timeout=40)
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
                timeout=45,
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
