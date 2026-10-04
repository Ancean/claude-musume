"""桌宠的提示音：用程序合成的木琴、拇指琴一样的柔和音色，存成 wav 后用 winsound 播放。只用标准库。

上一版的“啵”和铃声起音太快、音高偏高，容易吓一跳。现在每个音都有十几毫秒的渐起，
泛音少、衰减快，整体音量也低了很多；点她的声音是五声音阶里的单音，连着点会一个个往上走。
音量改了或者合成方式变了（VERSION）就整套重新生成。在 sounds\\自定义 里放同名的 wav 可以换成自己的声音。
"""

from __future__ import annotations

import array
import json
import math
import random
import struct
import wave
from pathlib import Path

RATE = 22050
VERSION = 2
# 三档音量对应的峰值（满幅为 1）。上一版的峰值在 0.32 到 0.42 之间。
VOLUMES = {"low": 0.08, "medium": 0.14, "high": 0.22}
VOLUME_LABELS = {"low": "小声", "medium": "适中", "high": "大声"}
# 点她：C5 D5 E5 G5 A5。
PATS = (523.25, 587.33, 659.25, 783.99, 880.0)
CUSTOM = "自定义"
# 试听菜单和 README 用的名字：（文件名, 说明）。
NAMES = (("pat", "点她"), ("done", "任务完成"), ("chime", "额度重置"), ("warn", "额度告急"))
# 任务结束音：设置里的选项和说明。预设的 wav 在 sound-presets 文件夹（来自 DeepSeek-Balance-Whale-Widget），
# 按音量另存一份缩放过的到 sounds 文件夹；file 是自选的一个 wav，group 是一个文件夹，每次随机放其中一个。
DONE_SOUNDS = {
    "exp": "Minecraft·经验球（默认）",
    "a": "音效 A",
    "chime": "叮咚（合成）",
    "file": "自选文件…",
    "group": "音效组（文件夹，每次随机一个）…",
}
PRESETS = {"exp": "minecraft-exp-orb.wav", "a": "task-end-a.wav"}
# 预设音效按音量缩放的倍数：原文件本身已经很响。
PRESET_GAIN = {"low": 0.3, "medium": 0.55, "high": 0.85}


def mallet(freq: float, t: float, decay: float) -> float:
    """一个柔和的槌击音：渐起 12 毫秒，基音为主，二次泛音很快消失。"""
    rise = 0.5 - 0.5 * math.cos(math.pi * min(1.0, t / 0.012))
    w = 2 * math.pi * freq * t
    body = math.sin(w) * math.exp(-t / decay)
    body += 0.16 * math.sin(2 * w) * math.exp(-t / (decay * 0.45))
    body += 0.05 * math.sin(3.01 * w) * math.exp(-t / (decay * 0.2))
    return rise * body


def notes(parts: tuple[tuple[float, float, float], ...], length: float, decay: float) -> list[float]:
    """parts：（开始的秒数, 频率, 音量）。最后 40 毫秒淡出，免得结尾咔一声。"""
    out = [0.0] * int(RATE * length)
    for start, freq, gain in parts:
        first = int(start * RATE)
        for i in range(first, len(out)):
            out[i] += gain * mallet(freq, (i - first) / RATE, decay)
    tail = int(RATE * 0.04)
    for i in range(tail):
        out[-1 - i] *= i / tail
    # 一阶低通，把高频再压一压，听起来更圆。
    smooth, y = [], 0.0
    for x in out:
        y += 0.55 * (x - y)
        smooth.append(y)
    return smooth


def recipes() -> dict[str, tuple[list[float], float]]:
    """各个声音的波形和相对音量。"""
    made = {f"pat{i + 1}": (notes(((0.0, f, 1.0),), 0.32, 0.09), 0.8) for i, f in enumerate(PATS)}
    # 任务完成：E5 到 A5，轻轻的“叮咚”。
    made["done"] = (notes(((0.0, 659.25, 1.0), (0.13, 880.0, 0.9)), 0.95, 0.26), 1.0)
    # 额度重置：G4 B4 D5 G5 的上行琶音。
    made["chime"] = (notes(((0.0, 392.0, 0.9), (0.09, 493.88, 0.9), (0.18, 587.33, 0.9), (0.27, 783.99, 0.8)), 1.25, 0.3), 1.0)
    # 额度告急：A4 落到 F4，低一些、慢一些，提醒而不吓人。
    made["warn"] = (notes(((0.0, 440.0, 1.0), (0.2, 349.23, 1.0)), 0.95, 0.24), 0.85)
    return made


def write_wav(path: Path, samples: list[float], peak: float) -> None:
    top = max(abs(s) for s in samples) or 1.0
    frames = b"".join(struct.pack("<h", int(s / top * peak * 32767)) for s in samples)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(frames)


def file_names() -> list[str]:
    return [f"pat{i + 1}" for i in range(len(PATS))] + ["done", "chime", "warn"]


def playable(path: Path) -> bool:
    """winsound 只能放未压缩的 PCM wav。"""
    try:
        with wave.open(str(path), "rb") as w:
            return w.getcomptype() == "NONE" and w.getnframes() > 0
    except (OSError, wave.Error, EOFError):
        return False


def scaled(src: Path, dst: Path, gain: float) -> None:
    """把 16 位 PCM 的 wav 音量乘上 gain 另存；其他位深原样复制。"""
    with wave.open(str(src), "rb") as w:
        params, frames = w.getparams(), w.readframes(w.getnframes())
    if params.sampwidth == 2:
        samples = array.array("h", frames)
        samples = array.array("h", (max(-32768, min(32767, int(s * gain))) for s in samples))
        frames = samples.tobytes()
    with wave.open(str(dst), "wb") as w:
        w.setparams(params)
        w.writeframes(frames)


def preset_file(folder: Path, key: str) -> Path:
    return folder / f"done-{key}.wav"


def ensure_presets(folder: Path, presets: Path, volume: str) -> None:
    """按音量把预设的任务结束音另存到 folder；原文件缺了就跳过。"""
    gain = PRESET_GAIN.get(volume, PRESET_GAIN["medium"])
    for key, name in PRESETS.items():
        if (presets / name).exists():
            scaled(presets / name, preset_file(folder, key), gain)


def done_path(folder: Path, presets: Path, choice: str, chosen: str | None) -> Path | None:
    """任务结束时放哪个文件；自选的文件或文件夹不能用时退回默认的经验球，再没有就用音效 A。"""
    if choice in ("file", "group") and chosen:
        target = Path(chosen)
        if choice == "file" and target.is_file() and playable(target):
            return target
        if choice == "group" and target.is_dir():
            options = [p for p in target.iterdir() if p.suffix.lower() == ".wav" and playable(p)]
            if options:
                return random.choice(options)
    if choice == "chime":
        return path_for(folder, "done")
    # 经验球不随源码发布（版权），缺了就退回音效 A。
    for key in dict.fromkeys((choice if choice in PRESETS else "exp", "a")):
        for path in (preset_file(folder, key), presets / PRESETS[key]):
            if path.exists():
                return path
    return path_for(folder, "done")


def ensure(folder: Path, volume: str, presets: Path | None = None) -> None:
    """声音文件缺了、音量换了或合成方式更新了，就整套重新生成。"""
    folder.mkdir(exist_ok=True)
    (folder / CUSTOM).mkdir(exist_ok=True)
    stamp_file = folder / "version.json"
    stamp = {"version": VERSION, "volume": volume}
    try:
        current = json.loads(stamp_file.read_text(encoding="utf-8")) == stamp
    except (OSError, ValueError):
        current = False
    have_presets = presets is None or all(
        preset_file(folder, key).exists() for key, name in PRESETS.items() if (presets / name).exists()
    )
    if current and have_presets and all((folder / f"{name}.wav").exists() for name in file_names()):
        return
    if presets is not None:
        ensure_presets(folder, presets, volume)
    # 合成要零点几秒，文件都在时不做，免得拖慢启动。
    made = recipes()
    peak = VOLUMES.get(volume, VOLUMES["medium"])
    for name, (samples, gain) in made.items():
        write_wav(folder / f"{name}.wav", samples, peak * gain)
    # 上一版的“啵”，已经换成 pat1 到 pat5。
    (folder / "pop.wav").unlink(missing_ok=True)
    stamp_file.write_text(json.dumps(stamp), encoding="utf-8")


def path_for(folder: Path, name: str) -> Path:
    """自定义文件夹里有同名 wav 就用它；pat1 到 pat5 也认一个统一的 pat.wav。"""
    custom = folder / CUSTOM
    for candidate in (custom / f"{name}.wav", custom / f"{name.rstrip('0123456789')}.wav"):
        if candidate.exists():
            return candidate
    return folder / f"{name}.wav"
