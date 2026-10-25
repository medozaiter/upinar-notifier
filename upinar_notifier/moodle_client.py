import re
from typing import List, Set
from urllib.parse import urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup

from .models import ContentItem, Course

UPINAR_MY = "https://upinar.edu.ps/my/"
UPINAR_BASE = "https://upinar.edu.ps"

CATEGORY_BY_MOD = {
    "assign": "assignment",
    "quiz": "quiz",
    "forum": "announcement",
    "forum_post": "announcement",
    "resource": "file",
    "folder": "file",
    "folder_file": "resource",
    "book": "file",
    "page": "file",
    "url": "resource",
    "label": "resource",
}


def classify(mod_type: str) -> str:
    return CATEGORY_BY_MOD.get(mod_type, "resource")


class MoodleClient:
    def get_enrolled_courses(self, session: requests.Session) -> List[Course]:
        print("[Courses] Scanning all enrolled courses via Moodle API...")
        courses: List[Course] = []
        found_cids: Set[str] = set()

        try:
            response = session.get(UPINAR_MY, timeout=40)
            sess_match = re.search(r'"sesskey":"([^"]+)"', response.text)
            sesskey = sess_match.group(1) if sess_match else ""
            if sesskey:
                service_url = (
                    "https://upinar.edu.ps/lib/ajax/service.php"
                    f"?sesskey={sesskey}&info=core_course_get_enrolled_courses_by_timeline_classification"
                )
                payload = [{
                    "index": 0,
                    "methodname": "core_course_get_enrolled_courses_by_timeline_classification",
                    "args": {
                        "offset": 0,
                        "limit": 0,
                        "classification": "all",
                        "sort": "fullname",
                    },
                }]
                svc = session.post(service_url, json=payload, timeout=30)
                svc_data = svc.json()
                if isinstance(svc_data, list) and svc_data and not svc_data[0].get("error"):
                    for course in svc_data[0].get("data", {}).get("courses", []):
                        cid = str(course["id"])
                        if cid == "1" or cid in found_cids:
                            continue
                        found_cids.add(cid)
                        courses.append(
                            Course(
                                id=cid,
                                title=str(course.get("fullname", f"Course #{cid}")).strip(),
                                url=f"{UPINAR_BASE}/course/view.php?id={cid}",
                            )
                        )
        except Exception as exc:
            print(f"[Courses API Notice] {exc}")

        if not courses:
            courses.extend(self._courses_from_html(session, found_cids))

        print(f"[Courses] Successfully found {len(courses)} enrolled courses across your semester.")
        for index, course in enumerate(courses, 1):
            print(f"   {index}. {course.title} (ID: {course.id})")
        return courses

    def scan_course(self, session: requests.Session, course: Course) -> List[ContentItem]:
        items: List[ContentItem] = []
        seen_keys: Set[str] = set()
        try:
            response = session.get(course.url, timeout=60)
            if response.status_code != 200:
                print(
                    f"[Scan Notice] "
                    f"Course page returned HTTP {response.status_code}"
                )
                return items
            if "login" in response.url.lower():
                print(
                    f"[Scan Notice] "
                    f"Session appears to be expired for course {course.id}"
                )
                return items
            soup = BeautifulSoup(response.text, "html.parser")
            self._apply_official_title(soup, course)

            for anchor in soup.find_all("a", href=re.compile(r"/mod/([a-zA-Z0-9_]+)/view\.php\?id=(\d+)")):
                href = anchor.get("href", "")
                match = re.search(r"/mod/([a-zA-Z0-9_]+)/view\.php\?id=(\d+)", href)
                if not match:
                    continue
                mod_type = match.group(1).lower()
                mod_id = match.group(2)
                unique_key = f"{course.id}_{mod_type}_{mod_id}"
                if unique_key in seen_keys:
                    continue
                seen_keys.add(unique_key)

                title_text = self._clean_activity_title(anchor, f"{mod_type.capitalize()} #{mod_id}")
                item = ContentItem(
                    unique_key=unique_key,
                    course_id=course.id,
                    course_name=course.title,
                    category=classify(mod_type),
                    mod_type=mod_type,
                    title=title_text,
                    url=f"{UPINAR_BASE}/mod/{mod_type}/view.php?id={mod_id}",
                )
                items.append(item)

                if mod_type == "forum":
                    items.extend(self._scan_forum(session, course, item, seen_keys))
                elif mod_type == "folder":
                    items.extend(self._scan_folder(session, course, item, seen_keys))
        except Exception as exc:
            print(f"[Scan Error] Course ID {course.id}: {exc}")
        return items

    def _courses_from_html(self, session: requests.Session, found_cids: Set[str]) -> List[Course]:
        courses: List[Course] = []
        try:
            response = session.get(UPINAR_MY, timeout=40)
            soup = BeautifulSoup(response.text, "html.parser")
            for anchor in soup.find_all("a", href=re.compile(r"/course/view\.php\?id=\d+")):
                match = re.search(r"id=(\d+)", anchor["href"])
                if not match:
                    continue
                cid = match.group(1)
                if cid == "1" or cid in found_cids:
                    continue
                found_cids.add(cid)
                raw_title = anchor.get_text(strip=True)
                course_title = raw_title if raw_title and len(raw_title) > 2 else f"Course #{cid}"
                courses.append(
                    Course(
                        id=cid,
                        title=course_title,
                        url=f"{UPINAR_BASE}/course/view.php?id={cid}",
                    )
                )
        except Exception:
            pass
        return courses

    @staticmethod
    def _apply_official_title(soup: BeautifulSoup, course: Course) -> None:
        official_title = ""
        heading = soup.find("h1")
        if heading:
            official_title = heading.get_text(strip=True)

        breadcrumb = soup.find("nav", {"aria-label": re.compile(r"(breadcrumb|navigation)", re.I)})
        if breadcrumb:
            crumbs = [crumb.get_text(strip=True) for crumb in breadcrumb.find_all("li")]
            if len(crumbs) >= 2:
                candidate = crumbs[-1]
                if candidate and len(candidate) > len(official_title) and "تسجيل الدخول" not in candidate:
                    official_title = candidate

        if official_title and "تسجيل الدخول" not in official_title:
            course.title = official_title

    @staticmethod
    def _clean_activity_title(anchor, fallback: str) -> str:
        for hide in anchor.find_all(class_=re.compile(r"(accesshide|sr-only|badge)")):
            hide.decompose()
        instance = anchor.find(class_=re.compile(r"(instancename|activityname)"))
        title_text = instance.get_text(strip=True) if instance else anchor.get_text(strip=True)
        title_text = re.sub(
            r"^(Assignment|File|Folder|Quiz|URL|Forum)\s*",
            "",
            title_text,
            flags=re.IGNORECASE,
        )
        title_text = re.sub(
            r"(File|URL|Assignment|Quiz|Forum)$",
            "",
            title_text,
            flags=re.IGNORECASE,
        )
        title_text = re.sub(r"\s+", " ", title_text).strip()
        if len(title_text) > 300:
            title_text = title_text[:300]
        return title_text or fallback

    def _scan_forum(
        self,
        session: requests.Session,
        course: Course,
        forum: ContentItem,
        seen_keys: Set[str],
    ) -> List[ContentItem]:
        items: List[ContentItem] = []
        try:
            response = session.get(forum.url, timeout=45)
            if "login" in response.url.lower():
                return items
            soup = BeautifulSoup(response.text, "html.parser")
            for anchor in soup.find_all("a", href=re.compile(r"/mod/forum/discuss\.php\?d=\d+")):
                href = anchor.get("href", "")
                match = re.search(r"d=(\d+)", href)
                if not match:
                    continue
                discussion_id = match.group(1)
                unique_key = f"{course.id}_forum_post_{discussion_id}"
                if unique_key in seen_keys:
                    continue
                seen_keys.add(unique_key)
                title = re.sub(r"\s+", " ", anchor.get_text(strip=True)).strip() or f"Announcement #{discussion_id}"
                items.append(
                    ContentItem(
                        unique_key=unique_key,
                        course_id=course.id,
                        course_name=course.title,
                        category="announcement",
                        mod_type="forum_post",
                        title=title,
                        url=f"{UPINAR_BASE}/mod/forum/discuss.php?d={discussion_id}",
                    )
                )
        except Exception as exc:
            print(f"[Forum Scan Notice] {forum.url}: {exc}")
        return items

    def _scan_folder(
        self,
        session: requests.Session,
        course: Course,
        folder: ContentItem,
        seen_keys: Set[str],
    ) -> List[ContentItem]:
        items: List[ContentItem] = []
        try:
            response = session.get(folder.url, timeout=45)
            if "login" in response.url.lower():
                return items
            soup = BeautifulSoup(response.text, "html.parser")
            for anchor in soup.find_all("a", href=True):
                href = anchor["href"]
                if "pluginfile.php" not in href and "/mod_folder/" not in href:
                    continue
                absolute = _strip_query(urljoin(UPINAR_BASE + "/", href))
                if len(absolute) > 2000:
                    continue
                unique_key = f"{course.id}_folder_file_{absolute}"
                if unique_key in seen_keys:
                    continue
                seen_keys.add(unique_key)
                title = re.sub(r"\s+", " ", anchor.get_text(strip=True)).strip() or "Uploaded file"
                items.append(
                    ContentItem(
                        unique_key=unique_key,
                        course_id=course.id,
                        course_name=course.title,
                        category="resource",
                        mod_type="folder_file",
                        title=f"{folder.title} / {title}",
                        url=absolute,
                    )
                )
        except Exception as exc:
            print(f"[Folder Scan Notice] {folder.url}: {exc}")
        return items


def _strip_query(url: str) -> str:
    parsed = urlparse(url)
    return urlunparse(parsed._replace(query="", fragment=""))
