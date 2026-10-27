import html
import time
from collections import defaultdict
from typing import Dict, List, Optional

from .models import ContentItem, Course, StudentAccount
from .notifier import TelegramNotifier


def send_student_inventory(
    account: StudentAccount,
    courses: List[Course],
    items: List[ContentItem],
    notifier: Optional[TelegramNotifier] = None,
) -> None:
    """Send an organized, comprehensive inventory of all courses and their contents to a student."""
    if notifier is None:
        notifier = TelegramNotifier()

    # Intro message
    intro_message = (
        "📊 <b>تقرير الجرد الشامل لمساقات اليوبينار (UPINAR)</b>\n"
        "🎓 جامعة فلسطين\n"
        f"👤 الطالب: <code>{html.escape(account.student_id)}</code>\n"
        f"📚 عدد المساقات المسجلة: <b>{len(courses)} مساقات</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "إليك ملخص شامل بجميع الواجبات، الكويزات، الشباتر، المحاضرات، والإعلانات الحالية:"
    )
    notifier.send(account, intro_message)
    time.sleep(1.5)

    # Group items by course_id
    items_by_course: Dict[str, List[ContentItem]] = defaultdict(list)
    for item in items:
        items_by_course[item.course_id].append(item)

    for index, course in enumerate(courses, 1):
        course_items = items_by_course.get(course.id, [])
        grouped: Dict[str, List[ContentItem]] = defaultdict(list)
        for it in course_items:
            grouped[it.mod_type].append(it)

        assignments = grouped.get("assign", [])
        quizzes = grouped.get("quiz", [])
        files_chapters = (
            grouped.get("resource", [])
            + grouped.get("folder", [])
            + grouped.get("book", [])
            + grouped.get("folder_file", [])
            + grouped.get("page", [])
        )
        recordings_urls = grouped.get("url", [])
        forums = grouped.get("forum", []) + grouped.get("forum_post", [])

        safe_title = html.escape(course.title or f"مساق #{course.id}")
        raw_course_url = (course.url or "").strip()
        if raw_course_url.startswith("http://") or raw_course_url.startswith("https://"):
            safe_course_url = html.escape(raw_course_url)
            course_link_line = f"🔗 <a href='{safe_course_url}'>فتح صفحة المساق في اليوبينار</a>\n"
        else:
            course_link_line = ""

        card = (
            f"📖 <b>[{index}/{len(courses)}] {safe_title}</b>\n"
            f"{course_link_line}"
            "━━━━━━━━━━━━━━━━━━\n"
        )

        has_any_content = bool(assignments or quizzes or files_chapters or recordings_urls or forums)

        if not has_any_content:
            card += "<i>لا يوجد محتوى أو ملفات مرفوعة في هذا المساق حالياً.</i>\n"
        else:
            card += _section("📝 <b>الواجبات والتكاليف (Assignments):</b>", assignments, limit=10)
            card += _section("⏱️ <b>الكويزات والاختبارات (Quizzes):</b>", quizzes, limit=10)
            card += _section(
                f"📄 <b>الشباتر والمحاضرات والملفات ({len(files_chapters)}):</b>",
                files_chapters,
                limit=15,
            )
            card += _section(
                f"🎥 <b>تسجيلات وروابط المحاضرات ({len(recordings_urls)}):</b>",
                recordings_urls,
                limit=8,
            )
            card += _section("📢 <b>الإعلانات والمنتديات:</b>", forums, limit=8)

        notifier.send(account, card)
        time.sleep(1.8)

    # Conclusion message
    completion_message = (
        "✅ <b>اكتمل إرسال تقرير جميع مساقاتك بنجاح!</b>\n\n"
        "🔔 نظام المراقبة التلقائية نشط على حسابك.\n"
        "بمجرد أن يضيف أي دكتور واجباً جديداً، كويزاً، إعلاناً، أو ملفاً للمساق، "
        "سيصلك إشعار فوري هنا تلقائياً 🚀\n\n"
        "💡 أوامر مفيدة:\n"
        "/check — فحص فوري للمساقات الآن\n"
        "/courses — قائمة المساقات المسجلة\n"
        "/report — إعادة إرسال هذا التقرير الشامل\n"
        "/status — استعراض حالة المراقبة"
    )
    notifier.send(account, completion_message)


def _section(heading: str, items: List[ContentItem], limit: int = 0) -> str:
    if not items:
        return ""
    block = heading + "\n"
    visible = items if not limit else items[:limit]
    for item in visible:
        safe_title = html.escape(item.title or "عنصر")
        raw_url = (item.url or "").strip()
        if raw_url.startswith("http://") or raw_url.startswith("https://"):
            safe_url = html.escape(raw_url)
            block += f"• <a href='{safe_url}'>{safe_title}</a>\n"
        else:
            block += f"• {safe_title}\n"
    if limit and len(items) > limit:
        block += f"<i>...والمزيد في صفحة المساق ({len(items) - limit} عنصر إضافي)</i>\n"
    return block + "\n"


def generate_full_inventory(account: Optional[StudentAccount] = None) -> None:
    """CLI / administrative full inventory report."""
    from .auth import MoodleAuth
    from .db import get_database
    from .moodle_client import MoodleClient

    db = get_database()
    if account is None:
        accounts = db.accounts.load_all()
        if not accounts:
            print("[Report] No registered students. Students must send /start first.")
            return
        account = accounts[0]

    auth = MoodleAuth(session_store=db.sessions)
    client = MoodleClient()
    session = auth.get_session(account)
    if session is None:
        print("[Report] Could not authenticate student.")
        return

    courses = client.get_enrolled_courses(session)
    if not courses:
        print("[Report Notice] No courses found.")
        return

    all_items: List[ContentItem] = []
    for course in courses:
        all_items.extend(client.scan_course(session, course))

    send_student_inventory(account, courses, all_items)
