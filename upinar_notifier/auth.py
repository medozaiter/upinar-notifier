import re
import urllib.parse

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
    def __init__(self, session_store: SessionStore | None = None):
        self.session_store = session_store or FileSessionStore()

    def login_with_account(self, account: StudentAccount) -> bool:
        session = create_robust_session()
        return self.login(session, account)

    def get_session(self, account: StudentAccount) -> requests.Session | None:
        session = create_robust_session()
        cookies = self.session_store.load(account.student_id)
        if cookies:
            session.cookies.update(cookies)
            print("[Auth] Checking previous session validity...")
            if self.is_logged_in(session):
                print("[Auth] Previous session is valid! Skipping login.")
                return session

        print("[Auth] Performing fresh SSO login...")
        if self.login(session, account):
            return session
        print("[Error] Login flow failed. Will retry on next interval.")
        return None

    def is_logged_in(self, session: requests.Session) -> bool:
        try:
            response = session.get(UPINAR_MY, timeout=60, allow_redirects=True)
            if "login" in response.url.lower():
                return False
            body = response.text
            return any(marker in body for marker in ("sesskey", "logout.php", "user-menu", "my-courses"))
        except Exception:
            return False

    def login(self, session: requests.Session, account: StudentAccount) -> bool:
        student_id = str(account.student_id).strip()
        password = str(account.password).strip()

        if not student_id:
            return False

        if len(student_id) > 32:
            print("[Security] Invalid student ID length.")
            return False

        if len(password) > 128:
            print("[Security] Invalid password length.")
            return False
        print("[1/3] Connecting to Student Portal (students.up.edu.ps)...")
        try:
            r_get = session.get(PORTAL_LOGIN, timeout=60)
            if r_get.status_code != 200:
                print(f"[Error] Failed to load portal login page (HTTP {r_get.status_code})")
                return False

            match_version = re.search(r'version["\']\s*:\s*["\']([a-zA-Z0-9]+)["\']', r_get.text)
            inertia_version = match_version.group(1) if match_version else ""
            xsrf_cookie = session.cookies.get("XSRF-TOKEN", "")
            xsrf_token = urllib.parse.unquote(xsrf_cookie) if xsrf_cookie else ""

            post_headers = {
                "X-Inertia": "true",
                "X-Inertia-Version": inertia_version,
                "X-XSRF-TOKEN": xsrf_token,
                "Content-Type": "application/json",
                "Referer": PORTAL_LOGIN,
                "Origin": "https://students.up.edu.ps",
            }
            payload = {
                "name": student_id,
                "password": password,
                "remember": "on",
            }

            print("[2/3] Authenticating student credentials...")
            r_post = session.post(
                PORTAL_LOGIN,
                json=payload,
                headers=post_headers,
                timeout=60,
                allow_redirects=False,
            )
            if r_post.status_code not in [200, 302, 409]:
                print(f"[Error] Portal login returned HTTP {r_post.status_code}")
                return False
            if _portal_login_rejected(r_post):
                print("[Error] Student ID or password was rejected by the portal.")
                return False
            print(f"[OK] Portal login successful! (HTTP {r_post.status_code})")

            print("[3/3] Synchronizing with UPINAR (SSO)...")
            r_moodle_login = session.get(UPINAR_LOGIN, timeout=60)
            soup = BeautifulSoup(r_moodle_login.text, "html.parser")

            oauth_link = None
            for anchor in soup.find_all("a", href=True):
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

            r_sso = session.get(oauth_link, timeout=75, allow_redirects=True)
            if self.is_logged_in(session) or "login" not in r_sso.url.lower():
                print("[SUCCESS] Fully logged into UPINAR via SSO!")
                self.session_store.save(
                    account.student_id,
                    requests.utils.dict_from_cookiejar(session.cookies),
                )
                return True

            print(f"[Error] Failed to complete UPINAR login. Final URL: {r_sso.url}")
            return False
        except Exception as exc:
            print(f"[Exception in login_flow]: {exc}")
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
