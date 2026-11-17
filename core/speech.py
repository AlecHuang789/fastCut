"""Speech synthesis for Edge TTS and the keyed providers in settings."""

from __future__ import annotations

import base64
import binascii
import json
from pathlib import Path

import httpx

from config.providers import resolve_provider
from config.settings import Settings, get_settings


def synthesize_speech(
    text: str,
    voice: str,
    dest: Path,
    settings: Settings | None = None,
    *,
    force_edge: bool = False,
) -> None:
    cfg = settings or get_settings()
    spoken = " ".join(text.split())
    if not spoken:
        raise RuntimeError("没有可朗读的文字")
    provider = resolve_provider("tts", "" if force_edge else cfg.tts_provider)
    if force_edge:
        provider = resolve_provider("tts", "edge")
    chosen = voice.strip() or cfg.tts_voice
    if provider.protocol == "edge":
        _edge(spoken, chosen or "zh-CN-XiaoxiaoNeural", dest)
        return
    if provider.needs_key and not cfg.tts_api_key:
        raise RuntimeError(f"未填写{provider.name}的密钥")
    model = cfg.tts_model or provider.default_model
    timeout = httpx.Timeout(60.0, connect=10.0)
    if provider.protocol == "openai_speech":
        _openai_speech(provider.base_url, cfg.tts_api_key, model, chosen, spoken, dest, timeout)
        return
    if provider.protocol == "elevenlabs":
        _elevenlabs(provider.base_url, cfg.tts_api_key, model, chosen, spoken, dest, timeout)
        return
    if provider.protocol == "minimax":
        _minimax(provider.base_url, cfg.tts_api_key, model, chosen, spoken, dest, timeout)
        return
    if provider.protocol == "dashscope":
        dash_model = chosen if chosen.startswith("sambert") else model
        _dashscope(cfg.tts_api_key, dash_model or "sambert-zhichu-v1", spoken, dest, timeout)
        return
    if provider.protocol == "fish":
        _fish(provider.base_url, cfg.tts_api_key, model or "s1", chosen, spoken, dest, timeout)
        return
    raise RuntimeError(f"还不支持 {provider.name} 的语音协议")


def audio_from_minimax(body: dict) -> bytes:
    base = body.get("base_resp") or {}
    code = base.get("status_code")
    if code not in (None, 0):
        raise RuntimeError(str(base.get("status_msg") or "MiniMax 语音失败")[:180])
    audio = str((body.get("data") or {}).get("audio") or "")
    if not audio:
        raise RuntimeError("MiniMax 没有返回音频")
    try:
        if all(char in "0123456789abcdefABCDEF" for char in audio) and len(audio) % 2 == 0:
            return bytes.fromhex(audio)
    except ValueError:
        pass
    try:
        return base64.b64decode(audio)
    except (binascii.Error, ValueError) as exc:
        raise RuntimeError("MiniMax 音频无法解析") from exc


def _edge(text: str, voice: str, dest: Path) -> None:
    import asyncio

    import edge_tts

    async def _save() -> None:
        communicate = edge_tts.Communicate(text, voice)
        await communicate.save(str(dest))

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        asyncio.run(_save())
        return
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(lambda: asyncio.run(_save())).result()


def _openai_speech(base: str, key: str, model: str, voice: str, text: str, dest: Path, timeout: httpx.Timeout) -> None:
    url = f"{base.rstrip('/')}/audio/speech"
    payload = {
        "model": model or "tts-1",
        "input": text[:4000],
        "voice": voice or "alloy",
        "response_format": "mp3",
    }
    response = httpx.post(url, json=payload, headers=_bearer(key), timeout=timeout)
    _audio_response(response, dest, "语音")


def _elevenlabs(base: str, key: str, model: str, voice: str, text: str, dest: Path, timeout: httpx.Timeout) -> None:
    voice_id = voice if voice and voice != "default" else "21m00Tcm4TlvDq8ikWAM"
    url = f"{base.rstrip('/')}/v1/text-to-speech/{voice_id}"
    headers = {"xi-api-key": key, "Accept": "audio/mpeg", "Content-Type": "application/json"}
    payload = {"text": text[:4000], "model_id": model or "eleven_multilingual_v2"}
    response = httpx.post(url, json=payload, headers=headers, timeout=timeout)
    _audio_response(response, dest, "ElevenLabs")


def _minimax(base: str, key: str, model: str, voice: str, text: str, dest: Path, timeout: httpx.Timeout) -> None:
    url = f"{base.rstrip('/')}/v1/t2a_v2"
    payload = {
        "model": model or "speech-02-turbo",
        "text": text[:4000],
        "stream": False,
        "voice_setting": {"voice_id": voice or "female-shaonv", "speed": 1, "vol": 1, "pitch": 0},
        "audio_setting": {"sample_rate": 32000, "bitrate": 128000, "format": "mp3", "channel": 1},
    }
    response = httpx.post(url, json=payload, headers=_bearer(key), timeout=timeout)
    _raise_for_status(response, "MiniMax")
    dest.write_bytes(audio_from_minimax(response.json()))


def _dashscope(key: str, model: str, text: str, dest: Path, timeout: httpx.Timeout) -> None:
    url = "https://dashscope.aliyuncs.com/api/v1/services/audio/tts/SpeechSynthesizer"
    payload = {"model": model, "input": {"text": text[:1000]}}
    response = httpx.post(url, json=payload, headers=_bearer(key), timeout=timeout)
    content_type = response.headers.get("content-type", "")
    if response.status_code < 400 and content_type.startswith("audio"):
        dest.write_bytes(response.content)
        return
    _raise_for_status(response, "通义语音")
    body = response.json()
    output = body.get("output") or {}
    audio = output.get("audio")
    encoded = audio.get("data") if isinstance(audio, dict) else audio
    if isinstance(encoded, str) and encoded:
        dest.write_bytes(base64.b64decode(encoded))
        return
    url_value = audio.get("url") if isinstance(audio, dict) else ""
    if url_value:
        _download(str(url_value), dest, timeout)
        return
    message = str(body.get("message") or "通义语音没有返回音频")
    raise RuntimeError(message[:180])


def _fish(base: str, key: str, model: str, voice: str, text: str, dest: Path, timeout: httpx.Timeout) -> None:
    url = f"{base.rstrip('/')}/v1/tts"
    payload: dict = {"text": text[:4000], "format": "mp3"}
    if voice and voice != "default":
        payload["reference_id"] = voice
    headers = {**_bearer(key), "model": model or "s1"}
    response = httpx.post(url, json=payload, headers=headers, timeout=timeout)
    _audio_response(response, dest, "Fish Audio")


def _bearer(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}


def _audio_response(response: httpx.Response, dest: Path, name: str) -> None:
    content_type = response.headers.get("content-type", "")
    if response.status_code < 400 and (content_type.startswith("audio") or "octet-stream" in content_type or not content_type):
        if response.content:
            dest.write_bytes(response.content)
            return
    _raise_for_status(response, name)
    if response.content:
        dest.write_bytes(response.content)
        return
    raise RuntimeError(f"{name}没有返回音频")


def _raise_for_status(response: httpx.Response, name: str) -> None:
    if response.status_code < 400:
        return
    detail = ""
    try:
        body = response.json()
    except (ValueError, json.JSONDecodeError):
        body = None
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
            detail = str(error.get("message") or "")
        elif isinstance(error, str):
            detail = error
        detail = detail or str(body.get("message") or body.get("msg") or "")
    if not detail:
        detail = " ".join((response.text or "").split())[:180]
    raise RuntimeError(f"{name}失败：{detail or response.status_code}"[:220])


def _download(url: str, dest: Path, timeout: httpx.Timeout) -> None:
    response = httpx.get(url, timeout=timeout, follow_redirects=True)
    response.raise_for_status()
    dest.write_bytes(response.content)
