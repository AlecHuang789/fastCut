"""Remote image generation for the providers chosen in settings."""

from __future__ import annotations

import base64
import time
from pathlib import Path

import httpx

from config.providers import resolve_provider
from config.settings import Settings, get_settings


def generate_image(
    prompt: str,
    dest: Path,
    settings: Settings | None = None,
    log=None,
    aspect: str = "1:1",
) -> Path | None:
    cfg = settings or get_settings()
    provider = resolve_provider("image", cfg.image_provider, cfg.image_api_url)
    if provider.id == "off" or provider.protocol == "off":
        return None
    if not cfg.image_api_key:
        return None
    text = " ".join(prompt.split())[:1000] or "cinematic scene"
    try:
        if provider.protocol == "dashscope_image":
            return _dashscope(text, dest, cfg, aspect)
        if provider.protocol == "stability":
            return _stability(text, dest, cfg, aspect)
        if not cfg.image_api_url:
            return None
        return _openai_images(text, dest, cfg, aspect)
    except (httpx.HTTPError, OSError, ValueError, RuntimeError) as exc:
        if log:
            detail = " ".join(str(exc).split())[:180]
            log(f"远程文生图失败：{detail or exc.__class__.__name__}")
        return None


def openai_image_bytes(body: dict) -> bytes | None:
    data = (body.get("data") or [{}])[0]
    encoded = data.get("b64_json")
    if not encoded:
        return None
    return base64.b64decode(encoded)


def openai_image_url(body: dict) -> str:
    data = (body.get("data") or [{}])[0]
    return str(data.get("url") or "")


def _openai_images(prompt: str, dest: Path, settings: Settings, aspect: str) -> Path | None:
    model = settings.image_model or "dall-e-3"
    sizes = _openai_sizes(aspect)
    if sizes[0] != "1024x1024":
        sizes.append("1024x1024")
    timeout = httpx.Timeout(120.0, connect=10.0)
    headers = {"Authorization": f"Bearer {settings.image_api_key}", "Content-Type": "application/json"}
    last = "没有返回图片"
    with httpx.Client(timeout=timeout) as client:
        for size in sizes:
            payload: dict = {"model": model, "prompt": prompt, "size": size, "n": 1}
            if not model.startswith("gpt-image"):
                payload["response_format"] = "b64_json"
            response = client.post(settings.image_api_url, json=payload, headers=headers)
            if response.status_code == 400 and size != sizes[-1]:
                last = _detail(response)
                continue
            if response.status_code >= 400:
                raise RuntimeError(_detail(response) or f"HTTP {response.status_code}")
            body = response.json()
            raw = openai_image_bytes(body)
            if raw:
                dest.write_bytes(raw)
                return dest
            url = openai_image_url(body)
            if url and _download(client, url, dest):
                return dest
            last = "没有返回图片"
    raise RuntimeError(last)


def _dashscope(prompt: str, dest: Path, settings: Settings, aspect: str) -> Path | None:
    url = settings.image_api_url or "https://dashscope.aliyuncs.com/api/v1/services/aigc/text2image/image-synthesis"
    headers = {
        "Authorization": f"Bearer {settings.image_api_key}",
        "Content-Type": "application/json",
        "X-DashScope-Async": "enable",
    }
    payload = {
        "model": settings.image_model or "wanx2.1-t2i-turbo",
        "input": {"prompt": prompt},
        "parameters": {"size": _wanx_size(aspect), "n": 1},
    }
    timeout = httpx.Timeout(40.0, connect=10.0)
    with httpx.Client(timeout=timeout) as client:
        response = client.post(url, json=payload, headers=headers)
        if response.status_code >= 400:
            raise RuntimeError(_detail(response) or f"HTTP {response.status_code}")
        task_id = str((response.json().get("output") or {}).get("task_id") or "")
        if not task_id:
            raise RuntimeError("通义万相没有返回任务")
        poll = f"https://dashscope.aliyuncs.com/api/v1/tasks/{task_id}"
        auth = {"Authorization": headers["Authorization"]}
        for _ in range(30):
            result = client.get(poll, headers=auth)
            if result.status_code >= 400:
                raise RuntimeError(_detail(result) or f"HTTP {result.status_code}")
            output = result.json().get("output") or {}
            status = output.get("task_status")
            if status == "SUCCEEDED":
                image_url = str((output.get("results") or [{}])[0].get("url") or "")
                if image_url and _download(client, image_url, dest):
                    return dest
                raise RuntimeError("通义万相没有返回图片")
            if status in {"FAILED", "UNKNOWN", "CANCELED"}:
                message = str(output.get("message") or output.get("code") or "通义万相出图失败")
                raise RuntimeError(message[:180])
            time.sleep(2)
    raise RuntimeError("通义万相出图超时")


def _stability(prompt: str, dest: Path, settings: Settings, aspect: str) -> Path | None:
    model = settings.image_model or "core"
    if model == "core":
        url = "https://api.stability.ai/v2beta/stable-image/generate/core"
        fields = {
            "prompt": (None, prompt),
            "output_format": (None, "jpeg"),
            "aspect_ratio": (None, _stability_ratio(aspect)),
        }
    else:
        url = "https://api.stability.ai/v2beta/stable-image/generate/sd3"
        fields = {
            "prompt": (None, prompt),
            "model": (None, model),
            "output_format": (None, "jpeg"),
            "aspect_ratio": (None, _stability_ratio(aspect)),
        }
    headers = {"Authorization": f"Bearer {settings.image_api_key}", "Accept": "image/*"}
    timeout = httpx.Timeout(120.0, connect=10.0)
    response = httpx.post(url, headers=headers, files=fields, timeout=timeout)
    content_type = response.headers.get("content-type", "")
    if response.status_code < 400 and content_type.startswith("image") and response.content:
        dest.write_bytes(response.content)
        return dest
    if response.status_code >= 400:
        raise RuntimeError(_detail(response) or f"HTTP {response.status_code}")
    raise RuntimeError("Stability 没有返回图片")


def _openai_sizes(aspect: str) -> list[str]:
    if aspect == "9:16":
        return ["1024x1792"]
    if aspect == "16:9":
        return ["1792x1024"]
    return ["1024x1024"]


def _wanx_size(aspect: str) -> str:
    if aspect == "9:16":
        return "720*1280"
    if aspect == "16:9":
        return "1280*720"
    return "1024*1024"


def _stability_ratio(aspect: str) -> str:
    if aspect in {"9:16", "16:9", "1:1"}:
        return aspect
    return "1:1"


def _download(client: httpx.Client, url: str, dest: Path) -> bool:
    response = client.get(url, follow_redirects=True)
    response.raise_for_status()
    if not response.content:
        return False
    dest.write_bytes(response.content)
    return True


def _detail(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return " ".join((response.text or "").split())[:180]
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
            return str(error.get("message") or "")[:180]
        if isinstance(error, str):
            return error[:180]
        message = body.get("message") or body.get("msg")
        if message:
            return str(message)[:180]
    return " ".join((response.text or "").split())[:180]
