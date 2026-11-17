"""HTTP API and the local web page for creating videos."""

from __future__ import annotations

import hashlib
import json
import re
import threading
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from config.local_store import save_config, secret_hint
from config.providers import public_catalog, resolve_provider, voice_choices
from config.settings import get_settings, reset_settings
from core.audio_aligner import _speak
from core.control import Stopped, clear_stop, request_stop
from core.cover import make_cover
from core.intent import interpret_brief
from core.models import Storyboard
from core.pipeline import (
    PipelineError,
    finish_stop,
    run_create,
    run_highlight,
    run_produce,
    run_storyboard,
    save_storyboard_edits,
)
from core.workspace import delete_task, load_task

app = FastAPI(title="fastCut", version="0.1.0")
DIST = Path(__file__).resolve().parent / "web" / "dist"
PAGE = DIST / "index.html"
_assets = DIST / "assets"
if _assets.is_dir():
    app.mount("/assets", StaticFiles(directory=_assets), name="web-assets")
_TASK_ID = re.compile(r"^[a-f0-9]{8,32}$")
_busy = False
_active_id: str | None = None
_lock = threading.Lock()


class CreateBody(BaseModel):
    topic: str = Field(min_length=1)
    duration: float = 30
    aspect_ratio: str = "9:16"
    voice: str | None = None
    bgm: str | None = None
    quality: str = "draft"
    use_llm: bool = True
    align: str = "auto"
    visual_source: str = "auto"
    visual_style: str = ""
    audio: str = "auto"
    caption_size: int = 0


class AgentBody(BaseModel):
    brief: str = Field(min_length=1, max_length=2000)
    confirm: bool = False


class ConfigBody(BaseModel):
    llm_provider: str | None = None
    openai_base_url: str | None = None
    openai_model: str | None = None
    openai_api_key: str | None = None
    clear_openai_api_key: bool = False
    pexels_api_key: str | None = None
    clear_pexels_api_key: bool = False
    pixabay_api_key: str | None = None
    clear_pixabay_api_key: bool = False
    unsplash_access_key: str | None = None
    clear_unsplash_access_key: bool = False
    freesound_api_key: str | None = None
    clear_freesound_api_key: bool = False
    image_provider: str | None = None
    image_api_url: str | None = None
    image_model: str | None = None
    image_api_key: str | None = None
    clear_image_api_key: bool = False
    tts_provider: str | None = None
    tts_model: str | None = None
    tts_voice: str | None = None
    tts_api_key: str | None = None
    clear_tts_api_key: bool = False


def _check(duration: float, aspect: str, quality: str) -> None:
    if duration < 5 or duration > 180:
        raise HTTPException(400, "时长要在 5 到 180 秒之间")
    if aspect not in {"9:16", "16:9", "4:3"}:
        raise HTTPException(400, "比例只支持 9:16、16:9 或 4:3")
    if quality not in {"draft", "looks", "delivery"}:
        raise HTTPException(400, "quality 只支持 draft、looks、delivery")


def _visual_source(value: str) -> str:
    if value not in {"auto", "web", "sd", "video", "ai"}:
        raise HTTPException(400, "画面来源只支持自动、网络图片、AI 生图、本地绘图或视频素材")
    return value


def _visual_style(value: str) -> str:
    if value not in {"", "cinematic", "documentary", "bright"}:
        raise HTTPException(400, "画面风格只支持电影、纪实或明亮")
    return value


def _audio(value: str) -> str:
    if value not in {"auto", "freesound", "none"}:
        raise HTTPException(400, "配乐只支持自动、Freesound 或不用网络配乐")
    return value


def _caption_size(value: int) -> int:
    if value == 0:
        return 0
    if value < 48 or value > 140:
        raise HTTPException(400, "字幕大小要在 48 到 140 之间，0 表示默认")
    return value


def _task_or_404(task_id: str):
    if not _TASK_ID.match(task_id):
        raise HTTPException(404, "任务不存在")
    try:
        return load_task(task_id)
    except FileNotFoundError:
        raise HTTPException(404, "任务不存在") from None


def _acquire() -> None:
    global _busy
    with _lock:
        if _busy:
            raise HTTPException(409, "上一项还在处理，完成后再继续")
        _busy = True


def _bind(task_id: str) -> None:
    global _active_id
    with _lock:
        _active_id = task_id


def _release() -> None:
    global _busy, _active_id
    with _lock:
        _busy = False
        _active_id = None


def _is_live(task_id: str) -> bool:
    with _lock:
        return _busy and _active_id == task_id


def _guard(task_id: str, func, *args, **kwargs) -> None:
    kwargs["task_id"] = task_id
    kwargs["resume"] = True
    try:
        func(*args, **kwargs)
    except Stopped:
        return
    except (PipelineError, OSError) as exc:
        try:
            load_task(task_id).status(state="failed", error=str(exc))
        except OSError:
            return
    finally:
        clear_stop(task_id)
        _release()


def _summary(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    title = str(data.get("topic") or "")
    board = path.parent / "storyboard.json"
    if board.exists():
        try:
            title = str(json.loads(board.read_text(encoding="utf-8")).get("title") or title)
        except (OSError, json.JSONDecodeError):
            pass
    output = data.get("output")
    return {
        "task_id": data.get("task_id") or path.parent.name,
        "state": data.get("state") or "running",
        "step": data.get("step") or "",
        "progress": data.get("progress") or 0,
        "title": title,
        "topic": data.get("topic") or "",
        "error": data.get("error"),
        "notes": data.get("notes") or [],
        "updated_at": data.get("updated_at") or path.stat().st_mtime,
        "has_output": bool(output and Path(str(output)).exists()),
        "voice": data.get("voice") or "",
        "quality": data.get("quality") or "draft",
        "aspect_ratio": data.get("aspect_ratio") or "",
        "duration": data.get("duration"),
        "mode": data.get("mode") or "",
        "live": _is_live(str(data.get("task_id") or path.parent.name)),
        "cover_state": data.get("cover_state") or "",
        "cover_error": data.get("cover_error") or "",
        "has_cover": (path.parent / "cover.jpg").exists(),
        "visual_source": data.get("visual_source") or "auto",
        "visual_style": data.get("visual_style") or "",
        "audio": data.get("audio") or "auto",
        "caption_size": int(data.get("caption_size") or 0),
        "plan": data.get("plan") or None,
        "capcut_apps": data.get("capcut_apps") or [],
        "capcut_note": data.get("capcut_note") or "",
        "has_capcut": bool(data.get("capcut_zip") and Path(str(data.get("capcut_zip"))).exists()),
    }


@app.get("/")
def home() -> FileResponse:
    if not PAGE.is_file():
        raise HTTPException(503, "网页还没构建。在 frontend 目录执行 npm install 和 npm run build")
    return FileResponse(PAGE, headers={"Cache-Control": "no-cache"})


@app.get("/config")
def read_config() -> dict:
    settings = get_settings()
    llm = resolve_provider("llm", settings.llm_provider, settings.openai_base_url)
    speech = resolve_provider("tts", settings.tts_provider)
    image = resolve_provider("image", settings.image_provider, settings.image_api_url)
    tts_ready = (not speech.needs_key) or bool(settings.tts_api_key)
    return {
        "voice": settings.tts_voice,
        "voices": voice_choices(speech, settings.tts_voice),
        "llm_ready": settings.llm_ready,
        "llm_provider": llm.id,
        "tts_provider": speech.id,
        "tts_model": settings.tts_model,
        "tts_ready": tts_ready,
        "tts_api_key_hint": secret_hint(settings.tts_api_key),
        "sd_ready": settings.sd_webui_enable,
        "unsplash_ready": bool(settings.unsplash_keys),
        "freesound_ready": bool(settings.freesound_keys),
        "pexels_ready": bool(settings.pexels_keys),
        "pixabay_ready": bool(settings.pixabay_keys),
        "image_ready": bool(settings.image_api_key and settings.image_api_url) and image.id != "off",
        "image_provider": image.id,
        "openai_base_url": settings.openai_base_url,
        "openai_model": settings.openai_model,
        "openai_api_key_hint": secret_hint(settings.openai_api_key),
        "pexels_api_key_hint": secret_hint(settings.pexels_keys[0] if settings.pexels_keys else ""),
        "pixabay_api_key_hint": secret_hint(settings.pixabay_keys[0] if settings.pixabay_keys else ""),
        "unsplash_access_key_hint": secret_hint(settings.unsplash_keys[0] if settings.unsplash_keys else ""),
        "freesound_api_key_hint": secret_hint(settings.freesound_keys[0] if settings.freesound_keys else ""),
        "image_api_url": settings.image_api_url,
        "image_model": settings.image_model,
        "image_api_key_hint": secret_hint(settings.image_api_key),
        "providers": public_catalog(),
        "key_hints": {
            "llm": _hint_map(llm.id, settings.openai_api_key, settings.llm_keys),
            "tts": _hint_map(speech.id, settings.tts_api_key, settings.tts_keys),
            "image": _hint_map(image.id, settings.image_api_key, settings.image_keys),
        },
    }


def _hint_map(active_id: str, active_secret: str, saved: dict[str, str]) -> dict[str, str]:
    hints = {key: secret_hint(value) for key, value in saved.items() if secret_hint(value)}
    current = secret_hint(active_secret)
    if current and active_id and active_id not in hints:
        hints[active_id] = current
    return hints


@app.post("/config")
def write_config(body: ConfigBody) -> dict:
    try:
        save_config(body.model_dump())
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    reset_settings()
    return read_config()


@app.get("/tasks")
def list_tasks() -> dict:
    root = get_settings().workspace_dir
    items = []
    if root.exists():
        for path in root.glob("*/status.json"):
            item = _summary(path)
            if item and _TASK_ID.match(str(item["task_id"])):
                items.append(item)
    items.sort(key=lambda item: item.get("updated_at") or 0, reverse=True)
    return {"tasks": items[:20]}


@app.delete("/tasks/{task_id}")
def remove_task(task_id: str) -> dict:
    task = _task_or_404(task_id)
    if task.read_status().get("state") in {"running", "stopping"} and _is_live(task_id):
        raise HTTPException(409, "这条片子还在处理，先停止再删除")
    clear_stop(task_id)
    try:
        delete_task(task_id)
    except FileNotFoundError:
        raise HTTPException(404, "任务不存在") from None
    except OSError as exc:
        raise HTTPException(409, f"文件正在使用，稍后再删：{exc.__class__.__name__}") from exc
    return {"task_id": task_id}


@app.post("/tasks/draft")
def create_draft(body: CreateBody, background: BackgroundTasks) -> dict:
    _check(body.duration, body.aspect_ratio, body.quality)
    source = _visual_source(body.visual_source)
    style = _visual_style(body.visual_style)
    audio = _audio(body.audio)
    caption_size = _caption_size(body.caption_size)
    from core.workspace import create_task as new_task

    _acquire()
    try:
        task = new_task()
        _bind(task.id)
        task.status(
            topic=body.topic.strip(),
            duration=body.duration,
            aspect_ratio=body.aspect_ratio,
            quality=body.quality,
            voice=body.voice or "",
            visual_source=source,
            visual_style=style,
            audio=audio,
            caption_size=caption_size,
        )
    except Exception:
        _release()
        raise
    background.add_task(
        _guard,
        task.id,
        run_storyboard,
        body.topic.strip(),
        body.duration,
        body.aspect_ratio,
        use_llm=body.use_llm,
    )
    return {"task_id": task.id}


class SceneEdit(BaseModel):
    narration: str = Field(min_length=1)
    search_keywords: list[str] = Field(default_factory=list)
    visual_style: str = "cinematic"


class ProduceBody(BaseModel):
    title: str = Field(min_length=1)
    scenes: list[SceneEdit] = Field(min_length=1)
    voice: str | None = None
    quality: str = "draft"
    align: str = "auto"
    visual_source: str = ""
    audio: str = ""
    caption_size: int = 0


class PreviewBody(BaseModel):
    voice: str = Field(min_length=1)
    text: str = ""


@app.post("/voices/preview")
def preview_voice(body: PreviewBody) -> FileResponse:
    settings = get_settings()
    text = " ".join(body.text.split())[:80] or "你好，这是当前音色的试听。"
    voice = body.voice.strip()
    provider = resolve_provider("tts", settings.tts_provider)
    if provider.needs_key and not settings.tts_api_key:
        raise HTTPException(400, f"先在密钥设置里填写{provider.name}的密钥")
    allowed = {item[0] for item in provider.voices}
    if settings.tts_voice:
        allowed.add(settings.tts_voice)
    if voice not in allowed:
        raise HTTPException(400, "这个音色还不支持试听")
    digest = hashlib.sha256(f"{voice}|{text}".encode("utf-8")).hexdigest()[:16]
    cache = settings.workspace_dir / "_cache" / "voices"
    cache.mkdir(parents=True, exist_ok=True)
    dest = cache / f"{digest}.mp3"
    if not dest.exists() or dest.stat().st_size < 200:
        try:
            _speak(text, voice, dest, settings=settings)
        except Exception as exc:
            raise HTTPException(502, f"试听失败：{exc.__class__.__name__}") from exc
    if not dest.exists():
        raise HTTPException(502, "试听没有生成声音")
    return FileResponse(dest, media_type="audio/mpeg")


@app.post("/tasks/{task_id}/produce")
def produce_task(task_id: str, body: ProduceBody, background: BackgroundTasks) -> dict:
    _check(30, "9:16", body.quality)
    if body.align not in {"auto", "estimate", "faster-whisper", "hyperframes"}:
        raise HTTPException(400, "align 不受支持")
    source = _visual_source(body.visual_source) if body.visual_source else ""
    audio = _audio(body.audio) if body.audio else ""
    caption_size = _caption_size(body.caption_size)
    task = _task_or_404(task_id)
    status = task.read_status()
    if status.get("state") in {"running", "stopping"} and _is_live(task_id):
        raise HTTPException(409, "这条片子正在处理")
    if status.get("state") not in {"review", "failed"}:
        raise HTTPException(409, "分镜确认后才能开始制作")
    try:
        save_storyboard_edits(task, body.title, [item.model_dump() for item in body.scenes])
    except PipelineError as exc:
        raise HTTPException(400, str(exc)) from exc
    _acquire()
    _bind(task.id)
    fields: dict = {"voice": body.voice or "", "quality": body.quality, "state": "running", "step": "voice", "progress": 0.22, "error": None}
    if source:
        fields["visual_source"] = source
    if audio:
        fields["audio"] = audio
    fields["caption_size"] = caption_size
    task.status(**fields)
    background.add_task(
        _guard,
        task.id,
        run_produce,
        voice=body.voice or None,
        quality=body.quality,
        align=body.align,
    )
    return {"task_id": task.id}


def _render_cover(task_id: str) -> None:
    try:
        task = load_task(task_id)
        board = Storyboard.model_validate_json(task.path("storyboard.json").read_text(encoding="utf-8"))
        note = make_cover(task, board)
        task.status(cover_state="ready", cover_error=note or None)
    except Exception as exc:
        try:
            load_task(task_id).status(cover_state="failed", cover_error=str(exc))
        except OSError:
            return
    finally:
        _release()


@app.post("/tasks/{task_id}/stop")
def stop_task(task_id: str) -> dict:
    task = _task_or_404(task_id)
    status = task.read_status()
    if status.get("state") not in {"running", "stopping"}:
        raise HTTPException(409, "现在没有正在生成的步骤")
    if not _is_live(task_id):
        finish_stop(task, interrupted=True)
        return {"task_id": task_id, "state": task.read_status().get("state")}
    request_stop(task_id)
    task.status(state="stopping", error=None)
    task.log("正在停止，当前这一步结束后回到分镜")
    return {"task_id": task_id, "state": "stopping"}


@app.post("/tasks/{task_id}/reopen")
def reopen_task(task_id: str) -> dict:
    task = _task_or_404(task_id)
    status = task.read_status()
    if status.get("state") in {"running", "stopping"} and _is_live(task_id):
        raise HTTPException(409, "还在生成，先停止再退回分镜")
    if not task.path("storyboard.json").exists():
        raise HTTPException(409, "没有可退回的分镜")
    if status.get("mode") == "highlight":
        raise HTTPException(409, "高光成片不能退回成分镜重做")
    clear_stop(task_id)
    task.status(state="review", step="review", progress=0.2, error=None)
    task.log("已退回分镜，可以用当前文案重新生成")
    return {"task_id": task_id, "state": "review"}


@app.post("/tasks/{task_id}/cover")
def create_cover(task_id: str, background: BackgroundTasks) -> dict:
    task = _task_or_404(task_id)
    if not task.path("storyboard.json").exists():
        raise HTTPException(409, "还没有分镜，生成不了封面")
    status = task.read_status()
    if status.get("state") == "running" and _is_live(task_id):
        raise HTTPException(409, "片子正在制作，完成后再生成封面")
    _acquire()
    task.status(cover_state="running", cover_error=None)
    background.add_task(_render_cover, task.id)
    return {"task_id": task.id, "cover_state": "running"}


@app.get("/tasks/{task_id}/cover")
def read_cover(task_id: str) -> FileResponse:
    path = _task_or_404(task_id).root / "cover.jpg"
    if not path.exists():
        raise HTTPException(404, "封面还没生成")
    return FileResponse(path, media_type="image/jpeg")


@app.post("/tasks/agent")
def create_from_brief(body: AgentBody, background: BackgroundTasks) -> dict:
    brief = body.brief.strip()
    if not brief:
        raise HTTPException(400, "先写你想要的片子")
    plan = interpret_brief(brief)
    if plan["mode"] == "highlight":
        raise HTTPException(400, "切高光需要上传原片，请改用「高光切片」")
    if len(str(plan["topic"])) < 2:
        raise HTTPException(400, "没有识别出主题，试着写清楚是关于什么的")
    _check(float(plan["duration"]), plan["aspect_ratio"], plan["quality"])
    _visual_source(plan["visual_source"])
    _visual_style(plan["visual_style"])
    _caption_size(int(plan.get("caption_size") or 0))
    from core.workspace import create_task as new_task

    _acquire()
    try:
        task = new_task()
        _bind(task.id)
        task.status(
            topic=plan["topic"],
            duration=plan["duration"],
            aspect_ratio=plan["aspect_ratio"],
            quality=plan["quality"],
            voice=plan["voice"] or "",
            visual_source=plan["visual_source"],
            visual_style=plan["visual_style"],
            audio=plan.get("audio") or "auto",
            caption_size=int(plan.get("caption_size") or 0),
            plan=plan,
            brief=brief,
        )
        task.log(f"已理解：{plan['summary']}")
    except Exception:
        _release()
        raise
    if body.confirm:
        background.add_task(
            _guard,
            task.id,
            run_storyboard,
            plan["topic"],
            plan["duration"],
            plan["aspect_ratio"],
            use_llm=plan["use_llm"],
        )
    else:
        background.add_task(
            _guard,
            task.id,
            run_create,
            plan["topic"],
            plan["duration"],
            plan["aspect_ratio"],
            voice=plan["voice"],
            quality=plan["quality"],
            use_llm=plan["use_llm"],
            align="auto",
        )
    return {"task_id": task.id, "plan": plan}


@app.post("/tasks")
def create_task(body: CreateBody, background: BackgroundTasks) -> dict:
    _check(body.duration, body.aspect_ratio, body.quality)
    source = _visual_source(body.visual_source)
    style = _visual_style(body.visual_style)
    audio = _audio(body.audio)
    caption_size = _caption_size(body.caption_size)
    if body.align not in {"auto", "estimate", "faster-whisper", "hyperframes"}:
        raise HTTPException(400, "align 不受支持")
    from core.workspace import create_task as new_task

    _acquire()
    try:
        task = new_task()
        _bind(task.id)
        task.status(
            topic=body.topic.strip(),
            duration=body.duration,
            aspect_ratio=body.aspect_ratio,
            quality=body.quality,
            visual_source=source,
            visual_style=style,
            audio=audio,
            caption_size=caption_size,
            voice=body.voice or "",
        )
    except Exception:
        _release()
        raise
    background.add_task(
        _guard,
        task.id,
        run_create,
        body.topic.strip(),
        body.duration,
        body.aspect_ratio,
        voice=body.voice or None,
        bgm=body.bgm,
        quality=body.quality,
        use_llm=body.use_llm,
        align=body.align,
    )
    return {"task_id": task.id}


@app.post("/tasks/highlight")
async def create_highlight(
    background: BackgroundTasks,
    video: UploadFile = File(...),
    duration: float = Form(30),
    aspect_ratio: str = Form("9:16"),
    quality: str = Form("draft"),
    use_llm: bool = Form(True),
) -> dict:
    _check(duration, aspect_ratio, quality)
    from core.workspace import create_task as new_task

    _acquire()
    try:
        task = new_task()
        _bind(task.id)
        dest = task.path("input", Path(video.filename or "source.mp4").name)
        dest.write_bytes(await video.read())
        task.status(topic=Path(video.filename or "source.mp4").stem, duration=duration, aspect_ratio=aspect_ratio)
    except Exception:
        _release()
        raise
    background.add_task(
        _guard,
        task.id,
        run_highlight,
        str(dest),
        duration,
        aspect_ratio,
        quality=quality,
        use_llm=use_llm,
    )
    return {"task_id": task.id}


@app.get("/tasks/{task_id}")
def read_task(task_id: str) -> dict:
    root = _task_or_404(task_id).root
    summary = _summary(root / "status.json")
    if summary is None:
        raise HTTPException(404, "任务不存在")
    board = root / "storyboard.json"
    if board.exists():
        try:
            summary["storyboard"] = json.loads(board.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            summary["storyboard"] = None
    return summary


@app.get("/tasks/{task_id}/log")
def read_log(task_id: str) -> dict:
    path = _task_or_404(task_id).root / "pipeline.log"
    if not path.exists():
        return {"lines": []}
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return {"lines": lines[-200:]}


@app.get("/tasks/{task_id}/download")
def download_task(task_id: str, download: int = 0) -> FileResponse:
    status = _task_or_404(task_id).read_status()
    output = status.get("output")
    if status.get("state") != "done" or not output or not Path(output).exists():
        raise HTTPException(409, "成片还没准备好")
    return FileResponse(
        output,
        media_type="video/mp4",
        filename=f"{task_id}.mp4",
        content_disposition_type="attachment" if download else "inline",
    )


@app.get("/tasks/{task_id}/capcut")
def download_capcut(task_id: str) -> FileResponse:
    task = _task_or_404(task_id)
    status = task.read_status()
    if status.get("state") != "done":
        raise HTTPException(409, "成片还没准备好")
    timeline_path = task.root / "timeline.json"
    if not timeline_path.exists():
        raise HTTPException(409, "这支片子没有时间轴，没法生成剪映草稿")
    from core.capcut import CapcutError, export_capcut
    from core.models import ProjectTimeline

    try:
        timeline = ProjectTimeline.model_validate_json(timeline_path.read_text(encoding="utf-8"))
        exported = export_capcut(task, timeline)
    except (CapcutError, OSError, json.JSONDecodeError) as exc:
        raise HTTPException(409, str(exc)) from exc
    task.status(
        state="done",
        capcut=str(exported.folder),
        capcut_zip=str(exported.zip_path),
        capcut_apps=exported.installed,
        capcut_note=exported.note,
    )
    return FileResponse(
        exported.zip_path,
        media_type="application/zip",
        filename=f"fastcut-{task_id}-jianying.zip",
        content_disposition_type="attachment",
    )


def recover_orphans() -> None:
    root = get_settings().workspace_dir
    if not root.is_dir():
        return
    for path in root.glob("*/status.json"):
        task_id = path.parent.name
        if not _TASK_ID.match(task_id) or _is_live(task_id):
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if data.get("state") not in {"running", "stopping"}:
            continue
        try:
            finish_stop(load_task(task_id), interrupted=True)
        except OSError:
            continue


recover_orphans()
