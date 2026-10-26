import json
import re
import time
import urllib.parse
from typing import Optional, Tuple

import requests
from bs4 import BeautifulSoup

from .models import SessionStore, StudentAccount, clean_student_id
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


def _safe_get_cookie(cookie_jar, name: str) -> str:
    """Safely extracts a cookie value by name, avoiding CookieConflictError when multiple domains/paths exist."""
    try:
        val = cookie_jar.get(name, "")
        if val:
            return val
    except Exception:
        pass
    for cookie in cookie_jar:
        if getattr(cookie, "name", "") == name:
            return getattr(cookie, "value", "")
    return ""


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
        student_id = clean_student_id(account.student_id)
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
        for attempt in range(1, 3):
            try:
                ok, err = self._attempt_portal_and_sso(session, student_id, password)
                if ok:
                    return True, ""
                # Do not retry if credentials were wrong
                if "غير صحيحة" in err or "طول" in err:
                    return False, err
                if attempt == 1:
                    print(f"[Portal SSO] Attempt 1 failed with ({err}), retrying in 2 seconds...")
                    time.sleep(2)
                    continue
                return False, err
            except Exception as exc:
                if attempt == 1:
                    print(f"[Portal SSO] Attempt 1 timed out ({exc}), retrying in 2 seconds...")
                    time.sleep(2)
                    continue
                return False, f"انتهت مهلة الاتصال بالجامعة: {exc}"
        return False, "تعذر تسجيل الدخول إلى بوابة الطالب."

    def _attempt_portal_and_sso(self, session: requests.Session, student_id: str, password: str) -> Tuple[bool, str]:
        session.cookies.clear()
        # 1. Fetch portal login page
        print("[Portal Step 1/4] GET https://students.up.edu.ps/login...")
        session.headers.update(CHROME_HEADERS)
        r_get = session.get(PORTAL_LOGIN, timeout=45)
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

        xsrf_cookie = _safe_get_cookie(session.cookies, "XSRF-TOKEN")
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
            timeout=45,
            allow_redirects=False,
        )
        print(f"[Portal Step 2/4] Response HTTP {r_post.status_code}")

        if r_post.status_code == 302:
            redirect_url = r_post.headers.get("location", "")
            print(f"[Portal Step 2/4] Redirect Location: {redirect_url}")

            # If redirected back to /login, credentials were wrong
            if redirect_url.rstrip("/").endswith("/login"):
                r_err = session.get(redirect_url, timeout=30)
                reason = _extract_inertia_error(r_err.text)
                return False, reason or "الرقم الجامعي أو كلمة المرور غير صحيحة في بوابة الطالب."

            # Follow redirect to dashboard to complete portal session
            r_dash = session.get(redirect_url, timeout=45, allow_redirects=True)
            print(f"[Portal Step 2/4] Reached: {r_dash.url} (HTTP {r_dash.status_code})")

        elif r_post.status_code == 200:
            reason = _extract_inertia_error(r_post.text)
            if reason:
                return False, reason
        else:
            body_snippet = r_post.text[:250].replace("\n", " ").strip()
            server_hdr = r_post.headers.get("server", "").lower()
            print(f"[Portal POST Error] HTTP {r_post.status_code} (Server: {server_hdr}): {body_snippet}")
            if "cloudflare" in server_hdr or "just a moment" in body_snippet.lower() or "attention required" in body_snippet.lower():
                return False, "جدار الحماية (Cloudflare) في الجامعة حجب الاتصال القادم من خادم أجنبي (HTTP 403)."
            return False, f"استجابة غير متوقعة من بوابة الطالب (HTTP {r_post.status_code}): {body_snippet or server_hdr}"

        # 3. Now that we are logged in to the portal, open UPINAR login page
        print("[Portal Step 3/4] Opening UPINAR login page...")
        r_moodle = session.get(UPINAR_LOGIN, timeout=45)
        soup_m = BeautifulSoup(r_moodle.text, "html.parser")

        oauth_link = None
        for a in soup_m.find_all("a", href=True):
            if "auth/oauth2" in a["href"]:
                oauth_link = a["href"]
                break

        if not oauth_link:
            return False, "لم يتم العثور على رابط تسجيل الدخول ببوابة الطالب داخل اليوبينار."

        # 4. Follow "Log in with بوابة الطالب" SSO link
        print(f"[Portal Step 4/4] Following SSO link: {oauth_link}...")
        r_sso = session.get(oauth_link, timeout=50, allow_redirects=True)
        print(f"[Portal Step 4/4] SSO finished at: {r_sso.url}")

        if not self.is_logged_in(session) and "oauth/authorize" in r_sso.url:
            r_sso = self._handle_oauth_consent(session, r_sso)

        if self.is_logged_in(session):
            print("[SUCCESS] Fully logged into UPINAR via Portal SSO!")
            return True, ""

        return False, f"تم الدخول لبوابة الطالب بنجاح، ولكن تعذر إكمال الربط مع اليوبينار (الرابط: {r_sso.url})"

    def _handle_oauth_consent(self, session: requests.Session, r_sso: requests.Response) -> requests.Response:
        """If Laravel Passport prompts for OAuth authorization, auto-approve it."""
        try:
            print("[Portal SSO] Detected OAuth authorization consent page. Auto-approving...")
            soup = BeautifulSoup(r_sso.text, "html.parser")

            parsed = urllib.parse.urlparse(r_sso.url)
            qs = urllib.parse.parse_qs(parsed.query)
            client_id = qs.get("client_id", [""])[0]
            state = qs.get("state", [""])[0]

            auth_token = ""
            form = soup.find("form")
            post_data = {}
            if form:
                for inp in form.find_all("input"):
                    n = inp.get("name")
                    if n:
                        post_data[n] = inp.get("value", "")
                        if "token" in n.lower() and inp.get("value"):
                            auth_token = inp["value"]

            # Check for Inertia data-page
            app_elem = soup.find(attrs={"data-page": True})
            if app_elem and app_elem.get("data-page"):
                try:
                    page_data = json.loads(app_elem["data-page"])
                    props = page_data.get("props", {})
                    auth_token = props.get("authToken") or props.get("auth_token") or auth_token
                    client = props.get("client", {})
                    client_id = client.get("id") or props.get("client_id") or client_id
                    state = props.get("state") or state
                except Exception:
                    pass

            if not post_data:
                post_data = {
                    "approve": "1",
                    "client_id": client_id,
                    "state": state,
                }
                if auth_token:
                    post_data["auth_token"] = auth_token
            else:
                post_data["approve"] = "1"

            xsrf_cookie = _safe_get_cookie(session.cookies, "XSRF-TOKEN")
            xsrf_token = urllib.parse.unquote(xsrf_cookie) if xsrf_cookie else ""
            headers = {
                "Origin": "https://students.up.edu.ps",
                "Referer": r_sso.url,
                "X-XSRF-TOKEN": xsrf_token,
            }

            post_url = form.get("action") if form and form.get("action") else "https://students.up.edu.ps/oauth/authorize"
            if not post_url.startswith("http"):
                post_url = urllib.parse.urljoin("https://students.up.edu.ps", post_url)

            print(f"[Portal SSO] Submitting approval to {post_url}...")
            r_approved = session.post(post_url, data=post_data, headers=headers, timeout=50, allow_redirects=True)
            print(f"[Portal SSO] Approval completed at: {r_approved.url}")
            return r_approved
        except Exception as exc:
            print(f"[Portal SSO Warning] OAuth consent handler error: {exc}")
            return r_sso

    def _login_via_direct_moodle(self, session: requests.Session, student_id: str, password: str) -> Tuple[bool, str]:
        try:
            m_session = create_robust_session()
            r_get = m_session.get(UPINAR_LOGIN, timeout=30)
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

            r_post = m_session.post(
                UPINAR_LOGIN,
                data=post_data,
                headers=headers,
                timeout=35,
                allow_redirects=True,
            )

            if self.is_logged_in(m_session):
                session.cookies.update(m_session.cookies)
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
