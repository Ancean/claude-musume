"""判断 Claude 桌面端是否开着、是不是前台窗口，桌宠 pet.pyw 和常驻的 follow_claude.pyw 共用。

以可见的主窗口为准：窗口在（最小化也算）就是开着；窗口关掉，不论程序彻底退出还是只剩托盘图标，
都算关闭。Claude Code 命令行也叫 claude.exe，但它没有自己的窗口，不会被误认。只用标准库。
"""

from __future__ import annotations

import ctypes
import os
import sys
from ctypes import wintypes

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
GW_OWNER = 4
GA_ROOTOWNER = 3
GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080
# 直接运行 claude.exe 时，系统会把控制台窗口报成它的窗口。
CONSOLE_CLASSES = {"ConsoleWindowClass", "PseudoConsoleWindow"}

if sys.platform == "win32":
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]
    user32.EnumWindows.restype = wintypes.BOOL
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetWindow.restype = wintypes.HWND
    user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.GetWindowLongW.restype = wintypes.LONG
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextLengthW.restype = ctypes.c_int
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetClassNameW.restype = ctypes.c_int
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.GetForegroundWindow.argtypes = []
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetAncestor.restype = wintypes.HWND
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL


def process_path(pid: int) -> str:
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""
    try:
        size = wintypes.DWORD(1024)
        buf = ctypes.create_unicode_buffer(size.value)
        return buf.value if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)) else ""
    finally:
        kernel32.CloseHandle(handle)


def claude_windows(first_only: bool = False) -> list[tuple[int, str]]:
    """Claude 桌面端的可见主窗口，返回（进程号, 程序路径）。"""
    found: list[tuple[int, str]] = []
    paths: dict[int, str] = {}

    def check(hwnd, _) -> bool:
        try:
            if not user32.IsWindowVisible(hwnd) or user32.GetWindow(hwnd, GW_OWNER):
                return True
            if user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & WS_EX_TOOLWINDOW or user32.GetWindowTextLengthW(hwnd) == 0:
                return True
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value not in paths:
                paths[pid.value] = process_path(pid.value)
            if os.path.basename(paths[pid.value]).lower() != "claude.exe":
                return True
            cls = ctypes.create_unicode_buffer(64)
            user32.GetClassNameW(hwnd, cls, 64)
            if cls.value in CONSOLE_CLASSES:
                return True
            found.append((pid.value, paths[pid.value]))
            return not first_only
        except Exception:  # 回调里的异常传不出去，跳过这个窗口
            return True

    user32.EnumWindows(WNDENUMPROC(check), 0)
    return found


def claude_open() -> bool | None:
    """Claude 桌面端开着返回 True，关着返回 False；不是 Windows、判断不了时返回 None。"""
    if sys.platform != "win32":
        return None
    try:
        return bool(claude_windows(first_only=True))
    except OSError:
        return None


def claude_foreground() -> bool:
    """前台窗口是不是 Claude 桌面端，也就是你正看着它。判断不了时当作不是。"""
    if sys.platform != "win32":
        return False
    try:
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return False
        root = user32.GetAncestor(hwnd, GA_ROOTOWNER) or hwnd
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(root, ctypes.byref(pid))
        if os.path.basename(process_path(pid.value)).lower() != "claude.exe":
            return False
        cls = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(root, cls, 64)
        return cls.value not in CONSOLE_CLASSES
    except OSError:
        return False


if __name__ == "__main__":
    print("Claude 桌面端开着" if claude_open() else "Claude 桌面端没开")
    for pid, path in claude_windows():
        print(pid, path)
