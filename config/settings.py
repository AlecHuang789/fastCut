"""Environment-backed settings."""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

from config.local_store import read_local

ROOT = Path(__file__).resolve().parents[1]


def _flag(name: str, default: str = "0") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


def _prefer(local: dict, name: str, env_value: str) -> str:
    if name in local:
        return str(local.get(name) or "").strip()
    return env_value.strip()


def _prefer_keys(local: dict, name: str, *env_names: str) -> tuple[str, ...]:
    if name in local:
        raw = str(local.get(name) or "")
        parts = [piece.strip() for piece in raw.split(",") if piece.strip()]
        return tuple(dict.fromkeys(parts))
    return _keys(*env_names)


def _map(local: dict, name: str) -> dict[str, str]:
    raw = local.get(name)
    if not isinstance(raw, dict):
        return {}
    saved: dict[str, str] = {}
    for key, value in raw.items():
        text = str(value or "").strip()
        if text:
            saved[str(key)] = text
    return saved


def _keys(*names: str) -> tuple[str, ...]:
    parts: list[str] = []
    for name in names:
        raw = os.getenv(name, "")
        parts.extend(piece.strip() for piece in raw.split(","))
    return tuple(dict.fromkeys(piece for piece in parts if piece))


def _bin(env_name: str, fallback: str) -> str:
    configured = os.getenv(env_name, "").strip()
    if configured:
        return configured
    found = shutil.which(fallback)
    return found or fallback


@dataclass(frozen=True)
class Settings:
    root: Path
    openai_api_key: str
    openai_base_url: str
    openai_model: str
    pexels_keys: tuple[str, ...]
    pixabay_keys: tuple[str, ...]
    unsplash_keys: tuple[str, ...]
    freesound_keys: tuple[str, ...]
    image_api_url: str
    image_api_key: str
    image_model: str
    image_provider: str
    image_keys: dict[str, str]
    llm_provider: str
    llm_keys: dict[str, str]
    tts_provider: str
    tts_model: str
    tts_api_key: str
    tts_keys: dict[str, str]
    sd_webui_enable: bool
    sd_webui_url: str
    sd_webui_user: str
    sd_webui_password: str
    sd_steps: int
    sd_cfg: float
    sd_sampler: str
    sd_scheduler: str
    sd_model: str
    sd_width: int
    sd_height: int
    sd_timeout: float
    sd_negative: str
    tts_voice: str
    bgm_path: str
    font_path: str
    clip_enable: bool
    clip_threshold: float
    cleanup_hours: float
    request_timeout: float
    workspace_dir: Path
    output_dir: Path
    templates_dir: Path
    ffmpeg_bin: str
    ffprobe_bin: str

    @property
    def llm_ready(self) -> bool:
        return bool(self.openai_api_key)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    load_dotenv(ROOT / ".env", override=False)
    local = read_local()
    base_url = _prefer(local, "openai_base_url", os.getenv("OPENAI_BASE_URL", "").strip()) or "https://api.openai.com/v1"
    model = _prefer(local, "openai_model", os.getenv("OPENAI_MODEL", "").strip()) or "gpt-4o-mini"
    image_model = _prefer(local, "image_model", os.getenv("IMAGE_MODEL", "").strip()) or "dall-e-3"
    return Settings(
        root=ROOT,
        openai_api_key=_prefer(local, "openai_api_key", os.getenv("OPENAI_API_KEY", "").strip()),
        openai_base_url=base_url.rstrip("/"),
        openai_model=model,
        pexels_keys=_prefer_keys(local, "pexels_api_key", "PEXELS_API_KEY", "PEXELS_API_KEY_BACKUP"),
        pixabay_keys=_prefer_keys(local, "pixabay_api_key", "PIXABAY_API_KEY", "PIXABAY_API_KEY_BACKUP"),
        unsplash_keys=_prefer_keys(local, "unsplash_access_key", "UNSPLASH_ACCESS_KEY"),
        freesound_keys=_prefer_keys(local, "freesound_api_key", "FREESOUND_API_KEY"),
        image_api_url=_prefer(local, "image_api_url", os.getenv("IMAGE_API_URL", "").strip()),
        image_api_key=_prefer(local, "image_api_key", os.getenv("IMAGE_API_KEY", "").strip()),
        image_model=image_model,
        image_provider=_prefer(local, "image_provider", ""),
        image_keys=_map(local, "image_keys"),
        llm_provider=_prefer(local, "llm_provider", ""),
        llm_keys=_map(local, "llm_keys"),
        tts_provider=_prefer(local, "tts_provider", "") or "edge",
        tts_model=_prefer(local, "tts_model", ""),
        tts_api_key=_prefer(local, "tts_api_key", ""),
        tts_keys=_map(local, "tts_keys"),
        sd_webui_enable=_flag("SD_WEBUI_ENABLE", "1"),
        sd_webui_url=os.getenv("SD_WEBUI_URL", "http://127.0.0.1:7860").rstrip("/"),
        sd_webui_user=os.getenv("SD_WEBUI_USER", "").strip(),
        sd_webui_password=os.getenv("SD_WEBUI_PASSWORD", "").strip(),
        sd_steps=int(os.getenv("SD_STEPS", "20")),
        sd_cfg=float(os.getenv("SD_CFG", "7")),
        sd_sampler=os.getenv("SD_SAMPLER", "Euler a").strip(),
        sd_scheduler=os.getenv("SD_SCHEDULER", "").strip(),
        sd_model=os.getenv("SD_MODEL", "REALISTIC_COMIC_BOOK").strip(),
        sd_width=int(os.getenv("SD_WIDTH", "0")),
        sd_height=int(os.getenv("SD_HEIGHT", "0")),
        sd_timeout=float(os.getenv("SD_TIMEOUT", "180")),
        sd_negative=os.getenv(
            "SD_NEGATIVE",
            "((frame, photo, 3D)), poorly drawn hair, squiggles, drawing artefact, child, black and white, sketch, artefact, text, cover, photo of pencil, frame, mutilated, extra fingers, mutated hands, poorly drawn hands, mutation, deformed, blurry, bad anatomy, extra limbs, cloned face, disfigured, malformed limbs, missing arms, missing legs, extra arms, extra legs, fused fingers, too many fingers, watermark, font, text, cropped, out of frame, worst quality, low quality, jpeg artifacts, duplicate, BadDream, UnrealisticDream, close-up, closeup, portrait, headshot, selfie, bust shot, face focus, large face, foreground person",
        ).strip(),
        tts_voice=_prefer(local, "tts_voice", os.getenv("TTS_VOICE", "").strip()) or "zh-CN-XiaoxiaoNeural",
        bgm_path=os.getenv("BGM_PATH", "").strip(),
        font_path=os.getenv("FONT_PATH", "").strip(),
        clip_enable=_flag("CLIP_ENABLE"),
        clip_threshold=float(os.getenv("CLIP_THRESHOLD", "0.22")),
        cleanup_hours=float(os.getenv("CLEANUP_HOURS", "24")),
        request_timeout=float(os.getenv("REQUEST_TIMEOUT", "15")),
        workspace_dir=ROOT / "workspace",
        output_dir=ROOT / "output",
        templates_dir=ROOT / "templates" / "hyperframes",
        ffmpeg_bin=_bin("FFMPEG_BIN", "ffmpeg"),
        ffprobe_bin=_bin("FFPROBE_BIN", "ffprobe"),
    )


def reset_settings() -> None:
    get_settings.cache_clear()
