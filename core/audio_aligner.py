"""Voice synthesis and word-level alignment."""

from __future__ import annotations

import json
import shutil
import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path

from config.providers import resolve_provider
from config.settings import Settings, get_settings
from core.control import Stopped, active_task, checkpoint, run_process, should_stop
from core.ffmpeg_engine import concat_audio, pad_audio, probe_duration, silence
from core.models import Scene, WordStamp
from core.text import assign_scenes, explode_cjk_words, split_phrases, words_from_transcript

LogFn = Callable[[str], None]


def _speak_once(text: str, voice: str, dest: Path, settings: Settings | None = None, *, force_edge: bool = False) -> None:
    from core.speech import synthesize_speech

    synthesize_speech(text, voice, dest, settings=settings, force_edge=force_edge)


def _speak(
    text: str,
    voice: str,
    dest: Path,
    *,
    attempts: int = 2,
    settings: Settings | None = None,
    force_edge: bool = False,
) -> None:
    last_error: Exception | None = None
    for attempt in range(max(1, attempts)):
        dest.unlink(missing_ok=True)
        try:
            _speak_once(text, voice, dest, settings, force_edge=force_edge)
            if dest.exists() and dest.stat().st_size >= 200:
                return
            last_error = RuntimeError("配音文件为空")
        except Exception as exc:
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(0.8)
    if last_error is None:
        raise RuntimeError("配音文件为空")
    raise last_error


def _run_until_stop(task_id: str | None, func) -> None:
    if task_id:
        checkpoint(task_id)
    error: list[BaseException] = []

    def work() -> None:
        try:
            func()
        except BaseException as exc:
            error.append(exc)

    thread = threading.Thread(target=work, daemon=True)
    thread.start()
    while thread.is_alive():
        thread.join(0.4)
        if task_id and should_stop(task_id):
            checkpoint(task_id)
    if error:
        raise error[0]


def _align_reason(name: str, exc: Exception) -> str:
    if isinstance(exc, ModuleNotFoundError):
        return f"未安装 {name}，字幕时间改为按字数估算"
    if isinstance(exc, subprocess.TimeoutExpired):
        return f"{name} 对齐超时，已改用按字数估算"
    text = " ".join(str(exc).split())
    if text:
        return f"{name} 对齐不可用：{text[:180]}"
    return f"{name} 对齐不可用：{exc.__class__.__name__}"


def _voice_failure(exc: Exception) -> str:
    if exc.__class__.__name__ == "NoAudioReceived":
        return "微软在线配音没有返回声音（NoAudioReceived），多半是访问 speech.platform.bing.com 被拦截或限流"
    detail = str(exc).strip()
    return detail or exc.__class__.__name__


def synthesize_scenes(
    scenes: list[Scene],
    audio_dir: Path,
    *,
    voice: str | None = None,
    settings: Settings | None = None,
    log: LogFn | None = None,
    task_id: str | None = None,
) -> tuple[list[Scene], Path, list[str]]:
    cfg = settings or get_settings()
    provider = resolve_provider("tts", cfg.tts_provider)
    chosen_voice = voice or cfg.tts_voice or (provider.voices[0][0] if provider.voices else "zh-CN-XiaoxiaoNeural")
    force_edge = False
    notes: list[str] = []
    if provider.needs_key and not cfg.tts_api_key:
        force_edge = True
        if not chosen_voice.endswith("Neural"):
            chosen_voice = "zh-CN-XiaoxiaoNeural"
        if log:
            log(f"未填写{provider.name}密钥，改用微软免费配音")
        notes.append(f"未填写{provider.name}密钥，已改用微软免费配音")
    elif log:
        log(f"配音：{provider.name}")
    audio_dir.mkdir(parents=True, exist_ok=True)
    updated: list[Scene] = []
    pieces: list[Path] = []
    no_audio = 0
    give_up = False
    explained = False
    for scene in scenes:
        if task_id:
            checkpoint(task_id)
        dest = audio_dir / f"scene_{scene.scene_id:03d}.mp3"
        if give_up:
            duration = max(3.0, len(scene.narration) / 4.2)
            dest = dest.with_suffix(".wav")
            silence(dest, duration, cfg)
        else:
            try:
                _run_until_stop(
                    task_id,
                    lambda narration=scene.narration, dest=dest, attempts=2 if no_audio == 0 else 1: _speak(
                        narration, chosen_voice, dest, attempts=attempts, settings=cfg, force_edge=force_edge
                    ),
                )
            except Stopped:
                raise
            except Exception as exc:
                if exc.__class__.__name__ == "NoAudioReceived":
                    no_audio += 1
                if log:
                    log(f"分镜 {scene.scene_id} 配音失败，改用静音：{_voice_failure(exc)}")
                if not explained:
                    notes.append("部分或全部分镜配音失败，已用静音占位")
                    explained = True
                if no_audio >= 2:
                    give_up = True
                    if log:
                        log("配音连续没有返回声音，后面的分镜不再重试，先用静音")
                duration = max(3.0, len(scene.narration) / 4.2)
                dest = dest.with_suffix(".wav")
                silence(dest, duration, cfg)
        duration = probe_duration(dest, cfg)
        pieces.append(dest)
        updated.append(scene.model_copy(update={"audio_path": str(dest), "actual_duration": duration}))
    narration = audio_dir / "narration.wav"
    concat_audio(pieces, narration, cfg)
    total = probe_duration(narration, cfg)
    if total < 3:
        padded = audio_dir / "narration_padded.wav"
        pad_audio(narration, padded, 3.0, cfg)
        narration = padded
        total = probe_duration(narration, cfg)
        notes.append("成片短于 3 秒，已补静音")
    raw = sum(scene.actual_duration or 0 for scene in updated) or total
    scale = total / raw if raw else 1
    cursor = 0.0
    timed: list[Scene] = []
    for scene in updated:
        length = (scene.actual_duration or 0) * scale
        timed.append(scene.model_copy(update={"start": cursor, "end": cursor + length, "actual_duration": length}))
        cursor += length
    return timed, narration, notes


def estimate_words(scenes: list[Scene]) -> list[WordStamp]:
    words: list[WordStamp] = []
    for scene in scenes:
        if scene.start is None or scene.end is None:
            continue
        phrases = split_phrases(scene.narration)
        if not phrases:
            continue
        span = max(0.2, scene.end - scene.start)
        weight = sum(max(1, len(phrase)) for phrase in phrases)
        cursor = scene.start
        for phrase in phrases:
            piece = span * max(1, len(phrase)) / weight
            words.append(WordStamp(text=phrase, start=cursor, end=cursor + piece, scene_id=scene.scene_id))
            cursor += piece
    return words


def _faster_whisper(path: Path, language: str) -> list[WordStamp]:
    from faster_whisper import WhisperModel

    model = WhisperModel("small", device="cpu", compute_type="int8")
    segments, _info = model.transcribe(str(path), word_timestamps=True, language=language)
    words: list[WordStamp] = []
    for segment in segments:
        if segment.words:
            for word in segment.words:
                text = (word.word or "").strip()
                if text:
                    words.append(WordStamp(text=text, start=float(word.start), end=float(word.end)))
        elif (segment.text or "").strip():
            words.append(WordStamp(text=segment.text.strip(), start=float(segment.start), end=float(segment.end)))
    return explode_cjk_words(words)


def _hyperframes_transcribe(path: Path, workdir: Path, language: str) -> list[WordStamp]:
    npx = shutil.which("npx")
    if not npx:
        raise RuntimeError("找不到 npx")
    completed = run_process(
        [npx, "--yes", "hyperframes", "transcribe", str(path), "--model", "small", "--language", language, "--json"],
        cwd=str(workdir),
        timeout=240,
    )
    if completed.returncode != 0:
        raise RuntimeError((completed.stderr or completed.stdout)[-800:])
    transcript = workdir / "assets" / "transcript.json"
    if not transcript.exists():
        transcript = workdir / "transcript.json"
    if transcript.exists():
        return words_from_transcript(json.loads(transcript.read_text(encoding="utf-8")))
    text = completed.stdout.strip()
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        raise RuntimeError("转写没有返回 JSON")
    return words_from_transcript(json.loads(text[start : end + 1]))


def align_words(
    narration: Path,
    scenes: list[Scene],
    workdir: Path,
    *,
    method: str = "auto",
    language: str = "zh",
    log: LogFn | None = None,
) -> tuple[list[WordStamp], str]:
    order = ["faster-whisper", "hyperframes"] if method == "auto" else [method]
    if method == "estimate":
        order = []
    task_id = active_task()
    for name in order:
        if task_id:
            checkpoint(task_id)
        try:
            if name == "faster-whisper":
                found: list[list[WordStamp]] = []

                def transcribe() -> None:
                    found.append(_faster_whisper(narration, language))

                _run_until_stop(task_id or None, transcribe)
                words = found[0] if found else []
            elif name == "hyperframes":
                words = _hyperframes_transcribe(narration, workdir, language)
            else:
                continue
            if words:
                if log:
                    log(f"字级时间戳来自 {name}，共 {len(words)} 个")
                return assign_scenes(words, scenes), name
        except Stopped:
            raise
        except Exception as exc:
            if log:
                log(_align_reason(name, exc))
    if log:
        log("按字数把旁白摊到每镜时长上")
    return estimate_words(scenes), "estimate"
