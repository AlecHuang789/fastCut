export const VOICES = [
  ["zh-CN-XiaoxiaoNeural", "晓晓 · 女声"],
  ["zh-CN-XiaoyiNeural", "晓伊 · 女声"],
  ["zh-CN-YunxiNeural", "云希 · 男声"],
  ["zh-CN-YunjianNeural", "云健 · 男声"],
  ["zh-CN-YunyangNeural", "云扬 · 男声"],
];

export const STEPS = [
  ["storyboard", "分镜"],
  ["review", "确认"],
  ["voice", "配音"],
  ["transcribe", "字幕"],
  ["assets", "画面"],
  ["cut", "切片"],
  ["overlay", "字幕画面"],
  ["encode", "合成"],
  ["done", "完成"],
];

export const STYLES = [
  ["cinematic", "电影"],
  ["documentary", "纪实"],
  ["bright", "明亮"],
];

export const ASPECTS = [
  ["9:16", "竖屏 9:16"],
  ["16:9", "横屏 16:9"],
  ["4:3", "4:3"],
];

export const QUALITIES = [
  ["draft", "草稿，速度优先"],
  ["looks", "观感更好"],
  ["delivery", "交付"],
];

export const SOURCES = [
  ["auto", "自动：先找素材，没有再 AI 生图"],
  ["web", "网络图片（Unsplash / Pexels）"],
  ["video", "只要视频素材"],
  ["ai", "AI 生图（密钥里选的模型）"],
  ["sd", "本地 Stable Diffusion"],
];

export const AUDIOS = [
  ["auto", "有密钥就用可商用配乐"],
  ["freesound", "Freesound（CC0 / CC BY）"],
  ["none", "不用网络配乐"],
];

export const SOURCE_LABEL = {
  auto: "自动选素材",
  web: "网络图片",
  sd: "本地绘图",
  video: "视频素材",
  ai: "AI 生图",
};

export const STYLE_LABEL = {
  cinematic: "电影",
  documentary: "纪实",
  bright: "明亮",
};

export const AUDIO_LABEL = {
  auto: "自动配乐",
  freesound: "可商用配乐",
  none: "无网络配乐",
};

export const QUALITY_LABEL = {
  draft: "草稿",
  looks: "观感更好",
  delivery: "交付",
};

export const STATE = {
  running: "生成中",
  review: "待确认",
  stopping: "正在停止",
  done: "已完成",
  failed: "失败",
};

export const MODES = [
  ["agent", "自动成片"],
  ["create", "手动调整"],
  ["highlight", "高光切片"],
];

export const MODE_HINT = {
  agent: "写清楚主题、时长和风格。Ctrl + Enter 开始生成。",
  create: "先出分镜。确认文案后才会配音和找画面。",
  highlight: "高光需要上传原片，不能只靠一句话。",
};

const ALLOWED = {
  mode: new Set(["agent", "create", "highlight"]),
  aspect: new Set(["9:16", "16:9", "4:3"]),
  quality: new Set(["draft", "looks", "delivery"]),
  source: new Set(["auto", "web", "video", "sd", "ai"]),
  style: new Set(["", "cinematic", "documentary", "bright"]),
  audio: new Set(["auto", "freesound", "none"]),
  voice: new Set(VOICES.map(([value]) => value)),
};

export function allowed(group, value, fallback) {
  if (group === "voice") {
    return typeof value === "string" && /^[\w:./@+-]{1,160}$/.test(value) ? value : fallback;
  }
  return ALLOWED[group].has(value) ? value : fallback;
}

export function voiceLabel(value, voices = VOICES) {
  return voices.find(([id]) => id === value)?.[1] || value || "默认音色";
}

export function stepLabel(value) {
  return STEPS.find(([id]) => id === value)?.[1] || "";
}

export function splitKeywords(value) {
  return String(value || "")
    .split(/[,，]/)
    .map((item) => item.trim())
    .filter(Boolean);
}

export function formatWhen(value) {
  const numeric = Number(value);
  const date = Number.isFinite(numeric) && numeric > 1e9
    ? new Date(numeric > 1e12 ? numeric : numeric * 1000)
    : new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  const now = new Date();
  const time = date.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
  if (date.toDateString() === now.toDateString()) return time;
  return `${date.getMonth() + 1}/${date.getDate()} ${time}`;
}

export function formatBytes(size) {
  const n = Number(size) || 0;
  if (n < 1024 * 1024) return `${Math.max(1, Math.round(n / 1024))} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

export function detailMessage(payload, fallback) {
  const detail = payload?.detail;
  return typeof detail === "string" && detail ? detail : fallback;
}
