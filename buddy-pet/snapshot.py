"""自检：把桌宠的几种状态各渲染一帧存成 PNG，再拼一张总览 sheet.png，用来检查画面，不显示窗口。

由 python pet.pyw --snapshot 目录 调用。
"""

from __future__ import annotations

import math
import time
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from PIL import Image

from art import Art
from bubble import TONES, Bubble
from usage_data import Usage

if TYPE_CHECKING:
    from pet import Pet  # pet.pyw，只给类型标注用；运行时导入会把主程序再执行一遍


def snapshot(folder: Path, new_pet: Callable[..., Pet], art: Art) -> None:
    """new_pet(**改动) 按当前设置加上这些改动造一只只用来截图的桌宠，比如 new_pet(size="small")。"""
    folder.mkdir(parents=True, exist_ok=True)
    t = 1000.0
    now = time.time()
    # 摆拍用的数据：装了插件的样子，重置时间准确，有消耗速度、上下文和花费。
    sample = Usage(
        five=62,
        seven=41,
        at=now - 60,
        five_reset=now + 2 * 3600 + 900,
        seven_reset=now + 3 * 86400 + 3600,
        five_exact=True,
        seven_exact=True,
        five_rate=21.0,
        seven_rate=0.6,
        tokens=48_200,
        window=200_000,
        cost=1.37,
    )
    pets = {}

    def make(**changes) -> Pet:
        key = tuple(sorted(changes.items()))
        if key not in pets:
            pets[key] = new_pet(**changes)
            pets[key].fixed_clock = t
        return pets[key]

    main_pet = make(card="detail")
    real = main_pet.usage

    def fresh(p: Pet) -> None:
        p.motion = type(p.motion)()  # 换一套全新的 Motion
        p.usage = real
        p.working = p.asleep = p.dragging = False
        p.act = None
        p.cheer_until = 0.0
        p.floaters = []
        p.phase = 0.5
        p.shown_face = p.prev_face = None
        p.expr = p.target_key = None
        p.repick_at = 0.0
        # 头边的小特效平时随机轮换，截图时不让它自己冒，要哪种由下面指定。
        p.fx, p.fx_last, p.fx_next = None, None, math.inf

    def fx(kind: str, usage: Usage):
        def setup(p: Pet) -> None:
            p.usage = usage
            p.fx = (kind, t - 0.9, t + 3)

        return setup

    def blink(p: Pet) -> None:
        p.motion.blinks = [(t - 0.05, t + 0.08)]

    def hover(p: Pet) -> None:
        p.motion.hovered, p.motion.hover, p.motion.blush, p.motion.tilt = True, 1.0, 1.0, -4.0

    def press(p: Pet) -> None:
        p.motion.sx.value, p.motion.sy.value = 1.1, 0.86
        p.motion.heart_at = t - 0.3

    def cheer(p: Pet) -> None:
        p.motion.cheer(t - 0.44)
        p.cheer_until = p.motion.happy_until
        p.motion.blush = 1.0

    warn_usage = Usage(five=78, seven=40, at=time.time())
    danger_usage = Usage(five=93, seven=61, at=time.time())
    calm_usage = Usage(five=22, seven=35, at=time.time())

    def warn(p: Pet) -> None:
        fx("sweat", warn_usage)(p)
        p.floaters = [("+6%", TONES["warn"], t - 0.5)]

    def danger(p: Pet) -> None:
        fx("bang", danger_usage)(p)

    def working(p: Pet) -> None:
        p.working = True

    def stale(p: Pet) -> None:
        p.usage = Usage(five=31, seven=58, at=time.time() - 3 * 3600)

    def sleep(p: Pet) -> None:
        p.asleep = True

    def pout(p: Pet) -> None:
        p.act = ("pout", t + 1)

    def wave(p: Pet) -> None:
        p.act = ("wave", t + 1)

    def drag(p: Pet) -> None:
        p.dragging = True
        p.motion.tilt = -6.0

    def plugin(p: Pet) -> None:
        p.usage = sample

    shots = [
        ("idle", main_pet, lambda p: None),
        ("blink", main_pet, blink),
        ("hover", main_pet, hover),
        ("press", main_pet, press),
        ("cheer", main_pet, cheer),
        ("warn", main_pet, warn),
        ("danger", main_pet, danger),
        ("working", main_pet, working),
        ("stale", main_pet, stale),
        ("sleep", main_pet, sleep),
        ("pout", main_pet, pout),
        ("wave", main_pet, wave),
        ("drag", main_pet, drag),
        ("fx-sigh", main_pet, fx("sigh", warn_usage)),
        ("fx-swirl", main_pet, fx("swirl", warn_usage)),
        ("fx-gloom", main_pet, fx("gloom", warn_usage)),
        ("fx-rain", main_pet, fx("rain", danger_usage)),
        ("fx-notes", main_pet, fx("notes", calm_usage)),
        ("plugin", main_pet, plugin),
        ("compact", make(card="compact"), plugin),
        ("small", make(card="detail", size="small"), plugin),
        ("large", make(card="detail", size="large"), plugin),
    ]

    # 每种表情除第一张以外的差分各拍一张：先摆出这种表情，再指定用哪张图。
    poses = {
        "happy": hover,
        "worried": warn,
        "working": working,
        "sleep": sleep,
        "cheer": cheer,
        "wave": wave,
        "pout": pout,
        "drag": drag,
    }

    def variant(expr: str, key: str):
        def setup(p: Pet) -> None:
            poses.get(expr, lambda p: None)(p)
            p.expr, p.target_key, p.repick_at = expr, key, math.inf

        return setup

    for expr, options in art.variants.items():
        shots += [(key, main_pet, variant(expr, key)) for key, _ in options[1:]]
    files = []
    for name, p, setup in shots:
        fresh(p)
        setup(p)
        path = folder / f"{name}.png"
        p.grab().save(str(path))
        files.append(path)

    # 贴边：每条有图的边各拍一张。
    for side in art.edges:
        fresh(main_pet)
        main_pet.usage = sample
        main_pet.edge, main_pet.edge_key = side, art.edges[side][0][0]
        main_pet.layout_edge()
        path = folder / f"edge-{side}.png"
        main_pet.grab().save(str(path))
        files.append(path)
    main_pet.edge = main_pet.edge_key = None
    main_pet.layout_size()

    bubble = Bubble()
    for name, text in (
        ("greet", "早上好，今天读点什么呢？\n5 小时额度用了 31%，约 14:22 重置"),
        ("short", "嘿嘿"),
        ("long", "照这个速度，5 小时额度大约 15:40 就用完了。要不要先歇一会儿，把手头这件事想清楚再继续？"),
    ):
        bubble.set_text(text)
        bubble.grab().save(str(folder / f"bubble-{name}.png"))
        files.append(folder / f"bubble-{name}.png")

    images = [Image.open(f).convert("RGBA") for f in files]
    cell_w = max(im.width for im in images) + 12
    cell_h = max(im.height for im in images) + 12
    cols = 5
    rows = math.ceil(len(images) / cols)
    sheet = Image.new("RGBA", (cols * cell_w, rows * cell_h), (240, 240, 239, 255))
    for i, im in enumerate(images):
        sheet.alpha_composite(im, ((i % cols) * cell_w + 6, (i // cols) * cell_h + 6))
    sheet.save(folder / "sheet.png")
    print("\n".join(f.name for f in files))
    fresh(main_pet)
    print(main_pet.details())
