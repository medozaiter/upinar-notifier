import json
import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")

def main():
    print("=" * 60)
    print("      إعداد بيانات نظام مراقبة اليوبينار (UPINAR Notifier)")
    print("=" * 60)
    print()

    # Read existing config if present
    current_cfg = {}
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                current_cfg = json.load(f)
        except:
            pass

    default_student_id = current_cfg.get("student_id", "120251381")

    print(f"1. الرقم الجامعي الحالي: {default_student_id}")
    student_id_input = input("   اضغط Enter للإبقاء عليه، أو اكتب رقماً جديداً: ").strip()
    student_id = student_id_input if student_id_input else default_student_id

    print()
    password = input("2. أدخل كلمة مرور بوابة الطالب (UP Portal Password): ").strip()
    while not password:
        print("   ⚠️ كلمة المرور مطلوبة لتسجيل الدخول.")
        password = input("2. أدخل كلمة المرور: ").strip()

    print()
    print("3. توكن بوت التيليجرام (API Token من BotFather):")
    print("   مثال: 7891234567:AAFlz-xxxxxxxxxxxxxxxxxxxx")
    bot_token = input("   أدخل التوكن: ").strip()
    while not bot_token:
        print("   ⚠️ التوكن مطلوب لإرسال الإشعارات.")
        bot_token = input("   أدخل التوكن: ").strip()

    print()
    print("4. معرّف التيليجرام الخاص بك (Chat ID من userinfobot):")
    print("   مثال: 1234567890")
    chat_id = input("   أدخل رقم الـ Chat ID: ").strip()
    while not chat_id:
        print("   ⚠️ الـ Chat ID مطلوب.")
        chat_id = input("   أدخل رقم الـ Chat ID: ").strip()

    config_data = {
        "student_id": student_id,
        "password": password,
        "telegram_bot_token": bot_token,
        "telegram_chat_id": chat_id,
        "check_interval_minutes": current_cfg.get("check_interval_minutes", 30)
    }

    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config_data, f, ensure_ascii=False, indent=2)

    print()
    print("=" * 60)
    print("✅ تم حفظ الإعدادات بنجاح في config.json!")
    print("=" * 60)
    print()

    # Ask if user wants to test telegram
    test = input("هل ترغب في فحص الاتصال مع التيليجرام الآن؟ (y/n) [افتراضي y]: ").strip().lower()
    if test != 'n':
        print()
        from test_telegram import test_telegram
        test_telegram()

    print()
    print("جاهز الآن! يمكنك تشغيل النظام في أي وقت عبر تشغيل start.bat")

if __name__ == "__main__":
    main()
