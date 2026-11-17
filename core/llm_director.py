"""Storyboard generation, with schema retries and a local fallback."""

from __future__ import annotations

import json
from collections.abc import Callable

import httpx
from pydantic import ValidationError

from config.prompts import HIGHLIGHT_SYSTEM_PROMPT, SYSTEM_PROMPT, create_user_prompt, highlight_user_prompt
from config.providers import resolve_provider
from config.settings import Settings, get_settings
from core.models import Scene, Storyboard
from core.text import extract_json, scene_count_for, target_chars

CompleteFn = Callable[[list[dict[str, str]], str], str]

_KEYWORD_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("天空", ("blue sky clouds", "looking up sky", "daylight aerial")),
    ("海", ("ocean waves", "sea aerial", "coast drone")),
    ("城", ("city timelapse", "street night", "urban aerial")),
    ("山", ("mountain landscape", "fog valley", "hiking trail")),
    ("食", ("kitchen wide view", "kitchen steam", "fresh food")),
    ("脑", ("abstract science", "laboratory wide shot", "soft particles")),
    ("钱", ("business district", "finance office", "city traffic")),
    ("音", ("concert lights", "concert wide stage", "stage smoke")),
    ("球", ("sports slow motion", "stadium lights", "athlete training")),
)


def _keywords_for(topic: str, index: int) -> list[str]:
    for needle, keywords in _KEYWORD_HINTS:
        if needle in topic:
            start = index % len(keywords)
            rotated = list(keywords[start:]) + list(keywords[:start])
            return rotated[:3]
    if topic.isascii():
        return [topic, "cinematic b-roll", "natural light"]
    defaults = ["cinematic nature", "soft light abstract", "aerial landscape", "city aerial"]
    start = index % len(defaults)
    return [defaults[start], defaults[(start + 1) % len(defaults)]]


def _sentences(topic: str) -> list[str]:
    short = topic.strip() or "这件事"
    if len(short) > 12:
        short = short[:12]
    return [
        f"你有没有想过，{short}？",
        "看着简单的事，背后通常有一层机制。",
        f"我们把{short}说成一句人话。",
        "先看它发生之前，条件到底是什么。",
        "条件一变，后面的结果就会跟着变。",
        "再看中间那段过程，别急着跳结论。",
        "过程比口号更值得你记住。",
        "最后找一个生活里就能碰到的例子。",
        "例子对上了，原理就不抽象了。",
        f"所以{short}，关键在机制，不在感觉。",
        "下次再遇到，你可以自己拆开看。",
        "这一点，就是今天想让你带走的。",
    ]


def _highlights(narration: str) -> list[str]:
    for piece in narration.replace("，", " ").replace("。", " ").split():
        if len(piece) >= 2:
            return [piece[:4]]
    compact = "".join(ch for ch in narration if ch.strip("，。！？、 "))
    return [compact[:2]] if compact else []


def local_storyboard(topic: str, duration: float, aspect: str) -> Storyboard:
    budget = target_chars(duration)
    chosen: list[str] = []
    count = 0
    for line in _sentences(topic):
        if chosen and count >= budget:
            break
        if chosen and count + len(line) > int(budget * 1.2) and count >= int(budget * 0.7):
            break
        chosen.append(line)
        count += len(line)
    if len(chosen) < 2:
        chosen = _sentences(topic)[:2]
    scenes = []
    for index, line in enumerate(chosen, start=1):
        scenes.append(
            Scene(
                scene_id=index,
                narration=line,
                search_keywords=_keywords_for(topic, index),
                visual_style="cinematic" if index % 2 else "documentary",
                highlight_words=_highlights(line),
            )
        )
    title = topic.strip() or "未命名"
    return Storyboard(title=title[:16], aspect_ratio=aspect if aspect in {"9:16", "16:9", "4:3"} else "9:16", scenes=scenes)


def local_highlights(segments: list[dict], duration: float, aspect: str, source_duration: float) -> Storyboard:
    picked: list[Scene] = []
    used = 0.0
    for index, segment in enumerate(segments, start=1):
        start = float(segment["start"])
        end = float(segment["end"])
        length = end - start
        if length < 2.5 or length > 8.5:
            continue
        if used + length > duration * 1.1 and picked:
            break
        text = str(segment.get("text") or f"高光片段{index}").strip()
        picked.append(
            Scene(
                scene_id=len(picked) + 1,
                narration=text[:40] or f"高光片段{index}",
                search_keywords=["highlight"],
                visual_style="cinematic",
                highlight_words=_highlights(text) if text else [],
                source_start=round(start, 3),
                source_end=round(min(end, source_duration), 3),
            )
        )
        used += length
        if used >= duration:
            break
    if not picked and segments:
        first = segments[0]
        end = min(float(first["start"]) + min(6.0, duration), source_duration)
        picked.append(
            Scene(
                scene_id=1,
                narration=str(first.get("text") or "高光片段")[:40],
                search_keywords=["highlight"],
                visual_style="cinematic",
                highlight_words=[],
                source_start=float(first["start"]),
                source_end=end,
            )
        )
    if not picked:
        end = min(duration, source_duration)
        picked.append(
            Scene(
                scene_id=1,
                narration="原片高光",
                search_keywords=["highlight"],
                visual_style="cinematic",
                highlight_words=[],
                source_start=0,
                source_end=max(3.0, end),
            )
        )
    return Storyboard(title="高光切片", aspect_ratio=aspect if aspect in {"9:16", "16:9", "4:3"} else "9:16", scenes=picked)


def anthropic_text(body: dict) -> str:
    content = body.get("content")
    if isinstance(content, str) and content.strip():
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type", "text") == "text":
                parts.append(str(block.get("text") or ""))
        text = "\n".join(part for part in parts if part.strip())
        if text:
            return text
    raise RuntimeError("Claude 没有返回文本")


def chat_complete(
    messages: list[dict[str, str]],
    model: str,
    settings: Settings | None = None,
    timeout: float | None = None,
) -> str:
    cfg = settings or get_settings()
    if not cfg.llm_ready:
        raise RuntimeError("未配置大语言模型密钥")
    provider = resolve_provider("llm", cfg.llm_provider, cfg.openai_base_url)
    chosen = model or cfg.openai_model or provider.default_model
    if not chosen:
        raise RuntimeError("未填写大语言模型名称")
    timeout = httpx.Timeout(cfg.request_timeout if timeout is None else timeout, connect=10.0)
    if provider.protocol == "anthropic":
        return _anthropic_complete(messages, chosen, cfg, timeout)
    url = f"{cfg.openai_base_url.rstrip('/')}/chat/completions"
    headers = {"Authorization": f"Bearer {cfg.openai_api_key}", "Content-Type": "application/json"}
    payload: dict = {"model": chosen, "messages": messages, "temperature": 0.4}
    with httpx.Client(timeout=timeout) as client:
        response = client.post(url, headers=headers, json={**payload, "response_format": {"type": "json_object"}})
        if response.status_code == 400:
            response = client.post(url, headers=headers, json=payload)
        response.raise_for_status()
        body = response.json()
    return str(body["choices"][0]["message"]["content"])


def _anthropic_complete(
    messages: list[dict[str, str]],
    model: str,
    settings: Settings,
    timeout: httpx.Timeout,
) -> str:
    system: list[str] = []
    chat: list[dict[str, str]] = []
    for message in messages:
        role = str(message.get("role") or "user")
        content = str(message.get("content") or "")
        if role == "system":
            system.append(content)
        elif role in {"user", "assistant"}:
            chat.append({"role": role, "content": content})
    if not chat:
        chat = [{"role": "user", "content": "\n".join(system) or "请继续"}]
        system = []
    payload: dict = {"model": model, "max_tokens": 4096, "temperature": 0.4, "messages": chat}
    if system:
        payload["system"] = "\n\n".join(system)
    headers = {
        "x-api-key": settings.openai_api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    url = f"{settings.openai_base_url.rstrip('/')}/v1/messages"
    with httpx.Client(timeout=timeout) as client:
        response = client.post(url, headers=headers, json=payload)
        response.raise_for_status()
        body = response.json()
    return anthropic_text(body)


def _generate(
    messages: list[dict[str, str]],
    model: str,
    complete: CompleteFn | None,
    retries: int = 3,
) -> Storyboard:
    sender = complete or (lambda batch, chosen: chat_complete(batch, chosen))
    conversation = list(messages)
    last_error = "未知错误"
    for _ in range(retries):
        raw = sender(conversation, model)
        conversation.append({"role": "assistant", "content": raw})
        try:
            return Storyboard.model_validate(extract_json(raw))
        except (ValidationError, ValueError, json.JSONDecodeError) as exc:
            last_error = str(exc)
            conversation.append(
                {
                    "role": "user",
                    "content": f"校验失败：{last_error}\n请只返回修正后的 JSON 对象，不要解释。",
                }
            )
    raise RuntimeError(last_error)


def generate_storyboard(
    topic: str,
    duration: float,
    aspect: str,
    *,
    use_llm: bool = True,
    complete: CompleteFn | None = None,
    settings: Settings | None = None,
) -> tuple[Storyboard, str]:
    cfg = settings or get_settings()
    if not use_llm or (not cfg.llm_ready and complete is None):
        return local_storyboard(topic, duration, aspect), "local"
    chars = target_chars(duration)
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": create_user_prompt(topic, duration, aspect, chars, scene_count_for(duration))},
    ]

    def sender(batch: list[dict[str, str]], model: str) -> str:
        if complete:
            return complete(batch, model)
        return chat_complete(batch, model, cfg)

    try:
        board = _generate(messages, cfg.openai_model, sender)
    except Exception:
        return local_storyboard(topic, duration, aspect), "fallback"
    if board.aspect_ratio != aspect:
        board = board.model_copy(update={"aspect_ratio": aspect})
    return board, "llm"


def generate_highlights(
    segments: list[dict],
    duration: float,
    aspect: str,
    source_duration: float,
    *,
    use_llm: bool = True,
    complete: CompleteFn | None = None,
    settings: Settings | None = None,
) -> tuple[Storyboard, str]:
    cfg = settings or get_settings()
    if not use_llm or (not cfg.llm_ready and complete is None):
        return local_highlights(segments, duration, aspect, source_duration), "local"
    lines = []
    for segment in segments[:80]:
        lines.append(f"{float(segment['start']):.2f}-{float(segment['end']):.2f} {segment.get('text', '')}")
    messages = [
        {"role": "system", "content": HIGHLIGHT_SYSTEM_PROMPT},
        {"role": "user", "content": highlight_user_prompt(aspect, duration, "\n".join(lines), source_duration)},
    ]

    def sender(batch: list[dict[str, str]], model: str) -> str:
        if complete:
            return complete(batch, model)
        return chat_complete(batch, model, cfg)

    try:
        board = _generate(messages, cfg.openai_model, sender)
    except Exception:
        return local_highlights(segments, duration, aspect, source_duration), "fallback"
    if board.aspect_ratio != aspect:
        board = board.model_copy(update={"aspect_ratio": aspect})
    return board, "llm"
