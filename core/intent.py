"""Turn one sentence into a video brief. A model fills the gaps; explicit words stay put."""

from __future__ import annotations

import re
from collections.abc import Callable

from config.settings import Settings, get_settings
from core.llm_director import chat_complete
from core.text import extract_json

CompleteFn = Callable[[list[dict[str, str]], str], str]

VOICES = {
    "晓晓": "zh-CN-XiaoxiaoNeural",
    "晓伊": "zh-CN-XiaoyiNeural",
    "云希": "zh-CN-YunxiNeural",
    "云健": "zh-CN-YunjianNeural",
    "云扬": "zh-CN-YunyangNeural",
    "zh-CN-XiaoxiaoNeural": "zh-CN-XiaoxiaoNeural",
    "zh-CN-XiaoyiNeural": "zh-CN-XiaoyiNeural",
    "zh-CN-YunxiNeural": "zh-CN-YunxiNeural",
    "zh-CN-YunjianNeural": "zh-CN-YunjianNeural",
    "zh-CN-YunyangNeural": "zh-CN-YunyangNeural",
}

_SOURCE_PATTERNS = (
    ("ai", r"文生图|ai\s*生图|人工智能出图|dall-?e|flux|万相|cogview|可灵"),
    ("sd", r"稳定扩散|stable\s*diffusion|本地\s*(sd|绘图|模型|生成)|sd\s*模型|用\s*sd"),
    ("web", r"网络图片|图库|unsplash|网上找图|使用图片|用图片"),
    ("video", r"视频素材|素材视频|只要视频"),
)
_STYLE_PATTERNS = (
    ("documentary", r"纪实|纪录片"),
    ("bright", r"明亮|清新|活泼"),
    ("cinematic", r"电影|cinematic|质感"),
)

INTENT_PROMPT = """你是 fastCut 的导演助理。把用户的一句话整理成 JSON，不要解释。
字段：
- topic: 视频在讲什么。去掉时长、比例、风格、素材来源这些制作说明。
- duration_seconds: 5 到 180 的秒数。用户说分钟要换算成秒。没说就填 30。
- aspect_ratio: 只能是 9:16、16:9、4:3。竖屏是 9:16，横屏是 16:9。没说就填 9:16。
- quality: draft、looks、delivery 之一。没说就填 draft。
- voice: 晓晓、晓伊、云希、云健、云扬 之一，或空字符串。男声用云希，女声用晓晓。没说就空字符串。
- visual_source: auto、web、sd、video、ai 之一。网络图片、Unsplash、图库是 web。文生图、Flux、万相、CogView 是 ai。本地 SD 是 sd。只要视频素材是 video。没说是 auto。
- visual_style: cinematic、documentary、bright 或空字符串。电影感是 cinematic，纪实是 documentary，明亮是 bright。
- audio: auto、freesound、none 之一。要配乐、背景音乐、音效或 Freesound 时填 freesound。不要配乐时填 none。没说填 auto。
- caption_size: 0 到 140 的整数。0 表示默认字号。字幕大一点大约 120，小一点大约 72，很大大约 132。用户给了数字就用数字，并限制在 48 到 140。没说填 0。
- summary: 一句中文，概括即将制作的片子。
- mode: 用户要切已有长视频的高光时填 highlight，否则填 create。
"""


def interpret_brief(
    text: str,
    *,
    complete: CompleteFn | None = None,
    settings: Settings | None = None,
) -> dict:
    fallback = parse_brief_locally(text)
    cfg = settings or get_settings()
    if complete is None and not cfg.llm_ready:
        return _public(fallback, "local")
    messages = [
        {"role": "system", "content": INTENT_PROMPT},
        {"role": "user", "content": text.strip()},
    ]
    try:
        if complete:
            raw = complete(messages, cfg.openai_model)
        else:
            raw = chat_complete(messages, cfg.openai_model, cfg, timeout=45)
        payload = extract_json(raw)
    except Exception:
        return _public(fallback, "local")
    if not isinstance(payload, dict):
        return _public(fallback, "local")
    merged = _merge(payload, fallback)
    summary = " ".join(str(payload.get("summary") or "").split())
    merged["summary"] = summary[:120] if summary else describe(merged)
    return _public(merged, "llm")


def parse_brief_locally(text: str) -> dict:
    raw = " ".join(text.split())
    duration, duration_explicit = _duration(raw)
    aspect, aspect_explicit = _aspect(raw)
    source, source_explicit = _choice(raw, _SOURCE_PATTERNS, "auto")
    if re.search(r"不要.{0,8}(本地|sd|模型生成)", raw, re.IGNORECASE):
        source, source_explicit = "web", True
    elif re.search(r"不要.{0,8}(网络|图库|unsplash|图片)", raw, re.IGNORECASE):
        source, source_explicit = "sd", True
    style, style_explicit = _choice(raw, _STYLE_PATTERNS, "")
    quality = "draft"
    quality_explicit = False
    if re.search(r"交付|高清成片", raw):
        quality, quality_explicit = "delivery", True
    elif re.search(r"观感|好看一些", raw):
        quality, quality_explicit = "looks", True
    elif "草稿" in raw:
        quality, quality_explicit = "draft", True
    voice = ""
    voice_explicit = False
    for name, voice_id in VOICES.items():
        if name.startswith("zh-"):
            continue
        if name in raw:
            voice, voice_explicit = voice_id, True
            break
    if not voice_explicit:
        if re.search(r"男声|男配音", raw):
            voice, voice_explicit = "zh-CN-YunxiNeural", True
        elif re.search(r"女声|女配音", raw):
            voice, voice_explicit = "zh-CN-XiaoxiaoNeural", True
    mode = "highlight" if re.search(r"切高光|长视频|原片", raw) else "create"
    use_llm = not bool(re.search(r"不要大模型|不用大模型|本地导演", raw))
    audio, audio_explicit = "auto", False
    if re.search(r"不要.{0,6}(配乐|背景音乐|音乐|音效)|无配乐|不要\s*bgm", raw, re.IGNORECASE):
        audio, audio_explicit = "none", True
    elif re.search(r"freesound|配乐|背景音乐|音效|加.{0,4}音乐|用.{0,4}音乐", raw, re.IGNORECASE):
        audio, audio_explicit = "freesound", True
    caption_size, caption_explicit = _caption_size(raw)
    plan = {
        "topic": _topic(raw),
        "duration": duration,
        "aspect_ratio": aspect,
        "quality": quality,
        "voice": voice,
        "visual_source": source,
        "visual_style": style,
        "use_llm": use_llm,
        "mode": mode,
        "duration_explicit": duration_explicit,
        "aspect_explicit": aspect_explicit,
        "source_explicit": source_explicit,
        "style_explicit": style_explicit,
        "quality_explicit": quality_explicit,
        "voice_explicit": voice_explicit,
        "audio": audio,
        "audio_explicit": audio_explicit,
        "caption_size": caption_size,
        "caption_explicit": caption_explicit,
    }
    plan["summary"] = describe(plan)
    return plan


def describe(plan: dict) -> str:
    source = {"auto": "自动选素材", "web": "网络图片", "sd": "本地绘图", "video": "视频素材", "ai": "AI 生图"}.get(
        plan.get("visual_source") or "auto", "自动选素材"
    )
    style = {"cinematic": "电影风格", "documentary": "纪实风格", "bright": "明亮风格"}.get(plan.get("visual_style") or "", "默认风格")
    aspect = {"9:16": "竖屏", "16:9": "横屏", "4:3": "4:3"}.get(plan.get("aspect_ratio") or "9:16", "竖屏")
    seconds = int(float(plan.get("duration") or 30))
    length = f"{seconds // 60} 分钟" if seconds >= 60 and seconds % 60 == 0 else f"{seconds} 秒"
    topic = plan.get("topic") or "这个主题"
    music = {"freesound": "，可商用配乐", "none": "，不加网络配乐"}.get(plan.get("audio") or "auto", "")
    size = int(plan.get("caption_size") or 0)
    caption = f"，字幕 {size}" if size else ""
    if plan.get("mode") == "highlight":
        return f"从长视频里切一段约 {length} 的{aspect}高光{music}{caption}。"
    return f"关于「{topic}」的{aspect}片子，大约 {length}，{source}，{style}{music}{caption}。"


def _public(plan: dict, parser: str) -> dict:
    voice = plan.get("voice") or None
    return {
        "topic": plan["topic"],
        "duration": float(plan["duration"]),
        "aspect_ratio": plan["aspect_ratio"],
        "quality": plan["quality"],
        "voice": voice,
        "visual_source": plan["visual_source"],
        "visual_style": plan["visual_style"],
        "use_llm": bool(plan["use_llm"]),
        "audio": plan.get("audio") or "auto",
        "caption_size": int(plan.get("caption_size") or 0),
        "mode": plan["mode"],
        "summary": plan.get("summary") or describe(plan),
        "parser": parser,
    }


def _merge(payload: dict, fallback: dict) -> dict:
    merged = dict(fallback)
    topic = " ".join(str(payload.get("topic") or "").split())
    if len(topic) >= 2:
        merged["topic"] = topic[:80]
    if not fallback["duration_explicit"]:
        merged["duration"] = _clamp(_number(payload.get("duration_seconds"), fallback["duration"]))
    if not fallback["aspect_explicit"]:
        aspect = str(payload.get("aspect_ratio") or "")
        if aspect in {"9:16", "16:9", "4:3"}:
            merged["aspect_ratio"] = aspect
    if not fallback["quality_explicit"]:
        quality = str(payload.get("quality") or "")
        if quality in {"draft", "looks", "delivery"}:
            merged["quality"] = quality
    if not fallback["source_explicit"]:
        source = str(payload.get("visual_source") or "")
        if source in {"auto", "web", "sd", "video", "ai"}:
            merged["visual_source"] = source
    if not fallback["style_explicit"]:
        style = str(payload.get("visual_style") or "")
        if style in {"", "cinematic", "documentary", "bright"}:
            merged["visual_style"] = style
    if not fallback["voice_explicit"]:
        merged["voice"] = _voice(payload.get("voice"), fallback["voice"])
    if not fallback.get("audio_explicit"):
        audio = str(payload.get("audio") or "")
        if audio in {"auto", "freesound", "none"}:
            merged["audio"] = audio
    if not fallback.get("caption_explicit"):
        size = _number(payload.get("caption_size"), 0)
        merged["caption_size"] = _fit_caption(size) if size else 0
    if not merged.get("summary"):
        merged["summary"] = describe(merged)
    return merged


def _duration(raw: str) -> tuple[float, bool]:
    minutes = re.search(r"(\d+(?:\.\d+)?)\s*分钟", raw)
    if minutes:
        return _clamp(float(minutes.group(1)) * 60), True
    seconds = re.search(r"(\d+(?:\.\d+)?)\s*秒", raw)
    if seconds:
        return _clamp(float(seconds.group(1))), True
    return 30.0, False


def _aspect(raw: str) -> tuple[str, bool]:
    if re.search(r"横屏|16\s*[:：]\s*9|宽屏", raw):
        return "16:9", True
    if re.search(r"4\s*[:：]\s*3", raw):
        return "4:3", True
    if re.search(r"竖屏|9\s*[:：]\s*16", raw):
        return "9:16", True
    return "9:16", False


def _choice(raw: str, patterns: tuple[tuple[str, str], ...], default: str) -> tuple[str, bool]:
    found: list[tuple[int, str]] = []
    for name, pattern in patterns:
        matches = list(re.finditer(pattern, raw, flags=re.IGNORECASE))
        if matches:
            found.append((matches[-1].start(), name))
    if not found:
        return default, False
    found.sort()
    return found[-1][1], True


def _topic(raw: str) -> str:
    matched = re.search(r"关于(.{2,40}?)(?:的(?:视频|片子|短片)?|，|。|,|$)", raw)
    if matched:
        topic = _clean_topic(matched.group(1))
        if len(topic) >= 2:
            return topic[:80]
    return (_clean_topic(raw) or "未命名主题")[:80]


def _clean_topic(text: str) -> str:
    cleaned = text
    patterns = (
        r"帮我",
        r"请帮?",
        r"(?:生成|制作|做)(?:一段|一个)?",
        r"大概|大约|左右",
        r"使用|用",
        r"\d+(?:\.\d+)?\s*(?:分钟|秒)",
        r"网络图片|图库|unsplash|本地\s*sd(?:\s*模型)?(?:生成)?|本地绘图|本地模型|视频素材",
        r"freesound|背景音乐|配乐|音效",
        r"电影风格|纪实风格|明亮风格|电影感|纪录片?",
        r"横屏|竖屏|宽屏",
        r"16\s*[:：]\s*9|9\s*[:：]\s*16|4\s*[:：]\s*3",
        r"男声|女声|男配音|女配音|晓晓|晓伊|云希|云健|云扬",
        r"(?:字幕|字号)[^\d，。]{0,8}\d{0,3}",
        r"\d{2,3}\s*(?:px|像素)?(?:的)?(?:字幕|字号)",
        r"(?:大一点|大一些|小一点|小一些|放大|缩小|很大|超大|巨大)的?字幕",
        r"视频|片子|短片|成片",
        r"风格",
    )
    for pattern in patterns:
        cleaned = re.sub(pattern, " ", cleaned, flags=re.IGNORECASE)
    return " ".join(cleaned.split()).strip(" ，。、的是")


def _voice(value: object, fallback: str) -> str:
    text = str(value or "").strip()
    if text in VOICES:
        return VOICES[text]
    if "男" in text:
        return "zh-CN-YunxiNeural"
    if "女" in text:
        return "zh-CN-XiaoxiaoNeural"
    return fallback


def _number(value: object, fallback: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def _clamp(value: float) -> float:
    return min(180.0, max(5.0, value))


def _fit_caption(value: float) -> int:
    return max(48, min(140, int(round(value))))


def _caption_size(raw: str) -> tuple[int, bool]:
    numbered = re.search(r"(?:字幕|字号)[^\d]{0,8}(\d{2,3})", raw)
    if not numbered:
        numbered = re.search(r"(\d{2,3})\s*(?:px|像素)?[^\d]{0,4}(?:字幕|字号)", raw)
    if numbered:
        return _fit_caption(float(numbered.group(1))), True
    if re.search(r"(?:很大|超大|巨大).{0,4}字幕|字幕.{0,6}(?:很大|超大|巨大)", raw):
        return 132, True
    if re.search(r"(?:大一点|大一些|放大|再大).{0,4}字幕|字幕.{0,8}(?:大一点|大一些|放大|再大)", raw):
        return 120, True
    if re.search(r"(?:小一点|小一些|缩小|再小).{0,4}字幕|字幕.{0,8}(?:小一点|小一些|缩小|再小)", raw):
        return 72, True
    return 0, False
