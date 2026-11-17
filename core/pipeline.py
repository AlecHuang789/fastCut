"""End-to-end create and highlight pipelines."""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path

from config.ffmpeg_presets import frame_size
from config.settings import get_settings
from core.asset_fetcher import download, fetch_scene_asset
from core.capcut import export_capcut
from core.audio_aligner import align_words, synthesize_scenes
from core.cleanup import cleanup_expired
from core.control import Stopped, checkpoint, clear_stop, task_binding
from core.freesound import bed_query, fetch_bed
from core.ffmpeg_engine import (
    MediaError,
    concat_videos,
    cut_clip,
    detect_encoder,
    extract_audio,
    fit_audio,
    ken_burns,
    make_bed,
    normalize_video,
    probe_duration,
    speech_regions,
    composite,
)
from core.llm_director import generate_highlights, generate_storyboard, local_highlights
from core.models import ProjectTimeline, Scene, Storyboard, WordStamp
from core.text import cjk_ratio
from core.visual_renderer import render_overlay
from core.workspace import Task, create_task, load_task


class PipelineError(RuntimeError):
    pass


@dataclass
class RunResult:
    task_id: str
    output_path: Path
    duration: float
    notes: list[str]


def run_create(
    topic: str,
    duration: float = 30,
    aspect: str = "9:16",
    *,
    voice: str | None = None,
    bgm: str | None = None,
    quality: str = "draft",
    use_llm: bool = True,
    align: str = "auto",
    task_id: str | None = None,
    resume: bool = False,
) -> RunResult:
    task = load_task(task_id) if resume else create_task(task_id)
    with task_binding(task.id):
        return _run_create(task, topic, duration, aspect, voice, bgm, quality, use_llm, align)


def _run_create(task, topic, duration, aspect, voice, bgm, quality, use_llm, align):
    try:
        return _create(task, topic, duration, aspect, voice, bgm, quality, use_llm, align)
    except Stopped:
        _return_to_board(task)
        return None
    except Exception as exc:
        task.status(state="failed", step="error", error=str(exc))
        task.log(f"失败：{exc}")
        raise


def run_highlight(
    video: str,
    duration: float = 30,
    aspect: str = "9:16",
    *,
    bgm: str | None = None,
    quality: str = "draft",
    use_llm: bool = True,
    align: str = "auto",
    task_id: str | None = None,
    resume: bool = False,
) -> RunResult:
    task = load_task(task_id) if resume else create_task(task_id)
    with task_binding(task.id):
        return _run_highlight(task, video, duration, aspect, bgm, quality, use_llm, align)


def _run_highlight(task, video, duration, aspect, bgm, quality, use_llm, align):
    try:
        return _highlight(task, video, duration, aspect, bgm, quality, use_llm, align)
    except Stopped:
        _return_to_board(task)
        return None
    except Exception as exc:
        task.status(state="failed", step="error", error=str(exc))
        task.log(f"失败：{exc}")
        raise


def run_storyboard(
    topic: str,
    duration: float,
    aspect: str,
    *,
    use_llm: bool = True,
    task_id: str | None = None,
    resume: bool = False,
) -> Storyboard:
    task = load_task(task_id) if resume else create_task(task_id)
    with task_binding(task.id):
        return _run_storyboard(task, topic, duration, aspect, use_llm)


def _run_storyboard(task, topic, duration, aspect, use_llm):
    try:
        board = _write_storyboard(task, topic, duration, aspect, use_llm)
        task.status(state="review", step="review", progress=0.2, error=None)
        task.log("分镜已写好，等待确认")
        return board
    except Stopped:
        _return_to_board(task)
        raise
    except Exception as exc:
        task.status(state="failed", step="error", error=str(exc))
        task.log(f"失败：{exc}")
        raise


def run_produce(
    *,
    voice: str | None = None,
    bgm: str | None = None,
    quality: str = "draft",
    align: str = "auto",
    task_id: str | None = None,
    resume: bool = False,
) -> RunResult | None:
    if not task_id:
        raise PipelineError("缺少任务编号")
    task = load_task(task_id)
    with task_binding(task.id):
        return _run_produce(task, voice, bgm, quality, align)


def _run_produce(task, voice, bgm, quality, align):
    try:
        board = Storyboard.model_validate_json(task.path("storyboard.json").read_text(encoding="utf-8"))
        return _render_board(task, board, voice, bgm, quality, align)
    except Stopped:
        _return_to_board(task)
        return None
    except Exception as exc:
        task.status(state="review", step="review", progress=0.2, error=str(exc))
        task.log(f"失败：{exc}")
        return None


def apply_visual_style(board: Storyboard, style: str) -> Storyboard:
    if style not in {"cinematic", "documentary", "bright"}:
        return board
    scenes = [scene.model_copy(update={"visual_style": style}) for scene in board.scenes]
    return board.model_copy(update={"scenes": scenes})


def save_storyboard_edits(task: Task, title: str, scenes: list[dict]) -> Storyboard:
    raw = task.path("storyboard.json")
    if not raw.exists():
        raise PipelineError("还没有分镜")
    current = Storyboard.model_validate_json(raw.read_text(encoding="utf-8"))
    if not scenes:
        raise PipelineError("至少保留一镜")
    updated: list[Scene] = []
    for index, item in enumerate(scenes, start=1):
        narration = " ".join(str(item.get("narration") or "").split())
        if not narration:
            raise PipelineError(f"第 {index} 镜的文案是空的")
        old = current.scenes[index - 1] if index - 1 < len(current.scenes) else None
        keywords = [str(word).strip() for word in item.get("search_keywords") or [] if str(word).strip()]
        if not keywords and old:
            keywords = list(old.search_keywords)
        if not keywords:
            keywords = ["cinematic still"]
        style = str(item.get("visual_style") or (old.visual_style if old else "cinematic")).strip() or "cinematic"
        highlights = [word for word in (old.highlight_words if old else []) if word and word in narration]
        updated.append(
            Scene(
                scene_id=index,
                narration=narration,
                search_keywords=keywords,
                visual_style=style,
                highlight_words=highlights,
            )
        )
    cleaned = " ".join(title.split())[:40] or current.title
    board = Storyboard(title=cleaned, aspect_ratio=current.aspect_ratio, scenes=updated)
    _dump(raw, board)
    task.status(error=None)
    return board


def finish_stop(task: Task, *, interrupted: bool = False) -> None:
    clear_stop(task.id)
    mode = str(task.read_status().get("mode") or "")
    if mode == "highlight" or not task.path("storyboard.json").exists():
        error = "上次生成已中断" if interrupted else "已停止生成"
        task.status(state="failed", step="stopped", error=error)
        task.log(error)
        return
    task.status(state="review", step="review", progress=0.2, error=None)
    if interrupted:
        task.log("上次生成已中断，已回到分镜")
    else:
        task.log("已停止生成，已回到分镜，可以用当前分镜重新生成")


def _return_to_board(task: Task) -> None:
    finish_stop(task)


def _create(
    task: Task,
    topic: str,
    duration: float,
    aspect: str,
    voice: str | None,
    bgm: str | None,
    quality: str,
    use_llm: bool,
    align: str,
) -> RunResult:
    board = _write_storyboard(task, topic, duration, aspect, use_llm)
    return _render_board(task, board, voice, bgm, quality, align)


def _write_storyboard(task: Task, topic: str, duration: float, aspect: str, use_llm: bool) -> Storyboard:
    cleanup_expired()
    style = str(task.read_status().get("visual_style") or "")
    task.status(step="storyboard", progress=0.08, state="running", mode="create")
    board, how = generate_storyboard(topic, duration, aspect, use_llm=use_llm)
    board = apply_visual_style(board.model_copy(update={"aspect_ratio": aspect}), style)
    if how == "local":
        task.note("未配置大模型，分镜由本地导演生成")
    elif how == "fallback":
        task.note("大模型返回无法通过校验，已改用本地导演")
    task.log(f"分镜 {len(board.scenes)} 镜：{board.title}")
    _dump(task.path("storyboard.json"), board)
    return board


def _render_board(
    task: Task,
    board: Storyboard,
    voice: str | None,
    bgm: str | None,
    quality: str,
    align: str,
) -> RunResult:
    checkpoint(task.id)
    width, height = frame_size(board.aspect_ratio)
    task.status(state="running", step="voice", progress=0.22, error=None, mode="create")
    scenes, narration, voice_notes = synthesize_scenes(
        board.scenes, task.path("audio"), voice=voice, log=task.log, task_id=task.id
    )
    for message in voice_notes:
        task.note(message)
    words, align_how = align_words(narration, scenes, task.root, method=align, log=task.log)
    if align_how == "estimate":
        task.note("未使用 Whisper，字幕时间按字数摊到每镜")
    total = probe_duration(narration)
    timeline = _timeline(task, "create", board, scenes, words, narration, total, width, height)
    _dump(task.path("timeline.json"), timeline)

    checkpoint(task.id)
    task.status(step="assets", progress=0.45)
    background = _build_background(task, scenes, board.aspect_ratio, width, height)
    return _finish(task, timeline, background, narration, bgm, quality, width, height)


def _highlight(
    task: Task,
    video: str,
    duration: float,
    aspect: str,
    bgm: str | None,
    quality: str,
    use_llm: bool,
    align: str,
) -> RunResult:
    settings = get_settings()
    cleanup_expired()
    task.status(mode="highlight")
    width, height = frame_size(aspect)
    source = _resolve_video(video, task.path("input"), settings)
    source_duration = probe_duration(source)
    if source_duration <= 0:
        raise PipelineError("读不到原片时长")
    task.status(step="transcribe", progress=0.15)
    speech = task.path("audio", "source.wav")
    extract_audio(source, speech)
    regions = speech_regions(speech)
    words, align_how = align_words(speech, [], task.root, method=align, log=task.log)
    segments = _segments_from_words(words) or [
        {"start": start, "end": end, "text": "原片高光"} for start, end in regions
    ]
    if not segments:
        segments = [{"start": 0.0, "end": min(source_duration, max(6.0, duration)), "text": "原片高光"}]
    board, how = generate_highlights(segments, duration, aspect, source_duration, use_llm=use_llm)
    board = _sanitize_highlights(board, source_duration, duration, segments, aspect)
    if how != "llm":
        task.note("高光选择未走大模型，已按片段时长挑选")
    if align_how == "estimate":
        task.note("原片没有字级转写，字幕使用片段文本")
    task.log(f"选出 {len(board.scenes)} 段高光")

    task.status(step="cut", progress=0.4)
    pieces: list[Path] = []
    timed: list[Scene] = []
    cursor = 0.0
    for scene in board.scenes:
        checkpoint(task.id)
        dest = task.path("build", f"cut_{scene.scene_id:03d}.mp4")
        cut_clip(
            source,
            dest,
            start=float(scene.source_start),
            end=float(scene.source_end),
            width=width,
            height=height,
        )
        length = probe_duration(dest)
        pieces.append(dest)
        timed.append(scene.model_copy(update={"start": cursor, "end": cursor + length, "actual_duration": length}))
        cursor += length
    background = task.path("build", "background.mp4")
    concat_videos(pieces, background)
    narration = task.path("audio", "narration.wav")
    extract_audio(background, narration, sample_rate=48000, channels=2)
    shifted = _shift_words(words, timed) or _placeholder_words(timed)
    total = probe_duration(narration)
    timeline = _timeline(task, "highlight", board, timed, shifted, narration, total, width, height)
    _dump(task.path("timeline.json"), timeline)
    return _finish(task, timeline, background, narration, bgm, quality, width, height)


def _finish(
    task: Task,
    timeline: ProjectTimeline,
    background: Path,
    narration: Path,
    bgm: str | None,
    quality: str,
    width: int,
    height: int,
) -> RunResult:
    settings = get_settings()
    checkpoint(task.id)
    task.status(step="overlay", progress=0.72)
    visual = render_overlay(
        timeline,
        task.path("hf"),
        task.path("build", "picture.mp4"),
        plate=background,
        quality=quality,
        log=task.log,
        caption_size=int(task.read_status().get("caption_size") or 0),
    )
    for message in visual.notes:
        if message:
            task.note(message)
    bed = task.path("audio", "bgm.wav")
    _lay_bed(task, timeline, bgm, bed, settings)
    voice = task.path("audio", "voice.wav")
    fit_audio(narration, voice, timeline.total_duration)
    checkpoint(task.id)
    task.status(step="encode", progress=0.86)
    encoder = detect_encoder()
    task.log(f"成片编码：{encoder}")
    final = task.path("build", "final.mp4")
    picture = visual.overlay or background
    try:
        composite(
            picture,
            voice,
            bed,
            final,
            duration=timeline.total_duration,
            width=width,
            height=height,
            overlay=None,
            ass_path=None if visual.overlay else visual.ass_path,
        )
    except MediaError as exc:
        raise PipelineError(f"合成失败：{exc}") from exc
    settings.output_dir.mkdir(parents=True, exist_ok=True)
    output = settings.output_dir / f"{task.id}.mp4"
    shutil.copyfile(final, output)
    capcut_fields: dict = {}
    try:
        exported = export_capcut(task, timeline)
        task.note(exported.note)
        capcut_fields = {
            "capcut": str(exported.folder),
            "capcut_zip": str(exported.zip_path),
            "capcut_apps": exported.installed,
            "capcut_note": exported.note,
        }
    except Stopped:
        raise
    except Exception as exc:
        task.log(f"剪映草稿未生成：{exc}")
        capcut_fields = {"capcut_note": "成片已经做好。剪映草稿这次没有生成，可以稍后点「导入剪映」再试。"}
    notes = list(task.read_status().get("notes") or [])
    checkpoint(task.id)
    clear_stop(task.id)
    task.status(state="done", step="done", progress=1, output=str(output), error=None, notes=notes, **capcut_fields)
    task.log(f"成片 {output}")
    return RunResult(task.id, output, timeline.total_duration, notes)


def _lay_bed(task: Task, timeline: ProjectTimeline, bgm: str | None, bed: Path, settings) -> None:
    bed_source = Path(bgm) if bgm else (Path(settings.bgm_path) if settings.bgm_path else None)
    if bed_source and bed_source.exists():
        fit_audio(bed_source, bed, timeline.total_duration)
        return
    preference = str(task.read_status().get("audio") or "auto")
    if preference not in {"auto", "freesound", "none"}:
        preference = "auto"
    if preference != "none" and settings.freesound_keys:
        board = timeline.storyboard
        keywords: list[str] = []
        for scene in board.scenes:
            keywords.extend(scene.search_keywords)
        style = str(task.read_status().get("visual_style") or "")
        if style not in {"cinematic", "documentary", "bright"} and board.scenes:
            style = board.scenes[0].visual_style
        found = fetch_bed(bed_query(board.title or str(task.read_status().get("topic") or ""), style, keywords), settings, task.log)
        if found:
            path, credit = found
            fit_audio(path, bed, timeline.total_duration)
            task.note(credit)
            return
        if preference == "freesound":
            task.note("没有找到可商用的 Freesound 配乐，已生成一段轻铺底")
        else:
            task.note("Freesound 没有合适的可商用配乐，已生成一段轻铺底")
        make_bed(bed, timeline.total_duration)
        return
    make_bed(bed, timeline.total_duration)
    if preference == "none":
        task.note("按设定不加网络配乐，已生成一段轻铺底")
    elif preference == "freesound":
        task.note("未配置 Freesound，已生成一段轻铺底")
    else:
        task.note("未提供背景音乐，已生成一段轻铺底")


def _build_background(task: Task, scenes: list[Scene], aspect: str, width: int, height: int) -> Path:
    used: set[str] = set()
    pieces: list[Path] = []
    saw_generated = False
    origin = str(task.read_status().get("visual_source") or "auto")
    if origin not in {"auto", "web", "sd", "video", "ai"}:
        origin = "auto"
    source_label = {
        "auto": "自动选素材",
        "web": "网络图片",
        "sd": "本地绘图",
        "video": "视频素材",
        "ai": "AI 生图",
    }[origin]
    task.log(f"画面来源：{source_label}")
    for scene in scenes:
        checkpoint(task.id)
        fetched = fetch_scene_asset(scene, task.path("assets"), aspect, log=task.log, used=used, source=origin)
        if fetched.asset_kind == "generated":
            saw_generated = True
        media = Path(fetched.asset_path or "")
        dest = task.path("build", f"scene_{scene.scene_id:03d}.mp4")
        length = max(0.8, float(scene.actual_duration or 4))
        try:
            if fetched.asset_kind == "video":
                normalize_video(media, dest, width=width, height=height, duration=length)
            else:
                ken_burns(media, dest, width=width, height=height, duration=length)
        except MediaError as exc:
            task.log(f"分镜 {scene.scene_id} 素材处理失败，改用本地画面：{exc}")
            from core.asset_fetcher import paint_fallback

            still = task.path("assets", f"scene_{scene.scene_id:03d}_fallback.jpg")
            paint_fallback(scene, still, width, height)
            ken_burns(still, dest, width=width, height=height, duration=length)
            saw_generated = True
        pieces.append(dest)
    if origin == "sd":
        task.note("画面按设定使用本地绘图")
    elif origin == "ai":
        task.note("画面按设定使用 AI 生图")
    elif saw_generated and origin == "web":
        task.note("有分镜没有找到网络图片，已用本地绘图或色块补上")
    elif saw_generated:
        task.note("有分镜没有拿到图库素材，已用 Stable Diffusion 或色块画面补上")
    background = task.path("build", "background.mp4")
    return concat_videos(pieces, background)


def _timeline(
    task: Task,
    mode: str,
    board: Storyboard,
    scenes: list[Scene],
    words: list[WordStamp],
    narration: Path,
    total: float,
    width: int,
    height: int,
) -> ProjectTimeline:
    updated = board.model_copy(update={"scenes": scenes})
    return ProjectTimeline(
        task_id=task.id,
        mode=mode,  # type: ignore[arg-type]
        storyboard=updated,
        words=words,
        narration_path=str(narration),
        total_duration=round(total, 3),
        width=width,
        height=height,
    )


def _resolve_video(video: str, dest_dir: Path, settings) -> Path:
    if video.startswith("http://") or video.startswith("https://"):
        dest = dest_dir / "source.mp4"
        if not download(video, dest, settings):
            raise PipelineError("原片下载超时或失败")
        return dest
    path = Path(video).expanduser()
    if not path.exists():
        raise PipelineError(f"找不到视频：{video}")
    return path


def _segments_from_words(words: list[WordStamp]) -> list[dict]:
    if not words:
        return []
    chunks: list[list[WordStamp]] = []
    bucket: list[WordStamp] = []
    origin = words[0].start
    for word in words:
        gap = bool(bucket) and word.start - bucket[-1].end > 0.45
        long = bool(bucket) and word.end - origin >= 5.5
        if gap or long:
            chunks.append(bucket)
            bucket = []
            origin = word.start
        bucket.append(word)
    if bucket:
        chunks.append(bucket)
    segments = []
    for bucket in chunks:
        raw = "".join(item.text for item in bucket)
        text = raw if cjk_ratio(raw) >= 0.3 else " ".join(item.text for item in bucket)
        length = bucket[-1].end - bucket[0].start
        if length < 2.2:
            continue
        segments.append({"start": bucket[0].start, "end": bucket[-1].end, "text": text[:80]})
    return segments


def _sanitize_highlights(
    board: Storyboard,
    source_duration: float,
    duration: float,
    segments: list[dict],
    aspect: str,
) -> Storyboard:
    ordered = sorted(board.scenes, key=lambda scene: scene.source_start if scene.source_start is not None else 1e9)
    kept: list[Scene] = []
    cursor = 0.0
    used = 0.0
    for scene in ordered:
        if scene.source_start is None or scene.source_end is None:
            continue
        start = max(cursor, float(scene.source_start))
        end = min(source_duration, float(scene.source_end))
        if end - start < 2:
            continue
        if used >= duration and kept:
            break
        if used + (end - start) > duration * 1.15 and kept:
            end = start + max(2.5, duration - used)
            end = min(end, source_duration)
        kept.append(
            scene.model_copy(
                update={"scene_id": len(kept) + 1, "source_start": round(start, 3), "source_end": round(end, 3)}
            )
        )
        cursor = end
        used += end - start
    if kept:
        return board.model_copy(update={"scenes": kept, "aspect_ratio": aspect})
    return local_highlights(segments, duration, aspect, source_duration)


def _shift_words(words: list[WordStamp], scenes: list[Scene]) -> list[WordStamp]:
    shifted: list[WordStamp] = []
    for scene in scenes:
        if scene.source_start is None or scene.start is None:
            continue
        for word in words:
            if word.end <= scene.source_start or word.start >= float(scene.source_end):
                continue
            start = max(word.start, scene.source_start) - scene.source_start + scene.start
            end = min(word.end, float(scene.source_end)) - scene.source_start + scene.start
            if end - start < 0.05:
                continue
            shifted.append(WordStamp(text=word.text, start=start, end=end, scene_id=scene.scene_id))
    return shifted


def _placeholder_words(scenes: list[Scene]) -> list[WordStamp]:
    from core.audio_aligner import estimate_words

    return estimate_words(scenes)


def _dump(path: Path, model) -> None:
    path.write_text(json.dumps(model.model_dump(), ensure_ascii=False, indent=2), encoding="utf-8")
