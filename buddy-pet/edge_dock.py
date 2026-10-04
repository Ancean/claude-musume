"""贴边吸附：把她拖到屏幕边松手，她换成贴边图贴在那条边上；从边上拖开就恢复原样。

贴边图在 buddy-art 里，名字是 edge_left、edge_right、edge_top、edge_bottom（见 art.py）。有图的边才会吸附；
没有右边的图时用左边的镜像。贴边时脚下只显示简洁的额度胶囊，放在图旁边。

这是 Pet 的一部分（混入类），从 pet.pyw 拆出来，免得主程序太长；用到 Pet 的 box、cfg、art、motion 等属性。
"""

from __future__ import annotations

import math
import random

from PyQt5.QtCore import QPoint, QPointF, QRect, QRectF
from PyQt5.QtGui import QGuiApplication, QPainter

# 身体离屏幕左、右、上边这么近（像素）或者已经拖出了边，松手就吸附。
# 下边按整个窗口（含脚下的卡片）算，要碰到或拖出屏幕底边才吸附，免得放在右下角的默认位置也被吸住。
SNAP = 16
# 贴边图显示多大，单位是她平常方框的边长：四条边都指图的高度。贴边时她只是在屏幕边上待着，
# 比平常小一圈（头大约是平常的八成）；上边是单手吊着的全身图，按头算会太高，所以头再小一些。
EDGE_SCALE = {"left": 1.15, "right": 1.15, "top": 1.45, "bottom": 0.72}
# 图和额度胶囊之间的空隙（像素）。
GAP = 6


class EdgeDock:
    """贴边状态：edge 是贴着的边（None 表示没贴），edge_key 是用的那张图，edge_rect 是图在窗口里的位置。"""

    def init_edge(self) -> None:
        self.edge: str | None = None
        self.edge_key: str | None = None
        self.edge_rect = QRectF()
        self.pill_origin = QPointF()

    def screen_area(self, point: QPoint) -> QRect:
        screen = QGuiApplication.screenAt(point) or QGuiApplication.primaryScreen()
        return screen.availableGeometry()

    def edge_size(self) -> tuple[float, float]:
        w, h = self.art.size_of(self.edge_key)
        ih = self.box * EDGE_SCALE[self.edge]
        return ih * w / h, ih

    def layout_edge(self) -> None:
        """贴边时的窗口大小、图和胶囊的位置。贴着的那一侧就是窗口的边，图的平直切边对齐它。"""
        iw, ih = self.edge_size()
        pw, ph = self.compact_metrics(self.card_px())
        side = self.edge
        if side in ("left", "right"):
            w, h = max(iw, pw + 4), ih + GAP + ph + 4
            x = 0 if side == "left" else w - iw
            self.edge_rect = QRectF(x, 0, iw, ih)
            self.pill_origin = QPointF(4 if side == "left" else w - pw - 4, ih + GAP)
        else:
            w, h = iw + GAP + pw + 4, max(ih, ph + 8)
            y = h - ih if side == "bottom" else 0
            self.edge_rect = QRectF(0, y, iw, ih)
            self.pill_origin = QPointF(iw + GAP, h - ph - 4 if side == "bottom" else 4)
        self.setFixedSize(math.ceil(w), math.ceil(h))
        # 其他地方（存位置、歪头方向）用到的脚底，放在图的底边中点。
        self.feet = QPointF(self.edge_rect.center().x(), self.edge_rect.bottom())

    def place_edge(self, along: int, area: QRect) -> None:
        """按贴着的边摆窗口；along 是沿着这条边的位置（左右边是窗口顶的 y，上下边是窗口左的 x）。"""
        w, h = self.width(), self.height()
        if self.edge in ("left", "right"):
            x = area.left() if self.edge == "left" else area.right() + 1 - w
            y = min(max(along, area.top()), area.bottom() + 1 - h)
        else:
            y = area.top() if self.edge == "top" else area.bottom() + 1 - h
            x = min(max(along, area.left()), area.right() + 1 - w)
        self.move(x, y)

    def snap_side(self) -> str | None:
        """松手时该吸到哪条边；身体同时靠近两条边时吸到拖出去更多的那条。"""
        g = self.feet_global()
        area = self.screen_area(g)
        half = round(self.box / 2)
        body = QRect(g.x() - half, g.y() - self.box, self.box, self.box)
        bottom = self.mapToGlobal(QPoint(0, self.height() - 1)).y()
        near = {
            "left": body.left() - area.left(),
            "right": area.right() - body.right(),
            "top": body.top() - area.top(),
            "bottom": area.bottom() - bottom + SNAP,
        }
        sides = [(d, side) for side, d in near.items() if d <= SNAP and side in self.art.edges]
        return min(sides)[1] if sides else None

    def dock(self, side: str, along: int | None = None) -> None:
        """贴到 side 这条边。along 不给时按她现在的位置算。"""
        g = self.feet_global()
        area = self.screen_area(g)
        center = QPointF(g.x(), g.y() - self.box / 2)  # 平常时她身体的中心
        options = self.art.edges[side]
        keys, weights = zip(*options)
        self.edge, self.edge_key = side, random.choices(keys, weights)[0]
        self.art.cache.clear()
        self.layout_edge()
        if along is None:
            # 让贴边图的中心对着她原来身体的中心。
            c = self.edge_rect.center()
            along = round(center.y() - c.y()) if side in ("left", "right") else round(center.x() - c.x())
        self.place_edge(along, area)
        self.motion.land(self.clock())
        self.update()

    def undock(self, cursor: QPoint | None = None) -> None:
        """离开边：恢复平常的样子。cursor 给了就让她的身体中间落在鼠标下面（从边上拖走时）。"""
        if self.edge is None:
            return
        center = self.mapToGlobal(self.edge_rect.center().toPoint())
        self.edge = self.edge_key = None
        self.layout_size()
        target = cursor if cursor is not None else center
        self.move(target - QPoint(round(self.feet.x()), round(self.feet.y() - self.box * 0.5)))
        self.update()

    def edge_along(self) -> int:
        return self.x() if self.edge in ("top", "bottom") else self.y()

    def paint_edge(self, p: QPainter, now: float) -> None:
        """贴边时的画面：贴边图（以贴着的那条边为基准轻轻呼吸，按下时压扁一点）、额度胶囊和飘字。"""
        r, m = self.edge_rect, self.motion
        s = (1 - math.cos(2 * math.pi * self.phase)) / 2
        anchor = {
            "left": QPointF(r.left(), r.center().y()),
            "right": QPointF(r.right(), r.center().y()),
            "top": QPointF(r.center().x(), r.top()),
            "bottom": QPointF(r.center().x(), r.bottom()),
        }[self.edge]
        across = 1 + 0.012 * s
        sx, sy = (across * m.sy.value, 1.0) if self.edge in ("left", "right") else (1.0, across * m.sy.value)
        p.save()
        p.translate(anchor)
        p.scale(sx, sy)
        p.translate(-anchor)
        p.drawPixmap(r.topLeft(), self.art.edge_pixmap(self.edge_key, r.width(), r.height(), self.devicePixelRatioF()))
        p.restore()
        self.draw_compact(p)
        self.draw_floaters(p, now)
