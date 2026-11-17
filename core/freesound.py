"""Freesound beds that are free to use commercially (CC0 and CC BY)."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import httpx

from config.settings import Settings, get_settings
from core.asset_fetcher import _cached_download
from core.text import cjk_ratio

LogFn = Callable[[str], None]

# Solr filter. Attribution Noncommercial and Sampling+ stay out.
COMMERCIAL_FILTER = 'license:("Creative Commons 0" OR "Attribution")'
_SEARCH_URL = "https://freesound.org/apiv2/search/text/"
_MOOD = {
    "cinematic": "cinematic ambient",
    "documentary": "soft documentary",
    "bright": "light acoustic",
}


def commercial_license(value: str) -> bool:
    text = " ".join(value.lower().split())
    if not text:
        return False
    if any(token in text for token in ("noncommercial", "non-commercial", "by-nc", "sampling+", "by-nd")):
        return False
    if "publicdomain/zero" in text or text in {"creative commons 0", "cc0"}:
        return True
    if "by-sa" in text or "share alike" in text or "sharealike" in text:
        return False
    if "/licenses/by/" in text or text == "attribution":
        return True
    return False


def license_label(value: str) -> str:
    if "publicdomain/zero" in value.lower() or "creative commons 0" in value.lower():
        return "CC0"
    if commercial_license(value):
        return "CC BY"
    return value or "未知协议"


def pick_freesound(sound: dict, *, min_duration: float = 8.0) -> dict | None:
    try:
        duration = float(sound.get("duration") or 0)
    except (TypeError, ValueError):
        return None
    if duration < min_duration:
        return None
    license_value = str(sound.get("license") or "")
    if not commercial_license(license_value):
        return None
    previews = sound.get("previews") or {}
    url = previews.get("preview-hq-mp3") or previews.get("preview-lq-mp3") or previews.get("preview-hq-ogg")
    if not url:
        return None
    name = str(sound.get("name") or "Freesound").strip() or "Freesound"
    username = str(sound.get("username") or "Freesound").strip() or "Freesound"
    return {
        "id": sound.get("id"),
        "name": name,
        "username": username,
        "license": license_value,
        "duration": duration,
        "url": str(url),
    }


def bed_query(topic: str, style: str, keywords: list[str]) -> str:
    english = [word.strip() for word in keywords if word.strip() and cjk_ratio(word) < 0.3]
    if topic.strip() and cjk_ratio(topic) < 0.3:
        english.insert(0, topic.strip())
    mood = _MOOD.get(style, "ambient")
    core = " ".join(english[:5]).strip()
    if core:
        return f"{core} {mood} music"
    return f"{mood} music background"


def search_freesound(query: str, settings: Settings, *, min_duration: float = 8.0) -> list[dict]:
    if not settings.freesound_keys:
        return []
    timeout = httpx.Timeout(settings.request_timeout, connect=10.0)
    params = {
        "query": query,
        "filter": f"{COMMERCIAL_FILTER} duration:[{int(min_duration)} TO 300]",
        "fields": "id,name,username,license,duration,previews",
        "sort": "score",
        "page_size": 10,
        "group_by_pack": 0,
    }
    last_error: Exception | None = None
    for key in settings.freesound_keys:
        try:
            response = httpx.get(
                _SEARCH_URL,
                params=params,
                headers={"Authorization": f"Token {key}"},
                timeout=timeout,
            )
            response.raise_for_status()
            found = []
            for sound in response.json().get("results") or []:
                chosen = pick_freesound(sound, min_duration=min_duration)
                if chosen:
                    found.append(chosen)
            return found
        except (httpx.HTTPError, OSError, ValueError) as exc:
            last_error = exc
    if last_error:
        raise last_error
    return []


def fetch_bed(
    query: str,
    settings: Settings | None = None,
    log: LogFn | None = None,
) -> tuple[Path, str] | None:
    cfg = settings or get_settings()
    if not cfg.freesound_keys:
        return None
    queries = [query.strip() or "ambient music background"]
    if queries[0] != "ambient music background":
        queries.append("ambient music background")
    for text in queries:
        try:
            hits = search_freesound(text, cfg)
        except (httpx.HTTPError, OSError, ValueError) as exc:
            _log(f"Freesound 检索失败：{exc.__class__.__name__}", log)
            return None
        for hit in hits:
            suffix = ".ogg" if str(hit["url"]).lower().split("?", 1)[0].endswith(".ogg") else ".mp3"
            local = _cached_download(hit["url"], suffix, cfg)
            if local is None:
                _log("Freesound 音频下载超过时限，尝试下一条", log)
                continue
            credit = (
                f"背景音乐来自 Freesound「{hit['name']}」，作者 {hit['username']}"
                f"（{license_label(hit['license'])}，可免费商用）"
            )
            return local, credit
    return None


def _log(message: str, log: LogFn | None) -> None:
    if log:
        log(message)
