import asyncio
import html
import os
import time

from telegram import Update
from telegram.error import InvalidToken
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
from .models import StudentAccount
from .runtime import get_scan_service, scan_all_active
from .settings import get_settings

ASK_STUDENT_ID, ASK_PASSWORD = range(2)

WELCOME = (
    "مرحباً بك في بوت يوبينار UPINAR Notifier 🎓\n"
    "━━━━━━━━━━━━━━━━━━\n"
    "هذا البوت مخصص لطلاب <b>جامعة فلسطين</b> لمتابعة مساقات الموديل (اليوبينار) تلقائياً، "
    "وإرسال إشعارات فورية بكل واجب، كويز، محاضرة، إعلان، أو شبتر جديد.\n\n"
    "الخطوة [1/2]:\n"
    "من فضلك أرسل <b>رقمك الجامعي</b> للمتابعة:"
)

ASK_ID_AGAIN = (
    "الرجاء إدخال رقمك الجامعي بشكل صحيح (أرقام فقط)."
)

ASK_PASSWORD_MSG = (
    "الخطوة [2/2]:\n"
    "الآن أرسل <b>كلمة مرور بوابة الطالب</b> (Portal Password):\n\n"
    "🔒 <i>ملاحظة أمنية مشددة:</i>\n"
    "• سيتم حذف رسالتك تلقائياً فور استلامها لحماية خصوصيتك.\n"
    "• يتم تشفير كلمة المرور بقوة (AES-256) قبل حفظها في قاعدة البيانات.\n"
    "• لن يطّلع عليها أي شخص ولن يتم مشاركتها أبداً."
)

CHECKING = (
    "⏳ <b>جاري التحقق من بيانات الدخول...</b>\n"
    "يتم الاتصال ببوابة الطالب ونظام الموديل (SSO). قد يستغرق ذلك نصف دقيقة، انتظر لحظة..."
)

LOGIN_FAILED = (
    "❌ <b>تعذر تسجيل الدخول!</b>\n"
    "الرقم الجامعي أو كلمة المرور غير صحيحة، أو أن خوادم الجامعة بطيئة حالياً.\n\n"
    "يرجى التأكد من بيانات الدخول وإرسال /start للمحاولة من جديد."
)

REGISTERED_HEADER = (
    "✅ <b>تم التحقق وتفعيل الحساب بنجاح!</b>\n"
    "━━━━━━━━━━━━━━━━━━\n"
    "🎓 الرقم الجامعي: <code>{student_id}</code>\n"
    "🔔 دورة المراقبة: <b>كل {interval} دقيقة</b>\n\n"
    "🔍 <i>جاري الآن فحص مساقاتك وجلب الواجبات، الشباتر، والمحاضرات لأول مرة... "
    "سنرسل لك تقريراً شاملاً ومرتباً بالأسفل خلال لحظات 🚀</i>"
)

HELP = (
    "📖 <b>دليل أوامر بوت يوبينار (UPINAR Notifier)</b>\n"
    "━━━━━━━━━━━━━━━━━━\n"
    "/start — تسجيل أو تحديث بيانات الحساب والبدء\n"
    "/check — فحص فوري للمساقات للبحث عن أي جديد الآن\n"
    "/report — إرسال تقرير وجرد شامل لجميع مساقاتك والمحتوى الحالي\n"
    "/courses — استعراض مساقاتك المسجلة\n"
    "/status — عرض حالة المراقبة وآخر موعد فحص\n"
    "/stop — إيقاف التنبيهات مؤقتاً\n"
    "/cancel — إلغاء عملية التسجيل الحالية\n"
    "/help — عرض هذه المساعدة\n\n"
    "🔒 <b>الأمان والخصوصية:</b>\n"
    "بيانات كل طالب معزولة تماماً، وتُحفظ كلمات المرور مشفرة بتقنية AES-256."
)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    if update.message:
        await update.message.reply_text(WELCOME, parse_mode="HTML")
    return ASK_STUDENT_ID


async def receive_student_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message or not update.message.text:
        return ASK_STUDENT_ID

    student_id = update.message.text.strip()
    if not student_id or not student_id.isdigit():
        await update.message.reply_text(ASK_ID_AGAIN)
        return ASK_STUDENT_ID

    if len(student_id) < 5 or len(student_id) > 32:
        await update.message.reply_text("الرقم الجامعي غير صالح (يجب أن يكون بين 5 و 32 رقماً).")
        return ASK_STUDENT_ID

    context.user_data["student_id"] = student_id
    await update.message.reply_text(ASK_PASSWORD_MSG, parse_mode="HTML")
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

    student_id = str(context.user_data.get("student_id", "")).strip()
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
        await context.bot.send_message(chat_id=chat.id, text=fail_msg, parse_mode="HTML")
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
            interval=settings.check_interval_minutes,
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
    try:
        await asyncio.to_thread(
            get_scan_service().scan_account,
            account,
            force_inventory=force_inventory,
        )
        get_database().accounts.mark_scan(account.student_id)
    except Exception as exc:
        print(f"[Bot] Scan failed for {account.student_id}: {exc}")
        get_database().accounts.mark_scan(account.student_id, error=str(exc))


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    if update.message:
        await update.message.reply_text("تم إلغاء العملية. أرسل /start للبدء من جديد.")
    return ConversationHandler.END


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_chat:
        return
    db = get_database()
    settings = get_settings()
    row = db.accounts.get_row_by_chat_id(str(update.effective_chat.id))
    if not row:
        await update.message.reply_text("لم يتم تسجيل حساب بعد. أرسل /start للبدء.")
        return

    student_id = row["student_id"]
    active_str = "🟢 مفعّلة وتعمل تلقائياً" if row["is_active"] else "🔴 متوقفة مؤقتاً"
    last_scan = row["last_scan_at"] or "لم يتم بعد"
    tracked_count = db.seen.count(student_id)

    msg = (
        "📊 <b>حالة الحساب والمراقبة</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"🎓 <b>الرقم الجامعي:</b> <code>{html.escape(student_id)}</code>\n"
        f"🔔 <b>المراقبة:</b> {active_str}\n"
        f"📦 <b>العناصر المأرشفة:</b> {tracked_count} عنصر\n"
        f"⏱️ <b>آخر فحص:</b> {last_scan}\n"
        f"🔄 <b>دورة الفحص:</b> كل {settings.check_interval_minutes} دقيقة\n\n"
        "💡 للفحص الفوري الآن: /check\n"
        "💡 لعرض تقرير المحتوى: /report"
    )
    await update.message.reply_text(msg, parse_mode="HTML")


async def check_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_chat:
        return
    db = get_database()
    account = db.accounts.load_by_chat_id(str(update.effective_chat.id))
    if not account:
        await update.message.reply_text("لم تقم بربط حسابك بعد. أرسل /start للبدء.")
        return

    status_msg = await update.message.reply_text("🔍 جاري فحص مساقاتك على اليوبينار الآن... انتظر لحظات.")
    scanner = get_scan_service()
    result = await asyncio.to_thread(scanner.scan_account, account, False)

    if result is None:
        await status_msg.edit_text(
            "⚠️ تعذر الاتصال باليوبينار حالياً. تأكد من أن كلمة مرورك لم تتغير في البوابة، أو أعد المحاولة لاحقاً."
        )
    elif result.new_items:
        await status_msg.edit_text(
            f"🎉 تم الفحص: تم العثور على <b>{len(result.new_items)}</b> عنصر جديد وتم إرسال التنبيهات بالأعلى!",
            parse_mode="HTML",
        )
    else:
        await status_msg.edit_text(
            f"✅ فحص مكتمل: تم فحص <b>{len(result.courses)} مساقات</b>، وجميع محتوياتك محدثة ولا يوجد أي واجب أو ملف جديد.",
            parse_mode="HTML",
        )


async def report_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_chat:
        return
    db = get_database()
    account = db.accounts.load_by_chat_id(str(update.effective_chat.id))
    if not account:
        await update.message.reply_text("لم تقم بربط حسابك بعد. أرسل /start للبدء.")
        return

    await update.message.reply_text("📊 جاري إعداد تقرير شامل لجميع مساقاتك والمحتوى المتوفر الآن... انتظر قليلاً.")
    asyncio.create_task(_scan_in_background(account, force_inventory=True))


async def stop_monitoring(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_chat:
        return
    db = get_database()
    row = db.accounts.get_row_by_chat_id(str(update.effective_chat.id))
    if not row:
        await update.message.reply_text("لا يوجد حساب مرتبط بهذه المحادثة. أرسل /start.")
        return
    db.accounts.set_active(str(update.effective_chat.id), False)
    await update.message.reply_text(
        "⏸️ تم إيقاف التنبيهات الدورية لحسابك.\nيمكنك إعادة تفعيلها في أي وقت بإرسال /start."
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message:
        await update.message.reply_text(HELP, parse_mode="HTML")


async def monitor_loop(application: Application) -> None:
    settings = get_settings()
    interval_seconds = settings.check_interval_minutes * 60
    print(f"[Monitor] Background checker configured: every {settings.check_interval_minutes} minutes.")
    await asyncio.sleep(10)
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
    print("[Bot] Telegram bot service started successfully. Listening for student commands...")


def build_application() -> Application:
    settings = get_settings()
    application = (
        Application.builder()
        .token(settings.telegram_bot_token)
        .post_init(on_startup)
        .build()
    )

    conversation = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            ASK_STUDENT_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_student_id)],
            ASK_PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_password)],
        },
        fallbacks=[
            CommandHandler("start", start),
            CommandHandler("cancel", cancel),
        ],
        allow_reentry=True,
    )

    application.add_handler(conversation)
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(CommandHandler("check", check_command))
    application.add_handler(CommandHandler("report", report_command))
    application.add_handler(CommandHandler("courses", report_command))
    application.add_handler(CommandHandler("stop", stop_monitoring))
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

    settings = get_settings()
    print("=" * 60)
    print("      UPINAR Notifier - Multi-Student University Bot")
    print(f"      Monitoring interval: every {settings.check_interval_minutes} minutes")
    print("=" * 60)

    # Pre-flight check to verify Telegram token before initializing the polling loop
    try:
        import requests
        check = requests.get(
            f"https://api.telegram.org/bot{settings.telegram_bot_token}/getMe",
            timeout=5,
        )
        data = check.json()
        if not data.get("ok"):
            print("\n" + "!" * 65)
            print("❌ خطأ: تم رفض توكن البوت من سيرفر تيليجرام (Invalid/Rejected Token)!")
            print(f"السبب: {data.get('description', 'Unauthorized')}")
            print("الرجاء وضع التوكن الصحيح الذي أنشأته من @BotFather داخل ملف .env")
            print("مثال: TELEGRAM_BOT_TOKEN=123456789:ABCdefGhIJKlmNoPQRstuVWXyz")
            print("!" * 65 + "\n")
            return
        bot_info = data.get("result", {})
        print(f"[Bot] Connected to Telegram as @{bot_info.get('username', 'Unknown')}")
    except Exception as exc:
        print(f"[Warning] Could not perform pre-flight token check: {exc}")

    try:
        application = build_application()
        application.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)
    except InvalidToken:
        print("\n" + "!" * 65)
        print("❌ خطأ: تم رفض توكن البوت من سيرفر تيليجرام (Invalid/Rejected Token)!")
        print("الرجاء التأكد من وضع التوكن الصحيح الذي أنشأته من @BotFather داخل ملف .env")
        print("!" * 65 + "\n")
    except Exception as exc:
        import traceback
        traceback.print_exc()
        print(f"\n[Bot Error] {exc}")
