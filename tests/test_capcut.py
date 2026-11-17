import json
from pathlib import Path

import pytest

from core.capcut import CapcutError, configured_draft_root, export_capcut
from core.models import ProjectTimeline, Scene, Storyboard, WordStamp
from core.workspace import Task


def _timeline(voice: Path) -> ProjectTimeline:
    scene = Scene(
        scene_id=1,
        narration="天空为什么是蓝色的",
        search_keywords=["sky"],
        start=0,
        end=3.2,
        actual_duration=3.2,
    )
    return ProjectTimeline(
        task_id="abc123",
        mode="create",
        storyboard=Storyboard(title="蓝色天空", aspect_ratio="9:16", scenes=[scene]),
        words=[
            WordStamp(text="天空", start=0.2, end=0.8, scene_id=1),
            WordStamp(text="为什么是蓝色的", start=0.8, end=2.6, scene_id=1),
        ],
        narration_path=str(voice),
        total_duration=3.2,
        width=1080,
        height=1920,
    )


def _task(root: Path) -> Task:
    task = Task("abc123", root)
    (root / "build").mkdir(parents=True)
    (root / "audio").mkdir()
    (root / "build" / "scene_001.mp4").write_bytes(b"video")
    (root / "audio" / "voice.wav").write_bytes(b"voice")
    (root / "audio" / "bgm.wav").write_bytes(b"bed")
    return task


def test_draft_lands_in_capcut_without_dropping_other_projects(tmp_path: Path):
    task = _task(tmp_path / "job")
    drafts = tmp_path / "drafts"
    drafts.mkdir()
    other = drafts / "旧草稿"
    other.mkdir()
    original = {
        "draft_fold_path": str(other),
        "draft_id": "OTHER",
        "draft_name": "旧草稿",
        "extra": {"keep": 1},
    }
    (drafts / "root_meta_info.json").write_text(
        json.dumps({"all_draft_store": [original]}, ensure_ascii=False),
        encoding="utf-8",
    )

    exported = export_capcut(task, _timeline(task.path("audio", "voice.wav")), roots=[(drafts, "剪映")])

    assert exported.installed == ["剪映"]
    assert "剪映" in exported.note
    installed = drafts / "fastCut_abc123"
    content = json.loads((installed / "draft_content.json").read_text(encoding="utf-8"))
    assert content["canvas_config"] == {"height": 1920, "ratio": "9:16", "width": 1080}
    assert len(content["materials"]["videos"]) == 1
    assert len(content["materials"]["audios"]) == 2
    assert content["materials"]["texts"]
    assert [track["type"] for track in content["tracks"]] == ["video", "audio", "audio", "text"]
    video_path = Path(content["materials"]["videos"][0]["path"])
    assert video_path.exists()
    assert video_path.is_relative_to(installed)
    assert content["materials"]["videos"][0]["duration"] > 0
    assert content["tracks"][0]["segments"][0]["volume"] == 0.0
    caption = json.loads(content["materials"]["texts"][-1]["content"])
    assert "天空" in caption["text"]
    index = json.loads((drafts / "root_meta_info.json").read_text(encoding="utf-8"))
    kept = next(item for item in index["all_draft_store"] if item["draft_id"] == "OTHER")
    assert kept == original
    added = next(item for item in index["all_draft_store"] if item["draft_id"] != "OTHER")
    assert added["draft_name"] == "蓝色天空"
    assert Path(added["draft_fold_path"]) == installed
    assert (drafts / "root_meta_info.json.bak").exists()
    assert exported.zip_path.exists()
    assert (exported.folder / "draft_info.json").exists()


def test_unreadable_draft_index_is_left_alone(tmp_path: Path):
    task = _task(tmp_path / "job")
    drafts = tmp_path / "drafts"
    drafts.mkdir()
    index = drafts / "root_meta_info.json"
    payload = b"\x00not-json"
    index.write_bytes(payload)

    exported = export_capcut(task, _timeline(task.path("audio", "voice.wav")), roots=[(drafts, "CapCut")])

    assert index.read_bytes() == payload
    assert exported.installed == ["CapCut"]
    assert "索引" in exported.note
    assert (drafts / "fastCut_abc123" / "draft_content.json").exists()


def test_missing_picture_is_rejected(tmp_path: Path):
    task = Task("abc123", tmp_path / "job")
    task.root.mkdir()
    with pytest.raises(CapcutError):
        export_capcut(task, _timeline(tmp_path / "missing.wav"), roots=[])


def test_custom_draft_root_is_read_from_app_config(tmp_path: Path):
    drafts = tmp_path / "com.lveditor.draft"
    drafts.mkdir()
    config = tmp_path / "User Data" / "Config"
    config.mkdir(parents=True)
    (config / "globalSetting").write_text(
        json.dumps({"currentCustomDraftPath": str(drafts)}),
        encoding="utf-8",
    )
    assert configured_draft_root(tmp_path) == drafts
