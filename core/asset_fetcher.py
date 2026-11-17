"""Stock footage search, with timeouts and a local art fallback."""

from __future__ import annotations

import base64
import hashlib
import re
from collections.abc import Callable
from pathlib import Path

import httpx
from PIL import Image, ImageDraw

from config.settings import Settings, get_settings
from core.models import Scene
from core.text import cjk_ratio

LogFn = Callable[[str], None]


def _log(message: str, log: LogFn | None) -> None:
    if log:
        log(message)


def orientation_matches(width: int, height: int, aspect: str) -> bool:
    if width < 1080 or height < 1080:
        return False
    if aspect == "9:16":
        return height > width
    return width > height


def pick_pexels_file(video: dict, aspect: str) -> dict | None:
    files = video.get("video_files") or []
    ranked = []
    for item in files:
        width = int(item.get("width") or 0)
        height = int(item.get("height") or 0)
        link = item.get("link") or ""
        file_type = item.get("file_type") or ""
        if "mp4" not in file_type and not link.lower().endswith(".mp4"):
            continue
        if not orientation_matches(width, height, aspect):
            continue
        ranked.append((width * height, item))
    if not ranked:
        return None
    ranked.sort(key=lambda pair: pair[0])
    # Prefer the smallest file that is still at least 1080p, so a 15s budget can finish.
    return ranked[0][1]


def _cache_path(url: str, suffix: str, settings: Settings) -> Path:
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:20]
    folder = settings.workspace_dir / "_cache" / "raw"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"{digest}{suffix}"


def download(url: str, dest: Path, settings: Settings, headers: dict | None = None) -> bool:
    timeout = httpx.Timeout(settings.request_timeout, connect=10.0)
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            with client.stream("GET", url, headers=headers or {}) as response:
                response.raise_for_status()
                dest.parent.mkdir(parents=True, exist_ok=True)
                temporary = dest.with_suffix(dest.suffix + ".part")
                with temporary.open("wb") as handle:
                    for chunk in response.iter_bytes():
                        handle.write(chunk)
                temporary.replace(dest)
        return dest.exists() and dest.stat().st_size > 1000
    except (httpx.HTTPError, OSError):
        dest.unlink(missing_ok=True)
        return False


def _cached_download(url: str, suffix: str, settings: Settings, headers: dict | None = None) -> Path | None:
    dest = _cache_path(url, suffix, settings)
    if dest.exists() and dest.stat().st_size > 1000:
        return dest
    if download(url, dest, settings, headers):
        return dest
    return None


def _pexels_videos(query: str, aspect: str, key: str, settings: Settings) -> list[dict]:
    orientation = "portrait" if aspect == "9:16" else "landscape"
    timeout = httpx.Timeout(settings.request_timeout, connect=10.0)
    response = httpx.get(
        "https://api.pexels.com/videos/search",
        params={"query": query, "orientation": orientation, "per_page": 8, "size": "large"},
        headers={"Authorization": key},
        timeout=timeout,
    )
    response.raise_for_status()
    found = []
    for video in response.json().get("videos") or []:
        chosen = pick_pexels_file(video, aspect)
        if chosen and chosen.get("link"):
            found.append({"url": chosen["link"], "kind": "video", "duration": video.get("duration") or 0})
    return found


def _pexels_photos(query: str, aspect: str, key: str, settings: Settings) -> list[dict]:
    orientation = "portrait" if aspect == "9:16" else "landscape"
    timeout = httpx.Timeout(settings.request_timeout, connect=10.0)
    response = httpx.get(
        "https://api.pexels.com/v1/search",
        params={"query": query, "orientation": orientation, "per_page": 6, "size": "large"},
        headers={"Authorization": key},
        timeout=timeout,
    )
    response.raise_for_status()
    found = []
    for photo in response.json().get("photos") or []:
        width = int(photo.get("width") or 0)
        height = int(photo.get("height") or 0)
        if not orientation_matches(width, height, aspect):
            continue
        src = (photo.get("src") or {}).get("large2x") or (photo.get("src") or {}).get("original")
        if src:
            found.append({"url": src, "kind": "image", "duration": 0})
    return found


def _pixabay_videos(query: str, aspect: str, key: str, settings: Settings) -> list[dict]:
    timeout = httpx.Timeout(settings.request_timeout, connect=10.0)
    response = httpx.get(
        "https://pixabay.com/api/videos/",
        params={"key": key, "q": query, "per_page": 8},
        timeout=timeout,
    )
    response.raise_for_status()
    found = []
    for hit in response.json().get("hits") or []:
        videos = hit.get("videos") or {}
        for size_name in ("large", "medium", "small"):
            item = videos.get(size_name) or {}
            width = int(item.get("width") or 0)
            height = int(item.get("height") or 0)
            url = item.get("url")
            if url and orientation_matches(width, height, aspect):
                found.append({"url": url, "kind": "video", "duration": hit.get("duration") or 0})
                break
    return found


def pick_unsplash_photo(photo: dict, aspect: str) -> dict | None:
    width = int(photo.get("width") or 0)
    height = int(photo.get("height") or 0)
    if not orientation_matches(width, height, aspect):
        return None
    urls = photo.get("urls") or {}
    link = urls.get("regular") or urls.get("full") or urls.get("small")
    if not link:
        return None
    user = photo.get("user") or {}
    credit = str(user.get("name") or "Unsplash").strip() or "Unsplash"
    download_at = str((photo.get("links") or {}).get("download_location") or "")
    return {
        "url": link,
        "kind": "image",
        "duration": 0,
        "credit": credit,
        "download_location": download_at,
    }


def unsplash_results(payload: dict, aspect: str, client_id: str = "") -> list[dict]:
    found = []
    for photo in payload.get("results") or []:
        chosen = pick_unsplash_photo(photo, aspect)
        if not chosen:
            continue
        if client_id:
            chosen["client_id"] = client_id
        found.append(chosen)
    return found


def _unsplash_photos(query: str, aspect: str, key: str, settings: Settings) -> list[dict]:
    orientation = "portrait" if aspect == "9:16" else "landscape"
    timeout = httpx.Timeout(settings.request_timeout, connect=10.0)
    response = httpx.get(
        "https://api.unsplash.com/search/photos",
        params={"query": query, "orientation": orientation, "per_page": 8, "content_filter": "high"},
        headers={"Authorization": f"Client-ID {key}", "Accept-Version": "v1"},
        timeout=timeout,
    )
    response.raise_for_status()
    return unsplash_results(response.json(), aspect, key)


def _track_unsplash(location: str, client_id: str, settings: Settings) -> None:
    if not location or not client_id:
        return
    timeout = httpx.Timeout(settings.request_timeout, connect=10.0)
    try:
        httpx.get(location, headers={"Authorization": f"Client-ID {client_id}"}, timeout=timeout)
    except (httpx.HTTPError, OSError):
        return


def _search(query: str, aspect: str, settings: Settings, log: LogFn | None, source: str = "auto") -> list[dict]:
    hits: list[dict] = []
    if source in {"auto", "video"}:
        for key in settings.pexels_keys:
            try:
                hits.extend(_pexels_videos(query, aspect, key, settings))
                if hits:
                    return hits
            except (httpx.HTTPError, OSError) as exc:
                _log(f"Pexels 视频检索失败，切换备用：{exc.__class__.__name__}", log)
        for key in settings.pixabay_keys:
            try:
                hits.extend(_pixabay_videos(query, aspect, key, settings))
                if hits:
                    return hits
            except (httpx.HTTPError, OSError) as exc:
                _log(f"Pixabay 视频检索失败：{exc.__class__.__name__}", log)
        if source == "video":
            return hits
    if source in {"auto", "web"}:
        for key in settings.unsplash_keys:
            try:
                found = _unsplash_photos(query, aspect, key, settings)
                if found:
                    return found
            except (httpx.HTTPError, OSError) as exc:
                _log(f"Unsplash 图片检索失败：{exc.__class__.__name__}", log)
        for key in settings.pexels_keys:
            try:
                hits.extend(_pexels_photos(query, aspect, key, settings))
                if hits:
                    return hits
            except (httpx.HTTPError, OSError) as exc:
                _log(f"Pexels 图片检索失败：{exc.__class__.__name__}", log)
    return hits


def _clip_score(image_path: Path, text: str, settings: Settings) -> float | None:
    if not settings.clip_enable:
        return None
    try:
        import open_clip
        import torch
        from PIL import Image as PilImage
    except Exception:
        return None
    if not hasattr(_clip_score, "_model"):
        model, _, preprocess = open_clip.create_model_and_transforms("ViT-B-32", pretrained="openai")
        tokenizer = open_clip.get_tokenizer("ViT-B-32")
        model.eval()
        _clip_score._model = (model, preprocess, tokenizer)  # type: ignore[attr-defined]
    model, preprocess, tokenizer = _clip_score._model  # type: ignore[attr-defined]
    image = preprocess(PilImage.open(image_path).convert("RGB")).unsqueeze(0)
    tokens = tokenizer([text])
    with torch.no_grad():
        image_features = model.encode_image(image)
        text_features = model.encode_text(tokens)
        image_features = image_features / image_features.norm(dim=-1, keepdim=True)
        text_features = text_features / text_features.norm(dim=-1, keepdim=True)
        return float((image_features @ text_features.T).squeeze().item())


def _preview_frame(path: Path, dest: Path, settings: Settings) -> Path | None:
    if path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}:
        return path
    from core.ffmpeg_engine import run_cmd

    try:
        run_cmd(
            [settings.ffmpeg_bin, "-y", "-ss", "0.4", "-i", str(path), "-frames:v", "1", str(dest)],
            timeout=20,
        )
    except RuntimeError:
        return None
    return dest if dest.exists() else None


def paint_fallback(scene: Scene, dest: Path, width: int, height: int) -> Path:
    digest = hashlib.sha256(" ".join(scene.search_keywords).encode("utf-8")).digest()
    color_a = tuple(digest[0:3])
    color_b = tuple(digest[3:6])
    color_c = tuple(digest[6:9])
    image = Image.new("RGB", (width, height), color_a)
    draw = ImageDraw.Draw(image, "RGBA")
    for y in range(height):
        blend = y / max(1, height - 1)
        color = tuple(int(color_a[channel] * (1 - blend) + color_b[channel] * blend) for channel in range(3))
        draw.line([(0, y), (width, y)], fill=color)
    for index in range(5):
        cx = int(digest[index] / 255 * width)
        cy = int(digest[index + 3] / 255 * height)
        radius = 180 + int(digest[index + 1] / 255 * 420)
        draw.ellipse(
            [cx - radius, cy - radius, cx + radius, cy + radius],
            fill=(*color_c, 90),
        )
    dest.parent.mkdir(parents=True, exist_ok=True)
    image.save(dest, quality=92)
    return dest


def sd_dimensions(aspect: str, settings: Settings) -> tuple[int, int]:
    if settings.sd_width > 0 and settings.sd_height > 0:
        return _multiple_of_8(settings.sd_width), _multiple_of_8(settings.sd_height)
    if aspect == "16:9":
        return 768, 432
    if aspect == "4:3":
        return 768, 576
    return 432, 768


def _multiple_of_8(value: int) -> int:
    return max(64, value // 8 * 8)


_ANIME_TOKENS = (
    "anime",
    "niji",
    "anything",
    "counterfeit",
    "tmnd",
    "cetus",
    "waifu",
    "pony",
    "illustrious",
    "meina",
    "pastel",
)
_REAL_TOKENS = (
    "realistic",
    "realvis",
    "photon",
    "juggernaut",
    "majic",
    "dreamshaper",
    "deliberate",
    "chillout",
    "9527",
)
_FAST_TOKENS = ("lcm", "lightning", "turbo", "hyper")


def model_family(title: str) -> str:
    name = title.lower()
    if any(token in name for token in _ANIME_TOKENS):
        return "anime"
    if any(token in name for token in _REAL_TOKENS):
        return "real"
    return "general"


def wanted_family(visual_style: str) -> str:
    """Storyboard style never selects an anime checkpoint. Comic-realistic is the default."""
    del visual_style
    return "real"


def _compact_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def match_checkpoint(needle: str, titles: list[str]) -> str | None:
    """Match REALISTIC_COMIC_BOOK to realisticComicBook_v10.safetensors and similar names."""
    key = _compact_name(needle)
    if not key:
        return None
    for title in titles:
        if key in _compact_name(title):
            return title
    tokens = [token for token in re.split(r"[^a-z0-9]+", needle.lower()) if len(token) >= 4]
    if not tokens:
        return None
    best: str | None = None
    best_score = 0
    for title in titles:
        folded = _compact_name(title)
        score = sum(1 for token in tokens if token in folded)
        if score > best_score:
            best = title
            best_score = score
    if best is not None and best_score == len(tokens):
        return best
    return None


def checkpoint_titles(models: list[dict]) -> list[str]:
    titles = []
    for item in models:
        title = str(item.get("title") or item.get("model_name") or "").strip()
        if title:
            titles.append(title)
    return titles


def sd_model_order(scene: Scene, models: list[dict], forced: str = "") -> list[str]:
    """Prefer the configured checkpoint. Never invent a title that is not installed."""
    titles = checkpoint_titles(models)
    if not titles:
        return []
    if forced.strip():
        matched = match_checkpoint(forced, titles)
        return [matched] if matched else []
    family = wanted_family(scene.visual_style)
    preferred = [title for title in titles if model_family(title) == family]
    if not preferred:
        preferred = [title for title in titles if model_family(title) != "anime"]
    pool = preferred or titles
    chosen = pool[sum(ord(char) for char in family) % len(pool)]
    rest = [title for title in pool if title != chosen]
    rest.extend(title for title in titles if title not in pool and model_family(title) != "anime")
    return [chosen, *rest]


def sampling_for_checkpoint(settings: Settings, checkpoint: str) -> tuple[int, float]:
    name = checkpoint.lower()
    if any(token in name for token in _FAST_TOKENS):
        return min(settings.sd_steps, 8), min(settings.sd_cfg, 2.0)
    return settings.sd_steps, settings.sd_cfg


_STYLE_PROMPT = (
    "realistic illustration, intricate details, cinematic, "
    "extreme wide shot, long shot, establishing shot, vast environment, "
    "tiny distant people, figures small in the frame, environment dominates"
)
_CLOSE_WORDS = ("portrait", "close-up", "closeup", "headshot", "selfie", "face", "bust")
_WIDE_NEGATIVE = (
    "close-up, closeup, portrait, headshot, selfie, bust shot, face focus, large face, "
    "foreground person, anime girl, 1girl, loli, chibi, big eyes, medium shot, cowboy shot, large character"
)


def _wide_keywords(keywords: list[str]) -> list[str]:
    kept: list[str] = []
    for word in keywords:
        low = word.lower()
        if cjk_ratio(word) >= 0.3:
            continue
        if any(token in low for token in _CLOSE_WORDS):
            continue
        kept.append(word)
    return kept[:4]


def sd_prompt(scene: Scene) -> str:
    parts = [_STYLE_PROMPT]
    keywords = ", ".join(_wide_keywords(scene.search_keywords))
    if keywords:
        parts.append(keywords)
    narration = " ".join(scene.narration.split())
    if narration and cjk_ratio(narration) < 0.3:
        parts.append(narration)
    return ", ".join(parts)


def sd_negative(settings: Settings) -> str:
    base = settings.sd_negative.strip().rstrip(",")
    extra = [part for part in _WIDE_NEGATIVE.split(", ") if part.lower() not in base.lower()]
    if not extra:
        return base
    return f"{base}, {', '.join(extra)}"


def decode_sd_image(payload: dict) -> bytes | None:
    images = payload.get("images") or []
    if not images or not isinstance(images[0], str):
        return None
    raw = images[0]
    if raw.startswith("data:") and "," in raw:
        raw = raw.split(",", 1)[1]
    try:
        data = base64.b64decode(raw, validate=True)
    except (ValueError, TypeError):
        return None
    return data or None


def _sd_payload(scene: Scene, aspect: str, settings: Settings, checkpoint: str = "") -> dict:
    width, height = sd_dimensions(aspect, settings)
    steps, cfg = sampling_for_checkpoint(settings, checkpoint)
    payload: dict = {
        "prompt": sd_prompt(scene),
        "negative_prompt": sd_negative(settings),
        "steps": steps,
        "cfg_scale": cfg,
        "sampler_name": settings.sd_sampler,
        "width": width,
        "height": height,
        "seed": -1,
        "batch_size": 1,
        "n_iter": 1,
        "save_images": False,
    }
    if settings.sd_scheduler:
        payload["scheduler"] = settings.sd_scheduler
    return payload


def _checkpoint_name(title: str) -> str:
    return title.split("[", 1)[0].strip() or title


def _same_checkpoint(current: str, wanted: str) -> bool:
    if not current or not wanted:
        return False
    return current == wanted or wanted in current or current in wanted


def _switch_checkpoint(client: httpx.Client, settings: Settings, title: str, log: LogFn | None) -> None:
    options_url = f"{settings.sd_webui_url}/sdapi/v1/options"
    current = str(client.get(options_url).json().get("sd_model_checkpoint") or "")
    if _same_checkpoint(current, title):
        return
    _log(f"切换生图模型：{_checkpoint_name(title)}", log)
    response = client.post(options_url, json={"sd_model_checkpoint": title})
    response.raise_for_status()


def _stable_diffusion(scene: Scene, dest: Path, aspect: str, settings: Settings, log: LogFn | None) -> Path | None:
    if not settings.sd_webui_enable:
        return None
    url = f"{settings.sd_webui_url}/sdapi/v1/txt2img"
    timeout = httpx.Timeout(settings.sd_timeout, connect=3.0)
    auth = None
    if settings.sd_webui_user:
        auth = (settings.sd_webui_user, settings.sd_webui_password)
    try:
        with httpx.Client(timeout=timeout, auth=auth, trust_env=False) as client:
            listed = client.get(f"{settings.sd_webui_url}/sdapi/v1/sd-models")
            listed.raise_for_status()
            listed_models = listed.json()
            order = sd_model_order(scene, listed_models, settings.sd_model)
            if settings.sd_model.strip():
                if not order:
                    names = ", ".join(_checkpoint_name(title) for title in checkpoint_titles(listed_models)[:8])
                    _log(f"没有找到模型 {settings.sd_model}。当前已安装：{names or '无'}", log)
                    return None
                order = order[:1]
            else:
                order = order[:3]
            if not order:
                order = [""]
            last_detail = ""
            for title in order:
                if title:
                    _switch_checkpoint(client, settings, title, log)
                response = client.post(url, json=_sd_payload(scene, aspect, settings, title))
                if response.status_code >= 400:
                    last_detail = (response.text or "")[:180].replace("\n", " ")
                    _log(f"{_checkpoint_name(title) or '当前模型'} 出图失败，尝试下一个", log)
                    continue
                data = decode_sd_image(response.json())
                if not data:
                    last_detail = "没有返回图片"
                    continue
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(data)
                _log(f"分镜 {scene.scene_id} 使用 {_checkpoint_name(title) or '当前模型'} 出图", log)
                return dest
    except httpx.HTTPStatusError as exc:
        last_detail = (exc.response.text or "")[:180].replace("\n", " ")
        hint = "。请去掉 WebUI 启动参数 --xformers 后重启" if "xformers" in last_detail.lower() else ""
        _log(f"Stable Diffusion 返回错误{hint}：{last_detail}", log)
        return None
    except (httpx.HTTPError, OSError, ValueError) as exc:
        _log(f"Stable Diffusion 不可用，改用色块画面：{exc.__class__.__name__}", log)
        return None
    hint = "。请去掉 WebUI 启动参数 --xformers 后重启" if "xformers" in last_detail.lower() else ""
    _log(f"Stable Diffusion 出图失败{hint}：{last_detail or '没有返回图片'}", log)
    return None


def _image_api(prompt: str, dest: Path, settings: Settings, log: LogFn | None, aspect: str = "1:1") -> Path | None:
    from core.image_gen import generate_image

    return generate_image(prompt, dest, settings, log, aspect)


def fetch_scene_asset(
    scene: Scene,
    dest_dir: Path,
    aspect: str,
    *,
    settings: Settings | None = None,
    log: LogFn | None = None,
    used: set[str] | None = None,
    source: str = "auto",
) -> Scene:
    cfg = settings or get_settings()
    used = used if used is not None else set()
    origin = source if source in {"auto", "web", "sd", "video", "ai"} else "auto"
    dest_dir.mkdir(parents=True, exist_ok=True)
    queries = []
    if scene.search_keywords and origin not in {"sd", "ai"}:
        queries.append(" ".join(scene.search_keywords[:2]))
        queries.extend(scene.search_keywords)
    seen: set[str] = set()
    candidates: list[dict] = []
    for query in queries:
        if query in seen:
            continue
        seen.add(query)
        try:
            candidates.extend(_search(query, aspect, cfg, log, origin))
        except (httpx.HTTPError, OSError) as exc:
            _log(f"检索 {query} 失败：{exc.__class__.__name__}", log)
    for item in candidates:
        url = item["url"]
        if url in used:
            continue
        suffix = ".mp4" if item["kind"] == "video" else ".jpg"
        local = _cached_download(url, suffix, cfg)
        if local is None:
            _log("素材下载超过时限，尝试下一条", log)
            continue
        if cfg.clip_enable:
            frame = _preview_frame(local, dest_dir / f"scene_{scene.scene_id:03d}_frame.jpg", cfg)
            score = _clip_score(frame, scene.narration, cfg) if frame else None
            if score is not None and score < cfg.clip_threshold:
                _log(f"素材与旁白相似度过低（{score:.2f}），已丢弃", log)
                continue
        used.add(url)
        if item.get("download_location"):
            _track_unsplash(str(item["download_location"]), str(item.get("client_id") or ""), cfg)
        if item.get("credit"):
            _log(f"分镜 {scene.scene_id} 图片来自 Unsplash，作者 {item['credit']}", log)
        return scene.model_copy(update={"asset_path": str(local), "asset_kind": item["kind"]})

    still = dest_dir / f"scene_{scene.scene_id:03d}_art.jpg"
    if origin == "sd":
        generated = _stable_diffusion(scene, still, aspect, cfg, log)
    elif origin == "ai":
        generated = _image_api(scene.narration, still, cfg, log, aspect)
        if generated is None:
            _log("AI 生图没有出图，改用本地绘图或色块", log)
            generated = _stable_diffusion(scene, still, aspect, cfg, log)
    else:
        generated = _image_api(scene.narration, still, cfg, log, aspect)
        if generated is None:
            generated = _stable_diffusion(scene, still, aspect, cfg, log)
    if generated is None:
        from config.ffmpeg_presets import frame_size

        width, height = frame_size(aspect)
        paint_fallback(scene, still, width, height)
        _log(f"分镜 {scene.scene_id} 使用色块画面", log)
    return scene.model_copy(update={"asset_path": str(still), "asset_kind": "generated"})
