import base64
import os
import subprocess
import sys
import time
from typing import Optional

from .paths import BASE_DIR, ensure_data_dir

PID_FILE = os.path.join(BASE_DIR, "data", "bot.pid")
LOG_FILE = os.path.join(BASE_DIR, "data", "bot_service.log")


def _get_pythonw_executable() -> str:
    python_exe = sys.executable
    if sys.platform.startswith("win"):
        pythonw = os.path.join(os.path.dirname(python_exe), "pythonw.exe")
        if os.path.exists(pythonw):
            return pythonw
    return python_exe


def _is_process_running(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform.startswith("win"):
        try:
            output = subprocess.check_output(
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                text=True,
                errors="ignore",
            )
            return str(pid) in output
        except Exception:
            return False
    else:
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False


def _find_running_upinar_pids() -> list[int]:
    """Scans system processes to find any python instance running upinar_notifier."""
    pids: list[int] = []
    current_pid = os.getpid()
    if sys.platform.startswith("win"):
        try:
            ps_script = """
$ProgressPreference = 'SilentlyContinue'
Get-CimInstance Win32_Process | Where-Object { 
    ($_.Name -match '^python') -and ($_.CommandLine -match 'upinar_notifier') -and ($_.CommandLine -match 'run') 
} | ForEach-Object { $_.ProcessId }
"""
            encoded = base64.b64encode(ps_script.encode("utf-16le")).decode("ascii")
            res = subprocess.check_output(
                ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                stderr=subprocess.DEVNULL,
                text=True,
                errors="ignore",
            )
            for line in res.strip().split():
                if line.isdigit():
                    pid = int(line)
                    if pid != current_pid and _is_process_running(pid):
                        pids.append(pid)
        except Exception:
            pass
    return pids


def get_running_pid() -> Optional[int]:
    if os.path.exists(PID_FILE):
        try:
            with open(PID_FILE, "r", encoding="utf-8") as f:
                pid = int(f.read().strip())
            if _is_process_running(pid):
                return pid
            os.remove(PID_FILE)
        except Exception:
            pass

    # If pid file is missing or stale, probe active processes
    active_pids = _find_running_upinar_pids()
    if active_pids:
        pid = active_pids[0]
        try:
            ensure_data_dir()
            with open(PID_FILE, "w", encoding="utf-8") as f:
                f.write(str(pid))
        except Exception:
            pass
        return pid

    return None


def start_service() -> bool:
    ensure_data_dir()
    existing_pid = get_running_pid()
    if existing_pid:
        print("=" * 60)
        print(f"  🟢 البوت يعمل بالفعل في الخلفية بنشاط! (PID: {existing_pid})")
        print("=" * 60)
        print("💡 لفحص حالة البوت والسجلات: python -m upinar_notifier status")
        print("💡 لإيقاف البوت:              python -m upinar_notifier stop")
        return True

    python_exe = sys.executable

    flags = 0
    if sys.platform.startswith("win"):
        CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        DETACHED_PROCESS = getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
        CREATE_NEW_PROCESS_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
        flags = CREATE_NO_WINDOW | DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP

    proc = subprocess.Popen(
        [python_exe, "-u", "-m", "upinar_notifier", "run", "--service"],
        cwd=BASE_DIR,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        creationflags=flags,
    )

    with open(PID_FILE, "w", encoding="utf-8") as f:
        f.write(str(proc.pid))

    for _ in range(10):
        time.sleep(0.5)
        if _is_process_running(proc.pid):
            print("=" * 60)
            print("  🟢 تم تشغيل بوت يوبينار في خلفية الويندوز بنجاح!")
            print(f"  • رقم العملية (PID): {proc.pid}")
            print("  • يعمل الآن في صمت تام وبدون فتح أي شاشات سوداء.")
            print(f"  • ملف السجلات المباشر: {os.path.relpath(LOG_FILE, BASE_DIR)}")
            print("=" * 60)
            print("💡 لفحص حالة البوت: python -m upinar_notifier status")
            print("💡 لإيقاف البوت:     python -m upinar_notifier stop")
            return True

    print("❌ فشل تشغيل البوت في الخلفية. راجع ملف السجلات لمزيد من التفاصيل:")
    print(LOG_FILE)
    return False


def stop_service() -> bool:
    all_pids = set(_find_running_upinar_pids())
    tracked_pid = None
    if os.path.exists(PID_FILE):
        try:
            with open(PID_FILE, "r", encoding="utf-8") as f:
                tracked_pid = int(f.read().strip())
                all_pids.add(tracked_pid)
        except Exception:
            pass

    if not all_pids:
        print("ℹ️ البوت متوقف حالياً ولا توجد أي عملية شغالة في الخلفية.")
        if os.path.exists(PID_FILE):
            os.remove(PID_FILE)
        return True

    print(f"⏹️ جاري إيقاف البوت وعملياته في الخلفية ({', '.join(str(p) for p in all_pids)})...")
    for pid in all_pids:
        if sys.platform.startswith("win"):
            try:
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(pid)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            except Exception as e:
                print(f"تحذير أثناء إيقاف {pid}: {e}")
        else:
            try:
                import signal
                os.kill(pid, signal.SIGTERM)
            except Exception:
                pass

    time.sleep(1.0)
    if os.path.exists(PID_FILE):
        try:
            os.remove(PID_FILE)
        except Exception:
            pass

    print("✅ تم إيقاف جميع عمليات البوت بنجاح.")
    return True


def status_service() -> None:
    pid = get_running_pid()
    print("=" * 60)
    if pid:
        print(f"  🟢 حالة البوت: يعمل في الخلفية بنشاط (PID: {pid})")
    else:
        print("  🔴 حالة البوت: متوقف حالياً (Not running)")
    print("=" * 60)

    if os.path.exists(LOG_FILE):
        print("\n📜 آخر 15 سطر من ملف السجلات (Log):")
        print("-" * 60)
        try:
            with open(LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
                for line in lines[-15:]:
                    print(line.rstrip())
        except Exception as e:
            print(f"تعذر قراءة السجلات: {e}")
        print("-" * 60)
    else:
        print("لم يتم إنشاء ملف سجلات بعد.")


def _get_startup_folder() -> Optional[str]:
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return None
    folder = os.path.join(appdata, "Microsoft", "Windows", "Start Menu", "Programs", "Startup")
    if os.path.exists(folder):
        return folder
    return None


def install_autostart() -> bool:
    """Configures the bot to launch automatically when the user logs into Windows."""
    if not sys.platform.startswith("win"):
        print("التشغيل التلقائي عبر Startup مدعوم فقط على نظام Windows.")
        return False

    startup_folder = _get_startup_folder()
    if not startup_folder:
        print("❌ تعذر العثور على مجلد بدء تشغيل الويندوز (Startup Folder).")
        return False

    vbs_path = os.path.join(startup_folder, "UPINAR_Bot_Autostart.vbs")
    python_exe = sys.executable

    vbs_content = (
        'Set WshShell = CreateObject("WScript.Shell")\n'
        f'WshShell.CurrentDirectory = "{BASE_DIR}"\n'
        f'WshShell.Run """{python_exe}"" -m upinar_notifier start", 0, False\n'
    )

    try:
        with open(vbs_path, "w", encoding="utf-8") as f:
            f.write(vbs_content)
        print("=" * 60)
        print("  ⚡ تم تفعيل التشغيل التلقائي مع الويندوز بنجاح!")
        print("=" * 60)
        print("• سيبدأ البوت بالعمل تلقائياً وبشكل صامت في كل مرة تفتح فيها جهازك.")
        print(f"• تم إنشاء ملف التشغيل في: {vbs_path}")
        print("💡 لإلغاء التشغيل التلقائي: python -m upinar_notifier autostart --disable")
        return True
    except Exception as e:
        print(f"❌ حدث خطأ أثناء تفعيل التشغيل التلقائي: {e}")
        return False


def uninstall_autostart() -> bool:
    startup_folder = _get_startup_folder()
    if not startup_folder:
        return False
    vbs_path = os.path.join(startup_folder, "UPINAR_Bot_Autostart.vbs")
    if os.path.exists(vbs_path):
        try:
            os.remove(vbs_path)
            print("✅ تم إلغاء التشغيل التلقائي مع الويندوز بنجاح.")
            return True
        except Exception as e:
            print(f"❌ تعذر حذف ملف التشغيل التلقائي: {e}")
            return False
    else:
        print("ℹ️ التشغيل التلقائي غير مفعل بالفعل.")
        return True
