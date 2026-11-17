"""Poster image for a finished or in-review video."""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFont

from config.settings import Settings, get_settings
from core.asset_fetcher import _stable_diffusion, paint_fallback
from core.models import Scene, Storyboard
from core.visual_renderer import find_font
from core.workspace import Task

_HOOK_PREFIXES = ("什么是", "为什么", "为何", "如何", "怎么", "是不是")


def cover_hook(title: str) -> list[str]:
    """One short line, four characters at most, so the poster reads at a glance."""
    text = re.sub(r"[\s，。！？、；：,.!?\-—]", "", title or "")
    for prefix in _HOOK_PREFIXES:
        if text.startswith(prefix) and len(text) > len(prefix):
            text = text[len(prefix) :]
            break
    text = text[:4] or "看这里"
    return [text]


def stamp_cover(image: Image.Image, title: str) -> Image.Image:
    canvas = ImageEnhance.Contrast(image.convert("RGB")).enhance(1.35)
    canvas = ImageEnhance.Color(canvas).enhance(1.4).convert("RGBA")
    overlay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    width, height = canvas.size
    shade_top = int(height * 0.42)
    for y in range(shade_top, height):
        alpha = int(170 * (y - shade_top) / max(1, height - shade_top))
        draw.line([(0, y), (width, y)], fill=(0, 0, 0, alpha))
    lines = cover_hook(title)
    chars = max(1, max(len(line) for line in lines))
    size = int(width * 0.86 / chars)
    size = max(72, min(size, int(width * 0.28)))
    font = _font(size)
    stroke = max(10, size // 6)
    line_height = int(size * 1.05)
    block = line_height * len(lines)
    top = int(height * 0.66) - block // 2
    for index, line in enumerate(lines):
        bbox = draw.textbbox((0, 0), line, font=font, stroke_width=stroke)
        text_width = bbox[2] - bbox[0]
        x = (width - text_width) // 2
        y = top + index * line_height
        draw.text(
            (x, y),
            line,
            font=font,
            fill=(255, 230, 120, 255),
            stroke_width=stroke,
            stroke_fill=(12, 6, 4, 255),
        )
    return Image.alpha_composite(canvas, overlay).convert("RGB")


def make_cover(task: Task, board: Storyboard, settings: Settings | None = None) -> str:
    cfg = settings or get_settings()
    dest = task.root / "cover.jpg"
    plate = task.root / "cover_plate.jpg"
    scene = _cover_scene(board, str(task.read_status().get("topic") or ""))
    generated = _stable_diffusion(scene, plate, board.aspect_ratio, cfg, task.log)
    note = ""
    if generated is None:
        from core.asset_fetcher import sd_dimensions

        width, height = sd_dimensions(board.aspect_ratio, cfg)
        paint_fallback(scene, plate, width, height)
        note = "本地绘图不可用，封面用了排版底图"
        task.log(note)
    else:
        task.log("封面画面已生成")
    image = Image.open(plate).convert("RGB")
    stamp_cover(image, board.title).save(dest, quality=92)
    cfg.output_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(dest, cfg.output_dir / f"{task.id}.jpg")
    task.log(f"封面已写出 {dest.name}")
    return note


def _cover_scene(board: Storyboard, topic: str) -> Scene:
    keywords = [word for word in board.scenes[0].search_keywords if word][:2]
    if topic:
        keywords.insert(0, topic[:24])
    keywords.extend(["bold poster", "high contrast"])
    return Scene(
        scene_id=1,
        narration="extreme wide shot, vast environment, tiny distant figures, high contrast poster, saturated color, clean background, no text, no letters, no watermark, no close-up",
        search_keywords=keywords[:4] or ["dramatic poster"],
        visual_style="cinematic",
    )


def _font(size: int) -> ImageFont.ImageFont:
    path = find_font()
    if path:
        try:
            return ImageFont.truetype(str(path), size=size)
        except OSError:
            pass
    return ImageFont.load_default()


