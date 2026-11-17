"""Write a Jianying / CapCut draft so a finished video can be edited there.

The draft is the plaintext 5.9 project format (draft_content.json). Jianying 6+
encrypts drafts it saves itself; that only blocks reading those drafts back.
A plaintext draft generated here still opens in current Jianying and CapCut.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
import uuid
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from core.models import ProjectTimeline, Scene
from core.text import caption_line_limit, strip_brand
from core.visual_renderer import _caption_cues
from core.workspace import Task

_US = 1_000_000
_FOLDER = re.compile(r"[^A-Za-z0-9_-]")
_DRAFT_DIR = re.compile(r"[A-Za-z]:(?:\\\\|\\).*?com\.lveditor\.draft")


class CapcutError(RuntimeError):
    pass


@dataclass
class CapcutExport:
    folder: Path
    zip_path: Path
    name: str
    installed: list[str] = field(default_factory=list)
    note: str = ""


def export_capcut(
    task: Task,
    timeline: ProjectTimeline,
    *,
    roots: list[tuple[Path, str]] | None = None,
) -> CapcutExport:
    """Build an editable draft and, when the apps are installed, register it."""
    clips = _clips(task, timeline)
    if not clips:
        raise CapcutError("没有可以放进剪映的画面")
    voice = _audio(task, timeline)
    bed = task.path("audio", "bgm.wav")
    bed_path = bed if bed.exists() and bed.stat().st_size > 0 else None
    name = strip_brand(timeline.storyboard.title) or "fastCut"
    folder_name = _folder_name(task.id)
    draft_id = str(uuid.uuid4()).upper()
    cues = _cues(timeline)
    workspace = task.path("capcut", folder_name)
    duration = _write_draft(
        workspace,
        clips=clips,
        voice=voice,
        bed=bed_path,
        cues=cues,
        title=name,
        timeline=timeline,
        draft_id=draft_id,
    )
    installed: list[str] = []
    registered: list[str] = []
    blocked: list[str] = []
    targets = discover_draft_roots() if roots is None else roots
    for root, label in targets:
        if not root.is_dir():
            continue
        destination = root / folder_name
        try:
            _write_draft(
                destination,
                clips=clips,
                voice=voice,
                bed=bed_path,
                cues=cues,
                title=name,
                timeline=timeline,
                draft_id=draft_id,
            )
        except OSError as exc:
            blocked.append(f"{label}（{exc}）")
            continue
        installed.append(label)
        if _register(root, destination, draft_id, name, duration):
            registered.append(label)
        else:
            blocked.append(label)
    zip_path = task.path("capcut", f"{folder_name}.zip")
    _zip_folder(workspace, zip_path)
    running = _running_apps(installed)
    note = _note(name, installed, registered, blocked, running)
    return CapcutExport(workspace, zip_path, name, installed, note)


def discover_draft_roots() -> list[tuple[Path, str]]:
    """Draft folders for Jianying and CapCut that already exist on this machine."""
    local = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
    found: list[tuple[Path, str]] = []
    seen: set[str] = set()
    for app_dir, label in ((local / "JianyingPro", "剪映"), (local / "CapCut", "CapCut")):
        for candidate in (
            app_dir / "User Data" / "Projects" / "com.lveditor.draft",
            configured_draft_root(app_dir),
        ):
            if candidate is None or not candidate.is_dir():
                continue
            key = str(candidate.resolve()).lower()
            if key in seen:
                continue
            seen.add(key)
            found.append((candidate.resolve(), label))
    return found


def configured_draft_root(app_dir: Path) -> Path | None:
    """Read a custom draft location from the app config, when it is plaintext."""
    config = app_dir / "User Data" / "Config" / "globalSetting"
    if not config.is_file() or config.stat().st_size > 2_000_000:
        return None
    text = config.read_text(encoding="utf-8", errors="ignore")
    match = _DRAFT_DIR.search(text)
    if not match:
        return None
    raw = match.group(0).replace("\\\\", "\\")
    path = Path(raw)
    return path if path.is_dir() else None


def _clips(task: Task, timeline: ProjectTimeline) -> list[tuple[Path, float, str]]:
    clips: list[tuple[Path, float, str]] = []
    complete = True
    for scene in timeline.storyboard.scenes:
        length = _scene_length(scene)
        path = _scene_file(task, scene.scene_id)
        if length < 0.2 or path is None:
            complete = False
            break
        clips.append((path, length, f"分镜 {scene.scene_id}"))
    if complete and clips:
        return clips
    background = task.path("build", "background.mp4")
    if background.exists() and timeline.total_duration >= 0.2:
        return [(background, timeline.total_duration, "画面")]
    return clips


def _scene_length(scene: Scene) -> float:
    if scene.start is not None and scene.end is not None:
        return max(0.0, scene.end - scene.start)
    return max(0.0, float(scene.actual_duration or 0))


def _scene_file(task: Task, scene_id: int) -> Path | None:
    for name in (f"scene_{scene_id:03d}.mp4", f"cut_{scene_id:03d}.mp4"):
        path = task.path("build", name)
        if path.exists() and path.stat().st_size > 0:
            return path
    return None


def _audio(task: Task, timeline: ProjectTimeline) -> Path | None:
    voice = task.path("audio", "voice.wav")
    if voice.exists() and voice.stat().st_size > 0:
        return voice
    narration = Path(timeline.narration_path)
    if narration.exists() and narration.stat().st_size > 0:
        return narration
    return None


def _cues(timeline: ProjectTimeline) -> list[tuple[float, float, str]]:
    per_line = caption_line_limit(timeline.width)
    cues: list[tuple[float, float, str]] = []
    for start, duration, lines, _group in _caption_cues(timeline, per_line):
        text = "\n".join(line.strip() for line in lines if line.strip())
        if text and duration >= 0.2:
            cues.append((start, duration, text))
    return cues


def _write_draft(
    folder: Path,
    *,
    clips: list[tuple[Path, float, str]],
    voice: Path | None,
    bed: Path | None,
    cues: list[tuple[float, float, str]],
    title: str,
    timeline: ProjectTimeline,
    draft_id: str,
) -> int:
    if folder.exists():
        shutil.rmtree(folder)
    materials = folder / "materials"
    materials.mkdir(parents=True)
    videos: list[dict] = []
    audios: list[dict] = []
    texts: list[dict] = []
    speeds: list[dict] = []
    video_segments: list[dict] = []
    audio_segments: list[tuple[str, list[dict]]] = []
    text_segments: list[dict] = []
    cursor = 0
    for index, (source, length, label) in enumerate(clips, start=1):
        duration = _us(length)
        copied = _copy_media(source, materials / f"v{index:03d}{source.suffix.lower() or '.mp4'}")
        material, segment, speed = _video_segment(copied, label, timeline.width, timeline.height, cursor, duration)
        videos.append(material)
        speeds.append(speed)
        video_segments.append(segment)
        cursor += duration
    total = max(cursor, _us(timeline.total_duration))
    if voice is not None:
        copied = _copy_media(voice, materials / f"voice{voice.suffix.lower() or '.wav'}")
        material, segment, speed = _audio_segment(copied, "旁白", 0, total, 1.0)
        audios.append(material)
        speeds.append(speed)
        audio_segments.append(("旁白", [segment]))
    if bed is not None:
        copied = _copy_media(bed, materials / f"bgm{bed.suffix.lower() or '.wav'}")
        material, segment, speed = _audio_segment(copied, "配乐", 0, total, 0.2)
        audios.append(material)
        speeds.append(speed)
        audio_segments.append(("配乐", [segment]))
    title_text = strip_brand(title).strip()
    if title_text:
        hold = min(total, max(_us(1.6), min(_us(3.2), total)))
        material, segment = _text_segment(title_text, 0, hold, size=11.0, y=0.42)
        texts.append(material)
        text_segments.append(segment)
    caption_y = -0.72 if timeline.height > timeline.width else -0.68
    for start, duration, text in cues:
        start_us = min(_us(start), max(0, total - _us(0.3)))
        duration_us = min(_us(duration), total - start_us)
        if duration_us < _us(0.2):
            continue
        material, segment = _text_segment(text, start_us, duration_us, size=8.0, y=caption_y)
        texts.append(material)
        text_segments.append(segment)
    ratio = timeline.storyboard.aspect_ratio if timeline.storyboard.aspect_ratio in {"9:16", "16:9", "4:3"} else "original"
    content = _content(
        draft_id=draft_id,
        name=title,
        width=timeline.width,
        height=timeline.height,
        ratio=ratio,
        duration=total,
        videos=videos,
        audios=audios,
        texts=texts,
        speeds=speeds,
        tracks=_tracks(video_segments, audio_segments, text_segments),
    )
    _write_json(folder / "draft_content.json", content)
    _write_json(folder / "draft_info.json", content)
    now = int(time.time() * _US)
    _write_json(folder / "draft_meta_info.json", _meta(draft_id, title, folder, folder.parent, total, now))
    (folder / "draft_settings").write_text(_settings(), encoding="utf-8")
    return total


def _content(
    *,
    draft_id: str,
    name: str,
    width: int,
    height: int,
    ratio: str,
    duration: int,
    videos: list[dict],
    audios: list[dict],
    texts: list[dict],
    speeds: list[dict],
    tracks: list[dict],
) -> dict:
    now = int(time.time())
    materials = {key: [] for key in _MATERIAL_KEYS}
    materials["videos"] = videos
    materials["audios"] = audios
    materials["texts"] = texts
    materials["speeds"] = speeds
    return {
        "canvas_config": {"height": height, "ratio": ratio, "width": width},
        "color_space": 0,
        "config": {
            "adjust_max_index": 1,
            "attachment_info": [],
            "combination_max_index": 1,
            "export_range": None,
            "extract_audio_last_index": 1,
            "lyrics_recognition_id": "",
            "lyrics_sync": True,
            "lyrics_taskinfo": [],
            "maintrack_adsorb": True,
            "material_save_mode": 0,
            "multi_language_current": "none",
            "multi_language_list": [],
            "multi_language_main": "none",
            "multi_language_mode": "none",
            "original_sound_last_index": 1,
            "record_audio_last_index": 1,
            "sticker_max_index": 1,
            "subtitle_keywords_config": None,
            "subtitle_recognition_id": "",
            "subtitle_sync": True,
            "subtitle_taskinfo": [],
            "system_font_list": [],
            "video_mute": False,
            "zoom_info_params": None,
        },
        "cover": None,
        "create_time": now,
        "duration": duration,
        "extra_info": None,
        "fps": 30.0,
        "free_render_index_mode_on": False,
        "group_container": None,
        "id": draft_id,
        "keyframe_graph_list": [],
        "keyframes": {
            "adjusts": [],
            "audios": [],
            "effects": [],
            "filters": [],
            "handwrites": [],
            "stickers": [],
            "texts": [],
            "videos": [],
        },
        "last_modified_platform": _platform(),
        "platform": _platform(),
        "materials": materials,
        "mutable_config": None,
        "name": name,
        "new_version": "110.0.0",
        "relationships": [],
        "render_index_track_mode_on": False,
        "retouch_cover": None,
        "source": "default",
        "static_cover_image_path": "",
        "time_marks": None,
        "tracks": tracks,
        "update_time": now,
        "version": 360000,
    }


_MATERIAL_KEYS = (
    "ai_translates",
    "audio_balances",
    "audio_effects",
    "audio_fades",
    "audio_track_indexes",
    "audios",
    "beats",
    "canvases",
    "chromas",
    "color_curves",
    "digital_humans",
    "drafts",
    "effects",
    "flowers",
    "green_screens",
    "handwrites",
    "hsl",
    "images",
    "log_color_wheels",
    "loudnesses",
    "manual_deformations",
    "masks",
    "material_animations",
    "material_colors",
    "multi_language_refs",
    "placeholders",
    "plugin_effects",
    "primary_color_wheels",
    "realtime_denoises",
    "shapes",
    "smart_crops",
    "smart_relights",
    "sound_channel_mappings",
    "speeds",
    "stickers",
    "tail_leaders",
    "text_templates",
    "texts",
    "time_marks",
    "transitions",
    "video_effects",
    "video_trackings",
    "videos",
    "vocal_beautifys",
    "vocal_separations",
)


def _platform() -> dict:
    return {"app_id": 3704, "app_source": "lv", "app_version": "5.9.0", "os": "windows"}


def _video_segment(
    path: Path,
    label: str,
    width: int,
    height: int,
    start: int,
    duration: int,
) -> tuple[dict, dict, dict]:
    material_id = _hid()
    speed_id = _hid()
    material = {
        "audio_fade": None,
        "category_id": "",
        "category_name": "local",
        "check_flag": 63487,
        "crop": {
            "upper_left_x": 0.0,
            "upper_left_y": 0.0,
            "upper_right_x": 1.0,
            "upper_right_y": 0.0,
            "lower_left_x": 0.0,
            "lower_left_y": 1.0,
            "lower_right_x": 1.0,
            "lower_right_y": 1.0,
        },
        "crop_ratio": "free",
        "crop_scale": 1.0,
        "duration": duration,
        "height": height,
        "id": material_id,
        "local_material_id": "",
        "material_id": "",
        "material_name": label,
        "media_path": "",
        "path": str(path),
        "type": "video",
        "width": width,
    }
    segment = _visual_segment(material_id, start, duration, [speed_id], volume=0.0, render_index=0)
    return material, segment, {"curve_speed": None, "id": speed_id, "mode": 0, "speed": 1.0, "type": "speed"}


def _audio_segment(path: Path, label: str, start: int, duration: int, volume: float) -> tuple[dict, dict, dict]:
    material_id = _hid()
    speed_id = _hid()
    material = {
        "app_id": 0,
        "category_id": "",
        "category_name": "local",
        "check_flag": 1,
        "duration": duration,
        "id": material_id,
        "local_material_id": "",
        "music_id": "",
        "name": label,
        "path": str(path),
        "type": "extract_music",
        "wave_points": [],
    }
    segment = _visual_segment(material_id, start, duration, [speed_id], volume=volume, render_index=0)
    segment["clip"] = None
    segment["hdr_settings"] = None
    return material, segment, {"curve_speed": None, "id": speed_id, "mode": 0, "speed": 1.0, "type": "speed"}


def _text_segment(text: str, start: int, duration: int, *, size: float, y: float) -> tuple[dict, dict]:
    material_id = _hid()
    content = {
        "styles": [
            {
                "fill": {
                    "alpha": 1.0,
                    "content": {
                        "render_type": "solid",
                        "solid": {"alpha": 1.0, "color": [1.0, 1.0, 1.0]},
                    },
                },
                "range": [0, len(text)],
                "size": size,
                "bold": True,
                "italic": False,
                "underline": False,
                "strokes": [
                    {
                        "content": {"solid": {"alpha": 1.0, "color": [0.0, 0.0, 0.0]}},
                        "width": 0.08,
                    }
                ],
            }
        ],
        "text": text,
    }
    material = {
        "id": material_id,
        "content": json.dumps(content, ensure_ascii=False),
        "typesetting": 0,
        "alignment": 1,
        "letter_spacing": 0.0,
        "line_spacing": 0.02,
        "line_feed": 1,
        "line_max_width": 0.82,
        "force_apply_line_max_width": False,
        "check_flag": 15,
        "type": "subtitle",
        "global_alpha": 1.0,
        "font_size": size,
        "text_color": "#FFFFFF",
        "text_size": int(size),
        "font_path": "",
        "font_id": "",
        "font_name": "",
        "name": "",
    }
    segment = _visual_segment(material_id, start, duration, [], volume=1.0, render_index=14000)
    segment["source_timerange"] = None
    segment["clip"]["transform"]["y"] = y
    return material, segment


def _visual_segment(
    material_id: str,
    start: int,
    duration: int,
    extra: list[str],
    *,
    volume: float,
    render_index: int,
) -> dict:
    return {
        "enable_adjust": True,
        "enable_color_correct_adjust": False,
        "enable_color_curves": True,
        "enable_color_match_adjust": False,
        "enable_color_wheels": True,
        "enable_lut": True,
        "enable_smart_color_adjust": False,
        "last_nonzero_volume": 1.0,
        "reverse": False,
        "track_attribute": 0,
        "track_render_index": 0,
        "visible": True,
        "id": _hid(),
        "material_id": material_id,
        "target_timerange": {"start": start, "duration": duration},
        "common_keyframes": [],
        "keyframe_refs": [],
        "source_timerange": {"start": 0, "duration": duration},
        "speed": 1.0,
        "volume": volume,
        "extra_material_refs": extra,
        "is_tone_modify": False,
        "clip": {
            "alpha": 1.0,
            "flip": {"horizontal": False, "vertical": False},
            "rotation": 0.0,
            "scale": {"x": 1.0, "y": 1.0},
            "transform": {"x": 0.0, "y": 0.0},
        },
        "uniform_scale": {"on": True, "value": 1.0},
        "hdr_settings": {"intensity": 1.0, "mode": 1, "nits": 1000},
        "render_index": render_index,
    }


def _tracks(
    videos: list[dict],
    audios: list[tuple[str, list[dict]]],
    texts: list[dict],
) -> list[dict]:
    tracks = [_track("画面", "video", videos)]
    tracks.extend(_track(name, "audio", segments) for name, segments in audios)
    if texts:
        tracks.append(_track("字幕", "text", texts))
    return tracks


def _track(name: str, kind: str, segments: list[dict]) -> dict:
    return {
        "attribute": 0,
        "flag": 0,
        "id": _hid(),
        "is_default_name": False,
        "name": name,
        "segments": segments,
        "type": kind,
    }


def _meta(draft_id: str, name: str, folder: Path, root: Path, duration: int, now: int) -> dict:
    return {
        "cloud_package_completed_time": "",
        "draft_cloud_capcut_purchase_info": "",
        "draft_cloud_last_action_download": False,
        "draft_cloud_materials": [],
        "draft_cloud_purchase_info": "",
        "draft_cloud_template_id": "",
        "draft_cloud_tutorial_info": "",
        "draft_cloud_videocut_purchase_info": "",
        "draft_cover": "",
        "draft_deeplink_url": "",
        "draft_enterprise_info": {
            "draft_enterprise_extra": "",
            "draft_enterprise_id": "",
            "draft_enterprise_name": "",
            "enterprise_material": [],
        },
        "draft_fold_path": str(folder),
        "draft_id": draft_id,
        "draft_is_ai_packaging_used": False,
        "draft_is_ai_shorts": False,
        "draft_is_ai_translate": False,
        "draft_is_article_video_draft": False,
        "draft_is_from_deeplink": "false",
        "draft_is_invisible": False,
        "draft_json_file": str(folder / "draft_content.json"),
        "draft_materials": [{"type": kind, "value": []} for kind in (0, 1, 2, 3, 6, 7, 8)],
        "draft_materials_copied_info": [],
        "draft_name": name,
        "draft_new_version": "",
        "draft_removable_storage_device": "",
        "draft_root_path": str(root),
        "draft_segment_extra_info": [],
        "draft_timeline_materials_size": 0,
        "draft_type": "",
        "tm_draft_cloud_completed": "",
        "tm_draft_cloud_modified": 0,
        "tm_draft_create": now,
        "tm_draft_modified": now,
        "tm_draft_removed": 0,
        "tm_duration": duration,
    }


def _register(root: Path, folder: Path, draft_id: str, name: str, duration: int) -> bool:
    """Add this draft to root_meta_info.json. Leave an unreadable index untouched."""
    index_path = root / "root_meta_info.json"
    now = int(time.time() * _US)
    fields = {
        "draft_cover": "",
        "draft_fold_path": str(folder),
        "draft_id": draft_id,
        "draft_is_ai_shorts": False,
        "draft_is_invisible": False,
        "draft_json_file": str(folder / "draft_content.json"),
        "draft_name": name,
        "draft_new_version": "",
        "draft_root_path": str(root),
        "draft_timeline_materials_size": 0,
        "tm_draft_create": now,
        "tm_draft_modified": now,
        "tm_draft_removed": 0,
        "tm_duration": duration,
    }
    if not index_path.exists():
        _write_json(index_path, {"all_draft_store": [fields]})
        return True
    raw = index_path.read_text(encoding="utf-8-sig", errors="replace")
    try:
        index = json.loads(raw)
    except json.JSONDecodeError:
        return False
    if not isinstance(index, dict):
        return False
    store_key = _store_key(index) or "all_draft_store"
    store = index.get(store_key)
    entries = [item for item in store if isinstance(item, dict)] if isinstance(store, list) else []
    kept = [item for item in entries if not _same_folder(item.get("draft_fold_path"), folder)]
    template = next((item for item in kept if isinstance(item, dict)), None)
    entry = {**json.loads(json.dumps(template)), **fields} if template else fields
    kept.append(entry)
    index[store_key] = kept
    backup = index_path.with_name("root_meta_info.json.bak")
    if not backup.exists():
        backup.write_text(raw, encoding="utf-8")
    _write_json(index_path, index)
    return True


def _store_key(index: dict) -> str | None:
    for key, value in index.items():
        if isinstance(value, list) and any(
            isinstance(item, dict) and ("draft_fold_path" in item or "draft_id" in item) for item in value
        ):
            return key
    for key, value in index.items():
        if isinstance(value, list) and re.search(r"all_draft_store|draft_store", key, re.IGNORECASE):
            return key
    return None


def _same_folder(stored: object, folder: Path) -> bool:
    if not isinstance(stored, str) or not stored.strip():
        return False
    return os.path.normcase(os.path.normpath(stored)) == os.path.normcase(os.path.normpath(folder))


def _note(name: str, installed: list[str], registered: list[str], blocked: list[str], running: list[str]) -> str:
    if installed and len(registered) == len(installed) and not blocked:
        text = f"剪映草稿「{name}」已放进{'、'.join(installed)}。打开软件就能改画面、旁白、配乐和字幕。"
        if running:
            text += f"先完全退出{'、'.join(running)}再打开，草稿才会出现在列表里。"
        return text
    if installed:
        text = f"草稿文件夹已写到{'、'.join(installed)}的草稿目录。"
        if blocked:
            text += "草稿列表索引没法安全更新，软件里可能暂时看不到，可下载压缩包后手动放进去。"
        if running:
            text += f"先完全退出{'、'.join(running)}再打开。"
        return text
    if blocked:
        return "剪映草稿已保存在任务目录。写入软件草稿目录时没有成功，可下载压缩包后手动放进去。"
    return "这台电脑没有找到剪映或 CapCut 的草稿目录。下载压缩包后，把里面的文件夹放进软件的草稿目录再打开。"


def _running_apps(labels: list[str]) -> list[str]:
    if os.name != "nt" or not labels:
        return []
    wanted: list[tuple[str, str]] = []
    if "剪映" in labels:
        wanted.append(("JianyingPro.exe", "剪映"))
    if "CapCut" in labels:
        wanted.append(("CapCut.exe", "CapCut"))
    running: list[str] = []
    for image, label in wanted:
        try:
            completed = subprocess.run(
                ["tasklist", "/FI", f"IMAGENAME eq {image}", "/NH"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if image.lower() in (completed.stdout or "").lower():
            running.append(label)
    return running


def _copy_media(source: Path, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, dest)
    return dest.resolve()


def _zip_folder(folder: Path, dest: Path) -> None:
    if dest.exists():
        dest.unlink()
    with zipfile.ZipFile(dest, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in folder.rglob("*"):
            if path.is_file():
                archive.write(path, Path(folder.name) / path.relative_to(folder))


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def _settings() -> str:
    now = int(time.time())
    return f"[General]\ndraft_create_time={now}\ndraft_last_edit_time={now}\nreal_edit_seconds=0\nreal_edit_keys=0\n"


def _folder_name(task_id: str) -> str:
    safe = _FOLDER.sub("", task_id)[:32] or "fastcut"
    return f"fastCut_{safe}"


def _hid() -> str:
    return uuid.uuid4().hex


def _us(seconds: float) -> int:
    return max(0, int(round(seconds * _US)))
