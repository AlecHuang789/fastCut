"""Text helpers shared by the director, aligner and caption renderer."""

from __future__ import annotations

import json
import re

from core.models import CaptionGroup, WordStamp

_PUNCT = "，。！？、；：,.!?;"
_CJK = re.compile(r"[\u4e00-\u9fff]")


def cjk_ratio(text: str) -> float:
    chars = [ch for ch in text if not ch.isspace()]
    if not chars:
        return 0.0
    return sum(1 for ch in chars if _CJK.match(ch)) / len(chars)


def extract_json(text: str) -> dict:
    raw = text.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start < 0 or end <= start:
            raise
        data = json.loads(raw[start : end + 1])
    if not isinstance(data, dict):
        raise ValueError("JSON 根节点必须是对象")
    return data


def target_chars(duration: float) -> int:
    return max(24, int(duration / 60 * 235))


def scene_count_for(duration: float) -> int:
    return max(2, min(14, round(duration / 4.5)))


_SENTENCE_END = "。！？!?"
_CLAUSE_BREAK = set("，、；：,;:")
_CAPTION_BREAK = set(_SENTENCE_END) | _CLAUSE_BREAK


def split_phrases(text: str, limit: int = 20) -> list[str]:
    """Split a spoken line into caption cues. Commas start the next cue, not another on-screen line."""
    cleaned = text.strip()
    if not cleaned:
        return []
    if cjk_ratio(cleaned) < 0.3:
        return [piece for piece in cleaned.split() if piece]
    if limit < 8:
        limit = 20
    compact = re.sub(r"\s+", "", cleaned)
    out: list[str] = []
    buf = ""
    for ch in compact:
        if ch in _CAPTION_BREAK:
            out.extend(_clip_phrase(buf.strip(_PUNCT), limit))
            buf = ""
            continue
        buf += ch
    out.extend(_clip_phrase(buf.strip(_PUNCT), limit))
    return out or _clip_phrase(compact, limit)


def _clip_phrase(piece: str, limit: int) -> list[str]:
    if not piece:
        return []
    if len(piece) <= limit:
        return [piece]
    return [piece[index : index + limit] for index in range(0, len(piece), limit)]


def explode_cjk_words(words: list[WordStamp]) -> list[WordStamp]:
    """Split a sentence-sized timestamp into short phrases by character share."""
    exploded: list[WordStamp] = []
    for word in words:
        text = word.text.strip()
        phrases = split_phrases(text)
        if len(phrases) <= 1:
            exploded.append(word)
            continue
        span = max(0.05, word.end - word.start)
        weight = sum(max(1, len(phrase)) for phrase in phrases)
        cursor = word.start
        for phrase in phrases:
            piece = span * max(1, len(phrase)) / weight
            exploded.append(
                WordStamp(text=phrase, start=cursor, end=cursor + piece, scene_id=word.scene_id)
            )
            cursor += piece
    return exploded


_BRAND = re.compile(r"fast\s*cut", re.IGNORECASE)


def strip_brand(text: str) -> str:
    """Drop the product name if it ever lands in on-screen copy."""
    cleaned = _BRAND.sub("", text or "")
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    return cleaned.strip()


def _ends_clause(text: str) -> bool:
    stripped = text.strip()
    return bool(stripped) and stripped[-1] in _CAPTION_BREAK


def _complete_clause(text: str) -> bool:
    """A CJK phrase long enough to be its own caption. Short tokens still join up."""
    compact = re.sub(r"\s+", "", text.strip(_PUNCT + " "))
    if len(compact) < 4 or cjk_ratio(compact) < 0.3:
        return False
    return True


def caption_line_limit(width: int, size: int = 96) -> int:
    if width >= 1800:
        base = 16
    elif width >= 1300:
        base = 13
    else:
        base = 10
    scale = 96 / max(48, min(160, size or 96))
    return max(6, round(base * scale))


def wrap_caption_lines(text: str, per_line: int) -> list[str]:
    """Keep one sentence intact and break it into visual lines when it is wide."""
    cleaned = strip_brand(text)
    if cjk_ratio(cleaned) >= 0.3:
        cleaned = re.sub(r"\s+", "", cleaned)
    else:
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if not cleaned:
        return []
    per_line = max(6, per_line)
    if len(cleaned) <= per_line:
        return [cleaned]
    lines: list[str] = []
    rest = cleaned
    while rest:
        if len(rest) <= per_line:
            lines.append(rest)
            break
        window = rest[:per_line]
        cut = 0
        for mark in ("，", "、", "；", "：", ",", " "):
            pos = window.rfind(mark)
            if pos >= max(4, per_line // 2):
                cut = pos + 1
                break
        if cut <= 0:
            cut = per_line
        piece = rest[:cut].strip()
        if not piece:
            break
        lines.append(piece)
        rest = rest[cut:].strip()
    return lines


def caption_pages(text: str, per_line: int, max_lines: int = 2) -> list[list[str]]:
    """At most two visual lines per cue. The rest waits for the next cue."""
    cleaned = strip_brand(text).strip()
    if not cleaned:
        return []
    clauses = [cleaned] if cjk_ratio(cleaned) < 0.3 else (split_phrases(cleaned) or [cleaned])
    pages: list[list[str]] = []
    step = max(1, max_lines)
    for clause in clauses:
        lines = wrap_caption_lines(clause, per_line)
        for index in range(0, len(lines), step):
            page = lines[index : index + step]
            if page:
                pages.append(page)
    return pages


def group_captions(words: list[WordStamp], max_chars: int = 20, max_items: int = 12) -> list[CaptionGroup]:
    """One short clause is one caption. A comma, or a full line budget, starts the next cue."""
    if not words:
        return []
    if max_chars < 8:
        max_chars = 20
    buckets: list[list[WordStamp]] = []
    current: list[WordStamp] = []
    chars = 0
    for word in words:
        scene_break = bool(current) and word.scene_id and current[-1].scene_id and word.scene_id != current[-1].scene_id
        clause_done = bool(current) and _ends_clause(current[-1].text)
        current_text = "".join(item.text for item in current)
        both_clauses = bool(current) and _complete_clause(current_text) and _complete_clause(word.text)
        too_long = bool(current) and (chars >= max_chars or len(current) >= max_items or chars + len(word.text.strip()) > max_chars)
        if scene_break or clause_done or both_clauses or too_long:
            buckets.append(current)
            current = []
            chars = 0
        current.append(word)
        chars += len(word.text.strip())
        if _ends_clause(word.text) or (len(word.text.strip()) > max_chars):
            buckets.append(current)
            current = []
            chars = 0
    if current:
        buckets.append(current)

    groups: list[CaptionGroup] = []
    for index, bucket in enumerate(buckets):
        start = bucket[0].start
        if index == 0 and start < 0.12:
            start = 0.12
        end = bucket[-1].end
        if index + 1 < len(buckets):
            end = min(end, buckets[index + 1][0].start - 0.02)
        if end < start + 0.24:
            end = start + 0.24
            if index + 1 < len(buckets):
                end = min(end, max(start + 0.08, buckets[index + 1][0].start - 0.02))
        if end <= start:
            continue
        groups.append(CaptionGroup(start=round(start, 3), duration=round(end - start, 3), words=bucket))
    return groups


def clamp_groups(groups: list[CaptionGroup], total: float) -> list[CaptionGroup]:
    clamped: list[CaptionGroup] = []
    limit = max(0.4, total - 0.04)
    for group in groups:
        start = min(group.start, limit - 0.3)
        start = max(0.12, start)
        duration = min(group.duration, limit - start)
        if duration < 0.28:
            continue
        clamped.append(group.model_copy(update={"start": round(start, 3), "duration": round(duration, 3)}))
    return clamped


def words_from_transcript(payload: dict) -> list[WordStamp]:
    """Accept faster-whisper style segments or a HyperFrames transcript."""
    words: list[WordStamp] = []
    raw_words = payload.get("words")
    if isinstance(raw_words, list) and raw_words:
        for item in raw_words:
            if not isinstance(item, dict):
                continue
            text = str(item.get("text") or item.get("word") or "").strip()
            if not text:
                continue
            words.append(WordStamp(text=text, start=float(item["start"]), end=float(item["end"])))
        return explode_cjk_words(words)

    segments = payload.get("segments") or payload.get("results") or []
    if isinstance(segments, dict):
        segments = segments.get("segments") or []
    for segment in segments:
        if not isinstance(segment, dict):
            continue
        nested = segment.get("words") or []
        if nested:
            for item in nested:
                text = str(item.get("text") or item.get("word") or "").strip()
                if not text:
                    continue
                words.append(WordStamp(text=text, start=float(item["start"]), end=float(item["end"])))
            continue
        text = str(segment.get("text") or "").strip()
        if not text:
            continue
        words.append(
            WordStamp(text=text, start=float(segment["start"]), end=float(segment.get("end") or segment["start"]))
        )
    return explode_cjk_words(words)


def assign_scenes(words: list[WordStamp], scenes: list) -> list[WordStamp]:
    assigned: list[WordStamp] = []
    for word in words:
        midpoint = (word.start + word.end) / 2
        scene_id = word.scene_id
        for scene in scenes:
            if scene.start is None or scene.end is None:
                continue
            if scene.start - 0.02 <= midpoint <= scene.end + 0.02:
                scene_id = scene.scene_id
                break
        assigned.append(word.model_copy(update={"scene_id": scene_id}))
    return assigned


def is_highlighted(text: str, highlights: list[str]) -> bool:
    token = text.strip(_PUNCT + " ")
    if not token:
        return False
    for highlight in highlights:
        piece = highlight.strip()
        if not piece:
            continue
        if token == piece or (len(token) >= 2 and token in piece):
            return True
    return False
