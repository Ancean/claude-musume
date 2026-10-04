"""角色图：读 buddy-art 里的表情图和差分，去白底、统一取景，按显示大小缓存成 QPixmap。

从 pet.pyw 拆出来，免得主程序太长。图片处理用 Pillow；角色图没有透明背景时还会用到 numpy 和 scipy。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Callable

from PIL import Image
from PyQt5.QtGui import QIcon, QImage, QPixmap

# 角色图的文件名：表情名，或者表情名加下划线（或短横线）再加任意说明，比如 happy_浅笑.png。
# 前五种是基本表情，后五种是动作；动作图缺了就用相近的表情代替，见 pet.pyw 的 Pet.face。
ART_NAME = re.compile(r"^(idle|blink|happy|worried|working|drag|sleep|wave|cheer|pout)(?:[_-].+)?$")
# 贴边图：贴在屏幕左、右、上、下边时用，比如 edge_left.png。画布大小随意，按各自的不透明范围裁切，
# 贴边的那一侧要是平直的切边（像从屏幕边探出来）。没有右边的图时用左边的镜像。
EDGE_NAME = re.compile(r"^edge_(left|right|top|bottom)(?:[_-].+)?$")
EDGES = ("left", "right", "top", "bottom")
# 特效的位置，单位是角色正方形边长的百分之一；buddy-art/anchors.json 可以覆盖。
DEFAULT_ANCHORS = {"cheekL": [38, 58], "cheekR": [62, 58], "sweat": [78, 24], "mark": [86, 8], "think": [80, 6], "heart": [50, 4]}


def remove_white(img: Image.Image) -> Image.Image:
    """没有透明通道的图：去掉与边缘相连的白底，保留角色内部的白色。"""
    rgba = img.convert("RGBA")
    alpha = rgba.getchannel("A")
    if sum(alpha.histogram()[:250]) > 0.01 * img.width * img.height:
        return rgba
    import numpy as np
    from scipy import ndimage

    px = np.array(rgba).astype(np.int16)
    rgb = px[..., :3]
    whiteness = rgb.min(axis=2)
    near_white = (whiteness >= 232) & (rgb.max(axis=2) - whiteness <= 18)
    labels, _ = ndimage.label(near_white)
    edge = np.unique(np.concatenate([labels[0], labels[-1], labels[:, 0], labels[:, -1]]))
    background = np.isin(labels, edge[edge != 0])
    ring = ndimage.binary_dilation(background, iterations=2) & ~background
    a = np.full(whiteness.shape, 255, np.int16)
    a[background] = 0
    a[ring] = np.clip((255 - whiteness[ring]) * 255 // 64, 0, 255)
    px[..., 3] = a
    return Image.fromarray(px.astype(np.uint8), "RGBA")


def square(images: dict[str, Image.Image]) -> dict[str, Image.Image]:
    """按所有图共同的不透明范围裁成同一个正方形，左右居中、底边对齐。"""
    boxes = [im.getchannel("A").point(lambda v: 255 if v > 8 else 0).getbbox() for im in images.values()]
    left = min(b[0] for b in boxes)
    top = min(b[1] for b in boxes)
    right = max(b[2] for b in boxes)
    bottom = max(b[3] for b in boxes)
    width, height = right - left, bottom - top
    pad = round(max(width, height) * 0.02)
    side = max(width, height) + 2 * pad
    out = {}
    for name, im in images.items():
        canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
        canvas.paste(im.crop((left, top, right, bottom)), ((side - width) // 2, side - height - pad))
        out[name] = canvas
    return out


def read_weights(folder: Path, report: Callable[[], None] | None = None) -> dict[str, float]:
    """buddy-art/weights.json：每张图的出现权重，没写的按 1 算。文件坏了时调用 report 记一笔。"""
    try:
        data = json.loads((folder / "weights.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        if report:
            report()
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: float(v) for k, v in data.items() if isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0}


class Art:
    STORE = 640  # 内存里保留的边长，够“大”号在 200% 缩放下用

    def __init__(self, folder: Path, report: Callable[[], None] | None = None) -> None:
        files: dict[str, Path] = {}
        # 同名的 png 和 webp 都在时用 png：按扩展名排序，png 后处理，覆盖掉 webp。
        for path in sorted(folder.iterdir(), key=lambda p: p.suffix.lower() == ".png"):
            if path.suffix.lower() in (".png", ".webp") and ART_NAME.match(path.stem):
                files[path.stem] = path
        if "idle" not in files:
            raise FileNotFoundError(f"{folder} 里没有 idle.png")
        weights = read_weights(folder, report)
        # 权重为 0 的图不加载；idle.png 例外，托盘图标和兜底都要用它。
        keep = {key for key in files if weights.get(key, 1) > 0} | {"idle"}
        images = {key: remove_white(Image.open(files[key])) for key in sorted(keep)}
        # 每种表情可选的图和权重，不带后缀的那张排第一。闭眼图不单独挑，跟着睁眼图配对，见 blink_for。
        self.variants: dict[str, list[tuple[str, float]]] = {}
        for key in sorted(images, key=lambda k: (k != ART_NAME.match(k).group(1), k)):
            expr = ART_NAME.match(key).group(1)
            if expr != "blink" and weights.get(key, 1) > 0:
                self.variants.setdefault(expr, []).append((key, weights.get(key, 1)))
        self.variants.setdefault("idle", [("idle", 1.0)])
        # 没有打瞌睡的图时，打瞌睡用闭眼图。
        if "sleep" not in self.variants and "blink" in images:
            self.variants["sleep"] = [("blink", 1.0)]
        # 画布尺寸一致的变体按同一取景对齐；尺寸不一的各自取景，至少脚底和居中对得上。
        if len({im.size for im in images.values()}) == 1:
            framed = square(images)
        else:
            framed = {name: square({name: im})[name] for name, im in images.items()}
        self.images = {
            name: im.convert("RGBa").resize((self.STORE, self.STORE), Image.LANCZOS) for name, im in framed.items()
        }
        self.edges = self.load_edges(folder, weights)
        self.anchors = dict(DEFAULT_ANCHORS)
        try:
            extra = json.loads((folder / "anchors.json").read_text(encoding="utf-8"))
            if isinstance(extra, dict):
                self.anchors.update(extra)
        except (OSError, ValueError):
            pass
        self.cache: dict[tuple[str, int], QPixmap] = {}

    def load_edges(self, folder: Path, weights: dict[str, float]) -> dict[str, list[tuple[str, float]]]:
        """读贴边图，裁到不透明范围、长边缩到 STORE。返回每条边可选的图和权重，图存进 self.images。"""
        edges: dict[str, list[tuple[str, float]]] = {}
        for path in sorted(folder.iterdir()):
            m = EDGE_NAME.match(path.stem)
            if not m or path.suffix.lower() not in (".png", ".webp") or weights.get(path.stem, 1) <= 0:
                continue
            im = remove_white(Image.open(path))
            box = im.getchannel("A").point(lambda v: 255 if v > 8 else 0).getbbox()
            if box is None:
                continue
            im = im.crop(box)
            k = self.STORE / max(im.size)
            self.images[path.stem] = im.convert("RGBa").resize((round(im.width * k), round(im.height * k)), Image.LANCZOS)
            edges.setdefault(m.group(1), []).append((path.stem, weights.get(path.stem, 1)))
        if "right" not in edges and "left" in edges:
            for key, w in edges["left"]:
                self.images[key + "_镜像"] = self.images[key].transpose(Image.FLIP_LEFT_RIGHT)
                edges.setdefault("right", []).append((key + "_镜像", w))
        return edges

    def size_of(self, name: str) -> tuple[int, int]:
        return self.images[name].size

    def edge_pixmap(self, name: str, w: float, h: float, dpr: float) -> QPixmap:
        """贴边图按给定的显示大小缩放。"""
        pw, ph = max(1, round(w * dpr)), max(1, round(h * dpr))
        pm = self.cache.get((name, pw))
        if pm is None:
            pm = self.to_pixmap(self.images[name].resize((pw, ph), Image.LANCZOS))
            pm.setDevicePixelRatio(dpr)
            if len(self.cache) > 24:
                self.cache.clear()
            self.cache[(name, pw)] = pm
        return pm

    @staticmethod
    def to_pixmap(im: Image.Image) -> QPixmap:
        data = im.tobytes()
        q = QImage(data, im.width, im.height, im.width * 4, QImage.Format_RGBA8888_Premultiplied).copy()
        return QPixmap.fromImage(q)

    def pixmap(self, name: str, box: float, dpr: float) -> QPixmap:
        px = max(1, round(box * dpr))
        pm = self.cache.get((name, px))
        if pm is None:
            pm = self.to_pixmap(self.images[name].resize((px, px), Image.LANCZOS))
            pm.setDevicePixelRatio(px / box)
            if len(self.cache) > 24:
                self.cache.clear()
            self.cache[(name, px)] = pm
        return pm

    def blink_for(self, key: str) -> str | None:
        """睁眼图对应的闭眼图：idle 配 blink，idle_xx 配 blink_xx；没有就不眨眼。"""
        if not key.startswith("idle"):
            return None
        blink = "blink" + key[4:]
        return blink if blink in self.images else None

    def icon(self) -> QIcon:
        s = self.STORE
        head = self.images["idle"].crop((int(s * 0.18), 0, int(s * 0.82), int(s * 0.64))).resize((64, 64), Image.LANCZOS)
        return QIcon(self.to_pixmap(head))
