import json
import os
import sys

# Ensure UTF-8 output on Windows console
if sys.stdout:
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except:
        pass

from telegram_notifier import send_telegram_message

CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

def test_telegram():
    if not os.path.exists(CONFIG_FILE):
        print("[Error] config.json not found!")
        return

    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    token = cfg.get("telegram_bot_token")
    chat_id = cfg.get("telegram_chat_id")

    if not token or token == "YOUR_TELEGRAM_BOT_TOKEN":
        print("[!] Please set telegram_bot_token in config.json.")
        return

    if not chat_id or chat_id == "YOUR_TELEGRAM_CHAT_ID":
        print("[!] Please set telegram_chat_id in config.json.")
        return

    print(f"Sending test message to Telegram (Chat ID: {chat_id})...")
    test_msg = (
        "🎉 <b>تم الاتصال بنجاح مع بوت تيليجرام!</b>\n\n"
        "هذه رسالة تجريبية لتأكيد عمل إشعارات نظام مراقبة اليوبينار (UPINAR Notifier) بنجاح."
    )
    ok = send_telegram_message(token, chat_id, test_msg)
    if ok:
        print("[OK] Test message sent successfully! Check your Telegram.")
    else:
        print("[FAIL] Could not send message. Please verify your Chat ID and make sure you clicked 'Start' in the bot.")

if __name__ == "__main__":
    test_telegram()
