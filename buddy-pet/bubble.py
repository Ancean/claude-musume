"""对话气泡的画法。

说话时的对话框是一个单独的置顶小窗，尾巴指向她的头顶：出现时轻轻弹出、逐字显示，点一下先显示全文，
再点一下收起。桌宠脚下的额度卡片和这里共用配色、进度条和投影的画法。从 pet.pyw 拆出来，免得主程序太长。
"""

from __future__ import annotations

import math
import time

from PyQt5.QtCore import QEasingCurve, QPoint, QPointF, QPropertyAnimation, QRectF, Qt, QTimer, QVariantAnimation
from PyQt5.QtGui import (
    QColor,
    QFont,
    QFontMetricsF,
    QGuiApplication,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QTextLayout,
    QTextOption,
    QTransform,
)
from PyQt5.QtWidgets import QWidget

FONT_FAMILY = "Microsoft YaHei UI"
# 置顶、不进任务栏、不抢键盘焦点：点她不会打断正在输入的窗口。
WINDOW_FLAGS = (
    Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint | Qt.NoDropShadowWindowHint | Qt.WindowDoesNotAcceptFocus
)

TEXT = QColor("#3a2f28")
LABEL = QColor("#9a8d80")
FAINT = QColor("#c3b5a7")
SEP = QColor("#cdbfb0")
BG = QColor("#fffaf3")
BORDER = QColor("#ecd6bf")
PAPER_TOP = QColor("#fffefb")
PAPER_BOTTOM = QColor("#fff3e3")
TRACK = QColor("#f2e8dd")
# 数值文字和进度条的颜色：平常、70% 起、90% 起。
TONES = {"ok": QColor("#3a2f28"), "warn": QColor("#d97b1f"), "danger": QColor("#e5484d")}
BARS = {"ok": QColor("#e9a25f"), "warn": QColor("#e07b2a"), "danger": QColor("#e5484d")}
PETAL = QColor("#f59a4c")
PISTIL = QColor("#ffd36a")


def font(px: float, bold: bool = False) -> QFont:
    f = QFont(FONT_FAMILY)
    f.setPixelSize(max(1, round(px)))
    f.setBold(bold)
    return f


def wrap(text: str, f: QFont, width: float) -> list[str]:
    """按宽度折行：中文逐字可断，英文尽量在词间断；原文里的换行保留。"""
    out: list[str] = []
    option = QTextOption()
    option.setWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
    for para in text.split("\n"):
        layout = QTextLayout(para, f)
        layout.setTextOption(option)
        layout.beginLayout()
        count = 0
        while True:
            line = layout.createLine()
            if not line.isValid():
                break
            line.setLineWidth(width)
            out.append(para[line.textStart() : line.textStart() + line.textLength()].rstrip())
            count += 1
        layout.endLayout()
        if count == 0:
            out.append("")
    return out


def soft_shadow(p: QPainter, path: QPainterPath, spread: int = 6, strength: int = 44, dy: float = 1.5) -> None:
    """柔和的投影：同一个轮廓描几圈越来越粗、越来越淡的边，叠起来靠近边缘深、往外渐淡。"""
    p.save()
    p.setBrush(Qt.NoBrush)
    shifted = path.translated(0, dy)
    for i in range(spread, 0, -1):
        alpha = max(1, round(strength * (1 - i / (spread + 1)) / spread))
        p.setPen(QPen(QColor(120, 78, 40, alpha), i * 2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.drawPath(shifted)
    p.restore()


def flower(p: QPainter, center: QPointF, r: float) -> None:
    """五瓣小花：橙色花瓣、白边、黄色花心，和她发饰上的花呼应。"""
    petal = QPainterPath()
    petal.addEllipse(QPointF(0, -r * 0.52), r * 0.42, r * 0.52)
    shape = QPainterPath()
    for k in range(5):
        shape = shape.united(QTransform().rotate(72 * k).map(petal))
    p.save()
    p.translate(center)
    p.setPen(QPen(Qt.white, max(1.2, r * 0.32), Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    p.setBrush(Qt.NoBrush)
    p.drawPath(shape)
    p.setPen(Qt.NoPen)
    p.setBrush(PETAL)
    p.drawPath(shape)
    p.setBrush(PISTIL)
    p.drawEllipse(QPointF(0, 0), r * 0.26, r * 0.26)
    p.restore()


def draw_bar(p: QPainter, rect: QRectF, frac: float, ghost: float | None, color: QColor) -> None:
    """圆头进度条：已用的部分实色；ghost 是照最近的速度到重置时会用到的位置，画成同色的浅影。"""
    r = rect.height() / 2
    frac = max(0.0, min(1.0, frac))
    p.save()
    p.setPen(Qt.NoPen)
    p.setBrush(TRACK)
    p.drawRoundedRect(rect, r, r)
    if ghost is not None and ghost > frac:
        shade = QColor(color)
        shade.setAlpha(72)
        p.setBrush(shade)
        p.drawRoundedRect(QRectF(rect.left(), rect.top(), max(rect.height(), rect.width() * min(1.0, ghost)), rect.height()), r, r)
    if frac > 0:
        fill = QLinearGradient(rect.topLeft(), rect.topRight())
        fill.setColorAt(0, color.lighter(118))
        fill.setColorAt(1, color)
        p.setBrush(fill)
        p.drawRoundedRect(QRectF(rect.left(), rect.top(), max(rect.height(), rect.width() * frac), rect.height()), r, r)
    p.restore()


class Bubble(QWidget):
    M = 8  # 四周留给投影、小花和弹出动画的边
    PAD_X, PAD_Y, TAIL, RADIUS = 14, 10, 10, 12
    MAX_W = 248  # 正文最宽
    LINE = 1.6  # 行距是字号的多少倍
    TYPE_SPEED = 0.03  # 逐字显示，每个字的秒数
    TYPE_MAX = 1.1  # 再长的话也在这么多秒内显示完

    def __init__(self) -> None:
        super().__init__(None, WINDOW_FLAGS)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.font_ = font(13)
        self.text = ""
        self.lines: list[str] = []
        self.line_h = 0.0
        self.body = QRectF()
        self.tail_x = 0.0
        # 逐字显示：一共多少字、已经显示了多少、从什么时候开始、要用多久。
        self.total = self.shown = 0
        self.type_at = self.type_for = 0.0
        self.read_for = 3.0
        self.type_timer = QTimer(self)
        self.type_timer.timeout.connect(self.type_step)
        self.hide_timer = QTimer(self)
        self.hide_timer.setSingleShot(True)
        self.hide_timer.timeout.connect(self.fade_out)
        self.fade = QPropertyAnimation(self, b"windowOpacity", self)
        self.fade.finished.connect(self.after_fade)
        self.fading_out = False
        self.pop = 1.0
        self.pop_anim = QVariantAnimation(self)
        self.pop_anim.setStartValue(0.0)
        self.pop_anim.setEndValue(1.0)
        self.pop_anim.setDuration(300)
        self.pop_anim.setEasingCurve(QEasingCurve.OutBack)
        self.pop_anim.valueChanged.connect(self.set_pop)

    # --- 排版 ---

    def resize_body(self, w: float, h: float) -> None:
        m = self.M
        self.body = QRectF(m, m, w, h)
        self.setFixedSize(math.ceil(w) + 2 * m, math.ceil(h) + self.TAIL + 2 * m)
        self.tail_x = self.width() / 2
        self.update()

    def set_text(self, text: str) -> None:
        """排好一段话，整段立刻显示；逐字显示由 say 开始。"""
        self.text = text
        fm = QFontMetricsF(self.font_)
        self.line_h = round(self.font_.pixelSize() * self.LINE)
        self.lines = wrap(text, self.font_, self.MAX_W)
        width = max((fm.horizontalAdvance(s) for s in self.lines), default=0)
        height = self.line_h * (len(self.lines) - 1) + fm.height()
        self.total = self.shown = sum(len(s) for s in self.lines)
        self.resize_body(math.ceil(width) + 2 * self.PAD_X, math.ceil(height) + 2 * self.PAD_Y)

    # --- 显示与收起 ---

    def say(self, text: str, anchor: QPoint, seconds: float | None = None) -> None:
        self.set_text(text)
        self.shown = 0
        self.type_for = min(self.TYPE_MAX, self.TYPE_SPEED * self.total)
        self.type_at = time.monotonic()
        self.type_timer.start(16)
        self.read_for = seconds or min(9.0, 3.0 + 0.13 * len(text))
        self.present(anchor, self.type_for + self.read_for)

    def present(self, anchor: QPoint, seconds: float) -> None:
        self.place(anchor)
        self.fade.stop()
        self.fading_out = False
        if self.isVisible() and self.windowOpacity() > 0.5:
            self.setWindowOpacity(1.0)
        else:
            self.setWindowOpacity(0.0)
            self.show()
            self.fade.setDuration(180)
            self.fade.setStartValue(0.0)
            self.fade.setEndValue(1.0)
            self.fade.start()
            self.pop_anim.stop()
            self.pop = 0.0
            self.pop_anim.start()
        self.raise_()
        self.hide_timer.start(int(1000 * seconds))

    def type_step(self) -> None:
        elapsed = time.monotonic() - self.type_at
        if self.type_for <= 0 or elapsed >= self.type_for:
            self.shown = self.total
        else:
            self.shown = min(self.total, int(self.total * elapsed / self.type_for) + 1)
        if self.shown >= self.total:
            self.type_timer.stop()
        self.update()

    def set_pop(self, value) -> None:
        self.pop = float(value)
        self.update()

    def place(self, anchor: QPoint) -> None:
        """尾巴尖对准 anchor；靠近屏幕边时整体挪进来，尾巴仍指向她。"""
        screen = QGuiApplication.screenAt(anchor) or QGuiApplication.primaryScreen()
        area = screen.availableGeometry()
        w, h, m = self.width(), self.height(), self.M
        x = min(max(anchor.x() - w // 2, area.left() - m + 4), area.right() - w + m - 4)
        y = max(anchor.y() - (h - m), area.top() - m + 4)
        self.move(x, y)
        self.tail_x = min(max(anchor.x() - x, m + self.RADIUS + 10), w - m - self.RADIUS - 10)
        self.update()

    def fade_out(self) -> None:
        if not self.isVisible():
            return
        self.fading_out = True
        self.fade.stop()
        self.fade.setDuration(320)
        self.fade.setStartValue(self.windowOpacity())
        self.fade.setEndValue(0.0)
        self.fade.start()

    def after_fade(self) -> None:
        if self.fading_out:
            self.fading_out = False
            self.type_timer.stop()
            self.hide()

    def mousePressEvent(self, e) -> None:
        # 还在逐字显示时，点一下先把整句显示出来；再点才收起。
        if self.shown < self.total:
            self.type_timer.stop()
            self.shown = self.total
            self.update()
            self.hide_timer.start(int(1000 * self.read_for))
            return
        self.hide_timer.stop()
        self.fade_out()

    # --- 绘制 ---

    def outline(self) -> QPainterPath:
        b, tx = self.body, self.tail_x
        shape = QPainterPath()
        shape.addRoundedRect(b, self.RADIUS, self.RADIUS)
        tail = QPainterPath(QPointF(tx - 10, b.bottom() - 2))
        tail.cubicTo(QPointF(tx - 5, b.bottom() + 1), QPointF(tx - 2, b.bottom() + 5), QPointF(tx, b.bottom() + self.TAIL))
        tail.cubicTo(QPointF(tx + 3, b.bottom() + 5), QPointF(tx + 6, b.bottom() + 1), QPointF(tx + 10, b.bottom() - 2))
        tail.closeSubpath()
        return shape.united(tail)

    def paintEvent(self, _) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)
        if self.pop < 1.0:
            # 从尾巴尖的位置弹出来：先小一点，再略微放大过头，最后回到原大小。
            s = 0.8 + 0.2 * self.pop
            tip = QPointF(self.tail_x, self.body.bottom() + self.TAIL)
            p.translate(tip)
            p.scale(s, s)
            p.translate(-tip)
        shape = self.outline()
        soft_shadow(p, shape)
        paper = QLinearGradient(self.body.topLeft(), self.body.bottomLeft())
        paper.setColorAt(0, PAPER_TOP)
        paper.setColorAt(1, PAPER_BOTTOM)
        p.setPen(QPen(BORDER, 1.2))
        p.setBrush(paper)
        p.drawPath(shape)
        flower(p, QPointF(self.body.left() + 4, self.body.top() + 4), 6.0)
        self.paint_text(p)

    def paint_text(self, p: QPainter) -> None:
        fm = QFontMetricsF(self.font_)
        p.setFont(self.font_)
        p.setPen(TEXT)
        x, y = self.body.left() + self.PAD_X, self.body.top() + self.PAD_Y + fm.ascent()
        left = self.shown
        for line in self.lines:
            if left <= 0:
                break
            p.drawText(QPointF(x, y), line[:left])
            left -= len(line)
            y += self.line_h
