"""桌宠用到的几个 Windows 窗口操作：保持置顶、判断前台是不是全屏程序、鼠标穿透。只用标准库。

置顶窗口在显示桌面、切换虚拟桌面、显示器休眠唤醒之后，偶尔会被系统排到普通窗口下面，
窗口样式里的置顶标记却还在，所以要定期检查、必要时重新置顶。
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

GW_HWNDPREV = 3
GWL_EXSTYLE = -20
WS_EX_TOPMOST = 0x00000008
WS_EX_TRANSPARENT = 0x00000020
VK_CONTROL = 0x11
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010
SWP_NOOWNERZORDER = 0x0200
MONITOR_DEFAULTTONEAREST = 2
# SHQueryUserNotificationState 的结果：有全屏程序、Direct3D 独占全屏、演示模式。
# 这几种情况下 Windows 自己也会暂停弹通知。
FULLSCREEN_STATES = {2, 3, 4}

if sys.platform == "win32":
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetWindow.restype = wintypes.HWND
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.GetWindowLongW.restype = wintypes.LONG
    user32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.LONG]
    user32.SetWindowLongW.restype = wintypes.LONG
    user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
    user32.GetAsyncKeyState.restype = ctypes.c_short
    user32.SetWindowPos.argtypes = [
        wintypes.HWND,
        wintypes.HWND,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.UINT,
    ]
    user32.SetWindowPos.restype = wintypes.BOOL
    user32.GetForegroundWindow.argtypes = []
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
    user32.MonitorFromWindow.restype = wintypes.HANDLE
    shell32.SHQueryUserNotificationState.argtypes = [ctypes.POINTER(ctypes.c_int)]
    shell32.SHQueryUserNotificationState.restype = ctypes.c_long


def buried(hwnd: int) -> bool:
    """置顶窗口上方出现了可见的普通窗口，说明它被压下去了。"""
    if sys.platform != "win32":
        return False
    above = user32.GetWindow(hwnd, GW_HWNDPREV)
    for _ in range(1000):
        if not above:
            return False
        if user32.IsWindowVisible(above) and not user32.GetWindowLongW(above, GWL_EXSTYLE) & WS_EX_TOPMOST:
            return True
        above = user32.GetWindow(above, GW_HWNDPREV)
    return False


def raise_topmost(hwnd: int) -> None:
    """放回置顶层的最上面，不抢焦点、不移动。"""
    if sys.platform == "win32":
        flags = SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_NOOWNERZORDER
        user32.SetWindowPos(hwnd, wintypes.HWND(-1), 0, 0, 0, 0, flags)


def fullscreen_app(near: int) -> bool:
    """前台有全屏程序（看视频、玩游戏、放幻灯片），而且和 near 这个窗口在同一块屏幕上。"""
    if sys.platform != "win32":
        return False
    state = ctypes.c_int()
    if shell32.SHQueryUserNotificationState(ctypes.byref(state)) != 0 or state.value not in FULLSCREEN_STATES:
        return False
    fg = user32.GetForegroundWindow()
    if not fg:
        return True
    here = user32.MonitorFromWindow(near, MONITOR_DEFAULTTONEAREST)
    return user32.MonitorFromWindow(fg, MONITOR_DEFAULTTONEAREST) == here


def set_click_through(hwnd: int, on: bool) -> None:
    """鼠标穿透：点击直接落到下面的窗口。

    透明背景的 Qt 窗口本来就是分层窗口，加上 WS_EX_TRANSPARENT 后整个窗口都不接鼠标。
    只改这一位，分层样式不能动，动了窗口会消失。样式没变时什么都不做，可以反复调用。
    """
    if sys.platform != "win32":
        return
    style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    new = style | WS_EX_TRANSPARENT if on else style & ~WS_EX_TRANSPARENT
    if new != style:
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, new)


def ctrl_down() -> bool:
    """Ctrl 键现在是不是按着；不管焦点在哪个窗口。"""
    return sys.platform == "win32" and bool(user32.GetAsyncKeyState(VK_CONTROL) & 0x8000)
