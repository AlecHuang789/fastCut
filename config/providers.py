"""Catalog of LLM, speech, and image providers. Keys stay in the settings page."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Provider:
    id: str
    name: str
    region: str
    protocol: str
    base_url: str = ""
    default_model: str = ""
    models: tuple[tuple[str, str], ...] = ()
    voices: tuple[tuple[str, str], ...] = ()
    needs_key: bool = True
    hint: str = ""


def _provider(
    pid: str,
    name: str,
    region: str,
    protocol: str,
    base_url: str = "",
    default_model: str = "",
    models: tuple[tuple[str, str], ...] = (),
    voices: tuple[tuple[str, str], ...] = (),
    needs_key: bool = True,
    hint: str = "",
) -> Provider:
    return Provider(pid, name, region, protocol, base_url, default_model, models, voices, needs_key, hint)


_EDGE_VOICES = (
    ("zh-CN-XiaoxiaoNeural", "晓晓 · 女声"),
    ("zh-CN-XiaoyiNeural", "晓伊 · 女声"),
    ("zh-CN-YunxiNeural", "云希 · 男声"),
    ("zh-CN-YunjianNeural", "云健 · 男声"),
    ("zh-CN-YunyangNeural", "云扬 · 男声"),
)

LLM: tuple[Provider, ...] = (
    _provider(
        "deepseek", "DeepSeek", "cn", "openai",
        "https://api.deepseek.com/v1", "deepseek-chat",
        (("deepseek-chat", "DeepSeek Chat"), ("deepseek-reasoner", "DeepSeek Reasoner")),
        hint="国内可直接访问。密钥在 platform.deepseek.com。",
    ),
    _provider(
        "qwen", "通义千问", "cn", "openai",
        "https://dashscope.aliyuncs.com/compatible-mode/v1", "qwen-plus",
        (("qwen-plus", "Qwen Plus"), ("qwen-turbo", "Qwen Turbo"), ("qwen-max", "Qwen Max"), ("qwen-long", "Qwen Long")),
        hint="使用阿里云百炼的 DashScope API Key。",
    ),
    _provider(
        "zhipu", "智谱 GLM", "cn", "openai",
        "https://open.bigmodel.cn/api/paas/v4", "glm-4-flash",
        (("glm-4-flash", "GLM-4 Flash"), ("glm-4-air", "GLM-4 Air"), ("glm-4-plus", "GLM-4 Plus"), ("glm-4.5", "GLM-4.5")),
        hint="密钥在开放平台 open.bigmodel.cn。",
    ),
    _provider(
        "moonshot", "Kimi", "cn", "openai",
        "https://api.moonshot.cn/v1", "moonshot-v1-auto",
        (("moonshot-v1-auto", "Moonshot Auto"), ("moonshot-v1-8k", "Moonshot 8K"), ("moonshot-v1-32k", "Moonshot 32K"), ("kimi-latest", "Kimi Latest")),
        hint="密钥在 Moonshot 开放平台。",
    ),
    _provider(
        "doubao", "豆包", "cn", "openai",
        "https://ark.cn-beijing.volces.com/api/v3", "",
        (),
        hint="模型填火山方舟的接入点 ID，例如 ep- 开头的那一串。",
    ),
    _provider(
        "siliconflow", "硅基流动", "cn", "openai",
        "https://api.siliconflow.cn/v1", "deepseek-ai/DeepSeek-V3",
        (
            ("deepseek-ai/DeepSeek-V3", "DeepSeek V3"),
            ("Qwen/Qwen2.5-72B-Instruct", "Qwen2.5 72B"),
            ("THUDM/glm-4-9b-chat", "GLM-4 9B"),
        ),
        hint="一个密钥可以调用平台上的多个开源模型。",
    ),
    _provider(
        "minimax", "MiniMax", "cn", "openai",
        "https://api.minimaxi.com/v1", "MiniMax-Text-01",
        (("MiniMax-Text-01", "MiniMax Text"), ("MiniMax-M1", "MiniMax M1")),
        hint="密钥在 MiniMax 开放平台，国内接口是 api.minimaxi.com。",
    ),
    _provider(
        "stepfun", "阶跃星辰", "cn", "openai",
        "https://api.stepfun.com/v1", "step-2-mini",
        (("step-2-mini", "Step 2 Mini"), ("step-2-16k", "Step 2 16K")),
        hint="密钥在阶跃星辰开放平台。",
    ),
    _provider(
        "qianfan", "百度千帆", "cn", "openai",
        "https://qianfan.baidubce.com/v2", "deepseek-v3",
        (("deepseek-v3", "DeepSeek V3"), ("ernie-4.0-turbo-8k", "文心 4.0 Turbo"), ("ernie-speed-128k", "文心 Speed")),
        hint="使用千帆 ModelBuilder 的 API Key，兼容 OpenAI 格式。",
    ),
    _provider(
        "openai", "OpenAI", "intl", "openai",
        "https://api.openai.com/v1", "gpt-4o-mini",
        (("gpt-4o-mini", "GPT-4o mini"), ("gpt-4o", "GPT-4o"), ("gpt-4.1-mini", "GPT-4.1 mini"), ("gpt-4.1", "GPT-4.1")),
        hint="密钥在 platform.openai.com。",
    ),
    _provider(
        "anthropic", "Claude", "intl", "anthropic",
        "https://api.anthropic.com", "claude-sonnet-4-5",
        (("claude-sonnet-4-5", "Claude Sonnet 4.5"), ("claude-haiku-4-5", "Claude Haiku 4.5"), ("claude-opus-4-1", "Claude Opus 4.1")),
        hint="密钥在 console.anthropic.com。走 Anthropic 官方接口，不是 OpenAI 格式。",
    ),
    _provider(
        "gemini", "Gemini", "intl", "openai",
        "https://generativelanguage.googleapis.com/v1beta/openai", "gemini-2.5-flash",
        (("gemini-2.5-flash", "Gemini 2.5 Flash"), ("gemini-2.5-pro", "Gemini 2.5 Pro"), ("gemini-2.0-flash", "Gemini 2.0 Flash")),
        hint="使用 Google AI Studio 的 API Key。已接在 Gemini 的 OpenAI 兼容地址上。",
    ),
    _provider(
        "xai", "Grok", "intl", "openai",
        "https://api.x.ai/v1", "grok-3",
        (("grok-4", "Grok 4"), ("grok-3", "Grok 3"), ("grok-3-mini", "Grok 3 Mini")),
        hint="密钥在 console.x.ai。",
    ),
    _provider(
        "groq", "Groq", "intl", "openai",
        "https://api.groq.com/openai/v1", "llama-3.3-70b-versatile",
        (("llama-3.3-70b-versatile", "Llama 3.3 70B"), ("llama-3.1-8b-instant", "Llama 3.1 8B")),
        hint="密钥在 console.groq.com。模型名以控制台为准，列表里没有可以选自定义。",
    ),
    _provider(
        "mistral", "Mistral", "intl", "openai",
        "https://api.mistral.ai/v1", "mistral-small-latest",
        (("mistral-small-latest", "Mistral Small"), ("mistral-large-latest", "Mistral Large")),
        hint="密钥在 console.mistral.ai。",
    ),
    _provider(
        "openrouter", "OpenRouter", "intl", "openai",
        "https://openrouter.ai/api/v1", "openai/gpt-4o-mini",
        (
            ("openai/gpt-4o-mini", "GPT-4o mini"),
            ("anthropic/claude-sonnet-4.5", "Claude Sonnet 4.5"),
            ("deepseek/deepseek-chat", "DeepSeek Chat"),
        ),
        hint="一个密钥可以转接到多家模型。模型名以 openrouter.ai 为准。",
    ),
    _provider(
        "custom", "自定义接口", "other", "openai",
        "", "", (),
        hint="填写任何兼容 OpenAI /chat/completions 的地址，以 /v1 结尾。",
    ),
)

TTS: tuple[Provider, ...] = (
    _provider(
        "edge", "微软 Edge", "other", "edge",
        "", "", (), _EDGE_VOICES, False,
        hint="免费，不用密钥。国内网络有时会连不上微软的语音服务。",
    ),
    _provider(
        "minimax", "MiniMax 语音", "cn", "minimax",
        "https://api.minimaxi.com", "speech-02-turbo",
        (("speech-02-turbo", "Speech 02 Turbo"), ("speech-02-hd", "Speech 02 HD")),
        (
            ("female-shaonv", "少女"),
            ("female-yujie", "御姐"),
            ("female-chengshu", "成熟女声"),
            ("male-qn-qingse", "青涩男声"),
            ("male-qn-jingying", "精英男声"),
            ("presenter_female", "女播音"),
            ("presenter_male", "男播音"),
        ),
        hint="国内语音。不填密钥时，成片会改用微软免费配音。",
    ),
    _provider(
        "dashscope", "通义语音", "cn", "dashscope",
        "https://dashscope.aliyuncs.com", "sambert-zhichu-v1",
        (),
        (
            ("sambert-zhichu-v1", "知初 · 男声"),
            ("sambert-zhimiao-v1", "知妙 · 女声"),
            ("sambert-zhiyan-v1", "知燕 · 女声"),
            ("sambert-zhinan-v1", "知楠 · 男声"),
            ("sambert-zhiqi-v1", "知琪 · 女声"),
        ),
        hint="阿里云百炼的 DashScope Key，走 Sambert 语音合成。",
    ),
    _provider(
        "siliconflow", "硅基流动 CosyVoice", "cn", "openai_speech",
        "https://api.siliconflow.cn/v1", "FunAudioLLM/CosyVoice2-0.5B",
        (("FunAudioLLM/CosyVoice2-0.5B", "CosyVoice 2"),),
        (
            ("FunAudioLLM/CosyVoice2-0.5B:alex", "Alex"),
            ("FunAudioLLM/CosyVoice2-0.5B:anna", "Anna"),
            ("FunAudioLLM/CosyVoice2-0.5B:bella", "Bella"),
            ("FunAudioLLM/CosyVoice2-0.5B:benjamin", "Benjamin"),
            ("FunAudioLLM/CosyVoice2-0.5B:claire", "Claire"),
            ("FunAudioLLM/CosyVoice2-0.5B:david", "David"),
        ),
        hint="CosyVoice 2，走硅基流动的 OpenAI 语音接口。",
    ),
    _provider(
        "openai", "OpenAI", "intl", "openai_speech",
        "https://api.openai.com/v1", "tts-1",
        (("tts-1", "TTS-1"), ("tts-1-hd", "TTS-1 HD"), ("gpt-4o-mini-tts", "GPT-4o mini TTS")),
        (
            ("alloy", "Alloy"),
            ("echo", "Echo"),
            ("fable", "Fable"),
            ("onyx", "Onyx"),
            ("nova", "Nova"),
            ("shimmer", "Shimmer"),
        ),
        hint="密钥在 platform.openai.com。中文可用，口音偏英语音色。",
    ),
    _provider(
        "elevenlabs", "ElevenLabs", "intl", "elevenlabs",
        "https://api.elevenlabs.io", "eleven_multilingual_v2",
        (("eleven_multilingual_v2", "Multilingual v2"), ("eleven_turbo_v2_5", "Turbo v2.5")),
        (
            ("21m00Tcm4TlvDq8ikWAM", "Rachel"),
            ("EXAVITQu4vr4xnSDxMaL", "Bella"),
            ("MF3mGyEYCl7XYWbV9V6O", "Elli"),
            ("pNInz6obpgDQGcFmaJgB", "Adam"),
            ("ErXwobaYiN019PkySvjV", "Antoni"),
            ("TxGEqnHWrfWFTfGW9XjX", "Josh"),
        ),
        hint="密钥在 elevenlabs.io。多语言模型可以读中文。自定义音色 ID 也可以填。",
    ),
    _provider(
        "fish", "Fish Audio", "intl", "fish",
        "https://api.fish.audio", "s1",
        (("s1", "S1"), ("speech-1.5", "Speech 1.5")),
        (("default", "默认音色"),),
        hint="密钥在 fish.audio。克隆音色把 reference id 填进自定义音色 ID。",
    ),
)

IMAGE: tuple[Provider, ...] = (
    _provider(
        "off", "不使用", "other", "off",
        needs_key=False,
        hint="不调用远程生图。找不到图库素材时，仍会试本地 Stable Diffusion。",
    ),
    _provider(
        "qwen", "通义万相", "cn", "dashscope_image",
        "https://dashscope.aliyuncs.com/api/v1/services/aigc/text2image/image-synthesis",
        "wanx2.1-t2i-turbo",
        (("wanx2.1-t2i-turbo", "万相 2.1 Turbo"), ("wanx2.1-t2i-plus", "万相 2.1 Plus"), ("wanx-v1", "万相 v1")),
        hint="阿里云百炼的 DashScope Key。画面来源选「AI 生图」，或让自动模式在没素材时补图。",
    ),
    _provider(
        "zhipu", "智谱 CogView", "cn", "openai_images",
        "https://open.bigmodel.cn/api/paas/v4/images/generations",
        "cogview-3-flash",
        (("cogview-3-flash", "CogView 3 Flash"), ("cogview-4", "CogView 4")),
        hint="和智谱对话模型不是同一类接口，密钥可以是同一个开放平台 Key。",
    ),
    _provider(
        "siliconflow", "硅基流动", "cn", "openai_images",
        "https://api.siliconflow.cn/v1/images/generations",
        "black-forest-labs/FLUX.1-schnell",
        (
            ("black-forest-labs/FLUX.1-schnell", "FLUX.1 Schnell"),
            ("Kwai-Kolors/Kolors", "可图 Kolors"),
            ("stabilityai/stable-diffusion-3-5-large", "SD 3.5 Large"),
        ),
        hint="Flux、可图和 SD 3.5 都走这里，按模型名切换。",
    ),
    _provider(
        "openai", "OpenAI", "intl", "openai_images",
        "https://api.openai.com/v1/images/generations",
        "dall-e-3",
        (("dall-e-3", "DALL·E 3"), ("dall-e-2", "DALL·E 2"), ("gpt-image-1", "GPT Image")),
        hint="密钥在 platform.openai.com。",
    ),
    _provider(
        "stability", "Stability", "intl", "stability",
        "https://api.stability.ai/v2beta/stable-image/generate/core",
        "core",
        (("core", "Stable Image Core"), ("sd3.5-large", "SD 3.5 Large")),
        hint="密钥在 platform.stability.ai。",
    ),
    _provider(
        "custom", "自定义接口", "other", "openai_images",
        "", "dall-e-3", (),
        hint="填写兼容 OpenAI images/generations 的完整地址。",
    ),
)

CATALOG = {"llm": LLM, "tts": TTS, "image": IMAGE}


def providers(kind: str) -> tuple[Provider, ...]:
    return CATALOG[kind]


def get_provider(kind: str, provider_id: str) -> Provider | None:
    wanted = (provider_id or "").strip()
    if not wanted:
        return None
    for item in CATALOG[kind]:
        if item.id == wanted:
            return item
    return None


def match_base(kind: str, url: str) -> Provider | None:
    text = (url or "").strip().rstrip("/")
    if not text:
        return None
    found: Provider | None = None
    for item in CATALOG[kind]:
        base = item.base_url.rstrip("/")
        if base and (text == base or text.startswith(base + "/")):
            if found is None or len(base) > len(found.base_url):
                found = item
    return found


def resolve_provider(kind: str, provider_id: str, base_url: str = "") -> Provider:
    chosen = get_provider(kind, provider_id)
    if chosen:
        return chosen
    matched = match_base(kind, base_url)
    if matched:
        return matched
    if kind == "tts":
        return get_provider("tts", "edge")  # type: ignore[return-value]
    if kind == "image":
        if (base_url or "").strip():
            return get_provider("image", "custom")  # type: ignore[return-value]
        return get_provider("image", "off")  # type: ignore[return-value]
    if (base_url or "").strip() and "api.openai.com" not in base_url:
        return get_provider("llm", "custom")  # type: ignore[return-value]
    return get_provider("llm", "openai")  # type: ignore[return-value]


def public_catalog() -> dict:
    payload = {}
    for kind, items in CATALOG.items():
        payload[kind] = [
            {
                "id": item.id,
                "name": item.name,
                "region": item.region,
                "protocol": item.protocol,
                "base_url": item.base_url,
                "default_model": item.default_model,
                "models": [list(pair) for pair in item.models],
                "voices": [list(pair) for pair in item.voices],
                "needs_key": item.needs_key,
                "hint": item.hint,
            }
            for item in items
        ]
    return payload


def voice_choices(provider: Provider, saved_voice: str) -> list[list[str]]:
    choices = [list(pair) for pair in provider.voices]
    voice = (saved_voice or "").strip()
    if voice and all(voice != pair[0] for pair in choices):
        choices.append([voice, voice])
    return choices
