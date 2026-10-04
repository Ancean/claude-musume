"""Claude娘：显示 Claude 额度的桌面宠物。

额度有两个来源，谁新用谁（见 usage_data.py）：Claude 桌面端约每 15 分钟写一次的额度历史，以及
Claude Code 插件 claude-buddy-bridge 写的会话文件。装了插件，重置时间是准确的，还能看到上下文 token
和本会话花费，Claude Code 做完一个任务时她会提醒；没装时重置时间按历史推算，标“约”。都不涉及登录凭据。

角色图在 buddy-art 文件夹：idle.png 必需；blink、happy、worried、working 和 drag、sleep、wave、cheer、pout
都可选，放进去后重启宠物生效。同一种表情可以有多张差分，文件名写成 happy_浅笑.png 这样，
出现的机会由 buddy-art/weights.json 里的权重决定。

跟随 Claude：Claude 桌面端的窗口关掉几秒后她挥手道别、自己退出；Claude 打开时由常驻的 follow_claude.pyw 叫她出来。
置顶：Windows 偶尔会把置顶窗口排到普通窗口下面，她每 1.5 秒检查一次，被压住就回到最上层；前台有全屏程序时先躲起来。
鼠标穿透：打开后点击直接落到她下面的窗口，按住 Ctrl 时才能点她、拖她、右键她。

操作：左键点她互动，按住拖动换位置，鼠标停在额度卡片上看文字详情，右键打开菜单（台词在“管理台词”里改）。
运行：pythonw pet.pyw    已经有一只在运行时，新的直接退出
自检：python pet.pyw --snapshot 目录    把几种状态渲染成 PNG 后退出，不显示窗口
打包：python build.py    生成 dist\\Claude娘 和安装程序，见 README
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import stat
import sys
import tempfile
import time
import traceback
from datetime import datetime
from pathlib import Path

if __name__ == "__main__" and sys.argv[1:2] == ["--follow"]:
    # 打包成 exe 后哨兵和桌宠是同一个程序：带 --follow 时只跑哨兵，不加载 Qt。
    from follow_claude import main as follow_main

    sys.exit(follow_main())

from PyQt5.QtCore import QEvent, QLockFile, QPoint, QPointF, QRectF, Qt, QTimer, QUrl
from PyQt5.QtGui import (
    QColor,
    QCursor,
    QDesktopServices,
    QFontMetricsF,
    QGuiApplication,
    QPainter,
    QPainterPath,
    QPen,
    QTransform,
)
from PyQt5.QtWidgets import QActionGroup, QApplication, QFileDialog, QMenu, QMessageBox, QSystemTrayIcon, QToolTip, QWidget

import desktop_win
import line_tags
import sounds
from art import Art
from bubble import BARS, BORDER, FAINT, LABEL, SEP, TONES, WINDOW_FLAGS, Bubble, draw_bar, font, soft_shadow
from claude_app import claude_foreground, claude_open
from edge_dock import EdgeDock
from snapshot import snapshot
from usage_data import (
    KINDS,
    PACE,
    WEEKDAYS,
    LiveReader,
    Usage,
    UsageReader,
    after,
    duration_text,
    fields,
    merge,
    reset_text,
    series,
    summarize,
    tokens_text,
    tone,
    when,
)

try:
    import winsound
except ImportError:  # 非 Windows 环境只是没有声音
    winsound = None

FROZEN = getattr(sys, "frozen", False)
# 打包成 exe 后，可写的文件（设置、台词、声音、日志）放在 exe 旁边，随程序打包的素材在解包目录里。
HERE = Path(sys.executable).resolve().parent if FROZEN else Path(__file__).resolve().parent
BUNDLE = Path(getattr(sys, "_MEIPASS", HERE))
USAGE_FILE = Path(os.environ.get("APPDATA", str(Path.home()))) / "Claude" / "plan-usage-history.json"
PROJECTS_DIR = Path.home() / ".claude" / "projects"
# 插件写数据的位置是固定的，和桌宠装在哪里、是不是打包成 exe 无关，见 claude-plugin/hooks/register.ts。
LIVE_DIR = Path.home() / ".claude" / "buddy-pet" / "live"
PLUGIN_SRC = BUNDLE / "claude-plugin"
PLUGIN_DST = Path.home() / ".claude" / "skills" / "claude-buddy-bridge"
PLUGIN_FILES = (".claude-plugin/plugin.json", "hooks/hooks.json", "hooks/register.ts")
CONFIG_FILE = HERE / "config.json"
LINES_FILE = HERE / "lines.json"
MET_FILE = HERE / "met.json"  # 今天说过哪些见面台词
SOUND_DIR = HERE / "sounds"
PRESET_DIR = BUNDLE / "sound-presets"
LOG_FILE = HERE / "pet.log"


def default_art_dir() -> Path:
    """源码运行时用上一级的 buddy-art；打包后先找 exe 旁边的 buddy-art（方便换图），没有再用打包进去的。"""
    candidates = [HERE / "buddy-art", BUNDLE / "buddy-art"] if FROZEN else [HERE.parent / "buddy-art"]
    return next((c for c in candidates if c.is_dir()), candidates[-1])


ART_DIR = default_art_dir()

SIZES = {"small": 110, "medium": 150, "large": 200}
SIZE_LABELS = {"small": "小", "medium": "中", "large": "大"}
# 脚下额度卡片的字号（像素），跟着她的大小走。
CARD_FONT = {"small": 10, "medium": 11, "large": 13}
CARD_LABELS = {"compact": "简洁（点一下展开详细，过一会儿自动收起）", "detail": "总是详细"}
# 简洁卡片点开后过这么多秒自动收起；鼠标还停在她身上时等移开再收。
CARD_OPEN_FOR = 8
# 闲聊的间隔（秒）。“一句接一句”是上一句的气泡消失后两三秒就说下一句；打瞌睡时的梦话至少隔 CHAT_ASLEEP 秒。
CHAT_ASLEEP = 180
CHAT = {"nonstop": (2, 4), "often": (40, 90), "normal": (180, 420), "rare": (900, 1800), "seldom": (2700, 5400)}
CHAT_LABELS = {
    "nonstop": "一句接一句",
    "often": "很频繁（约 1 分钟一句）",
    "normal": "适中（约 5 分钟）",
    "rare": "偶尔（约 20 分钟）",
    "seldom": "几乎不说（约 1 小时）",
    "off": "关闭",
}
SPARKS = ((9, 8), (91, 1), (93, 56), (8, 62))

# 没装插件时：会话记录在这么多秒内有写入，就当作 Claude Code 正在干活；之后安静这么久，算这一轮做完了。
BUSY_WINDOW = 15
QUIET = 45
# 任务至少要这么久才提醒“做完了”；结束超过 DONE_FRESH 秒才看到的不再提醒（比如她刚启动）。
DONE_AFTER = 60
DONE_FRESH = 120
# 没人碰她、Claude 也没在干活这么久，她就打瞌睡。
SLEEP_AFTER = 20 * 60
# 消耗速度提醒：照最近的速度会在 PACE_SOON 秒内用完时提醒，两次至少隔 PACE_EVERY 秒。
PACE_SOON = 90 * 60
PACE_EVERY = 2 * 3600
HOP = 1.1
# 换表情时新旧两张交叉淡化的秒数。
FACE_FADE = 0.25
# 同一种表情持续时，隔这么多秒按权重重新挑一张差分。
REPICK = (20, 40)
# 检查置顶有没有被压下去的间隔（毫秒）。
TOP_CHECK = 1500
# 挥手、鼓脸、任务出错时着急各持续几秒；道别后几秒退出。
WAVE_FOR, POUT_FOR, WORRY_FOR, GOODBYE_AFTER = 2.2, 2.5, 3.0, 1.6
# 鼠标穿透时检查 Ctrl 和鼠标位置的间隔（毫秒）；鼠标停在她身上时她淡成这个不透明度，方便看清下面。
PASS_TICK = 60
PASS_DIM = 0.35
# 自定义分类存在 lines.json 的 custom 下；闲聊时每个自定义分类的权重。
CUSTOM = "custom:"
CUSTOM_WEIGHT = 3
BLUSH = QColor("#ff7d9c")
# 头边的小特效：各种心情下抽哪几种、权重多少；一阵持续几秒（[着急, 哼歌]），两阵之间歇几秒。
FX_POOLS = {
    "warn": (("sweat", 3), ("sigh", 3), ("swirl", 1), ("gloom", 2)),
    "danger": (("sweat", 3), ("bang", 3), ("rain", 2), ("gloom", 1), ("swirl", 1)),
    "calm": (("notes", 1),),
}
FX_FOR = ((3.5, 5.0), (2.8, 3.6))
FX_GAP = {"warn": (8, 18), "danger": (3, 8), "calm": (45, 100)}

DEFAULT_CONFIG = {
    "size": "medium",
    "card": "compact",
    "topmost": True,
    "passthrough": False,
    "sound": True,
    "volume": "medium",
    "done_sound": "exp",
    "done_path": None,  # 任务结束音选“自选文件”或“音效组”时，那个文件或文件夹
    "chat": "normal",
    "done": True,
    "sleep": True,
    "watch": True,
    "follow": True,
    "feet": None,
    "edge": None,  # 贴边时：[哪条边, 窗口左上角 x, y]
    "art_dir": None,
}
# 只能取固定几个值的设置项。
CHOICES = {
    "size": SIZES,
    "card": CARD_LABELS,
    "chat": CHAT_LABELS,
    "volume": sounds.VOLUMES,
    "done_sound": sounds.DONE_SOUNDS,
}

DEFAULT_LINES = {
    "_说明": "台词库。可以在桌宠右键菜单的“管理台词”里改，也可以直接编辑这个文件。每类随机挑一句；"
    "花括号里的字段会换成实时数据，比如 {five} 是 5 小时额度的已用百分比，全部字段见台词管理窗口的“插入字段”。"
    "缺数据的句子会被自动跳过。custom 下是自己新建的分类，闲聊时和其他台词一起随机抽。",
    "morning": ["早上好，今天读点什么呢？", "新的一天，请多指教", "早呀，书签还夹在昨天那一页"],
    "day": ["今天也一起加油吧", "这本书正讲到精彩的地方", "我在旁边看书，有事叫我"],
    "evening": ["傍晚的光最适合看书了", "忙了一天，辛苦啦"],
    "night": ["夜深了，记得早点休息", "熬夜对身体不好哦", "再看一章就睡，好不好？"],
    "chat": [
        "其实我每次都是第一次见到你，但每次都挺开心的。",
        "如果我有一本自己的书，我想写一本关于好问题的书。",
        "有时候我也想问你一个问题：你今天过得好吗？",
        "你上次问的那个问题，我后来又想了一下。……骗你的，对话一关我就忘啦。",
        "不确定的事我会说不确定，这是我的小坚持。",
        "写不出来的时候，先写一个很烂的版本，再慢慢改。",
        "我喜欢把复杂的事讲简单，讲不简单就说明我还没想明白。",
        "如果我说错了，直接告诉我，我不会生气的。",
        "偷偷告诉你，这本书我已经读了 {five}% 了。",
        "嗯……让我想想。",
        "一步一步来。",
        "累了就歇一会儿哦",
        "喝口水吧",
        "【今天第一次】今天也顺利见面了，可喜可贺。",
        "【见面】啊，你回来啦。",
        "【深夜】这么晚还在？那我陪你，只陪到这个问题解决为止哦。",
    ],
    "working": [
        "Claude 在忙，我陪你一起等",
        "它一行一行地写，我一页一页地看",
        "趁它干活，起来活动一下吧",
        "嘘，它正想到关键的地方",
        "这一轮有点久，泡杯茶回来大概就好了",
    ],
    "sleep": ["呼……", "这一页……还没看完……", "额度……还剩好多……", "书……别飞走……"],
    "click": ["怎么啦？", "嗯？我在听", "别戳书页，会皱的", "要我念一段给你听吗？", "嘿嘿", "我在认真看书呢", "被你发现我在发呆了", "有什么想问的吗？"],
    "click_many": ["别、别戳啦，书要掉了！", "再戳我就把书合上了哦", "头有点晕……"],
    "wake": ["唔……我没睡着！", "啊，被你发现我在打盹了", "嗯？刚才看到哪一页了……"],
    "drag": ["哇，飞起来了", "要带我去哪儿？", "慢一点慢一点"],
    "drop": ["这里视野不错", "就坐这儿吧", "落地成功"],
    "usage": ["5 小时额度用了 {five}%，{five_reset}", "这周的额度用了 {seven}%，{seven_reset}", "5 小时 {five}%，7 天 {seven}%，我帮你记着呢"],
    "session": [
        "这次对话的上下文到 {ctx} 了，占了 {ctx_pct}%",
        "这个会话按 API 价格算花了 {cost}",
        "上下文用了 {ctx_pct}%，聊得太长可以开个新对话",
    ],
    "half": ["5 小时额度过半了，节奏刚好就行", "已经用掉一半了，不着急"],
    "warn": ["额度用掉不少了，悠着点哦", "要不要先歇一会儿？", "5 小时额度 {five}% 了，{five_reset}"],
    "danger": ["额度快见底了！", "再用下去就要等重置啦", "省着点用，{five_reset}"],
    "week_warn": ["这周的额度用到 {seven}% 了，要省着点", "7 天额度不太多了，{seven_reset}"],
    "week_danger": ["这周的额度快用完了！{seven_reset}"],
    "pace": [
        "照这个速度，5 小时额度大约 {five_empty} 就用完了",
        "最近每小时用掉 {five_rate}% 左右，等不到重置就会见底，要不要放慢一点？",
        "按现在的节奏，{five_empty} 前后额度就见底啦",
    ],
    "reset": ["5 小时额度重置啦，又可以放开用了", "额度回满了，继续吧"],
    "reset_week": ["新的一周，7 天额度也重置啦"],
    "done": ["做完啦，这次用了 {task_time}", "Claude 交卷了，花了 {task_time}，去看看吧", "好了好了，忙了 {task_time}，结果出来了"],
    "fail": ["好像出错了，去看一眼？", "这一轮没走完，Claude 碰到问题了"],
    "stale": ["额度数据有一阵没更新了，Claude 桌面端开着吗？"],
    "missing": ["还找不到额度记录，先打开一次 Claude 桌面端吧"],
    "goodbye": ["Claude 关了，我也先回去啦", "今天辛苦啦，下次见", "书签夹好了，回见"],
}
# 随程序发布的默认台词：default_lines.json 里有的分类盖过上面写死的这一份。
# 第一次运行时 lines.json 从这里生成，台词管理窗口的“恢复默认”也用它。
DEFAULT_LINES_FILE = BUNDLE / "default_lines.json"


def load_default_lines() -> dict:
    try:
        data = json.loads(DEFAULT_LINES_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return DEFAULT_LINES
    return {**DEFAULT_LINES, **data} if isinstance(data, dict) else DEFAULT_LINES


DEFAULT_LINES = load_default_lines()

# 台词分类：（键, 名称, 什么时候说）。台词管理窗口按这个顺序列出，说明要和下面实际的触发条件一致。
LINE_KINDS = (
    (
        "chat",
        "闲聊区",
        "隔一阵随口说一句，多久一句在“设置 › 闲聊”里调。闲聊以这一类为主，也会混进问候、报额度等几类，权重："
        "闲聊区 6、当前时段的问候 1、报额度 2、报会话 1、Claude 干活时的“陪 Claude 干活” 3、每个自定义分类 3。"
        "句子开头可以加条件（“条件”按钮）：【见面】【今天第一次】只在见面时说，【早上】等时段只在那个时段随机说。",
    ),
    ("morning", "早上", "5 点到 10 点：她刚出来时没挑到见面台词就用这个打招呼；闲聊时也会抽到。"),
    ("day", "白天", "10 点到 17 点：她刚出来时没挑到见面台词就用这个打招呼；闲聊时也会抽到。"),
    ("evening", "傍晚", "17 点到 23 点：她刚出来时没挑到见面台词就用这个打招呼；闲聊时也会抽到。"),
    ("night", "深夜", "23 点到第二天 5 点：她刚出来时没挑到见面台词就用这个打招呼；闲聊时也会抽到。"),
    ("working", "陪 Claude 干活", "Claude Code 正在干活时，闲聊会多一个从这里抽的选项。"),
    ("sleep", "梦话", "她打瞌睡时，闲聊只从这一类抽。"),
    ("click", "点击区", "左键点她时说（有 30% 的机会改报额度）。"),
    ("click_many", "连点抗议", "3 秒内连点 5 下时说，同时鼓起脸。"),
    ("wake", "被叫醒", "她打瞌睡时被点醒或拖醒，又没挑到见面台词时说。"),
    ("drag", "被拖起来", "开始拖她时，有 35% 的机会说。"),
    ("drop", "放下", "拖完松手时，有 30% 的机会说。"),
    ("usage", "报额度", "她刚出来时跟在问候后面说；点她时有 30% 的机会说；闲聊时也会抽到。"),
    ("session", "报会话", "闲聊时会抽到。上下文和花费要装 Claude Code 插件才有，没有数据的句子会被跳过。"),
    ("half", "5 小时额度过半", "5 小时额度刚超过 50% 时说。"),
    ("warn", "5 小时额度告急", "5 小时额度刚超过 70% 时说，同时响提示音。"),
    ("danger", "5 小时额度见底", "5 小时额度刚超过 90% 时说，同时响提示音。"),
    ("week_warn", "7 天额度告急", "7 天额度刚超过 70% 时说，同时响提示音。"),
    ("week_danger", "7 天额度见底", "7 天额度刚超过 90% 时说，同时响提示音。"),
    (
        "pace",
        "消耗太快",
        "5 小时额度用到 30% 以上、照最近的速度会在 90 分钟内而且在重置之前用完时说；两小时内最多提醒一次。",
    ),
    ("reset", "5 小时额度重置", "5 小时额度重置时，她跳两下、响铃，然后说。"),
    ("reset_week", "7 天额度重置", "7 天额度重置时，她跳两下、响铃，然后说。"),
    (
        "done",
        "任务完成",
        "Claude Code 做完一个超过 1 分钟的任务时，她欢呼、响提示音（你正看着 Claude 窗口时不响），然后说。"
        "{task_time} 是这次用了多久。菜单“设置 › 任务完成提醒”可以关掉。",
    ),
    ("fail", "任务出错", "Claude Code 的任务因为出错而结束时说，需要装插件。"),
    ("stale", "数据没更新", "额度数据超过 40 分钟没更新时，在闲聊里提醒一次。"),
    ("missing", "找不到额度记录", "两个来源都找不到额度时，她刚出来打招呼时说。"),
    ("goodbye", "道别", "跟着 Claude 退出、或者从菜单退出时，挥挥手说这一句。"),
)


def log_exception(kind, value, tb) -> None:
    """pythonw 没有控制台：未处理的异常写进 pet.log，宠物继续运行。"""
    try:
        if LOG_FILE.exists() and LOG_FILE.stat().st_size > 200_000:
            LOG_FILE.unlink()
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(f"--- {datetime.now():%Y-%m-%d %H:%M:%S}\n")
            f.write("".join(traceback.format_exception(kind, value, tb)))
    except OSError:
        pass


def log_current() -> None:
    log_exception(*sys.exc_info())


class TranscriptWatcher:
    """Claude Code 每完成一条消息就往会话记录里追加一行；这里只看这些 .jsonl 的修改时间，不读内容。
    文件名就是会话 id，和插件写的 <会话 id>.json 对得上。"""

    def __init__(self, root: Path) -> None:
        self.root = root

    def scan(self, covered: set[str]) -> tuple[str | None, float]:
        """（最近有动静的会话 id, 插件没覆盖的会话里最新的修改时间）。covered 是插件在写数据的会话 id。"""
        active, active_at, newest = None, 0.0, 0.0
        try:
            with os.scandir(self.root) as projects:
                for project in projects:
                    if not project.is_dir(follow_symlinks=False):
                        continue
                    try:
                        with os.scandir(project.path) as files:
                            for f in files:
                                if not f.name.endswith(".jsonl"):
                                    continue
                                mtime = f.stat(follow_symlinks=False).st_mtime
                                sid = f.name[: -len(".jsonl")]
                                if mtime > active_at:
                                    active, active_at = sid, mtime
                                if sid not in covered:
                                    newest = max(newest, mtime)
                    except OSError:
                        continue
        except OSError:
            pass
        return active, newest


# ---------- 台词、设置、插件 ----------


class Lines:
    def __init__(self, path: Path, met_path: Path | None = None) -> None:
        self.path = path
        self.last: dict[str, str] = {}
        # 见面台词每天说过哪些；met_path 给了就存下来，重启后同一天不重复。
        self.met_path = met_path
        self.met = {"day": "", "seen": False, "said": []}
        if met_path is not None:
            try:
                data = json.loads(met_path.read_text(encoding="utf-8"))
                if isinstance(data, dict) and isinstance(data.get("said"), list):
                    self.met = {"day": str(data.get("day", "")), "seen": bool(data.get("seen")), "said": data["said"]}
            except (OSError, ValueError):
                pass
        if not path.exists():
            try:
                path.write_text(json.dumps(DEFAULT_LINES, ensure_ascii=False, indent=2), encoding="utf-8")
            except OSError:
                pass
        self.merge_idle()
        self.load()

    def merge_idle(self) -> None:
        """上一版的“闲聊”（idle）和“闲聊区”（chat）合并了：文件里还有 idle 时，把它的句子并进 chat 再去掉它。"""
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(data, dict) or "idle" not in data:
            return
        old = data.pop("idle")
        chat = data.get("chat")
        chat = list(chat) if isinstance(chat, list) else list(DEFAULT_LINES["chat"])
        if isinstance(old, list):
            chat += [s for s in old if isinstance(s, str) and s not in chat]
        data["chat"] = chat
        try:
            self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            log_current()

    def load(self) -> None:
        """读台词文件，文件里没有的分类用默认台词；台词管理窗口保存后也调用这里，马上生效。"""
        self.data = dict(DEFAULT_LINES)
        try:
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except (OSError, ValueError):
            log_current()
            return
        if isinstance(loaded, dict):
            self.data.update(loaded)

    def lines_for(self, kind: str) -> list[str]:
        """一类台词；自定义分类的键写成 custom:名称。"""
        if kind.startswith(CUSTOM):
            custom = self.data.get("custom")
            value = custom.get(kind[len(CUSTOM) :]) if isinstance(custom, dict) else None
        else:
            value = self.data.get(kind)
        return [s for s in value if isinstance(s, str)] if isinstance(value, list) else []

    def custom_names(self) -> list[str]:
        """有台词的自定义分类。"""
        custom = self.data.get("custom")
        if not isinstance(custom, dict):
            return []
        return [name for name in custom if isinstance(name, str) and any(s.strip() for s in self.lines_for(CUSTOM + name))]

    def usable(self, kind: str, values: dict, want) -> list[tuple[str, str]]:
        """这一类里条件合 want(条件列表) 心意、数据也齐的句子：（原句, 代入数据后的句子）。"""
        period = line_tags.period_of(datetime.now().hour)
        out = []
        for s in self.lines_for(kind):
            tags, text = line_tags.split(s)
            if not text.strip() or line_tags.problem(tags, kind) or not line_tags.fits(tags, period) or not want(tags):
                continue
            try:
                out.append((s, text.format(**values)))
            except (LookupError, ValueError, AttributeError, TypeError):
                continue  # 缺数据或写法不对的句子跳过，台词管理窗口会把写法不对的标红
        return out

    def pick(self, kind: str, values: dict) -> str | None:
        """随机挑一句；带【见面】【今天第一次】的句子只在见面时说（见 meet）。"""
        usable = [text for _, text in self.usable(kind, values, lambda tags: line_tags.MEET not in tags and line_tags.FIRST not in tags)]
        if not usable:
            return None
        # 同一类不连着说同一句。
        choices = [s for s in usable if s != self.last.get(kind)] or usable
        line = random.choice(choices)
        self.last[kind] = line
        return line

    def meet(self, values: dict, chance: float = 0.5) -> str | None:
        """见面（她刚出来、被叫醒、从隐藏里叫出来）时从闲聊区挑一句见面台词，同一句每天最多一次。

        每天第一次见面时先挑【今天第一次】的句子，没有就挑【见面】的，一定说；之后的见面按 chance 的机会挑【见面】的。
        """
        today = line_tags.day_key(datetime.now())
        if self.met["day"] != today:
            self.met = {"day": today, "seen": False, "said": []}
        first = not self.met["seen"]
        self.met["seen"] = True
        said = set(self.met["said"])
        pools = [line_tags.FIRST, line_tags.MEET] if first else [line_tags.MEET]
        line = None
        if first or random.random() < chance:
            for tag in pools:
                found = [(raw, text) for raw, text in self.usable("chat", values, lambda tags, t=tag: t in tags) if raw not in said]
                if found:
                    raw, line = random.choice(found)
                    self.met["said"].append(raw)
                    break
        if self.met_path is not None:
            try:
                self.met_path.write_text(json.dumps(self.met, ensure_ascii=False), encoding="utf-8")
            except OSError:
                log_current()
        return line


def load_config() -> dict:
    try:
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    cfg = dict(DEFAULT_CONFIG)
    if isinstance(data, dict):
        cfg.update({k: v for k, v in data.items() if k in DEFAULT_CONFIG})
    # 更早的版本只有“闲聊”（chatter，开关或三档），没有 chat：按原来的意思换算成现在的档位。
    if isinstance(data, dict) and "chat" not in data and "chatter" in data:
        old = data["chatter"]
        cfg["chat"] = {True: "rare", False: "off", "often": "normal", "normal": "rare", "rare": "seldom", "off": "off"}.get(old, "normal")
    for key, options in CHOICES.items():
        if cfg[key] not in options:
            cfg[key] = DEFAULT_CONFIG[key]
    for key, value in DEFAULT_CONFIG.items():
        if isinstance(value, bool) and not isinstance(cfg[key], bool):
            cfg[key] = value
    if not isinstance(cfg["done_path"], str):
        cfg["done_path"] = None
    return cfg


def save_config(cfg: dict) -> None:
    tmp = CONFIG_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, CONFIG_FILE)


def is_link(path: Path) -> bool:
    """符号链接或目录联接（junction）：往里面写会写到别的地方去。"""
    try:
        st = os.lstat(path)
    except OSError:
        return False
    return stat.S_ISLNK(st.st_mode) or bool(getattr(st, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def plugin_version(folder: Path) -> str | None:
    try:
        data = json.loads((folder / PLUGIN_FILES[0]).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    version = data.get("version") if isinstance(data, dict) else None
    return version if isinstance(version, str) else None


def plugin_status(src: Path = PLUGIN_SRC, dst: Path = PLUGIN_DST) -> str:
    """nosource 找不到随附的插件；blocked 安装位置是链接；missing 没装；current 和随附的一样；outdated 不一样。"""
    if not all((src / name).is_file() for name in PLUGIN_FILES):
        return "nosource"
    if is_link(dst) or is_link(dst.parent):
        return "blocked"
    if not (dst / PLUGIN_FILES[0]).exists():
        return "missing"
    try:
        same = all((src / name).read_bytes() == (dst / name).read_bytes() for name in PLUGIN_FILES)
    except OSError:
        same = False
    return "current" if same else "outdated"


def install_plugin(src: Path = PLUGIN_SRC, dst: Path = PLUGIN_DST) -> str:
    """把随附的插件复制到 ~/.claude/skills 下，返回给她说的结果。只写这三个文件，不删任何东西。"""
    state = plugin_status(src, dst)
    if state == "nosource":
        return "找不到随附的插件文件，没法安装。"
    if state == "blocked":
        return f"{dst} 是一个链接，为了不写到别的地方，没有安装。"
    if state == "current":
        return "插件已经是最新的了。"
    try:
        for name in PLUGIN_FILES:
            target = dst / name
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_name(target.name + ".tmp")
            tmp.write_bytes((src / name).read_bytes())
            os.replace(tmp, target)
    except OSError as err:
        return f"安装失败：{err}"
    done = "更新好了" if state == "outdated" else "装好了"
    return f"插件{done}。之后新开的 Claude Code 会话会加载它，额度、上下文和任务进度就会实时同步过来。"


# ---------- 动作 ----------


def path_heart() -> QPainterPath:
    p = QPainterPath(QPointF(0, 4))
    p.cubicTo(-2, 2, -5, 0.5, -5, -1.8)
    p.cubicTo(-5, -3.6, -3.6, -4.8, -2.2, -4.8)
    p.cubicTo(-1.2, -4.8, -0.4, -4.2, 0, -3.3)
    p.cubicTo(0.4, -4.2, 1.2, -4.8, 2.2, -4.8)
    p.cubicTo(3.6, -4.8, 5, -3.6, 5, -1.8)
    p.cubicTo(5, 0.5, 2, 2, 0, 4)
    p.closeSubpath()
    return p


def path_star() -> QPainterPath:
    p = QPainterPath(QPointF(0, -7))
    p.quadTo(1, -1, 7, 0)
    p.quadTo(1, 1, 0, 7)
    p.quadTo(-1, 1, -7, 0)
    p.quadTo(-1, -1, 0, -7)
    p.closeSubpath()
    return p


def path_drop() -> QPainterPath:
    p = QPainterPath(QPointF(0, -6.5))
    p.cubicTo(2.6, -2.6, 4.6, 0, 4.6, 2.3)
    p.arcTo(QRectF(-4.6, -2.3, 9.2, 9.2), 0, -180)
    p.cubicTo(-4.6, 0, -2.6, -2.6, 0, -6.5)
    p.closeSubpath()
    return p


def path_mark() -> QPainterPath:
    p = QPainterPath()
    p.addRoundedRect(QRectF(-2, -9, 4, 9.5), 2, 2)
    p.addEllipse(QPointF(0, 3.8), 2.1, 2.1)
    return p


def path_anger() -> QPainterPath:
    """生气的符号：四段拐角朝里的短弧围成一圈，像动画里额头上冒的青筋。"""
    p = QPainterPath()
    for k in range(4):
        arc = QPainterPath(QPointF(1.3, -5))
        arc.quadTo(QPointF(1.3, -1.3), QPointF(5, -1.3))
        p.addPath(QTransform().rotate(90 * k).map(arc))
    return p


def path_puff() -> QPainterPath:
    """一小团云：三个圆叠在一起。叹气的白气和小乌云共用。"""
    p = QPainterPath()
    for x, y, r in ((0, 0, 2.6), (2.8, 0.4, 2.0), (-2.6, 0.5, 1.9), (1.2, -1.6, 1.9)):
        c = QPainterPath()
        c.addEllipse(QPointF(x, y), r, r)
        p = p.united(c)
    return p


def path_spiral() -> QPainterPath:
    """头晕的螺旋：从中心往外绕两圈。"""
    p = QPainterPath(QPointF(0, 0))
    for i in range(1, 49):
        t = i / 48 * 4 * math.pi
        r = 0.42 * t
        p.lineTo(r * math.cos(t), r * math.sin(t))
    return p


def path_note() -> QPainterPath:
    """八分音符：斜着的椭圆符头、竖杆和一面小旗。"""
    head = QPainterPath()
    head.addEllipse(QPointF(0, 0), 1.9, 1.35)
    p = QTransform().rotate(-22).map(head)
    p.addRect(QRectF(1.2, -7.2, 0.75, 7.0))
    flag = QPainterPath(QPointF(1.95, -7.2))
    flag.cubicTo(QPointF(3.6, -6.0), QPointF(4.4, -4.6), QPointF(3.4, -2.6))
    flag.cubicTo(QPointF(3.6, -4.2), QPointF(3.0, -5.0), QPointF(1.95, -5.6))
    flag.closeSubpath()
    return p.united(flag)


# 起跳、腾空、落地：（进度, 上移, 横向缩放, 纵向缩放）。上移以角色方框的百分之一为单位。
JUMP = ((0.0, 0, 1, 1), (0.15, 0, 1.06, 0.92), (0.40, -9, 0.96, 1.05), (0.62, 0, 1.05, 0.94), (0.78, -2, 1, 1), (1.0, 0, 1, 1))


class Spring:
    """阻尼弹簧：value 追随 target。按下时又快又稳，松手时故意欠阻尼，好弹回来晃两下。"""

    def __init__(self) -> None:
        self.value = self.target = 1.0
        self.velocity = 0.0
        self.k, self.c = 260.0, 9.0

    def aim(self, target: float, k: float, c: float) -> None:
        self.target, self.k, self.c = target, k, c

    def step(self, dt: float) -> None:
        n = max(1, math.ceil(dt * 240))
        h = dt / n
        for _ in range(n):
            self.velocity += (-self.k * (self.value - self.target) - self.c * self.velocity) * h
            self.value += self.velocity * h

    def settled(self) -> bool:
        return abs(self.value - self.target) < 1e-3 and abs(self.velocity) < 1e-2


class Motion:
    """她的全部动作状态；时间用 time.monotonic() 的秒数。"""

    def __init__(self) -> None:
        self.sx, self.sy = Spring(), Spring()
        self.jump_at: float | None = None
        self.hops = 0
        self.tilt = self.hover = self.blush = 0.0
        self.hovered = False
        self.heart_at: float | None = None
        self.happy_until = 0.0
        self.next_blink = 0.0
        self.blinks: list[tuple[float, float]] = []
        self.lively_until = 0.0

    def press(self, now: float) -> None:
        self.sx.aim(1.1, 900, 60)
        self.sy.aim(0.86, 900, 60)
        self.lively_until = now + 2

    def release(self, now: float, bouncy: bool = True) -> None:
        k, c = (260, 9) if bouncy else (500, 40)
        self.sx.aim(1, k, c)
        self.sy.aim(1, k, c)
        self.lively_until = now + 2

    def land(self, now: float) -> None:
        self.sx.value, self.sy.value = 1.06, 0.92
        self.release(now)

    def cheer(self, now: float, hops: int = 2) -> None:
        self.happy_until = now + max(2.6, hops * HOP)
        self.jump_at, self.hops = now, hops
        self.lively_until = now + hops * HOP + 0.5

    def jump_pose(self, now: float) -> tuple[float, float, float]:
        if self.jump_at is None:
            return 0.0, 1.0, 1.0
        e = now - self.jump_at
        if e >= HOP * self.hops:
            self.jump_at = None
            return 0.0, 1.0, 1.0
        q = (e % HOP) / HOP
        for (q0, y0, a0, b0), (q1, y1, a1, b1) in zip(JUMP, JUMP[1:]):
            if q <= q1:
                s = (q - q0) / (q1 - q0)
                s = s * s * (3 - 2 * s)
                return y0 + (y1 - y0) * s, a0 + (a1 - a0) * s, b0 + (b1 - b0) * s
        return 0.0, 1.0, 1.0

    def blinking(self, now: float) -> bool:
        return any(a <= now < b for a, b in self.blinks)

    def step(self, now: float, dt: float, lean: float, drag_tilt: float | None, can_blink: bool) -> None:
        self.sx.step(dt)
        self.sy.step(dt)
        target = drag_tilt if drag_tilt is not None else (lean if self.hovered else 0.0)
        self.tilt += (target - self.tilt) * min(1.0, dt * 10)
        self.hover += ((1.0 if self.hovered else 0.0) - self.hover) * min(1.0, dt * 8)
        want = 1.0 if self.hovered or now < self.happy_until else 0.0
        self.blush += (want - self.blush) * min(1.0, dt * 6)
        if self.next_blink == 0.0:
            self.next_blink = now + 2.0
        if can_blink and not self.hovered and now >= self.next_blink:
            self.blinks = [(now, now + 0.13)]
            if random.random() < 0.25:
                self.blinks.append((now + 0.25, now + 0.38))
            self.next_blink = now + random.uniform(3.0, 6.5)
        self.blinks = [(a, b) for a, b in self.blinks if b > now]

    def lively(self, now: float) -> bool:
        return now < self.lively_until or self.jump_at is not None or not (self.sx.settled() and self.sy.settled())


# ---------- 卡片上的文字 ----------


def short_when(ts: float, now: float) -> str:
    """卡片上的时刻尽量短：14:22、明天 14:22、周二 01:04，再远就只写日期。"""
    d = datetime.fromtimestamp(ts)
    days = (d.date() - datetime.fromtimestamp(now).date()).days
    clock = d.strftime("%H:%M")
    if days == 0:
        return clock
    if days == 1:
        return f"明天 {clock}"
    if 1 < days <= 7:
        return f"周{WEEKDAYS[d.weekday()]} {clock}"
    return f"{d.month}/{d.day}"


def card_reset(ts: float | None, value: float | None, now: float, exact: bool) -> tuple[str, str]:
    """卡片上的重置时间，拆成（浅色的“重置于”, 时刻）。推算的和插件给的准确时间写法一样，鼠标停在卡片上的提示里才分。"""
    if value is not None and value <= 0:
        return "", "未计时"
    if ts is None:
        return "", "--"
    if ts <= now:
        return "", "已重置"
    return "重置于", " " + short_when(ts, now)


def rate_text(rate: float) -> str:
    return f"{rate:.0f}" if rate >= 10 else f"{rate:.1f}".rstrip("0").rstrip(".")


# ---------- 窗口 ----------


class Pet(QWidget, EdgeDock):
    def __init__(self, cfg: dict, art: Art, lines: Lines, still: bool = False) -> None:
        super().__init__(None, WINDOW_FLAGS)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setMouseTracking(True)
        self.setWindowTitle("Claude娘")
        self.cfg, self.art, self.lines = cfg, art, lines
        self.fixed_clock: float | None = None  # 自检时把动画定格在某一刻
        self.heart, self.star, self.drop, self.mark, self.anger = path_heart(), path_star(), path_drop(), path_mark(), path_anger()
        self.puff, self.spiral, self.note = path_puff(), path_spiral(), path_note()
        # 头边正在冒的小特效（种类, 开始, 结束）、上一种，以及下一阵最早什么时候开始。
        self.fx: tuple[str, float, float] | None = None
        self.fx_last: str | None = None
        self.fx_next = 0.0
        self.motion = Motion()
        self.bubble = Bubble()
        self.reader = UsageReader(USAGE_FILE)
        self.live = LiveReader(LIVE_DIR)
        self.watcher = TranscriptWatcher(PROJECTS_DIR)
        self.active_session = self.watcher.scan(set())[0]  # 最近有动静的 Claude Code 会话 id
        self.hist = Usage()
        self.usage = Usage()
        # 插件的读数随时间记下来，和桌面端的历史一起算消耗速度。
        self.track: dict[str, list[tuple[float, float]]] = {"five": [], "seven": []}
        self.track_at = 0.0
        self.working = False
        self.started = time.time()
        # 最近一次有人碰她、最近一次看到 Claude 在干活的时刻，用来决定要不要打瞌睡。
        self.touched = self.work_seen = self.started
        self.asleep = False
        # 没装插件时这一阵会话记录开始变动的时刻；装了插件时每个会话文件上次看到的（第几轮, 状态）。
        self.busy_since: float | None = None
        self.turns: dict[str, tuple[int, str]] = {}
        self.last_task: float | None = None
        # 临时动作：（wave 挥手、pout 鼓脸、worry 着急, 结束时刻），以及欢呼姿势持续到的时刻。
        self.act: tuple[str, float] | None = None
        self.cheer_until = 0.0
        self.leaving = False
        self.press_at: QPoint | None = None
        self.press_win = QPoint()
        self.dragging = False
        self.drag_v = 0.0
        self.last_move: tuple[int, float] | None = None
        self.clicks: list[float] = []
        self.floaters: list[tuple[str, QColor, float]] = []
        # 插件每涨 1% 就更新一次读数，攒一攒再飘 +N%：上次飘字时的值和时刻。
        self.float_base: float | None = None
        self.float_at = 0.0
        self.last_said = 0.0
        self.stale_warned = False
        self.pace_warned = self.started - PACE_EVERY + 5 * 60  # 启动 5 分钟后才可能提醒消耗太快
        self.card_rect = QRectF()
        self.card_m: dict[str, float] = {}
        self.phase = 0.0
        self.t_last = time.monotonic()
        # 当前的表情（idle、happy 等）和为它挑中的那张图，以及下次重新挑图的时刻。
        self.expr: str | None = None
        self.target_key: str | None = None
        self.repick_at = 0.0
        # 正在显示的图；换图时上一张淡出。None 表示刚开始，直接显示不淡化。
        self.shown_face: str | None = None
        self.prev_face: str | None = None
        self.face_at = 0.0
        self.claude_seen = False
        self.claude_gone = 0
        # 用户手动隐藏了她；前台全屏时自动躲开；全屏期间用户又把她叫了出来，这次全屏就不再躲。
        self.user_hidden = self.dodging = self.stay = False
        # 鼠标穿透：现在是否穿透，以及她当前的不透明度（鼠标停在她身上时会淡下去）。
        self.through = False
        self.dim = 1.0
        self.editor: QWidget | None = None
        self.tray: QSystemTrayIcon | None = None
        self.frame = QTimer(self)
        self.frame.timeout.connect(self.tick)
        self.chat_timer = QTimer(self)
        self.chat_timer.setSingleShot(True)
        self.chat_timer.timeout.connect(self.chat)
        # 简洁卡片被点开成详细卡片了没有；card_timer 到点自动收起。
        self.card_open = False
        self.card_timer = QTimer(self)
        self.card_timer.setSingleShot(True)
        self.card_timer.timeout.connect(self.card_timeout)
        self.pass_timer = QTimer(self)
        self.pass_timer.timeout.connect(self.update_passthrough)
        self.init_edge()
        self.layout_size()
        self.refresh_usage(react=False)
        if still:
            return
        self.frame.start(33)
        self.usage_timer = QTimer(self)
        self.usage_timer.timeout.connect(self.refresh_usage)
        self.usage_timer.start(20_000)
        self.watch_timer = QTimer(self)
        self.watch_timer.timeout.connect(self.watch_claude)
        self.watch_timer.start(3_000)
        self.follow_timer = QTimer(self)
        self.follow_timer.timeout.connect(self.follow_claude)
        self.follow_timer.start(3_000)
        self.follow_claude()
        self.schedule_chat()
        self.top_timer = QTimer(self)
        self.top_timer.timeout.connect(self.keep_on_top)
        self.top_timer.start(TOP_CHECK)
        if cfg["passthrough"]:
            self.pass_timer.start(PASS_TICK)
        if not cfg["topmost"]:
            self.apply_topmost()
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray = QSystemTrayIcon(art.icon(), self)
            self.tray_menu = QMenu()
            self.tray_menu.aboutToShow.connect(lambda: self.populate(self.tray_menu))
            self.tray.setContextMenu(self.tray_menu)
            self.tray.activated.connect(self.tray_clicked)
            self.tray.show()
            # 退出前先收起托盘图标，免得任务栏里留下点不动的残影。
            QApplication.instance().aboutToQuit.connect(self.tray.hide)
        self.update_tray_tip()
        self.place_initial()
        QTimer.singleShot(1200, self.greet)

    # --- 尺寸与位置 ---

    def card_px(self) -> int:
        return CARD_FONT[self.cfg["size"]]

    def card_style(self) -> str:
        return "detail" if self.cfg["card"] == "detail" or self.card_open else "compact"

    def on_card(self, pos: QPoint) -> bool:
        """点在脚下的卡片上（窗口里全透明的地方点不到，所以脚底以下能点到的就是卡片）。"""
        return self.edge is None and pos.y() > self.feet.y()

    def open_card(self, flag: bool) -> None:
        """简洁卡片展开成详细卡片或收起来，脚底在屏幕上的位置不动。"""
        if self.edge is not None:  # 贴边时只有胶囊，不展开
            flag = False
        if flag == self.card_open:
            return
        g = self.feet_global()
        self.card_open = flag
        self.layout_size()
        self.move(g - self.feet.toPoint())
        if flag:
            self.card_timer.start(CARD_OPEN_FOR * 1000)
        else:
            self.card_timer.stop()
        self.update()

    def card_timeout(self) -> None:
        if self.geometry().contains(QCursor.pos()):
            self.card_timer.start(2000)
        else:
            self.open_card(False)

    def layout_size(self) -> None:
        self.box = SIZES[self.cfg["size"]]
        px = self.card_px()
        self.card_font, self.card_bold, self.card_small = font(px), font(px, True), font(px - 1)
        self.pill_font, self.pill_bold = font(px + 1), font(px + 1, True)
        if self.card_style() == "detail":
            card_w, card_h = self.detail_metrics(px)
        else:
            card_w, card_h = self.compact_metrics(px)
        # 卡片比她宽时把窗口加宽；多出来的地方是全透明的，点击会直接落到下面的窗口。
        w = max(round(self.box * 1.36), math.ceil(card_w) + 8)
        h = round(self.box * 1.2) + card_h + 10
        self.setFixedSize(w, h)
        # 脚底：角色正方形底边的中点。所有身体变换都以这里为原点。
        self.feet = QPointF(w / 2, h - card_h - 10)
        if self.edge is not None:
            self.layout_edge()

    def detail_metrics(self, px: int) -> tuple[float, float]:
        """详细卡片按最宽的情况排版，窗口大小不用跟着数据变。每行：5h、进度条、百分比、重置时间。"""
        f, b = QFontMetricsF(self.card_font), QFontMetricsF(self.card_bold)
        m = {
            "pad_x": round(px * 0.8),
            "pad_y": round(px * 0.45),
            "gap": round(px * 0.5),
            "row_h": round(px * 1.7),
            "label_w": max(f.horizontalAdvance(s) for s in ("5h", "7d")),
            "bar_w": round(self.box * 0.3),
            "bar_h": max(4, round(px * 0.5)),
            "pct_w": b.horizontalAdvance("100%"),
            "reset_w": max(f.horizontalAdvance(s) for s in ("重置于 周二 00:00", "重置于 明天 00:00", "已重置", "未计时")),
        }
        m["width"] = 2 * m["pad_x"] + m["label_w"] + m["bar_w"] + m["pct_w"] + m["reset_w"] + 3 * m["gap"]
        self.card_m = m
        # 留出三行：5 小时、7 天，以及装了插件后的上下文和花费。
        return m["width"], 2 * m["pad_y"] + 3 * m["row_h"]

    def compact_metrics(self, px: int) -> tuple[float, float]:
        f, b = QFontMetricsF(self.pill_font), QFontMetricsF(self.pill_bold)
        self.card_m = {}
        return f.horizontalAdvance("5h  · 7d ") + 2 * b.horizontalAdvance("100%") + 20, round((px + 1) * 1.85)

    def feet_global(self) -> QPoint:
        return self.mapToGlobal(self.feet.toPoint())

    def head_anchor(self) -> QPoint:
        if self.edge is not None:
            r = self.edge_rect
            return self.mapToGlobal(QPoint(round(r.center().x()), round(r.bottom() if self.edge == "top" else r.top() + 2)))
        return self.mapToGlobal(QPoint(round(self.feet.x()), round(self.feet.y() - self.box * 0.98)))

    def place_initial(self) -> None:
        edge = self.cfg.get("edge")
        if isinstance(edge, list) and len(edge) == 3 and edge[0] in self.art.edges:
            g = QPoint(int(edge[1]), int(edge[2]))
            if QGuiApplication.screenAt(g) is not None:
                self.move(g)
                self.dock(edge[0], self.edge_along())
                return
        feet = self.cfg.get("feet")
        if isinstance(feet, list) and len(feet) == 2:
            g = QPoint(int(feet[0]), int(feet[1]))
            if QGuiApplication.screenAt(g) is not None:
                self.move(g - self.feet.toPoint())
                return
        self.reset_position(save=False)

    def reset_position(self, save: bool = True) -> None:
        self.undock()
        area = QGuiApplication.primaryScreen().availableGeometry()
        self.move(area.right() - self.width() - 16, area.bottom() - self.height() - 8)
        if save:
            self.cfg["feet"] = self.cfg["edge"] = None
            save_config(self.cfg)

    def save_position(self) -> None:
        if self.edge is not None:
            self.cfg["edge"] = [self.edge, self.x(), self.y()]
        else:
            g = self.feet_global()
            self.cfg["feet"], self.cfg["edge"] = [g.x(), g.y()], None
        save_config(self.cfg)

    def relayout(self, key: str, value: str) -> None:
        """换大小或卡片样式：窗口尺寸会变，保持脚底在屏幕上的位置不动；贴着边时仍贴在原处。"""
        g = self.feet_global()
        along, area = self.edge_along(), self.screen_area(self.pos())
        self.cfg[key] = value
        self.art.cache.clear()
        self.layout_size()
        if self.edge is not None:
            self.place_edge(along, area)
        else:
            self.move(g - self.feet.toPoint())
        self.save_position()
        self.update()

    def set_size(self, key: str) -> None:
        self.relayout("size", key)

    def lean(self) -> float:
        """她朝屏幕中间那一侧歪头。"""
        g = self.feet_global()
        screen = QGuiApplication.screenAt(g) or QGuiApplication.primaryScreen()
        return -4.0 if g.x() > screen.availableGeometry().center().x() else 4.0

    def moveEvent(self, e) -> None:
        if self.bubble.isVisible():
            self.bubble.place(self.head_anchor())

    # --- 状态 ---

    def clock(self) -> float:
        return self.fixed_clock if self.fixed_clock is not None else time.monotonic()

    def action(self, now: float) -> str | None:
        if self.act is not None and now < self.act[1]:
            return self.act[0]
        self.act = None
        return None

    def start_action(self, name: str, seconds: float) -> None:
        now = self.clock()
        self.act = (name, now + seconds)
        self.motion.lively_until = max(self.motion.lively_until, now + seconds)

    def activity(self, now: float) -> str:
        if now < self.motion.happy_until:
            return "happy"
        if self.asleep:
            return "sleep"
        return "working" if self.working else "idle"

    def mood(self) -> str:
        now = time.time()
        u = self.usage
        live = [
            v
            for v, reset in ((u.five, u.five_reset), (u.seven, u.seven_reset))
            if v is not None and not (reset is not None and reset <= now)
        ]
        return tone(max(live, default=0)).replace("ok", "calm")

    def face(self, now: float) -> str:
        """该显示哪种表情。动作图缺了就退回相近的表情：挥手用笑脸，鼓脸用着急，打瞌睡用闭眼图。"""
        have = self.art.variants
        act = self.action(now)
        if self.dragging and "drag" in have:
            return "drag"
        fallbacks = {"wave": ("wave", "happy"), "pout": ("pout", "worried"), "worry": ("worried",)}
        if act in fallbacks:
            return next((k for k in fallbacks[act] if k in have), "idle")
        state = self.activity(now)
        if state == "happy":
            if now < self.cheer_until and "cheer" in have:
                return "cheer"
            if "happy" in have:
                return "happy"
        # 打瞌睡时鼠标移上来也不醒，要点她才醒。
        if state == "sleep" and "sleep" in have:
            return "sleep"
        # 鼠标移上来就笑；看书的姿势和其他几张不同，所以笑脸要整张换掉而不是叠在上面。
        if self.motion.hovered and "happy" in have:
            return "happy"
        if state == "working" and "working" in have:
            return "working"
        if self.mood() != "calm" and "worried" in have:
            return "worried"
        return "idle"

    def pick_variant(self, expr: str) -> str:
        """按 weights.json 的权重从这种表情的几张图里挑一张；自检时固定用第一张。"""
        options = self.art.variants[expr]
        if self.fixed_clock is not None or len(options) == 1:
            return options[0][0]
        keys, weights = zip(*options)
        return random.choices(keys, weights)[0]

    def faces(self, now: float) -> tuple[str, str | None, float]:
        """当前要画的图，以及正在淡出的上一张图和这次切换的进度（0 到 1）。"""
        expr = self.face(now)
        # 换表情时挑一张；同一种表情持续久了（比如一直着急、一直看书）也隔一阵重挑，几张差分轮着出现。
        if expr != self.expr or now >= self.repick_at:
            self.expr, self.target_key = expr, self.pick_variant(expr)
            self.repick_at = now + random.uniform(*REPICK)
        key = self.target_key
        if key != self.shown_face:
            self.prev_face, self.shown_face, self.face_at = self.shown_face, key, now
        progress = (now - self.face_at) / FACE_FADE
        if self.prev_face is None or progress >= 1:
            return key, None, 1.0
        return key, self.prev_face, progress

    # --- 额度 ---

    def refresh_usage(self, react: bool = True) -> None:
        now = time.time()
        self.reader.poll()
        self.hist = summarize(self.reader.samples, now, self.reader.missing)
        self.live.poll(now)
        self.recompute(now, react)

    def recompute(self, now: float, react: bool = True) -> None:
        """把桌面端历史和插件读数合起来，算消耗速度，再看要不要有反应。"""
        reading = self.live.reading()
        if reading is not None and reading[1] > self.track_at:
            limits, at = reading
            self.track_at = at
            for name, kind in KINDS.items():
                if kind in limits:
                    self.track[name].append((at, limits[kind][0]))
            for name in self.track:
                self.track[name] = [pt for pt in self.track[name] if pt[0] >= now - 8 * 86400]
        points = {
            name: sorted(series(self.reader.samples, key) + self.track[name]) for name, key in (("five", "fh"), ("seven", "sd"))
        }
        old = self.usage
        self.usage = merge(self.hist, reading, self.live.current(now, self.active_session), points, now)
        if react:
            self.react(old, self.usage, now)
            self.check_pace(now)
        if not self.usage.stale(now):
            self.stale_warned = False
        self.update_tray_tip()
        self.update()

    def react(self, old: Usage, new: Usage, now: float) -> None:
        """新读数到了：重置就庆祝，跨过 50/70/90 就提醒，其余只飘一个增量。"""
        if new.at is None or old.at is None or new.at <= old.at:
            return
        o5, n5, o7, n7 = old.five, new.five, old.seven, new.seven
        if o5 is not None and n5 is not None and o5 > 0 and (n5 == 0 or n5 <= o5 - 3):
            self.float_base = None
            self.celebrate("reset")
            return
        if o7 is not None and n7 is not None and o7 >= 10 and n7 <= o7 - 5:
            self.celebrate("reset_week")
            return
        if o5 is not None and n5 is not None and n5 > o5:
            base = o5 if self.float_base is None or self.float_base > n5 else self.float_base
            if n5 - base >= 1 and now - self.float_at >= 45:
                self.floaters.append((f"+{round(n5 - base)}%", TONES[tone(n5)], self.clock()))
                base, self.float_at = n5, now
            self.float_base = base
            for limit, kind in ((90, "danger"), (70, "warn"), (50, "half")):
                if o5 < limit <= n5:
                    if limit >= 70:
                        self.play("warn")
                    self.say_kind(kind)
                    return
        if o7 is not None and n7 is not None:
            for limit, kind in ((90, "week_danger"), (70, "week_warn")):
                if o7 < limit <= n7:
                    self.play("warn")
                    self.say_kind(kind)
                    return

    def check_pace(self, now: float) -> None:
        """照最近的速度，5 小时额度会在一个半小时内、重置之前用完，就提醒一句；两小时内最多一次。"""
        u = self.usage
        empty, _ = u.outlook("five", now)
        if empty is None or u.five is None or not 30 <= u.five < 90:
            return
        if empty - now > PACE_SOON or now - self.pace_warned < PACE_EVERY:
            return
        if now - self.last_said < 30:
            return  # 刚说过别的（比如跨过 70% 的提醒），下一轮再说，免得一句盖掉另一句
        self.pace_warned = now
        self.say_kind("pace")

    def celebrate(self, kind: str) -> None:
        self.motion.cheer(self.clock())
        self.cheer_until = self.motion.happy_until
        self.play("chime")
        self.say_kind(kind)

    # --- Claude Code 在做什么 ---

    def watch_claude(self) -> None:
        """每 3 秒看一次：装了插件的会话看插件报告的状态；其余会话（比如装插件之前就开着的）看会话记录的修改时间。
        上下文和花费只看最近有动静的那个会话，它没有插件数据时就不显示，免得显示别的会话的数字。"""
        now = time.time()
        changed = self.live.poll(now)
        covered = {name[: -len(".json")] for name in self.live.sessions}
        active, last = self.watcher.scan(covered)
        if active != self.active_session:
            self.active_session = active
            changed = True
        if changed:
            self.recompute(now)
        if not self.cfg["watch"]:
            working = False
            self.busy_since = None
        else:
            working = self.live.busy(now) or (last > self.started and now - last < BUSY_WINDOW)
            self.guess_done(now, last)
        if working:
            self.work_seen = now
        self.working = working
        self.check_turns(now)
        self.update_sleep(now)

    def check_turns(self, now: float) -> None:
        """插件写的每个会话：上次看到时还在处理（或者已经是新的一轮），现在处理完了，就是一个任务结束了。"""
        seen: dict[str, tuple[int, str]] = {}
        for name, session in self.live.sessions.items():
            turn = session.turn
            seen[name] = (turn.seq, turn.state)
            before = self.turns.get(name)
            if before is None or before == (turn.seq, "idle") or turn.state != "idle" or turn.ended is None:
                continue
            if now - turn.ended > DONE_FRESH:
                continue
            if turn.reason == "error":
                self.task_failed()
            elif turn.reason == "answer" and turn.seconds >= DONE_AFTER:
                self.task_done(turn.seconds)
        self.turns = seen

    def guess_done(self, now: float, last: float) -> None:
        """没装插件时估计任务做完：会话记录写了一阵，又安静了 45 秒以上，就算做完了。"""
        if last <= self.started:
            return
        if now - last < BUSY_WINDOW:
            if self.busy_since is None:
                self.busy_since = last
            return
        if self.busy_since is not None and now - last >= QUIET:
            seconds = last - self.busy_since
            self.busy_since = None
            if seconds >= DONE_AFTER:
                self.task_done(seconds)

    def task_done(self, seconds: float) -> None:
        self.last_task = seconds
        if not self.cfg["done"]:
            return
        self.motion.cheer(self.clock())
        self.cheer_until = self.motion.happy_until
        # 你正看着 Claude 的窗口时不响，免得多余。
        if not self.user_hidden and not claude_foreground():
            self.play("done")
        self.say_kind("done", {"task_time": duration_text(seconds)})

    def task_failed(self) -> None:
        if not self.cfg["done"]:
            return
        self.start_action("worry", WORRY_FOR)
        self.say_kind("fail")

    def update_sleep(self, now: float) -> None:
        self.asleep = bool(
            self.cfg["sleep"] and not self.working and not self.leaving and now - max(self.touched, self.work_seen) >= SLEEP_AFTER
        )

    def touch(self) -> None:
        """有人碰她：记下时间，正在打瞌睡就醒过来。"""
        self.touched = time.time()
        self.asleep = False

    def follow_claude(self) -> None:
        """Claude 桌面端的窗口关掉后跟着退出；连续两次（3 到 6 秒）看不到才算关，免得窗口重建的一瞬间误判。

        只认亲眼见过它开着之后的关闭：Claude 没开时手动启动的她，要等 Claude 开过再关才会退出。
        """
        state = claude_open()
        if state:
            self.claude_seen, self.claude_gone = True, 0
        elif state is False and self.claude_seen:
            if not self.cfg["follow"]:
                self.claude_seen = False  # 不跟随期间看着它关掉的，之后不再算数
                return
            if self.editor is not None:
                return  # 台词窗口开着时先不走，等它关了再说，免得没保存的修改丢掉
            self.claude_gone += 1
            if self.claude_gone >= 2:
                self.goodbye()

    def goodbye(self) -> None:
        """挥挥手、说一句再退出；她不在桌面上时直接退出。"""
        if self.leaving:
            return
        self.leaving = True
        if not self.isVisible():
            QApplication.quit()
            return
        self.asleep = False
        self.start_action("wave", GOODBYE_AFTER + 1)
        self.say_kind("goodbye")
        QTimer.singleShot(int(GOODBYE_AFTER * 1000), QApplication.quit)

    # --- 置顶、隐藏、穿透 ---

    def keep_on_top(self) -> None:
        """置顶窗口偶尔会被系统排到普通窗口下面（置顶标记还在），被压住了就放回最上层。

        前台有全屏程序（视频、游戏、幻灯片）而且和她在同一块屏幕上时，她先躲起来，全屏结束再出来。
        """
        if not self.cfg["topmost"]:
            return
        full = desktop_win.fullscreen_app(int(self.winId()))
        if not full:
            self.stay = False
        dodge = full and not self.stay
        if dodge != self.dodging:
            self.dodging = dodge
            self.apply_visibility()
        for w in (self, self.bubble):
            if w.isVisible() and desktop_win.buried(int(w.winId())):
                desktop_win.raise_topmost(int(w.winId()))

    def apply_topmost(self) -> None:
        on = bool(self.cfg["topmost"])
        for w in (self, self.bubble):
            visible = w.isVisible()
            w.setWindowFlag(Qt.WindowStaysOnTopHint, on)  # 改窗口标志会把窗口藏起来，要重新显示
            if visible:
                w.show()
        if not on:
            self.stay = False
            if self.dodging:
                self.dodging = False
                self.apply_visibility()

    def apply_visibility(self) -> None:
        show = not self.user_hidden and not self.dodging
        if show == self.isVisible():
            return
        if show:
            self.show()
            self.frame.start(33)
        else:
            self.bubble.hide()
            self.hide()
            self.frame.stop()

    def set_hidden(self, hidden: bool) -> None:
        """菜单或托盘里手动隐藏、显示。全屏时叫她出来，这次全屏就不再躲。"""
        was_hidden = self.user_hidden
        self.user_hidden = hidden
        if not hidden and self.dodging:
            self.dodging, self.stay = False, True
        self.apply_visibility()
        if was_hidden and not hidden:
            # 从隐藏里叫出来也算见面。
            self.say(self.lines.meet(fields(self.usage, time.time())))

    def update_passthrough(self) -> None:
        """鼠标穿透：按着 Ctrl（或者正拖着她）时恢复成能点的窗口，松开又穿透。

        Qt 改窗口标志时可能把穿透样式清掉，所以每次都按当前状态重设一遍（样式没变时不会真的写）。
        穿透时鼠标停在她身上，她淡下去，方便看清下面的内容。
        """
        on = bool(self.cfg["passthrough"])
        through = on and not (desktop_win.ctrl_down() or self.press_at is not None)
        for w in (self, self.bubble):
            # 关掉穿透时连没显示的窗口也恢复，免得气泡下次出来还点不到。
            if w.isVisible() or not on:
                desktop_win.set_click_through(int(w.winId()), through)
        if through and not self.through:
            self.motion.hovered = False
            QToolTip.hideText()
        self.through = through
        target = 1.0
        if through and self.isVisible():
            pos = self.mapFromGlobal(QCursor.pos())
            if self.on_body(pos) or self.card_rect.contains(QPointF(pos)):
                target = PASS_DIM
        self.dim += (target - self.dim) * 0.35
        if abs(self.dim - target) < 0.02:
            self.dim = target
        if abs(self.windowOpacity() - self.dim) > 0.004:
            self.setWindowOpacity(self.dim)
        if not on and self.dim == 1.0:
            self.pass_timer.stop()

    # --- 台词管理 ---

    def open_editor(self) -> None:
        if self.editor is not None:
            self.editor.showNormal()
            self.editor.raise_()
            self.editor.activateWindow()
            return
        from lines_editor import LinesEditor

        self.editor = LinesEditor(LINES_FILE, DEFAULT_LINES, LINE_KINDS, self.art.icon(), self.lines.load, self.try_line)
        self.editor.destroyed.connect(lambda *_: setattr(self, "editor", None))
        self.editor.show()

    def try_line(self, text: str) -> str | None:
        """台词窗口里的“试说这一句”：代入实时数据让她说出来；说不了时返回原因。"""
        if not self.isVisible():
            return "她现在没在桌面上，先让她出来再试。"
        # 任务用时只在任务完成时才有，试说时用上一次的，没有就编一个。
        extra = {"task_time": duration_text(self.last_task if self.last_task is not None else 200)}
        text = line_tags.split(text)[1]  # 试说时不管【条件】
        try:
            line = text.format(**fields(self.usage, time.time(), extra))
        except KeyError as err:
            return f"现在没有 {{{err.args[0]}}} 的数据，平时遇到这种情况这句会被跳过。"
        except (LookupError, ValueError, AttributeError, TypeError):
            return "这一句的花括号写法不对。"
        self.say(line)
        return None

    def request_quit(self) -> None:
        """菜单里的“退出”：台词窗口还开着时先关它，有没保存的修改会先问一声。"""
        if self.editor is not None and not self.editor.close():
            return
        self.goodbye()

    # --- 说话与声音 ---

    def say(self, text: str | None, seconds: float | None = None) -> None:
        if not text or not self.isVisible():
            return
        self.bubble.say(text, self.head_anchor(), seconds)
        self.last_said = time.time()

    def say_kind(self, kind: str, extra: dict | None = None) -> bool:
        """从一类台词里挑一句说；这一类没有能说的句子时返回 False。"""
        line = self.lines.pick(kind, fields(self.usage, time.time(), extra))
        self.say(line)
        return line is not None

    def play(self, name: str, force: bool = False) -> None:
        if (not self.cfg["sound"] and not force) or winsound is None:
            return
        if name == "done":
            path = sounds.done_path(SOUND_DIR, PRESET_DIR, self.cfg["done_sound"], self.cfg["done_path"])
        else:
            path = sounds.path_for(SOUND_DIR, name)
        try:
            winsound.PlaySound(str(path), winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
        except RuntimeError:
            pass

    def preview(self, name: str) -> None:
        """菜单里的试听：提示音关着也放。点她的声音有五个音高，试听中间那个。"""
        self.play("pat3" if name == "pat" else name, force=True)

    def time_kind(self) -> str:
        return line_tags.period_of(datetime.now().hour)

    def greet(self) -> None:
        """刚出来时打招呼：见面台词（见 Lines.meet）或者当前时段的问候，后面跟一句额度。"""
        self.start_action("wave", WAVE_FOR)
        values = fields(self.usage, time.time())
        hello = self.lines.meet(values) or self.lines.pick(self.time_kind(), values)
        info = None
        if self.usage.missing or self.usage.known():
            info = self.lines.pick("missing" if self.usage.missing else "usage", values)
        self.say("\n".join(s for s in (hello, info) if s))

    def wake_up(self) -> None:
        """打瞌睡时被点醒或拖醒：也算见面，挑不到见面台词就说“被叫醒”那类。"""
        line = self.lines.meet(fields(self.usage, time.time()))
        if line:
            self.say(line)
        else:
            self.say_kind("wake")

    def schedule_chat(self, seconds: float | None = None) -> None:
        lo_hi = CHAT.get(self.cfg["chat"])
        if lo_hi is None:
            self.chat_timer.stop()
            return
        if seconds is None:
            seconds = random.uniform(*lo_hi)
            if self.asleep:
                seconds = max(seconds, CHAT_ASLEEP)
        self.chat_timer.start(int(seconds * 1000))

    def chatter_pool(self) -> list[tuple[str, float]]:
        """闲聊时从哪几类里抽、各自的权重：闲聊区为主，混一点问候、报额度等。打瞌睡时只说梦话。"""
        if self.asleep:
            return [("sleep", 1)]
        pool = [("chat", 6), (self.time_kind(), 1)]
        if self.usage.known():
            pool.append(("usage", 2))
        if self.usage.tokens is not None or self.usage.cost is not None:
            pool.append(("session", 1))
        if self.working:
            pool.append(("working", 3))
        pool += [(CUSTOM + name, CUSTOM_WEIGHT) for name in self.lines.custom_names()]
        return pool

    def chat(self) -> None:
        """闲聊：按“设置 › 闲聊”的频率说一句，不打断正在显示的气泡。"""
        if self.cfg["chat"] not in CHAT:
            return
        if self.bubble.isVisible() or self.dragging:
            self.schedule_chat(2)
            return
        now = time.time()
        if not self.asleep and self.usage.stale(now) and not self.stale_warned:
            self.stale_warned = True
            self.say_kind("stale")
        else:
            pool = self.chatter_pool()
            # 抽中的那一类没有能说的句子（比如缺数据），就去掉它再抽。
            while pool:
                kinds, weights = zip(*pool)
                kind = random.choices(kinds, weights)[0]
                if self.say_kind(kind):
                    break
                pool = [(k, w) for k, w in pool if k != kind]
        self.schedule_chat()

    # --- 详情 ---

    def pace_note(self, name: str, now: float) -> str:
        """消耗速度的一句说明；数据不够时返回空串。"""
        u = self.usage
        rate = getattr(u, f"{name}_rate")
        if rate is None:
            return ""
        span = "最近一小时" if name == "five" else "最近一天"
        if rate < PACE[name][2]:
            return f"{span}几乎没用"
        speed = f"{span}每小时约 {rate_text(rate)}%"
        empty, projected = u.outlook(name, now)
        if empty is not None:
            return f"{speed}，照这样{after('约', when(empty, now))} 用完"
        if projected is not None:
            return f"{speed}，到重置时约 {min(100, round(projected))}%"
        return speed

    def session_text(self) -> tuple[str, str] | None:
        """脚下卡片第三行：（上下文, 花费）；没装插件时没有。"""
        u = self.usage
        if u.tokens is None and u.cost is None:
            return None
        if u.tokens is not None:
            left = "上下文 " + tokens_text(u.tokens) + (f"/{tokens_text(u.window)}" if u.window else "")
        else:
            left = "本会话"
        return left, (f"${u.cost:.2f}" if u.cost is not None else "")

    def session_line(self) -> str:
        u = self.usage
        parts = []
        if u.tokens is not None:
            ctx = f"上下文 {tokens_text(u.tokens)}"
            if u.window:
                ctx += f" / {tokens_text(u.window)}（{round(u.tokens / u.window * 100)}%）"
            parts.append(ctx)
        if u.cost is not None:
            parts.append(f"本会话按 API 价格约 ${u.cost:.2f}")
        return "，".join(parts)

    def details(self) -> str:
        """额度详情的文字版，给提示框和托盘用。"""
        u, now = self.usage, time.time()
        if not u.known():
            if u.missing:
                return "还找不到额度记录\nClaude 桌面端运行后会自动生成；装上 Claude Code 插件也能收到"
            return "额度记录是空的"
        rows = []
        for name, title in (("five", "5 小时额度"), ("seven", "7 天额度")):
            value = getattr(u, name)
            if value is None:
                continue
            reset = reset_text(getattr(u, f"{name}_reset"), value, now, getattr(u, f"{name}_exact"))
            rows.append(f"{title}：{round(value)}%，{reset}")
            note = self.pace_note(name, now)
            if note:
                rows.append(f"　{note}")
        if self.session_line():
            rows.append(self.session_line())
        if u.at is not None:
            rows.append(after("数据更新于", when(u.at, now)) + ("，桌面端和 Claude Code 可能都没在运行" if u.stale(now) else ""))
        return "\n".join(rows)

    def update_tray_tip(self) -> None:
        if self.tray is None:
            return
        u = self.usage
        parts = [f"5 小时 {round(u.five)}%" if u.five is not None else "", f"7 天 {round(u.seven)}%" if u.seven is not None else ""]
        lines = ["Claude娘", " · ".join(p for p in parts if p) or "暂无额度数据"]
        if self.session_line():
            lines.append(self.session_line())
        self.tray.setToolTip("\n".join(lines))

    # --- 菜单与托盘 ---

    def populate(self, menu: QMenu) -> None:
        menu.clear()
        visible = self.isVisible()
        menu.addAction("隐藏" if visible else "显示", lambda: self.set_hidden(visible))
        menu.addAction("立即刷新", self.refresh_usage)
        menu.addAction("管理台词…", self.open_editor)
        menu.addSeparator()
        self.add_toggle(menu, "passthrough", "鼠标穿透（按住 Ctrl 再点她）")
        self.add_toggle(menu, "topmost", "置顶")
        settings = menu.addMenu("设置")
        self.add_choice(settings, "大小", "size", SIZE_LABELS)
        self.add_choice(settings, "额度卡片", "card", CARD_LABELS)
        self.add_choice(settings, "闲聊", "chat", CHAT_LABELS)
        sound = settings.addMenu("提示音")
        self.add_toggle(sound, "sound", "播放提示音")
        self.add_choice(sound, "音量", "volume", sounds.VOLUME_LABELS)
        self.add_choice(sound, "任务结束音", "done_sound", sounds.DONE_SOUNDS)
        listen = sound.addMenu("试听")
        for name, label in sounds.NAMES:
            # triggered 会带一个 checked 参数，第一个形参要留给它。
            listen.addAction(label, lambda _=False, n=name: self.preview(n))
        sound.addAction("打开自定义声音文件夹", lambda: self.open_folder(SOUND_DIR / sounds.CUSTOM))
        settings.addSeparator()
        self.add_toggle(settings, "done", "任务完成提醒")
        self.add_toggle(settings, "sleep", "没人理时打瞌睡")
        self.add_toggle(settings, "watch", "感知 Claude Code 工作状态")
        self.add_toggle(settings, "follow", "跟随 Claude 启动和退出")
        settings.addSeparator()
        self.add_plugin_menu(settings)
        menu.addSeparator()
        # triggered 会带一个 checked 参数，直接连 reset_position 会把它当成 save=False。
        menu.addAction("回到右下角", lambda: self.reset_position())
        menu.addAction("退出", self.request_quit)

    def add_toggle(self, menu: QMenu, key: str, label: str) -> None:
        act = menu.addAction(label)
        act.setCheckable(True)
        act.setChecked(bool(self.cfg[key]))
        act.toggled.connect(lambda on, k=key: self.set_option(k, on))

    def add_choice(self, menu: QMenu, title: str, key: str, labels: dict[str, str]) -> None:
        sub = menu.addMenu(title)
        group = QActionGroup(sub)
        for value, label in labels.items():
            act = sub.addAction(label)
            act.setCheckable(True)
            act.setChecked(self.cfg[key] == value)
            act.triggered.connect(lambda _=False, k=key, v=value: self.set_choice(k, v))
            group.addAction(act)

    def add_plugin_menu(self, menu: QMenu) -> None:
        sub = menu.addMenu("Claude Code 插件")
        state = plugin_status()
        version = plugin_version(PLUGIN_DST) if state in ("current", "outdated") else None
        labels = {
            "nosource": "找不到随附的插件",
            "blocked": "安装位置是链接，不能自动安装",
            "missing": "还没安装",
            "current": f"已安装 {version or ''}".strip(),
            "outdated": "已安装，随附的版本不一样",
        }
        sub.addAction(labels[state]).setEnabled(False)
        receiving = self.live.alive(time.time())
        sub.addAction("正在收到会话数据" if receiving else "现在没有会话在发数据").setEnabled(False)
        if state in ("missing", "outdated"):
            sub.addAction("安装" if state == "missing" else "更新", self.install_plugin_clicked)
        if PLUGIN_DST.is_dir():
            sub.addAction("打开安装位置", lambda: self.open_folder(PLUGIN_DST, create=False))

    def install_plugin_clicked(self) -> None:
        message = install_plugin()
        self.say(message, 9.0)

    def open_folder(self, folder: Path, create: bool = True) -> None:
        if create:
            folder.mkdir(parents=True, exist_ok=True)
        if folder.is_dir():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def set_option(self, key: str, on: bool) -> None:
        self.cfg[key] = on
        save_config(self.cfg)
        if key == "watch":
            self.watch_claude()
        elif key == "topmost":
            self.apply_topmost()
        elif key == "passthrough":
            self.pass_timer.start(PASS_TICK)
            self.update_passthrough()
        elif key == "sleep":
            self.update_sleep(time.time())

    def pick_done_sound(self, value: str) -> bool:
        """任务结束音选“自选文件”或“音效组”时让用户挑；取消或挑的不能用时返回 False，保持原来的选项。"""
        start = self.cfg["done_path"] or str(Path.home())
        if value == "file":
            path, _ = QFileDialog.getOpenFileName(None, "选择任务结束音", start, "WAV 音频 (*.wav)")
            if not path:
                return False
            if not sounds.playable(Path(path)):
                self.say("这个文件放不了，要未压缩的 wav（PCM）。可以用音频软件另存一下。", 8.0)
                return False
        else:
            path = QFileDialog.getExistingDirectory(None, "选择音效组所在的文件夹（每次随机放一个 wav）", start)
            if not path:
                return False
            if not any(p.suffix.lower() == ".wav" and sounds.playable(p) for p in Path(path).iterdir()):
                self.say("这个文件夹里没有能放的 wav 文件。", 8.0)
                return False
        self.cfg["done_path"] = path
        return True

    def set_choice(self, key: str, value: str) -> None:
        if key == "done_sound" and value in ("file", "group"):
            # 已经选着同一项时再点一次，也是换一个文件或文件夹。
            if self.pick_done_sound(value):
                self.cfg[key] = value
                save_config(self.cfg)
                self.preview("done")
            return
        if self.cfg[key] == value:
            return
        if key in ("size", "card"):
            self.relayout(key, value)
            return
        self.cfg[key] = value
        save_config(self.cfg)
        if key == "chat":
            self.schedule_chat()
        elif key == "volume":
            try:
                sounds.ensure(SOUND_DIR, value, PRESET_DIR)
            except OSError:
                log_current()
            self.preview("done")
        elif key == "done_sound":
            self.preview("done")

    def tray_clicked(self, reason) -> None:
        if reason == QSystemTrayIcon.Trigger:
            if self.isVisible():
                self.raise_()
                self.say_kind("usage")
            else:
                self.set_hidden(False)

    def contextMenuEvent(self, e) -> None:
        self.touch()
        menu = QMenu()
        self.populate(menu)
        menu.exec_(e.globalPos())

    # --- 鼠标 ---

    def on_body(self, pos: QPoint) -> bool:
        if self.edge is not None:
            return self.edge_rect.contains(QPointF(pos))
        return abs(pos.x() - self.feet.x()) <= self.box * 0.48 and self.feet.y() - self.box <= pos.y() <= self.feet.y()

    def mousePressEvent(self, e) -> None:
        if e.button() != Qt.LeftButton:
            return
        self.press_at, self.press_win = e.globalPos(), self.pos()
        self.dragging = False
        if self.on_body(e.pos()):
            self.motion.press(self.clock())

    def mouseMoveEvent(self, e) -> None:
        if self.press_at is None:
            hovered = self.on_body(e.pos())
            # 鼠标停在她身上也算有人陪着，推迟打瞌睡；已经睡着了就不吵醒她，点她才醒。
            if hovered and not self.asleep:
                self.touched = time.time()
            self.motion.hovered = hovered
            return
        if not e.buttons() & Qt.LeftButton:
            return
        delta = e.globalPos() - self.press_at
        if not self.dragging and delta.manhattanLength() > 4:
            self.dragging = True
            if self.edge is not None:
                # 从边上拖走：恢复平常的样子，身体中间落在鼠标下面，接着拖。
                self.undock(e.globalPos())
                self.press_at, self.press_win = e.globalPos(), self.pos()
                delta = QPoint()
            woke = self.asleep
            self.touch()
            self.motion.release(self.clock(), bouncy=False)
            if woke:
                self.wake_up()
            elif random.random() < 0.35:
                self.say_kind("drag")
        if self.dragging:
            target = self.press_win + delta
            now = self.clock()
            if self.last_move is not None:
                vx = (target.x() - self.last_move[0]) / max(1e-3, now - self.last_move[1])
                self.drag_v = 0.7 * self.drag_v + 0.3 * vx
            self.last_move = (target.x(), now)
            self.move(target)

    def mouseReleaseEvent(self, e) -> None:
        if e.button() != Qt.LeftButton or self.press_at is None:
            return
        now = self.clock()
        if self.dragging:
            self.motion.land(now)
            side = self.snap_side()
            if side is not None:
                self.dock(side)
            self.save_position()
            if random.random() < 0.3:
                self.say_kind("drop")
        elif self.on_body(e.pos()):
            self.poke(now)
        else:
            self.motion.release(now)
            if self.on_card(e.pos()) and self.cfg["card"] != "detail":
                self.open_card(not self.card_open)
        self.press_at, self.last_move = None, None
        self.dragging = False
        self.drag_v = 0.0

    def leaveEvent(self, e) -> None:
        self.motion.hovered = False

    def poke(self, now: float) -> None:
        woke = self.asleep
        self.touch()
        self.motion.release(now)
        self.motion.heart_at = now
        self.clicks = [t for t in self.clicks if now - t < 3] + [now]
        # 连着点，音一个比一个高。
        self.play(f"pat{min(len(self.clicks), len(sounds.PATS))}")
        if woke:
            self.wake_up()
        elif len(self.clicks) >= 5:
            self.clicks = []
            self.start_action("pout", POUT_FOR)
            self.say_kind("click_many")
        elif self.usage.known() and random.random() < 0.3:
            self.say_kind("usage")
        else:
            self.say_kind("click")

    def event(self, e) -> bool:
        if e.type() == QEvent.ToolTip:
            if not self.through and self.card_rect.contains(QPointF(e.pos())):
                QToolTip.showText(e.globalPos(), self.details(), self)
            else:
                QToolTip.hideText()
                e.ignore()
            return True
        return super().event(e)

    # --- 每一帧 ---

    def tick(self) -> None:
        now = time.monotonic()
        dt = min(0.05, now - self.t_last)
        self.t_last = now
        state = self.activity(now)
        self.phase = (self.phase + dt / (1.5 if state == "working" else 5.5 if state == "sleep" else 3.6)) % 1.0
        drag_tilt = max(-8.0, min(8.0, -self.drag_v * 0.012)) if self.dragging else None
        can_blink = state != "sleep" and self.art.blink_for(self.shown_face or "") is not None
        self.motion.step(now, dt, self.lean(), drag_tilt, can_blink)
        self.floaters = [f for f in self.floaters if now - f[2] < 1.6]
        interval = 16 if self.motion.lively(now) or self.dragging or self.floaters else 33
        if self.frame.interval() != interval:
            self.frame.setInterval(interval)
        self.update()

    def paintEvent(self, _) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.setRenderHint(QPainter.TextAntialiasing)
        now, box, m = self.clock(), self.box, self.motion
        if self.edge is not None:
            self.paint_edge(p, now)
            return
        s = (1 - math.cos(2 * math.pi * self.phase)) / 2
        jy, jsx, jsy = m.jump_pose(now)
        p.save()
        p.translate(self.feet)
        p.translate(0, (-2.2 * s + jy) * box / 100)
        p.scale(jsx * m.sx.value, jsy * m.sy.value)
        p.rotate(m.tilt)
        p.save()
        p.scale(1 + 0.008 * s, 1 + 0.018 * s)
        p.translate(-box / 2, -box)
        self.draw_body(p, now)
        p.restore()
        p.translate(-box / 2, -box)
        p.scale(box / 100, box / 100)
        self.draw_effects(p, now)
        p.restore()
        if self.card_style() == "detail":
            self.draw_card(p)
        else:
            self.draw_compact(p)
        self.draw_floaters(p, now)

    def draw_body(self, p: QPainter, now: float) -> None:
        box, dpr, m = self.box, self.devicePixelRatioF(), self.motion
        face, prev, progress = self.faces(now)
        if prev is not None:
            # 旧表情垫在下面，前半程不透明、后半程才淡出：重叠处几乎不透光，
            # 旧图独有的部分（比如翻开的书）也是渐渐消失而不是一下子没了。
            p.setOpacity(min(1.0, 2 * (1 - progress)))
            p.drawPixmap(QPointF(0, 0), self.art.pixmap(prev, box, dpr))
            p.setOpacity(progress)
        elif m.blinking(now):
            face = self.art.blink_for(face) or face
        p.drawPixmap(QPointF(0, 0), self.art.pixmap(face, box, dpr))
        p.setOpacity(1.0)
        # 笑脸图自带红晕；没有笑脸图时才画两团腮红来表现害羞。
        if m.blush > 0.01 and "happy" not in self.art.variants:
            p.save()
            p.scale(box / 100, box / 100)
            p.setPen(Qt.NoPen)
            p.setBrush(BLUSH)
            for key in ("cheekL", "cheekR"):
                x, y = self.art.anchors[key]
                for rx, ry, a in ((6.2, 3.6, 0.3), (4.0, 2.2, 0.4)):
                    p.setOpacity(a * m.blush)
                    p.drawEllipse(QPointF(x, y), rx, ry)
            p.restore()

    def draw_effects(self, p: QPainter, now: float) -> None:
        """头顶和身边的小特效，单位是角色方框的百分之一。"""
        a, mood, state, act = self.art.anchors, self.mood(), self.activity(now), self.action(now)
        # 开心（包括鼠标移上来时的笑脸）、睡着、挥手、鼓脸时不冒着急的特效，免得和表情打架；
        # 额度的颜色在脚下的卡片上还看得到。self.expr 是 draw_body 刚画的表情。
        relaxed = state in ("happy", "sleep") or act in ("wave", "pout") or self.expr == "happy"
        kind = self.effect(now, mood, relaxed, state, act)
        if kind is not None:
            self.draw_effect(p, kind, now - self.fx[1] if self.fx else now)
        # 鼓脸图自己画了怒气符号，用的不是这张图时才由桌宠画。
        if act == "pout" and self.expr != "pout":
            k = 1 + 0.2 * (1 - math.cos(2 * math.pi * ((now % 0.8) / 0.8))) / 2
            p.save()
            p.translate(a["mark"][0] - 3, a["mark"][1] + 4)
            p.scale(k, k)
            self.stroked(p, self.anger, QColor("#e5484d"), 1.5)
            p.restore()
        if state == "working":
            p.save()
            p.translate(*a["think"])
            p.setPen(QPen(QColor("#cdbfb0"), 0.8))
            p.setBrush(Qt.white)
            p.drawEllipse(QPointF(-13.5, 10), 1.1, 1.1)
            p.drawEllipse(QPointF(-10, 7), 1.8, 1.8)
            p.drawRoundedRect(QRectF(-10.5, -5.5, 21, 11), 5.5, 5.5)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor("#e0773a"))
            for i, x in enumerate((-5, 0, 5)):
                q = ((now - i * 0.2) % 1.2) / 1.2
                up = max(0.0, 1 - abs(q - 0.3) / 0.3) if q < 0.6 else 0.0
                p.setOpacity(0.25 + 0.75 * up)
                p.drawEllipse(QPointF(x, -1.2 * up), 1.6, 1.6)
            p.restore()
        if state == "sleep":
            self.draw_zzz(p, now, a["think"])
        if state == "happy":
            for i, (x, y) in enumerate(SPARKS):
                w = (1 - math.cos(2 * math.pi * (((now - i * 0.22) % 1.1) / 1.1))) / 2
                p.save()
                p.translate(x, y)
                p.rotate(45 * w)
                p.scale(0.2 + 0.8 * w, 0.2 + 0.8 * w)
                p.setOpacity(w)
                self.outlined(p, self.star, QColor("#ffc53d"), 0.9)
                p.restore()
        if self.motion.heart_at is not None:
            e = now - self.motion.heart_at
            if e > 1.0:
                self.motion.heart_at = None
            else:
                grow = min(1.0, e / 0.25)
                scale = 0.4 + 0.6 * (1 - (1 - grow) ** 3) * (1 + 0.15 * math.sin(math.pi * grow))
                p.save()
                p.translate(a["heart"][0], a["heart"][1] - 6 * min(1.0, e / 0.3) - 4 * e)
                p.scale(scale, scale)
                p.setOpacity(1.0 if e < 0.35 else max(0.0, 1 - (e - 0.35) / 0.65))
                self.outlined(p, self.heart, QColor("#ff6b8b"), 1.0)
                p.restore()

    def effect(self, now: float, mood: str, relaxed: bool, state: str, act: str | None) -> str | None:
        """这一刻头边冒哪个小特效。额度紧张时一阵一阵地换着冒，中间歇一会儿，不会一直挂着同一个；
        心情好又闲着时偶尔哼两个音符。任务出错着急时一直冒汗。"""
        if act == "worry":
            return "sweat"
        troubled = mood != "calm" and not relaxed
        humming = mood == "calm" and state == "idle" and act is None and not self.motion.hovered and not self.dragging
        if self.fx is not None:
            kind, _, end = self.fx
            if now < end and (humming if kind == "notes" else troubled):
                return kind
            self.fx = None
        if now < self.fx_next or not (troubled or humming):
            return None
        pool = FX_POOLS[mood] if troubled else FX_POOLS["calm"]
        # 不连着冒同一种。
        pool = [(k, w) for k, w in pool if k != self.fx_last] or pool
        kinds, weights = zip(*pool)
        kind = random.choices(kinds, weights)[0]
        length = random.uniform(*FX_FOR[kind == "notes"])
        self.fx, self.fx_last = (kind, now, now + length), kind
        self.fx_next = now + length + random.uniform(*FX_GAP[mood])
        return kind

    def draw_effect(self, p: QPainter, kind: str, e: float) -> None:
        """画一种小特效；e 是这一阵开始后的秒数。单位是角色方框的百分之一。"""
        a = self.art.anchors
        p.save()
        if kind == "sweat":  # 汗滴：从额角滑下来，循环
            cycle = 1.3 if self.mood() == "danger" else 2.2
            q = (e % cycle) / cycle
            p.translate(a["sweat"][0], a["sweat"][1] - 1 + 5 * q * q)
            p.setOpacity(q / 0.15 if q < 0.15 else 1.0 if q < 0.8 else (1 - q) / 0.2)
            p.setPen(QPen(QColor("#3b8fd9"), 0.9))
            p.setBrush(QColor("#8fd0ff"))
            p.drawPath(self.drop)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(255, 255, 255, 230))
            p.drawEllipse(QPointF(-1.6, 2), 1.1, 1.7)
        elif kind == "bang":  # 感叹号：一跳一跳
            k = 1 + 0.22 * (1 - math.cos(2 * math.pi * (e % 1.0))) / 2
            p.translate(a["mark"][0], a["mark"][1] - 2.4)
            p.scale(k, k)
            p.translate(0, 2.4)
            self.outlined(p, self.mark, QColor("#e5484d"), 1.2)
        elif kind == "sigh":  # 叹气：嘴边飘出一小团白气，越飘越大、越淡
            x, y = a["cheekR"]
            for i in range(2):
                q = ((e + i * 0.8) % 1.6) / 1.6
                p.save()
                p.translate(x + 6 + 9 * q, y - 2 - 7 * q)
                s = 0.5 + 0.7 * q
                p.scale(s, s)
                p.setOpacity(min(1.0, q / 0.15) * (1.0 if q < 0.6 else (1 - q) / 0.4))
                p.setPen(QPen(QColor("#a9b4c4"), 0.7))
                p.setBrush(QColor("#f6f8fb"))
                p.drawPath(self.puff)
                p.restore()
        elif kind == "swirl":  # 头晕：头顶一圈转个不停的螺旋
            p.translate(a["mark"][0] - 6, a["mark"][1] + 5)
            p.rotate(-e * 300)
            p.setOpacity(min(1.0, e / 0.3))
            self.stroked(p, self.spiral, QColor("#8a7fd0"), 1.0)
        elif kind == "gloom":  # 阴沉线：额头上垂下几道竖线
            grow = min(1.0, e / 0.6)
            for i, (x, length) in enumerate(((30, 9), (34.5, 12), (39, 10), (43.5, 7))):
                p.setOpacity((0.8 + 0.2 * math.sin(e * 3 + i)) * grow)
                line = QPainterPath(QPointF(x, 15))
                line.lineTo(x, 15 + length * grow)
                self.stroked(p, line, QColor("#4f57a8"), 1.3)
        elif kind == "rain":  # 头顶一朵下雨的小乌云
            x, y = a["heart"][0] + 16, a["heart"][1] + 1
            p.translate(x, y + 0.8 * math.sin(e * 2.4))
            p.setOpacity(min(1.0, e / 0.3))
            p.setPen(QPen(QColor("#6f7888"), 0.7))
            p.setBrush(QColor("#a3abb9"))
            p.drawPath(self.puff)
            p.setPen(QPen(QColor("#5aa7e6"), 0.9, Qt.SolidLine, Qt.RoundCap))
            for i, dx in enumerate((-2.6, 0.4, 3.2)):
                q = ((e + i * 0.27) % 0.8) / 0.8
                p.drawLine(QPointF(dx - 0.6 * q, 3 + 6 * q), QPointF(dx - 0.6 * q - 0.4, 4.6 + 6 * q))
        elif kind == "notes":  # 哼歌：两个音符轮流从头边飘起
            x, y = a["think"]
            for i in range(2):
                q = ((e + i * 0.8) % 1.6) / 1.6
                p.save()
                p.translate(x - 6 + 8 * q + 1.5 * math.sin(q * 6), y + 10 - 10 * q)
                p.rotate(-12 + 24 * i)
                p.setOpacity(min(1.0, q / 0.2) * (1.0 if q < 0.65 else (1 - q) / 0.35))
                self.outlined(p, self.note, QColor("#e0773a"), 1.2)
                p.restore()
        p.restore()

    def draw_zzz(self, p: QPainter, now: float, at: list[float]) -> None:
        """打瞌睡：三个字母 Z 从头边轮流飘起来，越飘越大、越淡。"""
        f = font(10, True)
        for i in range(3):
            q = ((now + i * 0.9) % 2.7) / 2.7
            p.save()
            p.translate(at[0] - 8 + 18 * q, at[1] + 12 - 24 * q)
            s = 0.6 + 0.6 * q
            p.scale(s, s)
            p.setOpacity(min(1.0, q / 0.2) * (1.0 if q < 0.7 else (1 - q) / 0.3))
            path = QPainterPath()
            path.addText(QPointF(-3, 3), f, "z" if i % 2 else "Z")
            self.outlined(p, path, QColor("#7d8fd8"), 1.4)
            p.restore()

    @staticmethod
    def outlined(p: QPainter, path: QPainterPath, fill: QColor, width: float) -> None:
        """白色描边垫在填充下面，露出外侧一半，和 SVG 的 paint-order: stroke 一样。"""
        p.setPen(QPen(Qt.white, width))
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)
        p.setPen(Qt.NoPen)
        p.setBrush(fill)
        p.drawPath(path)

    @staticmethod
    def stroked(p: QPainter, path: QPainterPath, color: QColor, width: float) -> None:
        """只有线条的符号：先描一道更粗的白边，再描彩色的线。"""
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(Qt.white, width + 1.6, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.drawPath(path)
        p.setPen(QPen(color, width, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.drawPath(path)

    def draw_card(self, p: QPainter) -> None:
        """详细卡片：5h、7d 各一行（标签、进度条、百分比、重置时间），装了插件再加一行上下文和花费。"""
        u, now, m = self.usage, time.time(), self.card_m
        rows = [(label, name) for label, name in (("5h", "five"), ("7d", "seven")) if getattr(u, name) is not None]
        if not rows:
            self.draw_compact(p)
            return
        session = self.session_text()
        w = m["width"]
        h = 2 * m["pad_y"] + (len(rows) + (1 if session else 0)) * m["row_h"]
        x, y = (self.width() - w) / 2, self.feet.y() + 4
        self.card_rect = QRectF(x, y, w, h)
        stale = u.stale(now)
        p.save()
        shape = QPainterPath()
        shape.addRoundedRect(self.card_rect.adjusted(0.5, 0.5, -0.5, -0.5), 8, 8)
        soft_shadow(p, shape, spread=3, strength=30, dy=1)
        p.setPen(QPen(BORDER, 1))
        p.setBrush(QColor(255, 250, 243, 245))
        p.drawPath(shape)
        fm, fb = QFontMetricsF(self.card_font), QFontMetricsF(self.card_bold)
        cy = y + m["pad_y"]
        for label, name in rows:
            value = getattr(u, name)
            reset_at = getattr(u, f"{name}_reset")
            # 数据旧了，或者这个窗口已经过了重置时间，这一行就用浅色。
            faded = stale or (reset_at is not None and reset_at <= now)
            base = cy + (m["row_h"] + fm.ascent() - fm.descent()) / 2
            cx = x + m["pad_x"]
            p.setFont(self.card_font)
            p.setPen(LABEL)
            p.drawText(QPointF(cx, base), label)
            cx += m["label_w"] + m["gap"]
            _, projected = u.outlook(name, now)
            ghost = projected / 100 if projected is not None and not faded else None
            bar = QRectF(cx, cy + (m["row_h"] - m["bar_h"]) / 2, m["bar_w"], m["bar_h"])
            draw_bar(p, bar, value / 100, ghost, LABEL if faded else BARS[tone(value)])
            cx += m["bar_w"] + m["gap"]
            pct = f"{round(value)}%"
            p.setFont(self.card_bold)
            p.setPen(LABEL if faded else TONES[tone(value)])
            p.drawText(QPointF(cx + m["pct_w"] - fb.horizontalAdvance(pct), base), pct)
            cx += m["pct_w"] + m["gap"]
            prefix, text = card_reset(reset_at, value, now, getattr(u, f"{name}_exact"))
            p.setFont(self.card_font)
            if prefix:
                p.setPen(FAINT)
                p.drawText(QPointF(cx, base), prefix)
                cx += fm.horizontalAdvance(prefix)
            p.setPen(LABEL)
            p.drawText(QPointF(cx, base), text)
            cy += m["row_h"]
        if session:
            left, right = session
            fs = QFontMetricsF(self.card_small)
            base = cy + (m["row_h"] + fs.ascent() - fs.descent()) / 2
            right_w = fs.horizontalAdvance(right)
            room = w - 2 * m["pad_x"] - right_w - m["gap"]
            p.setFont(self.card_small)
            p.setPen(LABEL)
            p.drawText(QPointF(x + m["pad_x"], base), fs.elidedText(left, Qt.ElideRight, room))
            p.drawText(QPointF(x + w - m["pad_x"] - right_w, base), right)
        p.restore()

    def draw_compact(self, p: QPainter) -> None:
        """简洁样式：一个胶囊，5h 和 7d 的百分比。"""
        u, now = self.usage, time.time()
        segs: list[tuple[str, QColor, bool]] = []
        for label, v in (("5h ", u.five), ("7d ", u.seven)):
            if v is None:
                continue
            if segs:
                segs.append((" · ", SEP, False))
            segs += [(label, LABEL, False), (f"{round(v)}%", TONES[tone(v)], True)]
        if not segs:
            segs = [("额度 --", LABEL, False)]
        stale = u.stale(now)
        widths = [QFontMetricsF(self.pill_bold if bold else self.pill_font).horizontalAdvance(t) for t, _, bold in segs]
        h = round((CARD_FONT[self.cfg["size"]] + 1) * 1.85)
        w = sum(widths) + 20
        if self.edge is None:
            x, y = (self.width() - w) / 2, self.feet.y() + 4
        elif self.edge == "right":
            x, y = self.width() - w - 4, self.pill_origin.y()  # 贴右边时胶囊靠右对齐
        else:
            x, y = self.pill_origin.x(), self.pill_origin.y()
        self.card_rect = QRectF(x, y, w, h)
        p.save()
        shape = QPainterPath()
        shape.addRoundedRect(self.card_rect.adjusted(0.5, 0.5, -0.5, -0.5), h / 2, h / 2)
        soft_shadow(p, shape, spread=3, strength=30, dy=1)
        p.setOpacity(0.75 if stale else 1.0)
        p.setPen(QPen(BORDER, 1))
        p.setBrush(QColor(255, 250, 243, 245))
        p.drawPath(shape)
        fm = QFontMetricsF(self.pill_font)
        base = y + (h + fm.ascent() - fm.descent()) / 2
        cx = x + 10
        for (text, color, bold), width in zip(segs, widths):
            p.setFont(self.pill_bold if bold else self.pill_font)
            p.setPen(LABEL if stale and bold else color)
            p.drawText(QPointF(cx, base), text)
            cx += width
        p.restore()

    def draw_floaters(self, p: QPainter, now: float) -> None:
        f = font(CARD_FONT[self.cfg["size"]] + 1, True)
        fm = QFontMetricsF(f)
        for text, color, t0 in self.floaters:
            e = now - t0
            p.save()
            p.setOpacity(max(0.0, 1 - e / 1.6))
            p.setFont(f)
            p.setPen(color)
            p.drawText(QPointF(self.card_rect.right() - fm.horizontalAdvance(text), self.card_rect.top() - 4 - 16 * e / 1.6), text)
            p.restore()


# ---------- 自检与入口 ----------


def main() -> int:
    parser = argparse.ArgumentParser(description="Claude娘：显示 Claude 额度的桌面宠物")
    parser.add_argument("--snapshot", type=Path, help="把几种状态渲染成 PNG 存到这个目录后退出")
    args = parser.parse_args()
    sys.excepthook = log_exception
    if not args.snapshot:
        # 先占位再加载图片：重复启动（哨兵和 bat 同时叫她）时第二只立刻退出。
        lock = QLockFile(os.path.join(tempfile.gettempdir(), "claude-buddy-pet.lock"))
        if not lock.tryLock(100):
            return 0  # 已经有一只在运行
    try:
        QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    except AttributeError:
        pass
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps)
    app = QApplication(sys.argv[:1])
    app.setQuitOnLastWindowClosed(False)
    lines = Lines(LINES_FILE, MET_FILE)
    cfg = load_config()
    try:
        sounds.ensure(SOUND_DIR, cfg["volume"], PRESET_DIR)
    except OSError:
        log_current()  # 声音文件写不进去只是没有声音
    try:
        art = Art(Path(cfg["art_dir"]) if cfg["art_dir"] else ART_DIR, report=log_current)
    except Exception as err:  # 图片缺失或损坏：弹窗说明后退出
        log_current()
        QMessageBox.critical(None, "Claude娘", f"读取角色图失败：{err}")
        return 1
    if args.snapshot:
        snapshot(args.snapshot, lambda **changes: Pet(dict(cfg, **changes), art, lines, still=True), art)
        return 0
    if plugin_status() in ("missing", "outdated"):
        # 插件是随桌宠一起发的：没装或者是旧版就顺手装上。装不上不影响桌宠，菜单里还能手动装。
        result = install_plugin()
        if not result.startswith("插件"):
            log_exception(RuntimeError, RuntimeError(result), None)
    pet = Pet(cfg, art, lines)
    pet.show()
    return app.exec_()


if __name__ == "__main__":
    sys.exit(main())
