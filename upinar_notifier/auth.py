import json
import re
import urllib.parse
from typing import Optional

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

    def login_with_account(self, account: StudentAccount) -> bool:
        session = create_robust_session()
        return self.login(session, account)

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
        if self.login(session, account):
            return session
        print(f"[Error] Login flow failed for {account.student_id}.")
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
        student_id = str(account.student_id).strip()
        password = str(account.password).strip()

        if not student_id or not password:
            return False

        if len(student_id) > 32 or len(password) > 128:
            print("[Security] Invalid credentials length.")
            return False

        # Attempt Method 1: Student Portal SSO (Inertia.js -> UPINAR OAuth)
        print("[Auth 1/2] Attempting Student Portal SSO (students.up.edu.ps)...")
        if self._login_via_portal_sso(session, student_id, password):
            self.session_store.save(
                account.student_id,
                requests.utils.dict_from_cookiejar(session.cookies),
            )
            return True

        # Attempt Method 2: Direct UPINAR Moodle Login
        print("[Auth 2/2] Attempting direct UPINAR Moodle login...")
        if self._login_via_direct_moodle(session, student_id, password):
            self.session_store.save(
                account.student_id,
                requests.utils.dict_from_cookiejar(session.cookies),
            )
            return True

        return False

    def _login_via_portal_sso(self, session: requests.Session, student_id: str, password: str) -> bool:
        try:
            r_get = session.get(PORTAL_LOGIN, timeout=30)
            if r_get.status_code != 200:
                print(f"[Portal] GET login failed: HTTP {r_get.status_code}")
                return False

            # Extract Inertia version reliably from data-page attribute
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
                "Referer": PORTAL_LOGIN,
                "Origin": "https://students.up.edu.ps",
                "Accept": "text/html, application/xhtml+xml",
            }
            payload = {
                "name": student_id,
                "password": password,
                "remember": "on",
            }

            r_post = session.post(
                PORTAL_LOGIN,
                json=payload,
                headers=post_headers,
                timeout=30,
                allow_redirects=False,
            )

            # Check rejection based on redirect location or response
            if r_post.status_code == 302:
                redirect_url = r_post.headers.get("location", "").lower()
                if redirect_url.rstrip("/").endswith("/login"):
                    print("[Portal] Credentials rejected (redirected back to login).")
                    return False
            elif r_post.status_code not in (200, 302):
                print(f"[Portal] POST returned unexpected HTTP {r_post.status_code}")
                return False

            if _portal_login_rejected(r_post):
                print("[Portal] Login rejected by portal validation.")
                return False

            # Now synchronize with UPINAR via SSO OAuth
            r_moodle_login = session.get(UPINAR_LOGIN, timeout=40)
            soup_m = BeautifulSoup(r_moodle_login.text, "html.parser")

            oauth_link = None
            for anchor in soup_m.find_all("a", href=True):
                if "auth/oauth2" in anchor["href"]:
                    oauth_link = anchor["href"]
                    break
            if not oauth_link:
                match = re.search(
                    r"https?://upinar\.edu\.ps/auth/oauth2/login\.php\?[^\"']+",
                    r_moodle_login.text,
                )
                if match:
                    oauth_link = match.group(0)
            if not oauth_link:
                oauth_link = OAUTH_FALLBACK

            r_sso = session.get(oauth_link, timeout=45, allow_redirects=True)
            if self.is_logged_in(session) or "login" not in r_sso.url.lower():
                print("[SUCCESS] Fully logged into UPINAR via Portal SSO!")
                return True

            print(f"[Portal SSO Notice] SSO redirect finished at {r_sso.url}")
            return False
        except Exception as exc:
            print(f"[Portal SSO Exception]: {exc}")
            return False

    def _login_via_direct_moodle(self, session: requests.Session, student_id: str, password: str) -> bool:
        try:
            r_get = session.get(UPINAR_LOGIN, timeout=30)
            if r_get.status_code != 200:
                print(f"[Moodle] GET login page returned HTTP {r_get.status_code}")
                return False

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
                return True

            print(f"[Moodle Notice] Direct login completed. Logged in status: {self.is_logged_in(session)}")
            return False
        except Exception as exc:
            print(f"[Moodle Direct Login Exception]: {exc}")
            return False


def _portal_login_rejected(response: requests.Response) -> bool:
    try:
        payload = response.json()
    except Exception:
        return False
    if not isinstance(payload, dict):
        return False
    if payload.get("errors"):
        return True
    props = payload.get("props")
    if isinstance(props, dict) and props.get("errors"):
        return True
    return False
