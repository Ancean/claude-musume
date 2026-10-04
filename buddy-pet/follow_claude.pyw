"""常驻小哨兵：Claude 桌面端的窗口一出现，就把桌宠叫出来。

登录 Windows 时由注册表 Run 项 ClaudeBuddyPet 启动，只用标准库、不加载 Qt，每 3 秒看一眼，几乎不占资源。
Claude 关掉后桌宠自己退出（见 pet.pyw）。Claude 开着时你手动退出了桌宠，哨兵不会再叫她，
等 Claude 下次打开才会。桌宠菜单里关掉“跟随 Claude 启动和退出”后，哨兵也不再叫她。

运行：pythonw follow_claude.pyw      已经有一个在运行时，新的直接退出
打包成 exe 后没有单独的哨兵程序，用 “Claude娘.exe --follow” 运行这里（见 pet.pyw 开头）。
"""

from __future__ import annotations

import ctypes
import json
import subprocess
import sys
import time
import traceback
from ctypes import wintypes
from datetime import datetime
from pathlib import Path

from claude_app import claude_open

FROZEN = getattr(sys, "frozen", False)
HERE = Path(sys.executable).resolve().parent if FROZEN else Path(__file__).resolve().parent
PET = HERE / "pet.pyw"
CONFIG_FILE = HERE / "config.json"
LOG_FILE = HERE / "follow.log"
INTERVAL = 3
ERROR_ALREADY_EXISTS = 183


def log(text: str) -> None:
    """pythonw 没有控制台，出错时写进 follow.log。"""
    try:
        if LOG_FILE.exists() and LOG_FILE.stat().st_size > 200_000:
            LOG_FILE.unlink()
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(f"--- {datetime.now():%Y-%m-%d %H:%M:%S}\n{text}\n")
    except OSError:
        pass


def single_instance() -> int | None:
    """命名互斥量：拿到就返回句柄，进程退出时系统自动释放；已有哨兵在运行则返回 None。"""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    handle = kernel32.CreateMutexW(None, False, "Local\\ClaudeBuddyPetFollow")
    if not handle or ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        return None
    return handle


def follow_enabled() -> bool:
    try:
        cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return True
    return not (isinstance(cfg, dict) and cfg.get("follow") is False)


def launch_pet() -> None:
    # 桌宠自己保证只有一只，这里不用管她是不是已经在运行。
    if FROZEN:
        subprocess.Popen([sys.executable], cwd=str(HERE), close_fds=True)
        return
    exe = Path(sys.executable)
    pythonw = exe.with_name("pythonw.exe")
    subprocess.Popen([str(pythonw if pythonw.exists() else exe), str(PET)], cwd=str(HERE), close_fds=True)


def main() -> int:
    guard = single_instance()
    if guard is None:
        return 0
    was_open = False
    last_error = ""
    while True:
        try:
            now_open = claude_open() is True
            if now_open and not was_open and follow_enabled():
                launch_pet()
            was_open = now_open
            last_error = ""
        except Exception:
            # 同一个错误只记一次，免得每 3 秒写一遍。
            text = traceback.format_exc()
            if text != last_error:
                log(text)
                last_error = text
        time.sleep(INTERVAL)


if __name__ == "__main__":
    sys.exit(main())
