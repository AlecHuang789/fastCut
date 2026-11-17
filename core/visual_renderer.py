"""Build a HyperFrames overlay and render it to a transparent WebM."""

from __future__ import annotations

import html
import shutil
from dataclasses import dataclass
from pathlib import Path

from config.settings import Settings, get_settings
from core.models import ProjectTimeline
from core.text import (
    caption_line_limit,
    caption_pages,
    cjk_ratio,
    clamp_groups,
    group_captions,
    strip_brand,
)

_FONT_FORMATS = {".ttf": "truetype", ".otf": "opentype", ".ttc": "collection", ".woff2": "woff2"}


@dataclass
class VisualResult:
    overlay: Path | None
    ass_path: Path | None
    notes: list[str]


def find_font(settings: Settings | None = None) -> Path | None:
    cfg = settings or get_settings()
    if cfg.font_path:
        chosen = Path(cfg.font_path)
        if chosen.exists():
            return chosen
    candidates = [
        Path(r"C:\Windows\Fonts\NotoSansSC-VF.ttf"),
        Path(r"C:\Windows\Fonts\simhei.ttf"),
        Path(r"C:\Windows\Fonts\msyh.ttc"),
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
        Path("/System/Library/Fonts/Supplemental/Songti.ttc"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def _fill(template: str, values: dict[str, str]) -> str:
    text = template
    for key, value in values.items():
        text = text.replace("{{" + key + "}}", value)
    return text


def _font_face(project: Path, settings: Settings) -> tuple[str, str]:
    source = find_font(settings)
    if source is None:
        return "", "未找到中文字体，字幕可能缺字"
    fonts = project / "assets" / "fonts"
    fonts.mkdir(parents=True, exist_ok=True)
    dest = fonts / f"caption{source.suffix.lower()}"
    if not dest.exists():
        shutil.copy2(source, dest)
    font_format = _FONT_FORMATS.get(dest.suffix.lower(), "truetype")
    css = (
        "@font-face {"
        'font-family: "CaptionCN";'
        f"src: url('assets/fonts/{dest.name}') format('{font_format}');"
        "font-weight: 100 900;"
        "font-display: block;"
        "}"
    )
    return css, ""


def _index(width: int, height: int, duration: float, with_captions: bool, plate: str) -> str:
    footage = ""
    if plate:
        footage = f"""
    <video
      id="plate"
      class="clip"
      src="{plate}"
      muted
      playsinline
      data-start="0"
      data-duration="{duration:.3f}"
      data-track-index="0"
      style="position:absolute;left:0;top:0;width:{width}px;height:{height}px;object-fit:cover;z-index:1"
    ></video>"""
    captions = ""
    if with_captions:
        captions = f"""
    <div
      id="captions"
      data-composition-id="captions"
      data-composition-src="compositions/captions.html"
      data-start="0"
      data-duration="{duration:.3f}"
      data-width="{width}"
      data-height="{height}"
      data-track-index="3"
      data-track-kind="captions"
      style="z-index:4"
    ></div>"""
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <script src="https://cdn.jsdelivr.net/npm/gsap@3.15.0/dist/gsap.min.js"></script>
  <style>
    html, body {{ margin: 0; background: #000; }}
  </style>
</head>
<body>
  <div
    id="root"
    data-composition-id="main"
    data-start="0"
    data-duration="{duration:.3f}"
    data-fps="30"
    data-width="{width}"
    data-height="{height}"
  >{footage}{captions}
  </div>
  <script>
    window.__timelines = window.__timelines || {{}};
    window.__timelines["main"] = gsap.timeline({{ paused: true }});
  </script>
</body>
</html>
"""


def caption_font_size(width: int, height: int, requested: int = 0) -> int:
    base = 96 if height > width else 84
    if requested <= 0:
        return base
    scale = max(48, min(140, requested)) / 96
    return max(48, min(160, round(base * scale)))


def _caption_text(group) -> str:
    raw = "".join(word.text for word in group.words)
    if cjk_ratio(raw) < 0.3:
        raw = " ".join(word.text for word in group.words)
    return strip_brand(raw).strip("，、；：,;: ")


def _caption_cues(timeline: ProjectTimeline, per_line: int):
    groups = clamp_groups(group_captions(timeline.words, max_chars=per_line * 2), timeline.total_duration)
    cues = []
    for group in groups:
        pages = caption_pages(_caption_text(group), per_line, 2)
        if not pages:
            continue
        weights = [max(1, sum(len(line) for line in page)) for page in pages]
        total = sum(weights) or 1
        cursor = group.start
        for page, weight in zip(pages, weights):
            duration = group.duration * weight / total
            cues.append((cursor, duration, page, group))
            cursor += duration
    return cues


def _caption_markup(timeline: ProjectTimeline, per_line: int) -> tuple[str, str]:
    highlights = {scene.scene_id: scene.highlight_words for scene in timeline.storyboard.scenes}
    blocks: list[str] = []
    tweens: list[str] = []
    for index, (start, duration, lines, group) in enumerate(_caption_cues(timeline, per_line)):
        scene_ids = {word.scene_id for word in group.words}
        tokens: list[str] = []
        for scene_id in scene_ids:
            tokens.extend(highlights.get(scene_id) or [])
        spans = []
        for line_index, line in enumerate(lines):
            emph = " emph" if any(token and token in line for token in tokens) else ""
            spans.append(f'<span class="word{emph}" id="cap-g{index}-w{line_index}">{html.escape(line)}</span>')
            if emph and line_index == 0:
                moment = group.start + min(0.35, max(0.12, group.duration * 0.2))
                tweens.append(
                    "tl.fromTo("
                    f'"#cap-g{index}-w{line_index}", '
                    "{ scale: 1 }, "
                    '{ scale: 1.08, duration: 0.16, yoyo: true, repeat: 1, ease: "power2.out" }, '
                    f"{moment:.3f});"
                )
        blocks.append(
            f'<div id="cap-g{index}" class="clip" data-start="{start:.3f}" '
            f'data-duration="{duration:.3f}" data-track-index="1">'
            f'<div class="line" id="cap-g{index}-line"><span class="slant">{"<br>".join(spans)}</span></div></div>'
        )
        fade_at = start + max(0.12, duration - 0.14)
        tweens.append(
            "tl.fromTo("
            f'"#cap-g{index}-line", '
            "{ opacity: 0, y: 16 }, "
            '{ opacity: 1, y: 0, duration: 0.16, ease: "power3.out" }, '
            f"{start:.3f});"
        )
        tweens.append(
            f'tl.to("#cap-g{index}-line", {{ opacity: 0, duration: 0.1, ease: "power2.in" }}, {fade_at:.3f});'
        )
    return "\n".join(blocks), "\n".join(tweens)


def write_project(
    timeline: ProjectTimeline,
    project: Path,
    settings: Settings | None = None,
    plate: Path | None = None,
    caption_size: int = 0,
) -> list[str]:
    cfg = settings or get_settings()
    project.mkdir(parents=True, exist_ok=True)
    (project / "compositions").mkdir(exist_ok=True)
    font_css, font_note = _font_face(project, cfg)
    notes = [font_note] if font_note else []
    width, height = timeline.width, timeline.height
    duration = round(timeline.total_duration, 3)
    portrait = height > width
    size = caption_font_size(width, height, caption_size)
    per_line = caption_line_limit(width, size)
    blocks, tweens = _caption_markup(timeline, per_line)
    title = strip_brand(timeline.storyboard.title) or timeline.storyboard.title
    common = {
        "WIDTH": str(width),
        "HEIGHT": str(height),
        "DURATION": f"{duration:.3f}",
        "PROGRESS_DUR": f"{max(0.2, duration - 0.05):.3f}",
        "FONT_FACE": font_css,
        "TITLE": html.escape(title),
        "TITLE_TOP": "200px" if portrait else "72px",
        "TITLE_SIZE": "72" if len(title) <= 8 else "56",
        "TITLE_START": "0.200",
        "TITLE_CLIP": "0.800",
        "TITLE_IN": "0.200",
        "TITLE_OUT": "0.760",
        "CAPTION_BOTTOM": "640px" if portrait else "120px",
        "CAPTION_SIZE": str(size),
        "CAPTION_BLOCKS": blocks,
        "CAPTION_TWEENS": tweens,
    }
    templates = cfg.templates_dir / "compositions"
    for name in ("chrome.html", "title.html", "captions.html"):
        raw = (templates / name).read_text(encoding="utf-8")
        (project / "compositions" / name).write_text(_fill(raw, common), encoding="utf-8")
    plate_src = ""
    if plate and plate.exists():
        assets = project / "assets"
        assets.mkdir(parents=True, exist_ok=True)
        target = assets / "background.mp4"
        if plate.resolve() != target.resolve():
            shutil.copyfile(plate, target)
        plate_src = "assets/background.mp4"
    (project / "index.html").write_text(
        _index(width, height, duration, bool(blocks), plate_src),
        encoding="utf-8",
    )
    return notes


def write_ass(timeline: ProjectTimeline, dest: Path, caption_size: int = 0) -> Path:
    margin_v = 640 if timeline.height > timeline.width else 120
    font_size = caption_font_size(timeline.width, timeline.height, caption_size)
    per_line = caption_line_limit(timeline.width, font_size)
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {timeline.width}
PlayResY: {timeline.height}
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Noto Sans SC,{font_size},&H00FFFFFF,&H000000FF,&H00000000,&H00000000,-1,-1,0,0,100,100,0,-8,1,3,1,2,80,80,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = [header.rstrip() + "\n"]
    for start, duration, page, _group in _caption_cues(timeline, per_line):
        text = r"\N".join(page)
        lines.append(
            f"Dialogue: 0,{_ass_time(start)},{_ass_time(start + duration)},Default,,0,0,0,,{text}\n"
        )
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text("".join(lines), encoding="utf-8")
    return dest


def _ass_time(seconds: float) -> str:
    seconds = max(0.0, seconds)
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    whole = int(seconds % 60)
    centis = int(round((seconds - int(seconds)) * 100))
    if centis == 100:
        centis = 0
        whole += 1
    return f"{hours}:{minutes:02d}:{whole:02d}.{centis:02d}"


def render_overlay(
    timeline: ProjectTimeline,
    project: Path,
    dest: Path,
    *,
    plate: Path | None = None,
    quality: str = "draft",
    settings: Settings | None = None,
    log=None,
    caption_size: int = 0,
) -> VisualResult:
    cfg = settings or get_settings()
    notes = write_project(timeline, project, cfg, plate=plate, caption_size=caption_size)
    ass_path = project / "captions.ass"
    write_ass(timeline, ass_path, caption_size=caption_size)
    npx = shutil.which("npx")
    if not npx:
        notes.append("未找到 npx，字幕改用 ASS 烧录")
        return VisualResult(None, ass_path, notes)
    dest.parent.mkdir(parents=True, exist_ok=True)
    output_format = "mp4" if plate else "webm"
    cmd = [
        npx,
        "--yes",
        "hyperframes",
        "render",
        str(project),
        "--format",
        output_format,
        "--quality",
        quality,
        "--fps",
        "30",
        "--output",
        str(dest),
    ]
    if log:
        log("HyperFrames 开始渲染画面和字幕")
    from core.control import run_process

    completed = run_process(
        cmd,
        cwd=str(project),
        timeout=max(240, timeline.total_duration * 30),
    )
    log_path = project / "render.log"
    log_path.write_text((completed.stdout or "") + "\n" + (completed.stderr or ""), encoding="utf-8")
    if completed.returncode != 0 or not dest.exists() or dest.stat().st_size < 1000:
        tail = (completed.stderr or completed.stdout or "")[-600:]
        if log:
            log(f"HyperFrames 渲染失败，改用字幕烧录。{tail}")
        notes.append("HyperFrames 渲染失败，已改用 ASS 字幕")
        return VisualResult(None, ass_path, notes)
    if log:
        log(f"画面已写出 {dest.name}")
    return VisualResult(dest, None, notes)
