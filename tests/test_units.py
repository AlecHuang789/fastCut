import json
import os
from pathlib import Path

import base64
from dataclasses import replace

from config.local_store import save_config, secret_hint
from core.asset_fetcher import (
    decode_sd_image,
    orientation_matches,
    pick_pexels_file,
    pick_unsplash_photo,
    sampling_for_checkpoint,
    sd_dimensions,
    sd_model_order,
    sd_prompt,
)
from core.freesound import COMMERCIAL_FILTER, bed_query, commercial_license, pick_freesound
from core.intent import interpret_brief
from core.pipeline import apply_visual_style
from config.settings import get_settings
from core.cleanup import cleanup_expired
from core.ffmpeg_engine import probe_duration, silence
from core.llm_director import generate_storyboard, local_storyboard
from core.models import Scene, Storyboard, WordStamp
from core.text import (
    caption_line_limit,
    caption_pages,
    explode_cjk_words,
    extract_json,
    group_captions,
    split_phrases,
    strip_brand,
    words_from_transcript,
    wrap_caption_lines,
)
from core.visual_renderer import caption_font_size, write_project
from core.models import ProjectTimeline


def test_extract_json_from_fence():
    payload = extract_json('```json\n{"title":"天","aspect_ratio":"9:16","scenes":[]}\n```')
    assert payload["title"] == "天"


def test_local_storyboard_fits_spoken_scenes():
    board = local_storyboard("为什么天空是蓝色的", 30, "9:16")
    assert board.aspect_ratio == "9:16"
    assert len(board.scenes) >= 2
    for scene in board.scenes:
        assert scene.search_keywords
        assert scene.highlight_words
        assert scene.highlight_words[0] in scene.narration


def test_director_retries_then_accepts_json():
    good = local_storyboard("蓝天", 20, "16:9").model_dump_json()
    calls = {"n": 0}

    def complete(messages, model):
        calls["n"] += 1
        if calls["n"] < 3:
            return "这不是 JSON"
        return good

    board, how = generate_storyboard("蓝天", 20, "16:9", use_llm=True, complete=complete)
    assert how == "llm"
    assert calls["n"] == 3
    assert board.title


def test_director_falls_back_after_three_bad_replies():
    def complete(messages, model):
        return "仍然不行"

    board, how = generate_storyboard("海浪", 15, "9:16", use_llm=True, complete=complete)
    assert how == "fallback"
    assert board.scenes


def test_split_and_group_captions_do_not_overlap():
    phrases = split_phrases("你有没有想过，为什么晴朗的天空是蓝色的？")
    assert phrases == ["你有没有想过", "为什么晴朗的天空是蓝色的"]
    words = []
    cursor = 0.0
    for phrase in phrases:
        words.append(WordStamp(text=phrase, start=cursor, end=cursor + 0.4))
        cursor += 0.4
    groups = group_captions(words)
    assert len(groups) == 2
    for previous, nxt in zip(groups, groups[1:]):
        assert previous.start + previous.duration <= nxt.start + 0.001
    clauses = split_phrases("先看条件，再看过程，最后看例子。")
    assert clauses == ["先看条件", "再看过程", "最后看例子"]
    stamps = [WordStamp(text=item, start=index, end=index + 0.8) for index, item in enumerate(clauses)]
    assert len(group_captions(stamps)) == 3


def test_short_sentence_stays_together_and_wraps():
    words = [
        WordStamp(text="你有没有想过", start=0.2, end=1.0, scene_id=1),
        WordStamp(text="为什么晴朗的天空是蓝色的", start=1.7, end=3.4, scene_id=1),
    ]
    groups = group_captions(words)
    assert len(groups) == 2
    sentence = "".join(word.text for word in words)
    lines = wrap_caption_lines(sentence, 12)
    assert len(lines) >= 2
    assert "".join(lines) == sentence
    assert "fastcut" not in "".join(wrap_caption_lines("这是 fastCut 的字幕", 20)).lower()
    assert strip_brand("fastCut") == ""
    short = [WordStamp(text=char, start=index * 0.2, end=index * 0.2 + 0.2) for index, char in enumerate("你好世界")]
    assert len(group_captions(short)) == 1
    english = [WordStamp(text=word, start=index, end=index + 0.4) for index, word in enumerate("blue sky over the city".split())]
    assert len(group_captions(english)) == 1


def test_long_caption_moves_to_the_next_cue():
    text = "今天我们把一件很长的事情从头到尾讲清楚并且不使用任何标点符号来分隔"
    pages = caption_pages(text, 10, 2)
    assert len(pages) >= 2
    assert all(len(page) <= 2 for page in pages)
    assert caption_line_limit(1080, 96) == 10
    assert caption_line_limit(1080, 140) < caption_line_limit(1080, 96)
    assert caption_font_size(1080, 1920, 0) == 96
    assert caption_font_size(1920, 1080, 120) > 84


def test_explode_sentence_timestamp():
    exploded = explode_cjk_words([WordStamp(text="你有没有想过为什么。下一句呢？", start=1, end=3)])
    assert len(exploded) == 2
    assert abs(exploded[0].start - 1) < 0.01
    assert abs(exploded[-1].end - 3) < 0.05


def test_transcript_segments():
    words = words_from_transcript(
        {"segments": [{"text": "你好世界", "start": 0.2, "end": 1.2}]}
    )
    assert words
    assert words[0].start >= 0.2


def test_stable_diffusion_payload_helpers():
    scene = Scene(scene_id=1, narration="晴朗的天空", search_keywords=["blue sky"], visual_style="cinematic")
    settings = replace(get_settings(), sd_width=0, sd_height=0)
    assert sd_dimensions("9:16", settings) == (432, 768)
    assert sd_dimensions("16:9", settings) == (768, 432)
    assert sd_dimensions("4:3", settings) == (768, 576)
    assert sd_dimensions("9:16", replace(settings, sd_width=830, sd_height=1210)) == (824, 1208)
    prompt = sd_prompt(scene)
    assert prompt.startswith("realistic illustration, intricate details, cinematic")
    assert "wide shot" in prompt and "figures small in the frame" in prompt
    assert "blue sky" in prompt
    assert "晴朗的天空" not in prompt
    encoded = base64.b64encode(b"png-bytes").decode("ascii")
    assert decode_sd_image({"images": [f"data:image/png;base64,{encoded}"]}) == b"png-bytes"
    assert decode_sd_image({"images": []}) is None


def test_sd_model_switches_with_visual_style():
    models = [
        {"title": "tmndMix_tmndMixSPRAINBOW.safetensors [aaa]"},
        {"title": "DreamShaper8_LCM.safetensors [bbb]"},
        {"title": "9527_v10.ckpt [ccc]"},
    ]
    cinematic = Scene(scene_id=1, narration="城市夜里", search_keywords=["city"], visual_style="cinematic")
    bright = Scene(scene_id=2, narration="笑容", search_keywords=["smile"], visual_style="bright")
    real_order = sd_model_order(cinematic, models)
    anime_order = sd_model_order(bright, models)
    assert "DreamShaper" in real_order[0] or "9527" in real_order[0]
    assert "tmnd" not in anime_order[0]
    assert sd_model_order(cinematic, models)[0] == real_order[0]
    assert sd_model_order(cinematic, models, forced="9527")[0].startswith("9527")
    comic = [{"title": "realisticComicBook_v10.safetensors [abc]"}, {"title": "tmndMix.safetensors [ddd]"}]
    forced = sd_model_order(cinematic, comic, forced="REALISTIC_COMIC_BOOK")
    assert forced[0].startswith("realisticComicBook")
    assert sd_model_order(cinematic, models, forced="missing-model") == []
    settings = replace(get_settings(), sd_steps=20, sd_cfg=7)
    assert sampling_for_checkpoint(settings, real_order[0]) == (8, 2.0)


def test_pexels_file_must_match_orientation():
    video = {
        "video_files": [
            {"width": 1920, "height": 1080, "file_type": "video/mp4", "link": "http://land.mp4"},
            {"width": 1080, "height": 1920, "file_type": "video/mp4", "link": "http://port.mp4"},
            {"width": 720, "height": 1280, "file_type": "video/mp4", "link": "http://small.mp4"},
        ]
    }
    chosen = pick_pexels_file(video, "9:16")
    assert chosen["link"] == "http://port.mp4"
    assert not orientation_matches(720, 1280, "9:16")
    assert orientation_matches(1920, 1080, "16:9")


def test_cleanup_keeps_running_tasks(tmp_path: Path):
    old = tmp_path / "old"
    old.mkdir()
    (old / "status.json").write_text(json.dumps({"state": "done"}), encoding="utf-8")
    running = tmp_path / "live"
    running.mkdir()
    (running / "status.json").write_text(json.dumps({"state": "running"}), encoding="utf-8")
    cache = tmp_path / "_cache"
    cache.mkdir()
    stale = cache / "clip.mp4"
    stale.write_bytes(b"12345")
    ancient = 1_000_000_000
    os.utime(old, (ancient, ancient))
    os.utime(old / "status.json", (ancient, ancient))
    os.utime(stale, (ancient, ancient))
    removed = cleanup_expired(hours=1, root=tmp_path)
    assert "old" in removed
    assert not old.exists()
    assert running.exists()
    assert not stale.exists()


def test_write_overlay_project(tmp_path: Path):
    scene = Scene(
        scene_id=1,
        narration="天空为什么是蓝色的",
        search_keywords=["blue sky"],
        highlight_words=["蓝色"],
        start=0,
        end=4,
        actual_duration=4,
    )
    board = Storyboard(title="蓝天", aspect_ratio="9:16", scenes=[scene])
    words = [
        WordStamp(text="天空", start=0.2, end=0.8, scene_id=1),
        WordStamp(text="蓝色", start=0.8, end=1.4, scene_id=1),
    ]
    timeline = ProjectTimeline(
        task_id="t1",
        mode="create",
        storyboard=board,
        words=words,
        narration_path="narration.wav",
        total_duration=4,
        width=1080,
        height=1920,
    )
    notes = write_project(timeline, tmp_path / "hf")
    index = (tmp_path / "hf" / "index.html").read_text(encoding="utf-8")
    captions = (tmp_path / "hf" / "compositions" / "captions.html").read_text(encoding="utf-8")
    assert 'data-composition-id="main"' in index
    assert 'data-track-kind="captions"' in index
    assert "compositions/title.html" not in index
    assert "蓝色" in captions
    assert "window.__timelines" in captions
    assert "progress" not in (tmp_path / "hf" / "compositions" / "chrome.html").read_text(encoding="utf-8")
    assert "fastcut" not in captions.lower()
    assert notes is not None


def test_silence_duration(tmp_path: Path):
    dest = tmp_path / "quiet.wav"
    silence(dest, 0.4)
    assert abs(probe_duration(dest) - 0.4) < 0.08


def test_unsplash_photo_matches_orientation():
    photo = {
        "width": 2000,
        "height": 3000,
        "urls": {"regular": "https://images.example/portrait.jpg"},
        "user": {"name": "Ada"},
        "links": {"download_location": "https://api.unsplash.com/photos/abc/download"},
    }
    chosen = pick_unsplash_photo(photo, "9:16")
    assert chosen is not None
    assert chosen["url"].endswith("portrait.jpg")
    assert chosen["credit"] == "Ada"
    assert chosen["kind"] == "image"
    assert pick_unsplash_photo(photo, "16:9") is None
    small = {**photo, "width": 400, "height": 800}
    assert pick_unsplash_photo(small, "9:16") is None


def test_brief_reads_minutes_images_and_style():
    plan = interpret_brief(
        "帮我生成一段视频，是关于城市夜景的，大概1分钟左右，使用网络图片，电影风格",
        complete=lambda messages, model: (_ for _ in ()).throw(RuntimeError("offline")),
    )
    assert plan["parser"] == "local"
    assert plan["topic"] == "城市夜景"
    assert plan["duration"] == 60
    assert plan["visual_source"] == "web"
    assert plan["visual_style"] == "cinematic"
    assert plan["aspect_ratio"] == "9:16"
    assert "城市夜景" in plan["summary"]


def test_brief_keeps_explicit_choices_and_takes_the_model_topic():
    def complete(messages, model):
        return json.dumps(
            {
                "topic": "雨后的街道",
                "duration_seconds": 12,
                "aspect_ratio": "9:16",
                "visual_source": "sd",
                "visual_style": "documentary",
                "summary": "横屏电影感，讲雨后的街道。",
            },
            ensure_ascii=False,
        )

    plan = interpret_brief(
        "帮我生成一段视频，是关于城市夜景的，大概1分钟左右，横屏，使用网络图片，电影风格",
        complete=complete,
    )
    assert plan["parser"] == "llm"
    assert plan["topic"] == "雨后的街道"
    assert plan["duration"] == 60
    assert plan["aspect_ratio"] == "16:9"
    assert plan["visual_source"] == "web"
    assert plan["visual_style"] == "cinematic"
    assert "雨后的街道" in plan["summary"]


def test_highlight_brief_stays_out_of_create():
    plan = interpret_brief("把这段长视频切成30秒高光", complete=lambda messages, model: "{}")
    assert plan["mode"] == "highlight"
    assert plan["duration"] == 30


def test_local_sd_and_voice_are_read():
    plan = interpret_brief(
        "做一个关于咖啡的片子，20秒，男声，用本地SD模型，纪实",
        complete=lambda messages, model: "{}",
    )
    assert plan["topic"] == "咖啡"
    assert plan["duration"] == 20
    assert plan["voice"] == "zh-CN-YunxiNeural"
    assert plan["visual_source"] == "sd"
    assert plan["visual_style"] == "documentary"
    assert plan["caption_size"] == 0


def test_caption_size_can_be_said_in_the_brief():
    larger = interpret_brief(
        "帮我生成一段关于城市夜景的视频，大概1分钟，字幕大一点",
        complete=lambda messages, model: "{}",
    )
    assert larger["caption_size"] == 120
    assert larger["topic"] == "城市夜景"
    assert "字幕 120" in larger["summary"]
    smaller = interpret_brief("做一个关于咖啡的片子，字幕小一点", complete=lambda messages, model: "{}")
    assert smaller["caption_size"] == 72
    huge = interpret_brief("做一个关于海浪的片子，字幕很大", complete=lambda messages, model: "{}")
    assert huge["caption_size"] == 132
    numbered = interpret_brief("做一个关于星空的片子，字号 88", complete=lambda messages, model: "{}")
    assert numbered["caption_size"] == 88
    assert numbered["topic"] == "星空"

    def complete(messages, model):
        return json.dumps({"topic": "雨后的街道", "caption_size": 64, "summary": "字幕小一些。"}, ensure_ascii=False)

    kept = interpret_brief("关于城市夜景的片子，字幕大一点", complete=complete)
    assert kept["caption_size"] == 120
    assert kept["topic"] == "雨后的街道"


def test_apply_visual_style_rewrites_every_scene():
    board = local_storyboard("海", 12, "9:16")
    styled = apply_visual_style(board, "bright")
    assert {scene.visual_style for scene in styled.scenes} == {"bright"}
    assert apply_visual_style(board, "").scenes[0].visual_style == board.scenes[0].visual_style


def test_freesound_keeps_only_commercial_previews():
    assert "Creative Commons 0" in COMMERCIAL_FILTER
    assert "Attribution" in COMMERCIAL_FILTER
    assert "Noncommercial" not in COMMERCIAL_FILTER
    assert commercial_license("Attribution")
    assert commercial_license("https://creativecommons.org/publicdomain/zero/1.0/")
    assert commercial_license("http://creativecommons.org/licenses/by/4.0/")
    assert not commercial_license("http://creativecommons.org/licenses/by-nc/4.0/")
    assert not commercial_license("Attribution Noncommercial")
    assert not commercial_license("http://creativecommons.org/licenses/by-sa/4.0/")
    sound = {
        "id": 12,
        "name": "Soft rain",
        "username": "Ada",
        "duration": 42,
        "license": "https://creativecommons.org/publicdomain/zero/1.0/",
        "previews": {"preview-hq-mp3": "https://cdn.example/rain.mp3"},
    }
    chosen = pick_freesound(sound)
    assert chosen is not None
    assert chosen["url"].endswith("rain.mp3")
    assert chosen["username"] == "Ada"
    assert pick_freesound({**sound, "license": "http://creativecommons.org/licenses/by-nc/4.0/"}) is None
    assert pick_freesound({**sound, "duration": 1.2}) is None
    assert "ocean" in bed_query("城市夜景", "cinematic", ["ocean", "night city"])


def test_brief_can_turn_music_off():
    plan = interpret_brief("做一个关于海边的片子，不要配乐", complete=lambda messages, model: "{}")
    assert plan["audio"] == "none"
    assert plan["topic"] == "海边"
    assert "不加网络配乐" in plan["summary"]


def test_saved_keys_can_be_replaced_or_cleared(tmp_path: Path):
    dest = tmp_path / "local.json"
    saved = save_config(
        {
            "openai_base_url": "https://example.com/v1/",
            "openai_model": "demo",
            "openai_api_key": "sk-test-1234",
            "unsplash_access_key": "unsplash-key-9876",
        },
        dest,
    )
    assert saved["openai_base_url"] == "https://example.com/v1"
    assert saved["openai_api_key"] == "sk-test-1234"
    assert secret_hint(saved["unsplash_access_key"]) == "••••9876"
    assert secret_hint("short") == "已保存"
    cleared = save_config({"clear_openai_api_key": True, "openai_model": "demo-2"}, dest)
    assert cleared["openai_api_key"] == ""
    assert cleared["unsplash_access_key"] == "unsplash-key-9876"
    assert cleared["openai_model"] == "demo-2"
    try:
        save_config({"image_api_url": "ftp://nope"}, dest)
    except ValueError as exc:
        assert "http" in str(exc)
    else:
        raise AssertionError("bad url should be rejected")


def test_provider_presets_keep_a_key_per_service(tmp_path: Path):
    dest = tmp_path / "providers.json"
    saved = save_config(
        {
            "llm_provider": "deepseek",
            "openai_model": "deepseek-chat",
            "openai_api_key": "sk-deepseek-1234",
            "tts_provider": "openai",
            "tts_model": "tts-1",
            "tts_voice": "alloy",
            "tts_api_key": "sk-tts-9876",
            "image_provider": "zhipu",
            "image_model": "cogview-3-flash",
            "image_api_key": "cogview-key-2222",
        },
        dest,
    )
    assert saved["openai_base_url"] == "https://api.deepseek.com/v1"
    assert saved["llm_keys"]["deepseek"] == "sk-deepseek-1234"
    assert saved["image_api_url"] == "https://open.bigmodel.cn/api/paas/v4/images/generations"
    assert saved["tts_voice"] == "alloy"
    switched = save_config({"llm_provider": "openai", "openai_model": "gpt-4o-mini", "openai_api_key": "sk-openai-5555"}, dest)
    assert switched["openai_base_url"] == "https://api.openai.com/v1"
    assert switched["llm_keys"]["deepseek"] == "sk-deepseek-1234"
    assert switched["openai_api_key"] == "sk-openai-5555"
    restored = save_config({"llm_provider": "deepseek", "openai_model": "deepseek-chat"}, dest)
    assert restored["openai_api_key"] == "sk-deepseek-1234"
    assert restored["llm_keys"]["openai"] == "sk-openai-5555"
    cleared = save_config({"llm_provider": "deepseek", "clear_openai_api_key": True}, dest)
    assert cleared["openai_api_key"] == ""
    assert "deepseek" not in cleared["llm_keys"]
    assert cleared["llm_keys"]["openai"] == "sk-openai-5555"
    parked = save_config({"image_provider": "off"}, dest)
    assert parked["image_api_url"] == ""
    assert parked["image_keys"]["zhipu"] == "cogview-key-2222"
    try:
        save_config({"llm_provider": "not-a-provider"}, dest)
    except ValueError as exc:
        assert "服务" in str(exc)
    else:
        raise AssertionError("unknown provider should be rejected")


def test_remote_payloads_decode_audio_and_images():
    from core.image_gen import openai_image_bytes
    from core.llm_director import anthropic_text
    from core.speech import audio_from_minimax
    from config.providers import resolve_provider

    assert openai_image_bytes({"data": [{"b64_json": base64.b64encode(b"jpeg").decode()}]}) == b"jpeg"
    assert anthropic_text({"content": [{"type": "text", "text": "{\"ok\": true}"}]}) == "{\"ok\": true}"
    assert audio_from_minimax({"data": {"audio": b"mp3".hex()}, "base_resp": {"status_code": 0}}) == b"mp3"
    assert resolve_provider("llm", "", "https://api.deepseek.com/v1").id == "deepseek"
    assert resolve_provider("llm", "anthropic", "").protocol == "anthropic"
    assert resolve_provider("tts", "", "").id == "edge"
    assert resolve_provider("image", "", "").id == "off"


def test_brief_can_ask_for_generated_images():
    plan = interpret_brief("用文生图做一条关于咖啡的片子", complete=lambda messages, model: "{}")
    assert plan["visual_source"] == "ai"
    assert plan["topic"] == "咖啡"


def test_stop_kills_a_running_command():
    import sys
    import threading
    import time

    from core.control import Stopped, clear_stop, request_stop, run_process, task_binding

    task_id = "stopcmd01"

    def later() -> None:
        time.sleep(0.4)
        request_stop(task_id)

    threading.Thread(target=later, daemon=True).start()
    started = time.monotonic()
    try:
        with task_binding(task_id):
            try:
                run_process([sys.executable, "-c", "import time; time.sleep(30)"], timeout=20)
            except Stopped:
                pass
            else:
                raise AssertionError("stop should interrupt the command")
        assert time.monotonic() - started < 4
    finally:
        clear_stop(task_id)
