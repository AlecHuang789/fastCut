"""Keys saved from the web page. They override the same values in the environment."""

from __future__ import annotations

import json
from pathlib import Path

from config.providers import get_provider

ROOT = Path(__file__).resolve().parents[1]
LOCAL_PATH = ROOT / "config" / "local.json"

URL_FIELDS = ("openai_base_url", "image_api_url")
TEXT_LIMITS = {
    "openai_model": 160,
    "image_model": 160,
    "llm_provider": 40,
    "tts_provider": 40,
    "image_provider": 40,
    "tts_model": 160,
    "tts_voice": 160,
}
SECRET_FIELDS = (
    "openai_api_key",
    "pexels_api_key",
    "pixabay_api_key",
    "unsplash_access_key",
    "freesound_api_key",
    "image_api_key",
    "tts_api_key",
)
_KEY_BUCKETS = (
    ("openai_api_key", "llm_provider", "llm_keys"),
    ("tts_api_key", "tts_provider", "tts_keys"),
    ("image_api_key", "image_provider", "image_keys"),
)


def secret_hint(value: str) -> str:
    text = value.strip()
    if not text:
        return ""
    if len(text) < 8:
        return "已保存"
    return f"••••{text[-4:]}"


def read_local(path: Path | None = None) -> dict:
    target = path or LOCAL_PATH
    if not target.exists():
        return {}
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(key): value for key, value in data.items()}


def _clean_url(value: str) -> str:
    text = value.strip().rstrip("/")
    if not text:
        return ""
    if not text.startswith(("http://", "https://")) or any(char.isspace() for char in text):
        raise ValueError("地址要以 http:// 或 https:// 开头")
    return text


def _clean_text(value: str, limit: int = 80) -> str:
    text = " ".join(value.split())
    if len(text) > limit:
        raise ValueError("内容太长")
    return text


def _provider_id(kind: str, value: str) -> str:
    text = _clean_text(value, 40)
    if kind == "llm" and text in {"", "custom"}:
        return "custom" if text == "custom" else text
    if kind == "tts" and text in {"", "edge"}:
        return "edge"
    if kind == "image" and text in {"", "off"}:
        return "off"
    if get_provider(kind, text) is None:
        raise ValueError("不支持的服务")
    return text


def _clean_secret(value: str) -> str:
    text = value.strip()
    if any(char in text for char in "\r\n\t"):
        raise ValueError("密钥里不能有换行")
    if len(text) > 500:
        raise ValueError("密钥太长")
    return text


def save_config(updates: dict, path: Path | None = None) -> dict:
    """Apply a partial update. A non-empty secret replaces the saved one; clear_* empties it."""
    current = read_local(path)
    for key in URL_FIELDS:
        if key not in updates or updates[key] is None:
            continue
        current[key] = _clean_url(str(updates[key]))
    for key, limit in TEXT_LIMITS.items():
        if key not in updates or updates[key] is None:
            continue
        if key == "llm_provider":
            current[key] = _provider_id("llm", str(updates[key]))
        elif key == "tts_provider":
            current[key] = _provider_id("tts", str(updates[key]))
        elif key == "image_provider":
            current[key] = _provider_id("image", str(updates[key]))
        else:
            current[key] = _clean_text(str(updates[key]), limit)
    for key in SECRET_FIELDS:
        value = updates.get(key)
        clear = bool(updates.get(f"clear_{key}"))
        if isinstance(value, str) and value.strip():
            current[key] = _clean_secret(value)
        elif clear:
            current[key] = ""
    _apply_presets(current, updates)
    _remember_keys(current, updates)
    _write(current, path or LOCAL_PATH)
    return current


def _apply_presets(current: dict, updates: dict) -> None:
    if updates.get("llm_provider") is not None:
        provider = get_provider("llm", str(current.get("llm_provider") or ""))
        if provider and provider.id != "custom":
            current["openai_base_url"] = provider.base_url.rstrip("/")
            if not str(current.get("openai_model") or "").strip() and provider.default_model:
                current["openai_model"] = provider.default_model
    if updates.get("image_provider") is not None:
        provider_id = str(current.get("image_provider") or "off")
        if provider_id == "off":
            current["image_api_url"] = ""
        else:
            provider = get_provider("image", provider_id)
            if provider and provider.id != "custom" and provider.base_url:
                current["image_api_url"] = provider.base_url.rstrip("/")
            if provider and not str(current.get("image_model") or "").strip() and provider.default_model:
                current["image_model"] = provider.default_model
    if updates.get("tts_provider") is not None:
        provider = get_provider("tts", str(current.get("tts_provider") or "edge"))
        if provider and not str(current.get("tts_model") or "").strip() and provider.default_model:
            current["tts_model"] = provider.default_model
        if provider and not str(current.get("tts_voice") or "").strip() and provider.voices:
            current["tts_voice"] = provider.voices[0][0]


def _book(current: dict, name: str) -> dict[str, str]:
    raw = current.get(name)
    if not isinstance(raw, dict):
        return {}
    return {str(key): str(value) for key, value in raw.items() if str(value or "").strip()}


def _remember_keys(current: dict, updates: dict) -> None:
    for secret, provider_field, bucket in _KEY_BUCKETS:
        provider = str(current.get(provider_field) or "")
        typed = updates.get(secret)
        clear = bool(updates.get(f"clear_{secret}"))
        provider_sent = updates.get(provider_field) is not None
        if not provider:
            continue
        if not (isinstance(typed, str) and typed.strip()) and not clear and not provider_sent:
            continue
        saved = _book(current, bucket)
        if isinstance(typed, str) and typed.strip():
            saved[provider] = str(current.get(secret) or "")
        elif clear:
            saved.pop(provider, None)
        elif provider_sent and provider in saved:
            current[secret] = saved[provider]
        current[bucket] = saved


def _write(data: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)
