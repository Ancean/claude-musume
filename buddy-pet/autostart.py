"""开机自动启动：在注册表 HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run 下登记一项，登录 Windows 时直接叫她出来。

和安装程序登记的哨兵（ClaudeBuddyPet，Claude 打开时才叫她）是两项，互不影响；两项都开着时，
后启动的那只发现已经有一只在运行，会自己退出。在任务管理器的“启动应用”里禁用后，这里也当作关着。只用标准库。
"""

from __future__ import annotations

import sys
from pathlib import Path

try:
    import winreg
except ImportError:  # 非 Windows 没有开机启动
    winreg = None

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
# 任务管理器“启动应用”里的启用、禁用记在这里：值的第一个字节是奇数表示禁用。
APPROVED_KEY = r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run"
NAME = "ClaudeBuddyPetAutostart"


def available() -> bool:
    return winreg is not None


def command(script: Path) -> str:
    """登记的命令行：打包版是 exe 本身；源码版用 pythonw 运行 pet.pyw，不弹黑框。"""
    exe = Path(sys.executable)
    if getattr(sys, "frozen", False):
        return f'"{exe}"'
    pythonw = exe.with_name("pythonw.exe")
    return f'"{pythonw if pythonw.exists() else exe}" "{script}"'


def enabled() -> bool:
    """登记了、而且没在任务管理器里被禁用。"""
    if winreg is None:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, NAME)
    except OSError:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, APPROVED_KEY) as key:
            data, _ = winreg.QueryValueEx(key, NAME)
    except OSError:
        return True
    return not (isinstance(data, bytes) and data and data[0] & 1)


def set_enabled(on: bool, script: Path) -> None:
    """登记或去掉开机启动；script 是源码版的 pet.pyw。写不进注册表时抛 OSError。"""
    if winreg is None:
        raise OSError("只有 Windows 能开机启动")
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
        if on:
            winreg.SetValueEx(key, NAME, 0, winreg.REG_SZ, command(script))
        else:
            try:
                winreg.DeleteValue(key, NAME)
            except FileNotFoundError:
                pass
    # 去掉任务管理器里的禁用记录：打开时不再被它拦住，关掉时也不留下多余的一项。
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, APPROVED_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, NAME)
    except FileNotFoundError:
        pass
