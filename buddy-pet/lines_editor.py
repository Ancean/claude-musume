"""台词管理窗口：按分类查看和修改她的台词，插入实时数据字段，试说一句，保存后立刻生效。

由 pet.pyw 右键菜单里的“管理台词”打开，和桌宠在同一个进程里。台词存在 lines.json，
窗口里一行一句。内置的分类和各自什么时候说，由 pet.pyw 的 LINE_KINDS 决定；
另外可以新建自己的分类，存在 lines.json 的 custom 下，闲聊时和其他台词一起随机抽。
"""

from __future__ import annotations

import json
import os
import random
import shutil
from datetime import datetime
from pathlib import Path
from string import Formatter
from typing import Callable

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QColor, QIcon, QKeySequence, QTextCursor, QTextDocument, QTextFormat
from PyQt5.QtWidgets import (
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPlainTextDocumentLayout,
    QPlainTextEdit,
    QPushButton,
    QShortcut,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

import line_tags

# 台词里可以用的字段：（字段名, 菜单里的名称, 说明）。顺序就是“插入字段”菜单里的顺序。
FIELDS = (
    ("five", "5 小时额度已用", "已用的百分比，比如 62"),
    ("five_left", "5 小时额度剩余", "剩下的百分比，比如 38"),
    ("five_reset", "5 小时额度重置", "比如“14:22 重置”；没装插件时是推算的，会写成“约 14:22 重置”"),
    ("five_rate", "每小时消耗", "最近一小时平均每小时用掉的百分比，比如 21；几乎没在用时没有这个数据"),
    ("five_empty", "预计用完的时刻", "照最近的速度 5 小时额度用完的时刻，比如 15:40；重置前用不完时没有这个数据"),
    ("seven", "7 天额度已用", "已用的百分比，比如 41"),
    ("seven_left", "7 天额度剩余", "剩下的百分比，比如 59"),
    ("seven_reset", "7 天额度重置", "比如“周二 01:04 重置”；没装插件时同样带“约”"),
    ("ctx", "上下文 token", "最近活动的 Claude Code 会话的上下文长度，比如 48.2k；要装插件才有"),
    ("ctx_pct", "上下文占比", "上下文占窗口的百分比，比如 24；要装插件才有"),
    ("cost", "本会话花费", "按 API 价格折算的花费，比如 $1.37；要装插件才有"),
    ("task_time", "任务用时", "这次任务用了多久，比如“3 分 20 秒”；只有“任务完成”这一类有"),
)
# 检查写法时代入的样例，形状与 usage_data.fields() 的结果一致。
SAMPLE = {
    "five": 62,
    "five_left": 38,
    "five_reset": "约 14:22 重置",
    "five_rate": 21,
    "five_empty": "15:40",
    "seven": 41,
    "seven_left": 59,
    "seven_reset": "约周二 01:04 重置",
    "ctx": "48.2k",
    "ctx_pct": 24,
    "cost": "$1.37",
    "task_time": "3 分 20 秒",
}
# 只在某一类台词里才有数据的字段。
ONLY_IN = {"task_time": "done"}
# 自定义分类在这个窗口里的键是这个前缀加分类名；存进 lines.json 时放在 custom 下。
CUSTOM = "custom:"
CUSTOM_WHEN = "自己新建的分类：闲聊时和其他几类台词一起随机抽，各类的权重见“闲聊区”的说明。"
NAME_MAX = 16
TEXT = QColor("#3a2f28")
BAD_TEXT = QColor("#d13438")
BAD_LINE = QColor("#fde2dd")

STYLE = """
QWidget { background: #fffaf3; color: #3a2f28; font-family: "Microsoft YaHei UI"; font-size: 13px; }
QListWidget { background: #fffdf9; border: 1px solid #e6d9ca; border-radius: 8px; padding: 4px; outline: 0; }
QListWidget::item { padding: 4px 8px; border-radius: 6px; }
QListWidget::item:hover { background: #fbefe2; }
QListWidget::item:selected { background: #f6e3cf; }
QListWidget::item:disabled { background: transparent; color: #b3a596; padding-top: 10px; }
QScrollBar:vertical { background: transparent; width: 8px; margin: 2px; }
QScrollBar::handle:vertical { background: #e6d9ca; border-radius: 2px; min-height: 24px; }
QScrollBar::handle:vertical:hover { background: #d8c6b2; }
QScrollBar:horizontal { background: transparent; height: 8px; margin: 2px; }
QScrollBar::handle:horizontal { background: #e6d9ca; border-radius: 2px; min-width: 24px; }
QScrollBar::handle:horizontal:hover { background: #d8c6b2; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: none; }
QPlainTextEdit { background: #ffffff; border: 1px solid #e6d9ca; border-radius: 8px; padding: 6px;
                 font-size: 14px; selection-background-color: #f3c9a3; selection-color: #3a2f28; }
QLineEdit { background: #ffffff; border: 1px solid #e6d9ca; border-radius: 6px; padding: 4px 6px;
            selection-background-color: #f3c9a3; selection-color: #3a2f28; }
QLabel#title { font-size: 17px; font-weight: bold; }
QLabel#note { color: #9a8d80; }
QLabel#status { color: #6b5d50; }
QPushButton { background: #ffffff; border: 1px solid #e6d9ca; border-radius: 6px; padding: 5px 14px; }
QPushButton:hover { background: #fbefe2; }
QPushButton:pressed { background: #f6e3cf; }
QPushButton:disabled { color: #c3b5a7; background: #fffaf3; }
QPushButton#small { padding: 4px 8px; }
QPushButton#field { color: #b45f2a; }
QPushButton::menu-indicator { image: none; width: 0; }
QPushButton#primary { background: #e0773a; color: #ffffff; border: none; }
QPushButton#primary:hover { background: #d16a2f; }
QPushButton#primary:disabled { background: #efc9ae; }
QMenu { background: #fffdf9; border: 1px solid #e6d9ca; padding: 4px; }
QMenu::item { padding: 5px 22px 5px 12px; border-radius: 4px; background: transparent; }
QMenu::item:selected { background: #f6e3cf; }
"""


def field_names(line: str) -> set[str]:
    return {name.split(".")[0].split("[")[0] for _, name, _, _ in Formatter().parse(line) if name}


def problem(line: str, kind: str | None = None) -> str | None:
    """这句台词的写法有没有问题；有问题时返回原因，这样的句子不会被说出来。kind 是它所在的分类。"""
    tags, line = line_tags.split(line)
    why = line_tags.problem(tags, kind)
    if why:
        return why
    if not line.strip():
        return "只有条件，没有台词"
    try:
        line.format(**SAMPLE)
    except KeyError as err:
        return f"{{{err.args[0]}}} 不是可用的字段"
    except Exception:
        return "花括号写法不对：字段写成 {five} 这样；想显示花括号本身请写 {{ 或 }}"
    for name in field_names(line):
        if name in ONLY_IN and kind != ONLY_IN[name]:
            return f"{{{name}}} 只在“任务完成”这一类里有数据"
    return None


def to_text(lines: list[str]) -> str:
    """台词列表变成编辑框里的文字：一行一句，句中的换行写成 \\n。"""
    return "\n".join(s.replace("\n", "\\n") for s in lines)


def from_text(text: str) -> list[str]:
    return [s.strip().replace("\\n", "\n") for s in text.split("\n") if s.strip()]


def clean(value) -> list[str]:
    return [s for s in value if isinstance(s, str)] if isinstance(value, list) else []


def ask(parent: QWidget, title: str, text: str, choices: tuple[str, ...], icon=QMessageBox.Question) -> int:
    """按钮是中文的确认框，返回所点按钮的序号；直接关掉对话框算点了最后一个。"""
    box = QMessageBox(icon, title, text, QMessageBox.NoButton, parent)
    roles = [QMessageBox.AcceptRole] + [QMessageBox.DestructiveRole] * (len(choices) - 2) + [QMessageBox.RejectRole]
    buttons = [box.addButton(choice, role) for choice, role in zip(choices, roles)]
    box.setDefaultButton(buttons[0])
    box.setEscapeButton(buttons[-1])
    box.exec_()
    clicked = box.clickedButton()
    return buttons.index(clicked) if clicked in buttons else len(buttons) - 1


def ask_text(parent: QWidget, title: str, label: str, text: str = "") -> str | None:
    """按钮是中文的单行输入框；点取消或关掉时返回 None。"""
    dialog = QInputDialog(parent)
    dialog.setWindowFlag(Qt.WindowContextHelpButtonHint, False)
    dialog.setWindowTitle(title)
    dialog.setLabelText(label)
    dialog.setTextValue(text)
    dialog.setOkButtonText("确定")
    dialog.setCancelButtonText("取消")
    return dialog.textValue() if dialog.exec_() == QInputDialog.Accepted else None


class LinesEditor(QWidget):
    def __init__(
        self,
        path: Path,
        defaults: dict,
        kinds: tuple[tuple[str, str, str], ...],
        icon: QIcon,
        on_saved: Callable[[], None],
        on_try: Callable[[str], str | None],
    ) -> None:
        super().__init__(None, Qt.Window)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setWindowIcon(icon)
        self.setStyleSheet(STYLE)
        self.path, self.defaults, self.kinds = path, defaults, kinds
        self.builtin = [key for key, _, _ in kinds]
        self.whens = {key: when for key, _, when in kinds}
        self.on_saved, self.on_try = on_saved, on_try
        self.broken = False
        self.extra, self.saved = self.read()
        self.work = {key: list(lines) for key, lines in self.saved.items()}
        # 分类的显示名称：内置的来自 kinds，自定义的就是分类名。
        self.names = {key: name for key, name, _ in kinds}
        self.names.update({key: key[len(CUSTOM) :] for key in self.work if key.startswith(CUSTOM)})
        self.current = kinds[0][0]
        self.loading = False
        # 每个分类一个文档，切换分类时各自的撤销记录和光标位置都还在。
        self.docs: dict[str, QTextDocument] = {}
        self.build()
        for key in self.work:
            self.mark(key)
        self.categories.setCurrentRow(0)
        self.refresh_title()
        self.resize(900, 680)
        if self.broken:
            self.say_note("lines.json 的格式有误，读不出来，这里显示的是默认台词；保存时会先把原文件另存一份。", 0)

    # --- 读写 ---

    def read(self) -> tuple[dict, dict[str, list[str]]]:
        """读 lines.json：返回（不归这个窗口管的其他键, 各分类的台词）。文件里没有的内置分类用默认台词。"""
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            data = {}
        except (OSError, ValueError):
            data = None
        if not isinstance(data, dict):
            self.broken, data = True, {}
        extra = {k: v for k, v in data.items() if k not in self.builtin and k != "custom" and not k.startswith("_")}
        lines = {key: clean(data.get(key, self.defaults.get(key, []))) for key in self.builtin}
        custom = data.get("custom")
        if isinstance(custom, dict):
            for name, value in custom.items():
                if isinstance(name, str) and name.strip():
                    lines[CUSTOM + name] = clean(value)
        return extra, lines

    def save(self) -> bool:
        data = {"_说明": self.defaults.get("_说明", "")}
        data.update({key: self.work[key] for key in self.builtin})
        custom = {key[len(CUSTOM) :]: lines for key, lines in self.work.items() if key.startswith(CUSTOM)}
        if custom:
            data["custom"] = custom
        data.update(self.extra)
        try:
            if self.broken and self.path.exists():
                shutil.copy2(self.path, self.path.with_name(f"lines.broken-{datetime.now():%Y%m%d-%H%M%S}.json"))
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError as err:
            ask(self, "保存失败", f"写入 {self.path.name} 失败：{err}", ("知道了",), QMessageBox.Warning)
            return False
        self.broken = False
        self.saved = {key: list(lines) for key, lines in self.work.items()}
        self.on_saved()
        self.refresh_title()
        self.say_note("已保存，她马上就会用新台词。")
        return True

    def dirty(self) -> bool:
        return self.work != self.saved

    # --- 界面 ---

    def build(self) -> None:
        self.categories = QListWidget()
        self.items: dict[str, QListWidgetItem] = {}
        for key in self.builtin:
            self.add_item(key)
        header = QListWidgetItem("自定义分类")
        header.setFlags(Qt.NoItemFlags)
        header.setToolTip("点下面的“新建分类”添加自己的分类，闲聊时会一起抽到")
        self.categories.addItem(header)
        for key in self.work:
            if key.startswith(CUSTOM):
                self.add_item(key)
        self.categories.currentItemChanged.connect(self.switch)
        new_button = QPushButton("＋ 新建分类", objectName="small")
        new_button.clicked.connect(self.new_category)
        self.rename_button = QPushButton("重命名", objectName="small")
        self.rename_button.clicked.connect(self.rename_category)
        self.delete_button = QPushButton("删除", objectName="small")
        self.delete_button.clicked.connect(self.delete_category)
        manage = QHBoxLayout()
        manage.setSpacing(6)
        manage.addWidget(new_button, 1)
        manage.addWidget(self.rename_button)
        manage.addWidget(self.delete_button)
        left_box = QWidget()
        left_box.setFixedWidth(214)
        left = QVBoxLayout(left_box)
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(8)
        left.addWidget(self.categories, 1)
        left.addLayout(manage)

        self.title = QLabel(objectName="title")
        self.edit = QPlainTextEdit()
        self.edit.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.edit.setPlaceholderText("这一类还没有台词。一行写一句。")
        self.edit.textChanged.connect(self.edited)

        fields_button = QPushButton("插入字段 ▾", objectName="field")
        fields_button.setToolTip("把实时数据插到光标处，比如 {five} 会换成 5 小时额度的已用百分比")
        fields_menu = QMenu(fields_button)
        fields_menu.setToolTipsVisible(True)
        for name, label, tip in FIELDS:
            act = fields_menu.addAction(f"{label}　{{{name}}}")
            act.setToolTip(tip)
            act.triggered.connect(lambda _=False, n=name: self.insert(n))
        fields_button.setMenu(fields_menu)
        self.tag_button = QPushButton("条件 ▾", objectName="field")
        self.tag_button.setToolTip("给光标所在的那一句加条件（写在句子开头），比如只在见面时说；只有“闲聊区”能用")
        tag_menu = QMenu(self.tag_button)
        tag_menu.setToolTipsVisible(True)
        act = tag_menu.addAction("随机（去掉条件）")
        act.triggered.connect(lambda: self.set_condition(None))
        tag_menu.addSeparator()
        for name, tip in line_tags.CONDITIONS:
            act = tag_menu.addAction(f"【{name}】")
            act.setToolTip(tip)
            act.triggered.connect(lambda _=False, n=name: self.set_condition(n))
        self.tag_button.setMenu(tag_menu)
        add_button = QPushButton("＋ 新增一句")
        add_button.setToolTip("光标跳到最后另起一行")
        add_button.clicked.connect(self.add_line)
        try_button = QPushButton("试说这一句")
        try_button.setToolTip("让她把光标所在的那一句说出来；光标在空行时随机挑一句")
        try_button.clicked.connect(self.try_line)
        self.restore_button = QPushButton("恢复这一类的默认台词")
        self.restore_button.clicked.connect(self.restore)
        actions = QHBoxLayout()
        actions.addWidget(fields_button)
        actions.addWidget(self.tag_button)
        actions.addWidget(add_button)
        actions.addWidget(try_button)
        actions.addWidget(self.restore_button)
        actions.addStretch(1)
        self.status = QLabel(objectName="status", wordWrap=True)

        right = QVBoxLayout()
        right.setSpacing(8)
        right.addWidget(self.title)
        right.addWidget(self.edit, 1)
        right.addLayout(actions)
        right.addWidget(self.status)
        body = QHBoxLayout()
        body.setSpacing(14)
        body.addWidget(left_box)
        body.addLayout(right, 1)

        self.note = QLabel(objectName="note", wordWrap=True)
        self.note_timer = QTimer(self)
        self.note_timer.setSingleShot(True)
        self.note_timer.timeout.connect(self.note.clear)
        self.save_button = QPushButton("保存", objectName="primary")
        self.save_button.setToolTip("Ctrl+S")
        self.save_button.clicked.connect(self.save)
        close_button = QPushButton("关闭")
        close_button.clicked.connect(self.close)
        bottom = QHBoxLayout()
        bottom.addWidget(self.note, 1)
        bottom.addWidget(self.save_button)
        bottom.addWidget(close_button)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 14)
        root.setSpacing(12)
        root.addLayout(body, 1)
        root.addLayout(bottom)
        QShortcut(QKeySequence.Save, self, activated=self.save)
        QShortcut(QKeySequence(Qt.Key_Escape), self, activated=self.close)

    def add_item(self, key: str) -> QListWidgetItem:
        item = QListWidgetItem(self.names[key])
        item.setData(Qt.UserRole, key)
        self.categories.addItem(item)
        self.items[key] = item
        return item

    def switch(self, item: QListWidgetItem | None, _previous=None) -> None:
        if item is None:
            return
        self.current = item.data(Qt.UserRole)
        custom = self.current.startswith(CUSTOM)
        self.title.setText(self.names[self.current])
        self.rename_button.setEnabled(custom)
        self.delete_button.setEnabled(custom)
        self.restore_button.setEnabled(not custom)
        self.tag_button.setVisible(self.current in line_tags.TAGGED_KINDS)
        doc = self.docs.get(self.current)
        if doc is None:
            doc = QTextDocument(self)
            doc.setDocumentLayout(QPlainTextDocumentLayout(doc))
            doc.setDefaultFont(self.edit.font())
            doc.setPlainText(to_text(self.work[self.current]))
            self.docs[self.current] = doc
        self.loading = True
        self.edit.setDocument(doc)
        self.loading = False
        self.check()

    def edited(self) -> None:
        if self.loading:
            return
        self.work[self.current] = from_text(self.edit.toPlainText())
        self.check()
        self.refresh_title()

    def check(self) -> None:
        """标出写法有问题的行，更新下方说明和左侧的句数。"""
        marks, notes = [], []
        block = self.edit.document().firstBlock()
        while block.isValid():
            text = block.text().strip()
            why = problem(text.replace("\\n", "\n"), self.current) if text else None
            if why:
                mark = QTextEdit.ExtraSelection()
                mark.format.setBackground(BAD_LINE)
                mark.format.setProperty(QTextFormat.FullWidthSelection, True)
                mark.cursor = QTextCursor(block)
                marks.append(mark)
                notes.append(f"第 {block.blockNumber() + 1} 行：{why}")
            block = block.next()
        self.edit.setExtraSelections(marks)
        # 平常不显示说明；只在这一类空了或者有写法问题时提醒。
        parts = [] if self.work[self.current] else ["这一类现在没有台词，到时候她不会说话。"]
        if notes:
            more = "\n……" if len(notes) > 4 else ""
            parts.append("下面这些行写法有问题，到时候会被跳过：\n" + "\n".join(notes[:4]) + more)
        self.status.setText("\n".join(parts))
        self.status.setVisible(bool(parts))
        self.mark(self.current)

    def mark(self, key: str) -> None:
        lines = self.work[key]
        bad = sum(1 for s in lines if problem(s, key))
        item = self.items[key]
        item.setText(f"{self.names[key]}（{len(lines)}）")
        item.setForeground(BAD_TEXT if bad else TEXT)
        item.setToolTip(f"有 {bad} 句写法有问题" if bad else self.whens.get(key, CUSTOM_WHEN))

    def refresh_title(self) -> None:
        changed = self.dirty()
        self.setWindowTitle("台词管理 - Claude娘" + ("（有未保存的修改）" if changed else ""))
        self.save_button.setEnabled(changed or self.broken)

    def say_note(self, text: str, seconds: float = 6) -> None:
        self.note.setText(text)
        if seconds:
            self.note_timer.start(int(seconds * 1000))
        else:
            self.note_timer.stop()

    # --- 分类 ---

    def name_problem(self, name: str) -> str | None:
        if not name:
            return "分类名不能是空的。"
        if len(name) > NAME_MAX:
            return f"分类名最多 {NAME_MAX} 个字。"
        if name in self.names.values():
            return f"已经有叫“{name}”的分类了，换一个名字吧。"
        return None

    def ask_name(self, title: str, label: str, name: str = "") -> str | None:
        """问一个分类名，不合适就说明原因再问；取消时返回 None。"""
        while True:
            text = ask_text(self, title, label, name)
            if text is None:
                return None
            name = text.strip()
            why = self.name_problem(name)
            if why is None:
                return name
            ask(self, title, why, ("知道了",), QMessageBox.Warning)

    def new_category(self) -> None:
        name = self.ask_name("新建分类", "给新分类起个名字，比如“口头禅”“彩蛋”：")
        if name is None:
            return
        key = CUSTOM + name
        self.work[key] = []
        self.names[key] = name
        item = self.add_item(key)
        self.mark(key)
        self.categories.setCurrentItem(item)
        self.refresh_title()
        self.say_note(f"新建了“{name}”，写好台词后记得保存。")
        self.edit.setFocus()

    def rename_category(self) -> None:
        old = self.current
        if not old.startswith(CUSTOM):
            return
        before = self.names.pop(old)
        name = self.ask_name("重命名分类", "新的名字：", before)
        if name is None or name == before:
            self.names[old] = before
            return
        new = CUSTOM + name
        # 保持原来的先后顺序。
        self.work = {(new if k == old else k): v for k, v in self.work.items()}
        self.names[new] = name
        if old in self.docs:
            self.docs[new] = self.docs.pop(old)
        item = self.items.pop(old)
        item.setData(Qt.UserRole, new)
        self.items[new] = item
        self.current = new
        self.title.setText(name)
        self.mark(new)
        self.refresh_title()

    def delete_category(self) -> None:
        key = self.current
        if not key.startswith(CUSTOM):
            return
        count = len(self.work[key])
        what = f"“{self.names[key]}”这个分类" + (f"和里面的 {count} 句台词" if count else "")
        text = f"删掉{what}吗？\n保存之后才会从文件里删掉；保存前关掉窗口、选“不保存”就能找回。"
        if ask(self, "删除分类", text, ("删除", "取消"), QMessageBox.Warning) != 0:
            return
        row = self.categories.row(self.items[key])
        # 先选中旁边的分类，编辑框换成别的文档后再删。
        for r in (row + 1, row - 1, row - 2):
            neighbor = self.categories.item(r)
            if neighbor is not None and neighbor.flags() & Qt.ItemIsSelectable:
                self.categories.setCurrentRow(r)
                break
        self.categories.takeItem(row)
        del self.items[key], self.work[key], self.names[key]
        doc = self.docs.pop(key, None)
        if doc is not None:
            doc.deleteLater()
        self.refresh_title()

    # --- 台词 ---

    def insert(self, name: str) -> None:
        self.edit.insertPlainText("{" + name + "}")
        self.edit.setFocus()

    def set_condition(self, tag: str | None) -> None:
        """改光标所在那一句的条件：见面类和时段各留一个，选“随机”就全去掉。改动可以 Ctrl+Z 撤销。"""
        block = self.edit.textCursor().block()
        tags, text = line_tags.split(block.text())
        if not text.strip():
            self.say_note("先把光标放到一句台词上。")
            return
        if tag is None:
            tags = []
        else:
            group = (line_tags.MEET, line_tags.FIRST) if tag in (line_tags.MEET, line_tags.FIRST) else tuple(line_tags.PERIODS)
            tags = [t for t in tags if t not in group] + [tag]
        known = [line_tags.MEET, line_tags.FIRST, *line_tags.PERIODS]
        cursor = QTextCursor(block)
        cursor.movePosition(QTextCursor.EndOfBlock, QTextCursor.KeepAnchor)
        cursor.insertText(line_tags.join([t for t in tags if t in known], text))
        self.edit.setFocus()

    def add_line(self) -> None:
        """光标跳到最后；最后一行有字时另起一行。"""
        cursor = self.edit.textCursor()
        cursor.movePosition(QTextCursor.End)
        if cursor.block().text().strip():
            cursor.insertBlock()
        self.edit.setTextCursor(cursor)
        self.edit.setFocus()

    def try_line(self) -> None:
        text = self.edit.textCursor().block().text().strip().replace("\\n", "\n")
        if not text:
            usable = [s for s in self.work[self.current] if not problem(s, self.current)]
            if not usable:
                self.say_note("这一类还没有能说的台词。")
                return
            text = random.choice(usable)
        elif problem(text, self.current):
            self.say_note("这一句写法有问题，改好再试。")
            return
        self.say_note(self.on_try(text) or "她说出来了，看看气泡。")

    def restore(self) -> None:
        if self.current not in self.defaults:
            return
        name = self.names[self.current]
        text = f"把“{name}”换回默认台词吗？这一类现在的内容会被替换，换完后按 Ctrl+Z 可以撤销。"
        if ask(self, "恢复默认", text, ("恢复默认", "取消")) != 0:
            return
        # 用光标整体替换而不是 setPlainText，并包成一步，这样按一次 Ctrl+Z 就能整个撤销。
        cursor = self.edit.textCursor()
        cursor.beginEditBlock()
        cursor.select(QTextCursor.Document)
        cursor.insertText(to_text(self.defaults.get(self.current, [])))
        cursor.endEditBlock()

    def closeEvent(self, e) -> None:
        if self.dirty():
            choice = ask(self, "台词还没保存", "要保存这些修改吗？", ("保存", "不保存", "取消"))
            if choice == 2 or (choice == 0 and not self.save()):
                e.ignore()
                return
        e.accept()
