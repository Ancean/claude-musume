"""打包：PyInstaller 生成 dist\\Claude娘 文件夹，再用 Inno Setup 做成 dist\\Claude娘-安装程序.exe。

需要：pip install pyinstaller pillow；Inno Setup 6（https://jrsoftware.org/isdl.php，装在当前用户或系统都行）。
运行：python installer\\build.py        只要文件夹不要安装程序时加 --no-installer
sound-presets 里没有 minecraft-exp-orb.wav 也能打包，任务结束音会退回音效 A。
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
PET = HERE.parent
ART = PET.parent / "buddy-art"
BUILD = PET / "build"
DIST = PET / "dist"
NAME = "Claude娘"
VERSION = "1.0.0"
# Inno Setup 6 自带的语言里没有简体中文，用官方仓库里和 6.7.3 同一版本的非官方翻译。
CHINESE_ISL = "https://raw.githubusercontent.com/jrsoftware/issrc/is-6_7_3/Files/Languages/Unofficial/ChineseSimplified.isl"
ART_FILES = ("*.png", "anchors.json", "weights.json")
# Anaconda 里装了很多库，PyInstaller 会顺着可选导入把它们带进来；桌宠只用 PyQt5、Pillow 和标准库。
EXCLUDES = ("numpy", "pandas", "scipy", "matplotlib", "IPython", "jedi", "tkinter", "sqlite3", "PyQt5.QtWebEngineWidgets",
            "PyQt5.QtWebEngineCore", "PyQt5.QtQml", "PyQt5.QtQuick", "PyQt5.QtMultimedia", "PyQt5.QtSql", "PyQt5.QtNetwork")
# 打包后删掉的文件（相对 _internal 和其中的 Qt 插件目录）。
PRUNE = ("opengl32sw.dll", "libGLESv2.dll", "libEGL.dll", "d3dcompiler_*.dll", "Qt5Pdf_conda.dll", "Qt5Qml*_conda.dll",
         "Qt5Quick*_conda.dll", "Qt5VirtualKeyboard_conda.dll", "imageformats/qpdf*.dll", "platforminputcontexts",
         "PyQt5/Qt5/translations", "PIL/_avif*.pyd")


def make_icon() -> Path:
    """用 idle.png 补成正方形做 exe 和安装程序的图标。"""
    from PIL import Image

    out = BUILD / "icon.ico"
    img = Image.open(ART / "idle.png").convert("RGBA")
    img = img.crop(img.getbbox())
    side = max(img.size)
    square = Image.new("RGBA", (side, side))
    square.paste(img, ((side - img.width) // 2, (side - img.height) // 2))
    square.save(out, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    return out


def stage_data() -> list[tuple[Path, str]]:
    """要打包进去的素材，先复制到 build\\data，免得带上提示词文档和插件的测试文件。"""
    data = BUILD / "data"
    shutil.rmtree(data, ignore_errors=True)
    art = data / "buddy-art"
    art.mkdir(parents=True)
    for pattern in ART_FILES:
        for f in ART.glob(pattern):
            shutil.copy2(f, art / f.name)
    shutil.copytree(PET / "sound-presets", data / "sound-presets")
    shutil.copytree(PET / "claude-plugin", data / "claude-plugin", ignore=shutil.ignore_patterns("*.test.ts"))
    shutil.copy2(PET / "default_lines.json", data / "default_lines.json")
    return [(data / name, name if (data / name).is_dir() else ".") for name in
            ("buddy-art", "sound-presets", "claude-plugin", "default_lines.json")]


def pyinstaller(icon: Path) -> Path:
    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--windowed", "--onedir",
           "--name", NAME, "--icon", str(icon), "--distpath", str(DIST), "--workpath", str(BUILD / "work"),
           "--specpath", str(BUILD), "--paths", str(PET), "--hidden-import", "follow_claude"]
    for name in EXCLUDES:
        cmd += ["--exclude-module", name]
    for src, dst in stage_data():
        cmd += ["--add-data", f"{src}{os.pathsep}{dst}"]
    cmd.append(str(PET / "pet.pyw"))
    subprocess.run(cmd, check=True)
    prune(DIST / NAME)
    return DIST / NAME


def prune(app: Path) -> None:
    """Anaconda 的 Qt 插件会拖进 PDF、QML、虚拟键盘和软件 OpenGL，桌宠只用 QPainter 画 PNG，删掉省一半体积。"""
    internal = app / "_internal"
    plugins = internal / "PyQt5" / "Qt5" / "plugins"
    for pattern in PRUNE:
        for path in [*internal.glob(pattern), *plugins.glob(pattern)]:
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()


def iscc() -> Path:
    roots = [Path(os.environ[k]) for k in ("ProgramFiles(x86)", "ProgramFiles") if k in os.environ]
    if "LOCALAPPDATA" in os.environ:
        roots.insert(0, Path(os.environ["LOCALAPPDATA"]) / "Programs")
    for root in roots:
        if (root / "Inno Setup 6" / "ISCC.exe").is_file():
            return root / "Inno Setup 6" / "ISCC.exe"
    found = shutil.which("ISCC")
    if not found:
        sys.exit("找不到 Inno Setup 6 的 ISCC.exe，先装 Inno Setup，或者加 --no-installer 只生成文件夹。")
    return Path(found)


def installer(app: Path, icon: Path) -> Path:
    isl = BUILD / "ChineseSimplified.isl"
    if not isl.exists():
        urllib.request.urlretrieve(CHINESE_ISL, isl)
    defines = {"AppName": NAME, "AppVersion": VERSION, "AppDir": app, "PluginDir": BUILD / "data" / "claude-plugin",
               "IconFile": icon, "ChineseIsl": isl, "OutDir": DIST}
    cmd = [str(iscc()), *(f"/D{k}={v}" for k, v in defines.items()), str(HERE / "claude-musume.iss")]
    subprocess.run(cmd, check=True)
    return DIST / f"{NAME}-安装程序.exe"


def main() -> int:
    parser = argparse.ArgumentParser(description="把桌宠打包成 exe 和安装程序")
    parser.add_argument("--no-installer", action="store_true", help="只生成 dist\\Claude娘 文件夹")
    args = parser.parse_args()
    BUILD.mkdir(exist_ok=True)
    if not (PET / "sound-presets" / "minecraft-exp-orb.wav").exists():
        print("提示：没有 minecraft-exp-orb.wav，任务结束音默认会是音效 A。")
    icon = make_icon()
    app = pyinstaller(icon)
    print(f"程序文件夹：{app}")
    if not args.no_installer:
        print(f"安装程序：{installer(app, icon)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
