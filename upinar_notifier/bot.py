import asyncio
import html
import os
import time
from collections import defaultdict
from typing import Dict

from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import InvalidToken
from telegram.request import HTTPXRequest
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from .auth import MoodleAuth
from .db import get_database
from .models import StudentAccount, clean_student_id
from .runtime import get_scan_service, scan_all_active
from .settings import get_settings


async def safe_send_message(bot, chat_id, text: str, parse_mode: str = "HTML", retries: int = 3):
    """Sends a Telegram message with automatic retries on transient network drops."""
    for attempt in range(retries):
        try:
            return await bot.send_message(chat_id=chat_id, text=text, parse_mode=parse_mode)
        except Exception as exc:
            if attempt < retries - 1:
                import asyncio
                await asyncio.sleep(1.0)
            else:
                print(f"[Bot Send Warning] Delivery to {chat_id} failed: {exc}")
                return None

async def global_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Prevents uncaught network exceptions from freezing conversation handlers."""
    err_str = str(context.error)
    if 'ConnectError' in err_str or 'NetworkError' in err_str or 'timeout' in err_str.lower():
        print(f"[Network Warning] Transient Telegram drop: {err_str}")
    else:
        print(f"[Bot Exception Handler] {context.error}")

ASK_STUDENT_ID, ASK_PASSWORD = range(2)

WELCOME = (
    "مرحباً بك في بوت يوبينار جامعة فلسطين 🎓\n"
    "━━━━━━━━━━━━━━━━━━\n"
    "هذا البوت مخصص لطلاب <b>جامعة فلسطين</b> لمتابعة مساقات الموديل (اليوبينار UPINAR) تلقائياً، "
    "وإرسال إشعارات فورية بكل واجب، كويز، محاضرة، إعلان، أو شبتر جديد.\n\n"
    "⭐ <b>دعم وتقييم المشروع:</b>\n"
    "إذا أعجبك البوت وساعدك، يسعدنا جداً دعمك بوضع نجمة (Star) للمشروع على GitHub عبر الزر أدناه 🌟\n\n"
    "📌 <b>الخطوة [1/2]:</b>\n"
    "من فضلك أرسل <b>رقمك الجامعي</b> للمتابعة:"
)

ASK_ID_AGAIN = (
    "⚠️ <b>تنبيه:</b> الرجاء إدخال رقمك الجامعي بشكل صحيح (أرقام فقط بدون حروف أو رموز)."
)

ASK_PASSWORD_MSG = (
    "📌 <b>الخطوة [2/2]:</b>\n"
    "الآن أرسل <b>كلمة مرور بوابة الطالب</b> (Portal Password):\n\n"
    "🔒 <b>ملاحظة أمنية مشددة:</b>\n"
    "• سيتم حذف رسالتك تلقائياً فور استلامها لحماية خصوصيتك.\n"
    "• يتم تشفير كلمة المرور بقوة (AES-256) قبل حفظها في قاعدة البيانات.\n"
    "• لن يطّلع عليها أي شخص ولن يتم مشاركتها أبداً."
)

CHECKING = (
    "⏳ <b>جاري التحقق من بيانات الدخول...</b>\n"
    "يتم الاتصال ببوابة الطالب ونظام الموديل (SSO). يستغرق ذلك بضع ثوانٍ، انتظر لحظة..."
)

LOGIN_FAILED = (
    "❌ <b>تعذر تسجيل الدخول!</b>\n"
    "الرقم الجامعي أو كلمة المرور غير صحيحة، أو أن خوادم الجامعة بطيئة حالياً.\n\n"
    "يرجى التأكد من بيانات الدخول وإرسال /start للمحاولة من جديد."
)


def format_interval(minutes: int) -> str:
    if minutes >= 60 and minutes % 60 == 0:
        hours = minutes // 60
        if hours == 1:
            return "كل ساعة"
        elif hours == 2:
            return "كل ساعتين"
        elif 3 <= hours <= 10:
            return f"كل {hours} ساعات"
        else:
            return f"كل {hours} ساعة"
    return f"كل {minutes} دقيقة"


REGISTERED_HEADER = (
    "✅ <b>تم التحقق وتفعيل الحساب بنجاح!</b>\n"
    "━━━━━━━━━━━━━━━━━━\n"
    "🎓 الرقم الجامعي: <code>{student_id}</code>\n"
    "🔔 دورة المراقبة: <b>{interval_str}</b>\n\n"
    "🔍 <i>جاري الآن فحص مساقاتك وجلب الواجبات، الشباتر، والمحاضرات لأول مرة... "
    "سنرسل لك تقريراً شاملاً ومرتباً بالأسفل خلال لحظات 🚀</i>"
)

HELP = (
    "📖 <b>دليل أوامر بوت يوبينار (UPINAR Notifier)</b>\n"
    "━━━━━━━━━━━━━━━━━━\n"
    "🔹 /start — تسجيل حساب جديد أو استئناف التنبيهات\n"
    "🔹 /check — فحص فوري للمساقات للبحث عن أي تكليف جديد الآن\n"
    "🔹 /courses — عرض قائمة مساقاتك المسجلة وروابطها المباشرة\n"
    "🔹 /report — إرسال تقرير وجرد شامل لجميع المساقات والمحتويات\n"
    "🔹 /status — عرض حالة المراقبة وموعد الفحص القادم\n"
    "🔹 /stop — إيقاف التنبيهات الدورية مؤقتاً\n"
    "🔹 /logout — تسجيل الخروج وحذف بيانات الحساب بأمان\n"
    "🔹 /cancel — إلغاء خطوة التسجيل الحالية\n"
    "🔹 /help — عرض هذه المساعدة ودليل الأوامر\n\n"
    "⏱️ <b>المراقبة الدورية:</b> يقوم البوت بالفحص التلقائي كل <b>12 ساعة</b> وإرسال إشعار فوري عند أي جديد.\n"
    "🔒 <b>الأمان والخصوصية:</b> بيانات كل طالب معزولة تماماً، وتُحفظ كلمات المرور مشفرة بتقنية AES-256."
)

# Rate Limiting & Concurrency Tracker per Student
USER_ACTIVITY: Dict[str, Dict] = {}

GITHUB_REPO_URL = "https://github.com/medozaiter/upinar-notifier"
USER_LOCKS: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)


def get_github_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⭐ ادعم المشروع بنجمة (Star) على GitHub ⭐", url=GITHUB_REPO_URL)]
    ])

GLOBAL_COMMAND_COOLDOWN = 5.0        # At least 5 seconds between ANY command from same student
CHECK_COOLDOWN_SECONDS = 900         # 15 minutes cooldown between manual /check
REPORT_COOLDOWN_SECONDS = 1200       # 20 minutes cooldown between full /report
COURSES_COOLDOWN_SECONDS = 180       # 3 minutes cooldown between /courses
STATUS_COOLDOWN_SECONDS = 60         # 1 minute cooldown between /status
WARN_SUPPRESS_SECONDS = 15.0         # Cooldown warning throttle to prevent flood


def get_global_burst_remaining(chat_id: str) -> int:
    now = time.time()
    last_any = USER_ACTIVITY.get(chat_id, {}).get("last_any_command", 0)
    elapsed = now - last_any
    if elapsed < GLOBAL_COMMAND_COOLDOWN:
        return max(1, int(GLOBAL_COMMAND_COOLDOWN - elapsed))
    return 0


def format_remaining_time(seconds: int) -> str:
    if seconds >= 60:
        mins = seconds // 60
        secs = seconds % 60
        if secs > 0:
            return f"{mins} دقيقة و {secs} ثانية"
        return f"{mins} دقيقة"
    return f"{seconds} ثانية"


def format_arabic_datetime(iso_str: str | None) -> str:
    if not iso_str:
        return "لم يتم بعد"
    try:
        from datetime import datetime, timezone, timedelta
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        palestine_tz = timezone(timedelta(hours=3))
        local_dt = dt.astimezone(palestine_tz)
        period = "ص" if local_dt.hour < 12 else "م"
        hour_12 = local_dt.hour % 12
        if hour_12 == 0:
            hour_12 = 12
        return f"<code>{local_dt.year}-{local_dt.month:02d}-{local_dt.day:02d} | {hour_12:02d}:{local_dt.minute:02d} {period}</code>"
    except Exception:
        return f"<code>{iso_str}</code>"


def check_global_burst(chat_id: str) -> bool:
    """Returns True if the request should be silently dropped (burst clicks within cooldown)."""
    now = time.time()
    last_any = USER_ACTIVITY.get(chat_id, {}).get("last_any_command", 0)
    if now - last_any < GLOBAL_COMMAND_COOLDOWN:
        return True
    if chat_id not in USER_ACTIVITY:
        USER_ACTIVITY[chat_id] = {}
    USER_ACTIVITY[chat_id]["last_any_command"] = now
    return False


def should_suppress_warning(chat_id: str, action: str) -> bool:
    """Returns True if a warning was already sent recently (prevents flood of warning replies)."""
    now = time.time()
    key = f"warn_{action}"
    last_warn = USER_ACTIVITY.get(chat_id, {}).get(key, 0)
    if now - last_warn < WARN_SUPPRESS_SECONDS:
        return True
    if chat_id not in USER_ACTIVITY:
        USER_ACTIVITY[chat_id] = {}
    USER_ACTIVITY[chat_id][key] = now
    return False


def is_user_busy(chat_id: str) -> bool:
    return USER_ACTIVITY.get(chat_id, {}).get("is_scanning", False)


def set_user_busy(chat_id: str, busy: bool) -> None:
    if chat_id not in USER_ACTIVITY:
        USER_ACTIVITY[chat_id] = {}
    USER_ACTIVITY[chat_id]["is_scanning"] = busy


def get_cooldown_remaining(chat_id: str, action: str, cooldown_seconds: int) -> int:
    last_time = USER_ACTIVITY.get(chat_id, {}).get(action, 0)
    elapsed = time.time() - last_time
    if elapsed < cooldown_seconds:
        return int(cooldown_seconds - elapsed)
    return 0


def record_user_action(chat_id: str, action: str) -> None:
    if chat_id not in USER_ACTIVITY:
        USER_ACTIVITY[chat_id] = {}
    USER_ACTIVITY[chat_id][action] = time.time()


async def notify_if_delayed(update: Update) -> None:
    """If the student's message was sent while the laptop was offline, send a polite catch-up notice."""
    if not update.message or not update.message.date:
        return
    try:
        from datetime import datetime, timezone
        age = (datetime.now(timezone.utc) - update.message.date).total_seconds()
        if age > 90:  # Message was waiting for more than 1.5 minutes while bot was off
            chat_id = str(update.effective_chat.id)
            if not should_suppress_warning(chat_id, "delayed_notice"):
                await update.message.reply_text(
                    "⚡ <b>مرحباً بك!</b>\n"
                    "نعتذر عن التأخير؛ كان خادم البوت في وضع الاستراحة/الصيانة المؤقتة أثناء إغلاق الجهاز.\n"
                    "تم استئناف الخدمة بنجاح وجاري تنفيذ طلبك الآن 🚀",
                    parse_mode="HTML"
                )
    except Exception:
        pass


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message or not update.effective_chat:
        return ConversationHandler.END

    await notify_if_delayed(update)
    chat_id = str(update.effective_chat.id)
    burst_rem = get_global_burst_remaining(chat_id)
    if burst_rem > 0:
        if not should_suppress_warning(chat_id, "burst"):
            await update.message.reply_text(
                f"⏳ <b>مهلاً يا بطل!</b> يرجى الانتظار <b>{burst_rem} ثوانٍ</b> قبل إرسال أمر جديد لتجنب الضغط.",
                parse_mode="HTML"
            )
        return ConversationHandler.END

    record_user_action(chat_id, "last_any_command")
    context.user_data.clear()

    db = get_database()
    settings = get_settings()
    row = db.accounts.get_row_by_chat_id(chat_id)

    if row:
        student_id = row["student_id"]
        # If the student had previously stopped monitoring, reactivate it now!
        if not row["is_active"]:
            db.accounts.set_active(chat_id, True)
            status_text = "🟢 تم إعادة تفعيل التنبيهات الدورية لحسابك بنجاح!"
        else:
            status_text = "🟢 حسابك مفعّل والتنبيهات الدورية قيد التشغيل."

        welcome_back = (
            f"👋 <b>أهلاً بك يا <code>{html.escape(student_id)}</code>!</b> 🎓\n"
            "━━━━━━━━━━━━━━━━━━\n"
            f"✅ <b>حسابك مسجل ومربوط بالفعل في البوت، ولا حاجة لإعادة إدخال بياناتك.</b>\n"
            f"🔔 <b>المراقبة التلقائية:</b> {status_text}\n"
            f"⏱️ <b>دورة الفحص:</b> {format_interval(settings.check_interval_minutes)}\n\n"
            "💡 <b>الأوامر المتاحة لك:</b>\n"
            "• /check — فحص مساقاتك الآن للبحث عن أي جديد\n"
            "• /courses — استعراض قائمة مساقاتك المسجلة\n"
            "• /report — تقرير وجرد شامل لكافة المحتويات\n"
            "• /status — حالة المراقبة وآخر فحص\n"
            "• /stop — إيقاف التنبيهات الدورية مؤقتاً\n"
            "• /help — دليل الأوامر والمساعدة\n\n"
            "🔄 <i>إذا أردت تسجيل الخروج أو ربط رقم جامعي آخر، أرسل: /logout</i>"
        )
        await update.message.reply_text(welcome_back, parse_mode="HTML")
        return ConversationHandler.END

    # New student: prompt for student ID
    await update.message.reply_text(WELCOME, parse_mode="HTML", reply_markup=get_github_keyboard())
    return ASK_STUDENT_ID


async def receive_student_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message or not update.message.text:
        return ASK_STUDENT_ID

    student_id = clean_student_id(update.message.text)
    if not student_id or not student_id.isdigit():
        await update.message.reply_text(ASK_ID_AGAIN, parse_mode="HTML")
        return ASK_STUDENT_ID

    if len(student_id) < 5 or len(student_id) > 32:
        await update.message.reply_text("الرقم الجامعي غير صالح (يجب أن يكون بين 5 و 32 رقماً).")
        return ASK_STUDENT_ID

    context.user_data["student_id"] = student_id
    confirmation = (
        f"✅ <b>تم التعرف على رقمك الجامعي:</b> <code>{html.escape(student_id)}</code>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        + ASK_PASSWORD_MSG
    )
    await safe_send_message(context.bot, update.effective_chat.id, confirmation, parse_mode="HTML")
    return ASK_PASSWORD


async def receive_password(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message or not update.message.text:
        return ASK_PASSWORD

    password = update.message.text.strip()
    chat = update.effective_chat
    user = update.effective_user

    try:
        await update.message.delete()
    except Exception:
        pass

    if not chat:
        return ConversationHandler.END

    if len(password) > 128:
        await context.bot.send_message(
            chat_id=chat.id,
            text="كلمة المرور طويلة بشكل غير طبيعي. أرسل /start للمحاولة ثانية.",
        )
        context.user_data.clear()
        return ConversationHandler.END

    student_id = clean_student_id(context.user_data.get("student_id", ""))
    if not student_id:
        await context.bot.send_message(chat_id=chat.id, text=LOGIN_FAILED, parse_mode="HTML")
        context.user_data.clear()
        return ConversationHandler.END

    try:
        await context.bot.send_message(
            chat_id=chat.id,
            text="🔒 <i>تم استلام كلمة المرور ومحاولة حذفها من الدردشة لحمايتك.</i>",
            parse_mode="HTML",
        )
    except Exception:
        pass

    await context.bot.send_message(chat_id=chat.id, text=CHECKING, parse_mode="HTML")

    settings = get_settings()
    account = StudentAccount(
        student_id=student_id,
        password=password,
        telegram_chat_id=str(chat.id),
        telegram_bot_token=settings.telegram_bot_token,
        check_interval_minutes=settings.check_interval_minutes,
    )

    db = get_database()
    ok, error_detail = await asyncio.to_thread(_validate_login, account, db)
    if not ok:
        fail_msg = (
            "❌ <b>تعذر تسجيل الدخول!</b>\n"
            f"⚠️ <b>السبب:</b> {html.escape(error_detail or 'الرقم الجامعي أو كلمة المرور غير صحيحة.')}\n\n"
            "يرجى التأكد من بيانات الدخول وإرسال /start للمحاولة من جديد."
        )
        await safe_send_message(context.bot, chat.id, fail_msg, parse_mode="HTML")
        context.user_data.clear()
        return ConversationHandler.END

    saved = await asyncio.to_thread(
        db.accounts.upsert,
        telegram_chat_id=str(chat.id),
        telegram_user_id=user.id if user else None,
        student_id=student_id,
        password=password,
    )

    await context.bot.send_message(
        chat_id=chat.id,
        text=REGISTERED_HEADER.format(
            student_id=html.escape(saved.student_id),
            interval_str=format_interval(settings.check_interval_minutes),
        ),
        parse_mode="HTML",
    )

    asyncio.create_task(_scan_in_background(saved, force_inventory=True))
    context.user_data.clear()
    return ConversationHandler.END


def _validate_login(account: StudentAccount, db) -> tuple[bool, str]:
    auth = MoodleAuth(session_store=db.sessions)
    ok = auth.login_with_account(account)
    return ok, auth.last_error


async def _scan_in_background(account: StudentAccount, force_inventory: bool = False) -> None:
    chat_id = account.telegram_chat_id
    set_user_busy(chat_id, True)
    try:
        await asyncio.to_thread(
            get_scan_service().scan_account,
            account,
            force_inventory=force_inventory,
        )
        get_database().accounts.mark_scan(account.student_id)
        record_user_action(chat_id, "last_report" if force_inventory else "last_check")
    except Exception as exc:
        print(f"[Bot] Scan failed for {account.student_id}: {exc}")
        get_database().accounts.mark_scan(account.student_id, error=str(exc))
    finally:
        set_user_busy(chat_id, False)


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    if update.message:
        await update.message.reply_text("تم إلغاء عملية التسجيل. أرسل /start في أي وقت للبدء من جديد.")
    return ConversationHandler.END


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_chat:
        return
    await notify_if_delayed(update)
    chat_id = str(update.effective_chat.id)
    burst_rem = get_global_burst_remaining(chat_id)
    if burst_rem > 0:
        if not should_suppress_warning(chat_id, "burst"):
            await update.message.reply_text(
                f"⏳ <b>مهلاً يا بطل!</b> يرجى الانتظار <b>{burst_rem} ثوانٍ</b> قبل إرسال أمر جديد لتجنب الضغط.",
                parse_mode="HTML"
            )
        return
    record_user_action(chat_id, "last_any_command")

    db = get_database()
    settings = get_settings()
    row = db.accounts.get_row_by_chat_id(chat_id)
    if not row:
        await update.message.reply_text(
            "⚠️ <b>لم يتم تسجيل حساب بعد!</b>\n"
            "أرسل /start لتسجيل بياناتك وبدء متابعة مساقاتك.",
            parse_mode="HTML"
        )
        return

    remaining = get_cooldown_remaining(chat_id, "last_status", STATUS_COOLDOWN_SECONDS)
    if remaining > 0:
        if not should_suppress_warning(chat_id, "status"):
            await update.message.reply_text(
                f"⏳ يرجى الانتظار <b>{remaining} ثانية</b> قبل إعادة طلب الحالة.",
                parse_mode="HTML"
            )
        return

    record_user_action(chat_id, "last_status")
    student_id = row["student_id"]
    active_str = "🟢 مفعّلة وتعمل تلقائياً" if row["is_active"] else "🔴 متوقفة مؤقتاً"
    last_scan = format_arabic_datetime(row["last_scan_at"])
    tracked_count = db.seen.count(student_id)

    msg = (
        "📊 <b>حالة الحساب والمراقبة</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"🎓 <b>الرقم الجامعي:</b> <code>{html.escape(student_id)}</code>\n"
        f"🔔 <b>المراقبة التلقائية:</b> {active_str}\n"
        f"📦 <b>إجمالي العناصر المؤرشفة:</b> {tracked_count} عنصر\n"
        f"⏱️ <b>آخر فحص:</b> {last_scan}\n"
        f"🔄 <b>دورة المراقبة الدورية:</b> {format_interval(settings.check_interval_minutes)}\n\n"
        "💡 <i>أوامر سريعة:</i>\n"
        "• /check — فحص يدوي للمساقات الآن (كل 15 دقيقة)\n"
        "• /courses — قائمة المواد المسجلة\n"
        "• /report — تقرير الجرد الكامل\n"
        "• /logout — تسجيل الخروج وربط حساب جديد"
    )
    await update.message.reply_text(msg, parse_mode="HTML")


async def check_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_chat:
        return
    await notify_if_delayed(update)
    chat_id = str(update.effective_chat.id)
    burst_rem = get_global_burst_remaining(chat_id)
    if burst_rem > 0:
        if not should_suppress_warning(chat_id, "burst"):
            await update.message.reply_text(
                f"⏳ <b>مهلاً يا بطل!</b> يرجى الانتظار <b>{burst_rem} ثوانٍ</b> قبل إرسال أمر جديد لتجنب الضغط.",
                parse_mode="HTML"
            )
        return
    record_user_action(chat_id, "last_any_command")

    db = get_database()
    account = db.accounts.load_by_chat_id(chat_id)
    if not account:
        await update.message.reply_text(
            "⚠️ <b>لم تقم بربط حسابك بعد!</b>\n"
            "أرسل /start لتسجيل حسابك وبدء متابعة مساقاتك.",
            parse_mode="HTML"
        )
        return

    lock = USER_LOCKS[chat_id]
    if lock.locked() or is_user_busy(chat_id):
        if not should_suppress_warning(chat_id, "busy"):
            await update.message.reply_text(
                "⏳ <b>لديك طلب قيد المعالجة حالياً!</b>\n"
                "يرجى الانتظار بضع ثوانٍ حتى تكتمل العملية الجارية قبل إرسال أمر جديد 🛡️",
                parse_mode="HTML"
            )
        return

    remaining = get_cooldown_remaining(chat_id, "last_check", CHECK_COOLDOWN_SECONDS)
    if remaining > 0:
        if not should_suppress_warning(chat_id, "check"):
            time_str = format_remaining_time(remaining)
            await update.message.reply_text(
                f"⏳ <b>مهلاً! لقد قمت بالفحص قبل قليل.</b>\n"
                f"لتجنب حظر حسابك والضغط على سيرفرات الجامعة، يمكنك الفحص اليدوي مجدداً بعد <b>{time_str}</b>.\n\n"
                "💡 <i>تذكير: البوت يقوم بفحص مساقاتك تلقائياً كل 12 ساعة وسينبهك فور نزول أي واجب أو ملف جديد دون تدخل منك.</i>",
                parse_mode="HTML"
            )
        return

    status_msg = await update.message.reply_text(
        "🔍 <b>جاري فحص مساقاتك على اليوبينار الآن...</b>\nانتظر بضع ثوانٍ.",
        parse_mode="HTML"
    )
    set_user_busy(chat_id, True)
    try:
        scanner = get_scan_service()
        result = await asyncio.to_thread(scanner.scan_account, account, False)
        record_user_action(chat_id, "last_check")
        get_database().accounts.mark_scan(account.student_id)

        if result is None:
            await status_msg.edit_text(
                "⚠️ <b>تعذر الاتصال باليوبينار حالياً.</b>\n"
                "تأكد من أن كلمة مرورك لم تتغير في بوابة الطالب، أو أعد المحاولة لاحقاً.",
                parse_mode="HTML"
            )
        elif result.new_items:
            await status_msg.edit_text(
                f"🎉 <b>تم الفحص بنجاح:</b>\n"
                f"تم العثور على <b>{len(result.new_items)}</b> عنصر جديد وتم إرسال الإشعارات إليك بالأعلى!",
                parse_mode="HTML",
            )
        else:
            await status_msg.edit_text(
                f"✅ <b>فحص مكتمل:</b>\n"
                f"تم فحص <b>{len(result.courses)} مساقات</b> مسجلة، وجميع محتوياتك محدثة بالكامل ولا يوجد أي واجب أو كويز أو ملف جديد حالياً.",
                parse_mode="HTML",
            )
    except Exception as exc:
        print(f"[Check Command Error] {exc}")
        await status_msg.edit_text(f"⚠️ حدث خطأ أثناء الفحص: {exc}")
    finally:
        set_user_busy(chat_id, False)


async def courses_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_chat:
        return
    await notify_if_delayed(update)
    chat_id = str(update.effective_chat.id)
    burst_rem = get_global_burst_remaining(chat_id)
    if burst_rem > 0:
        if not should_suppress_warning(chat_id, "burst"):
            await update.message.reply_text(
                f"⏳ <b>مهلاً يا بطل!</b> يرجى الانتظار <b>{burst_rem} ثوانٍ</b> قبل إرسال أمر جديد لتجنب الضغط.",
                parse_mode="HTML"
            )
        return
    record_user_action(chat_id, "last_any_command")

    db = get_database()
    account = db.accounts.load_by_chat_id(chat_id)
    if not account:
        await update.message.reply_text(
            "⚠️ <b>لم تقم بتسجيل حسابك بعد!</b>\n"
            "أرسل /start لتسجيل حسابك وبدء متابعة مساقاتك.",
            parse_mode="HTML"
        )
        return

    lock = USER_LOCKS[chat_id]
    if lock.locked() or is_user_busy(chat_id):
        if not should_suppress_warning(chat_id, "busy"):
            await update.message.reply_text(
                "⏳ <b>لديك طلب قيد المعالجة حالياً!</b>\n"
                "يرجى الانتظار بضع ثوانٍ حتى تكتمل العملية الجارية قبل إرسال أمر جديد 🛡️",
                parse_mode="HTML"
            )
        return

    remaining = get_cooldown_remaining(chat_id, "last_courses", COURSES_COOLDOWN_SECONDS)
    if remaining > 0:
        if not should_suppress_warning(chat_id, "courses"):
            time_str = format_remaining_time(remaining)
            await update.message.reply_text(
                f"⏳ <b>مهلاً! لقد قمت بعرض المساقات قبل قليل.</b>\n"
                f"يرجى الانتظار <b>{time_str}</b> قبل إعادة طلب القائمة لتجنب إجهاد خوادم الجامعة.\n\n"
                "💡 <i>يمكنك مراجعة القائمة السابقة في المحادثة بالأعلى دون الحاجة لإعادة طلبها.</i>",
                parse_mode="HTML"
            )
        return

    set_user_busy(chat_id, True)
    status_msg = await update.message.reply_text("📚 <b>جاري جلب قائمة مساقاتك المسجلة...</b>", parse_mode="HTML")
    try:
        auth = MoodleAuth(session_store=db.sessions)
        session = await asyncio.to_thread(auth.get_session, account)
        if not session:
            await status_msg.edit_text(
                "⚠️ <b>تعذر الاتصال باليوبينار حالياً.</b>\n"
                "يرجى التأكد من استقرار الإنترنت والمحاولة لاحقاً.",
                parse_mode="HTML"
            )
            return

        from .moodle_client import MoodleClient
        client = MoodleClient()
        courses = await asyncio.to_thread(client.get_enrolled_courses, session)
        record_user_action(chat_id, "last_courses")
        if not courses:
            await status_msg.edit_text(
                "لم يتم العثور على أي مساقات مسجلة لهذا الفصل الدراسي.",
                parse_mode="HTML"
            )
            return

        lines = [
            "📚 <b>مساقاتك المسجلة في اليوبينار:</b>",
            "━━━━━━━━━━━━━━━━━━",
        ]
        for idx, c in enumerate(courses, 1):
            c_title = html.escape(c.title or f"مساق #{c.id}")
            c_url = html.escape(c.url or f"https://upinar.edu.ps/course/view.php?id={c.id}")
            lines.append(f"<b>{idx}.</b> <a href=\"{c_url}\">{c_title}</a>")

        lines.append("\n💡 <i>لعرض كافة الواجبات والمحتويات بالتفصيل لكل مساق، أرسل: /report</i>")
        await status_msg.edit_text(
            "\n".join(lines),
            parse_mode="HTML",
            disable_web_page_preview=True
        )
    except Exception as exc:
        print(f"[Courses Command Error] {exc}")
        await status_msg.edit_text(f"⚠️ حدث خطأ أثناء جلب المساقات: {exc}")
    finally:
        set_user_busy(chat_id, False)


async def report_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_chat:
        return
    await notify_if_delayed(update)
    chat_id = str(update.effective_chat.id)
    burst_rem = get_global_burst_remaining(chat_id)
    if burst_rem > 0:
        if not should_suppress_warning(chat_id, "burst"):
            await update.message.reply_text(
                f"⏳ <b>مهلاً يا بطل!</b> يرجى الانتظار <b>{burst_rem} ثوانٍ</b> قبل إرسال أمر جديد لتجنب الضغط.",
                parse_mode="HTML"
            )
        return
    record_user_action(chat_id, "last_any_command")

    db = get_database()
    account = db.accounts.load_by_chat_id(chat_id)
    if not account:
        await update.message.reply_text(
            "⚠️ <b>لم تقم بربط حسابك بعد!</b>\n"
            "أرسل /start لتسجيل حسابك وبدء متابعة مساقاتك.",
            parse_mode="HTML"
        )
        return

    lock = USER_LOCKS[chat_id]
    if lock.locked() or is_user_busy(chat_id):
        if not should_suppress_warning(chat_id, "busy"):
            await update.message.reply_text(
                "⏳ <b>لديك طلب قيد المعالجة حالياً!</b>\n"
                "يرجى الانتظار بضع ثوانٍ حتى تكتمل العملية الجارية قبل إرسال أمر جديد 🛡️",
                parse_mode="HTML"
            )
        return

    remaining = get_cooldown_remaining(chat_id, "last_report", REPORT_COOLDOWN_SECONDS)
    if remaining > 0:
        if not should_suppress_warning(chat_id, "report"):
            time_str = format_remaining_time(remaining)
            await update.message.reply_text(
                f"⏳ <b>تم إرسال تقرير الجرد مؤخراً!</b>\n"
                f"لتفادي إرهاق خوادم الجامعة، يمكنك طلب تقرير جديد بعد <b>{time_str}</b>.\n\n"
                "💡 <i>لعرض أسماء المواد المسجلة فقط بشكل سريع وبدون انتظار، أرسل: /courses</i>",
                parse_mode="HTML"
            )
        return

    await update.message.reply_text(
        "📊 <b>جاري إعداد تقرير شامل لجميع مساقاتك والمحتوى المتوفر الآن...</b>\n"
        "قد يستغرق ذلك نصف دقيقة، ستصلك البطاقات تباعاً بالأسفل 🚀",
        parse_mode="HTML"
    )
    asyncio.create_task(_scan_in_background(account, force_inventory=True))


async def stop_monitoring(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_chat:
        return
    chat_id = str(update.effective_chat.id)
    burst_rem = get_global_burst_remaining(chat_id)
    if burst_rem > 0:
        return
    record_user_action(chat_id, "last_any_command")

    db = get_database()
    row = db.accounts.get_row_by_chat_id(chat_id)
    if not row:
        await update.message.reply_text("لا يوجد حساب مرتبط بهذه المحادثة. أرسل /start للبدء.")
        return
    db.accounts.set_active(chat_id, False)
    await update.message.reply_text(
        "⏸️ <b>تم إيقاف التنبيهات الدورية لحسابك بنجاح.</b>\n"
        "لن تصلك أي إشعارات تلقائية حتى تقوم بإعادة تفعيلها بإرسال /start في أي وقت تشاء.",
        parse_mode="HTML"
    )


async def logout_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_chat:
        return
    chat_id = str(update.effective_chat.id)
    burst_rem = get_global_burst_remaining(chat_id)
    if burst_rem > 0:
        return
    record_user_action(chat_id, "last_any_command")

    db = get_database()
    row = db.accounts.get_row_by_chat_id(chat_id)
    if not row:
        await update.message.reply_text("أنت غير مسجل بالأساس! أرسل /start لتسجيل حسابك الجديد.")
        return

    student_id = row["student_id"]
    db.accounts.delete_by_chat_id(chat_id)
    try:
        db.sessions.delete(student_id)
    except Exception:
        pass
    context.user_data.clear()

    await update.message.reply_text(
        f"🚪 <b>تم تسجيل الخروج بنجاح!</b>\n"
        f"تم مسح بيانات الطالب <code>{html.escape(student_id)}</code> من البوت بأمان.\n\n"
        "لربط حساب جديد في أي وقت، أرسل: /start",
        parse_mode="HTML"
    )
    if not row:
        await update.message.reply_text("لا يوجد حساب مرتبط بهذه المحادثة. أرسل /start للبدء.")
        return
    db.accounts.set_active(chat_id, False)
    await update.message.reply_text(
        "⏸️ <b>تم إيقاف التنبيهات الدورية لحسابك بنجاح.</b>\n"
        "لن تصلك أي إشعارات تلقائية حتى تقوم بإعادة تفعيلها بإرسال /start في أي وقت تشاء.",
        parse_mode="HTML"
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_chat:
        return
    chat_id = str(update.effective_chat.id)
    if check_global_burst(chat_id):
        return
    await update.message.reply_text(HELP, parse_mode="HTML")


async def handle_media_in_conv(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message:
        try:
            await update.message.delete()
        except Exception:
            pass
        await update.message.reply_text(
            "⚠️ <b>يرجى إرسال نص فقط!</b>\n"
            "هذا البوت لا يستقبل الصور أو الوسائط أو الملفات. الرجاء كتابة رقمك الجامعي أو كلمة المرور كنص عادي.",
            parse_mode="HTML"
        )


async def handle_unsupported_media(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    try:
        await update.message.delete()
    except Exception:
        pass
    await update.message.reply_text(
        "🚫 <b>عذراً، استقبال الوسائط غير مدعوم!</b>\n"
        "تم حذف الرسالة تلقائياً للحفاظ على نظافة وأمان المحادثة. هذا البوت مخصص حصرياً لمتابعة مساقات ومنصة اليوبينار الجامعية، ولا يدعم استقبال الصور أو الفيديوهات أو الملفات.\n\n"
        "💡 <i>أرسل /help للاطلاع على قائمة الأوامر المتاحة.</i>",
        parse_mode="HTML"
    )


async def unknown_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_chat:
        return
    if check_global_burst(str(update.effective_chat.id)):
        return
    await update.message.reply_text(
        "⚠️ <b>عذراً، هذا الأمر غير معروف!</b>\n"
        "الأمر الذي أرسلته غير مسجل في النظام.\n\n"
        "📖 أرسل /help لعرض قائمة الأوامر المعتمدة، أو /start للبدء.",
        parse_mode="HTML"
    )


async def unknown_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_chat:
        return
    await notify_if_delayed(update)
    if check_global_burst(str(update.effective_chat.id)):
        return
    await update.message.reply_text(
        "مرحباً بك في بوت يوبينار جامعة فلسطين! 🎓\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "للتفاعل مع البوت، يرجى استخدام أحد الأوامر المعتمدة التالية:\n\n"
        "🔹 /start — تسجيل أو تحديث بيانات الحساب والبدء\n"
        "🔹 /check — فحص مساقاتك الآن للبحث عن أي جديد\n"
        "🔹 /courses — عرض قائمة مساقاتك المسجلة وروابطها\n"
        "🔹 /report — جرد كامل وشامل لجميع الواجبات والمحتويات\n"
        "🔹 /status — حالة المراقبة وموعد الفحص القادم\n"
        "🔹 /stop — إيقاف التنبيهات الدورية مؤقتاً\n"
        "🔹 /logout — تسجيل الخروج وربط حساب جديد\n"
        "🔹 /help — دليل استخدام البوت والمساعدة",
        parse_mode="HTML"
    )


async def monitor_loop(application: Application) -> None:
    settings = get_settings()
    interval_seconds = settings.check_interval_minutes * 60
    print(f"[Monitor] Background checker configured: every {settings.check_interval_minutes} minutes ({format_interval(settings.check_interval_minutes)}).")
    await asyncio.sleep(60)
    while True:
        started = time.time()
        try:
            await asyncio.to_thread(scan_all_active)
        except Exception as exc:
            print(f"[Monitor] Cycle error: {exc}")
        elapsed = time.time() - started
        sleep_for = max(10, interval_seconds - elapsed)
        print(f"[Monitor] Cycle complete. Sleeping {int(sleep_for)} seconds until next periodic check.")
        await asyncio.sleep(sleep_for)


async def on_startup(application: Application) -> None:
    get_database()
    application.bot_data["monitor_task"] = asyncio.create_task(monitor_loop(application))
    try:
        commands = [
            BotCommand("start", "بدء الاستخدام أو استئناف التنبيهات"),
            BotCommand("check", "فحص مساقاتك الآن للبحث عن أي جديد"),
            BotCommand("courses", "عرض قائمة مساقاتك المسجلة"),
            BotCommand("report", "جرد شامل لجميع المحتويات والواجبات"),
            BotCommand("status", "حالة المراقبة وموعد الفحص القادم"),
            BotCommand("stop", "إيقاف التنبيهات الدورية مؤقتاً"),
            BotCommand("logout", "تسجيل الخروج وحذف الحساب"),
            BotCommand("help", "دليل استخدام البوت والمساعدة"),
        ]
        await application.bot.set_my_commands(commands)
        desc = (
            "🎓 مرحباً بك في بوت يوبينار جامعة فلسطين (UPINAR Notifier)!\n\n"
            "هذا البوت مخصص لطلاب الجامعة لمتابعة منصة الموديل (UPINAR) وإرسال تنبيهات فورية بكل واجب أو كويز أو محاضرة جديدة 🚀\n\n"
            "⭐ ادعم المشروع بنجمة (Star) على GitHub:\n"
            "https://github.com/medozaiter/upinar-notifier"
        )
        await application.bot.set_my_description(description=desc)
        await application.bot.set_my_short_description(
            short_description="بوت يوبينار جامعة فلسطين لمتابعة المحاضرات والواجبات 🎓 | ادعمنا بنجمة على GitHub!"
        )
    except Exception as exc:
        print(f"[Bot Warning] Failed to set bot commands/description menu: {exc}")
    print("[Bot] Telegram bot service started successfully. Listening for student commands...")


def build_application() -> Application:
    settings = get_settings()
    request = HTTPXRequest(
        connection_pool_size=16,
        connect_timeout=35.0,
        read_timeout=35.0,
        write_timeout=35.0,
        pool_timeout=10.0,
        proxy=settings.telegram_proxy,
    )
    application = (
        Application.builder()
        .token(settings.telegram_bot_token)
        .request(request)
        .post_init(on_startup)
        .build()
    )

    conversation = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            ASK_STUDENT_ID: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_student_id),
                MessageHandler(~filters.TEXT & ~filters.COMMAND, handle_media_in_conv),
            ],
            ASK_PASSWORD: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_password),
                MessageHandler(~filters.TEXT & ~filters.COMMAND, handle_media_in_conv),
            ],
        },
        fallbacks=[
            CommandHandler("start", start),
            CommandHandler("cancel", cancel),
            CommandHandler("help", help_command),
            CommandHandler("status", status_command),
            CommandHandler("check", check_command),
            CommandHandler("report", report_command),
            CommandHandler("courses", courses_command),
            CommandHandler("stop", stop_monitoring),
            CommandHandler("logout", logout_command),
        ],
        allow_reentry=True,
    )

    application.add_handler(conversation)
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(CommandHandler("check", check_command))
    application.add_handler(CommandHandler("report", report_command))
    application.add_handler(CommandHandler("courses", courses_command))
    application.add_handler(CommandHandler("stop", stop_monitoring))
    application.add_handler(CommandHandler("logout", logout_command))

    # Reject unsupported media (photos, videos, audio, documents, stickers, animations)
    unsupported_media = (
        filters.PHOTO
        | filters.VIDEO
        | filters.AUDIO
        | filters.VOICE
        | filters.Document.ALL
        | filters.Sticker.ALL
        | filters.ANIMATION
    )
    application.add_handler(MessageHandler(unsupported_media, handle_unsupported_media))

    # Reject unknown slash commands
    application.add_handler(MessageHandler(filters.COMMAND, unknown_command_handler))

    # Handle arbitrary non-command text
    application.add_handler(MessageHandler(filters.TEXT, unknown_text_handler))
    application.add_error_handler(global_error_handler)

    return application


def _start_render_health_server() -> None:
    """If PORT is set (like on Render Web Services), start a minimal HTTP health-check server."""
    port_str = os.getenv("PORT")
    if not port_str:
        return
    try:
        from http.server import HTTPServer, BaseHTTPRequestHandler
        import threading

        port = int(port_str)

        class HealthHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.end_headers()
                self.wfile.write(b"UPINAR Telegram Bot is running!\n")

            def do_HEAD(self):
                self.send_response(200)
                self.end_headers()

            def log_message(self, format, *args):
                pass

        server = HTTPServer(("0.0.0.0", port), HealthHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        print(f"[Render] Health-check HTTP server listening on port {port}")
    except Exception as exc:
        print(f"[Render Warning] Could not start health server on port {port_str}: {exc}")


def run_bot() -> None:
    _start_render_health_server()

    # Ensure an active asyncio event loop exists in MainThread (required in Python 3.10+ / Linux / Render)
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    from .paths import BASE_DIR
    pid_file = os.path.join(BASE_DIR, "data", "bot.pid")
    try:
        os.makedirs(os.path.dirname(pid_file), exist_ok=True)
        with open(pid_file, "w", encoding="utf-8") as f:
            f.write(str(os.getpid()))
    except Exception:
        pass

    settings = get_settings()
    print("=" * 60)
    print("      UPINAR Notifier - Multi-Student University Bot")
    print(f"      Monitoring interval: every {settings.check_interval_minutes} minutes ({format_interval(settings.check_interval_minutes)})")
    print("=" * 60)

    # Wait for network connectivity (vital on system boot while Wi-Fi connects)
    _wait_for_network(settings.telegram_bot_token)

    # Pre-flight check to verify Telegram token
    try:
        import requests
        proxies = (
            {"http": settings.telegram_proxy, "https": settings.telegram_proxy}
            if settings.telegram_proxy
            else None
        )
        check = requests.get(
            f"https://api.telegram.org/bot{settings.telegram_bot_token}/getMe",
            timeout=25,
            proxies=proxies,
        )
        data = check.json()
        if not data.get("ok"):
            print("\n" + "!" * 65)
            print("❌ خطأ: تم رفض توكن البوت من سيرفر تيليجرام (Invalid/Rejected Token)!")
            print(f"السبب: {data.get('description', 'Unauthorized')}")
            print("الرجاء وضع التوكن الصحيح الذي أنشأته من @BotFather داخل ملف .env")
            print("!" * 65 + "\n")
            return
        bot_info = data.get("result", {})
        print(f"[Bot] Connected to Telegram as @{bot_info.get('username', 'Unknown')}")
    except Exception as exc:
        print(f"[Warning] Could not perform pre-flight token check: {exc}")

    # Resilient polling loop with auto-reconnect on network drops
    while True:
        try:
            application = build_application()
            application.run_polling(
                allowed_updates=Update.ALL_TYPES,
                drop_pending_updates=False,
                timeout=20,
                poll_interval=0.5,
                bootstrap_retries=-1,
            )
            break
        except InvalidToken:
            print("\n" + "!" * 65)
            print("❌ خطأ: تم رفض توكن البوت من سيرفر تيليجرام (Invalid/Rejected Token)!")
            print("الرجاء التأكد من وضع التوكن الصحيح الذي أنشأته من @BotFather داخل ملف .env")
            print("!" * 65 + "\n")
            break
        except Exception as exc:
            import traceback
            traceback.print_exc()
            print(f"\n[Bot Network Error] {exc}. Reconnecting in 5 seconds...")
            time.sleep(5)
            _wait_for_network(settings.telegram_bot_token, max_wait=60)
        finally:
            try:
                if os.path.exists(pid_file):
                    with open(pid_file, "r", encoding="utf-8") as f:
                        if f.read().strip() == str(os.getpid()):
                            os.remove(pid_file)
            except Exception:
                pass


def _wait_for_network(bot_token: str, max_wait: int = 120) -> bool:
    """Waits until DNS resolution and HTTP connectivity to Telegram are established."""
    import socket, requests
    print("[Network] Checking internet connection to Telegram...")
    start = time.time()
    while time.time() - start < max_wait:
        try:
            socket.getaddrinfo("api.telegram.org", 443)
            r = requests.get(f"https://api.telegram.org/bot{bot_token}/getMe", timeout=10)
            if r.status_code in (200, 401, 404):
                print("[Network] Internet connection confirmed active.")
                return True
        except Exception:
            pass
        time.sleep(3)
    print("[Network Warning] Network check timed out; attempting startup anyway.")
    return False
