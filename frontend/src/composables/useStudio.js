import { computed, inject, onMounted, onUnmounted, provide, reactive, ref, watch } from "vue";
import {
  allowed,
  detailMessage,
  QUALITY_LABEL,
  SOURCE_LABEL,
  splitKeywords,
  STATE,
  stepLabel,
  STYLE_LABEL,
  VOICES,
  voiceLabel,
} from "../lib/labels";

const KEY = Symbol("studio");
const PREFS = "fastcut-prefs";

function readJson(response) {
  return response.json().catch(() => ({}));
}

export function provideStudio() {
  const studio = createStudio();
  provide(KEY, studio);
  return studio;
}

export function useStudio() {
  const studio = inject(KEY);
  if (!studio) throw new Error("studio is not provided");
  return studio;
}

function createStudio() {
  const mode = ref("agent");
  const brief = ref("");
  const confirmFirst = ref(false);
  const topic = ref("");
  const duration = ref(30);
  const aspect = ref("9:16");
  const quality = ref("draft");
  const voice = ref("zh-CN-XiaoxiaoNeural");
  const visualSource = ref("auto");
  const visualStyle = ref("");
  const audio = ref("auto");
  const useLlm = ref(true);
  const captionCustom = ref(false);
  const captionSize = ref(96);
  const videoFile = ref(null);
  const dragOver = ref(false);

  const config = ref(null);
  const offline = ref(false);
  const jobs = ref([]);
  const activeId = ref("");
  const status = ref(null);
  const logLines = ref([]);
  const formError = ref("");
  const busySubmit = ref(false);
  const settingsOpen = ref(false);
  const previewUrl = ref("");
  const userChoseNew = ref(false);
  const reviewBuiltFor = ref("");
  const review = reactive({
    title: "",
    voice: "zh-CN-XiaoxiaoNeural",
    quality: "draft",
    source: "auto",
    audio: "auto",
    captionCustom: false,
    captionSize: 96,
    scenes: [],
  });

  let detailTimer = 0;
  let listTimer = 0;
  let seenState = "";

  function loadPrefs() {
    try {
      const data = JSON.parse(sessionStorage.getItem(PREFS) || "{}");
      mode.value = allowed("mode", data.mode, mode.value);
      aspect.value = allowed("aspect", data.aspect, aspect.value);
      quality.value = allowed("quality", data.quality, quality.value);
      voice.value = allowed("voice", data.voice, voice.value);
      visualSource.value = allowed("source", data.visualSource, visualSource.value);
      visualStyle.value = allowed("style", data.visualStyle, visualStyle.value);
      audio.value = allowed("audio", data.audio, audio.value);
      if (typeof data.confirmFirst === "boolean") confirmFirst.value = data.confirmFirst;
      if (typeof data.useLlm === "boolean") useLlm.value = data.useLlm;
      if (typeof data.captionCustom === "boolean") captionCustom.value = data.captionCustom;
      const seconds = Number(data.duration);
      if (seconds >= 5 && seconds <= 180) duration.value = seconds;
      const size = Number(data.captionSize);
      if (size >= 48 && size <= 140) captionSize.value = size;
    } catch {
      /* keep defaults when storage is blocked */
    }
  }

  function savePrefs() {
    const payload = {
      mode: mode.value,
      duration: duration.value,
      aspect: aspect.value,
      quality: quality.value,
      voice: voice.value,
      visualSource: visualSource.value,
      visualStyle: visualStyle.value,
      audio: audio.value,
      useLlm: useLlm.value,
      captionCustom: captionCustom.value,
      captionSize: captionSize.value,
      confirmFirst: confirmFirst.value,
    };
    try {
      sessionStorage.setItem(PREFS, JSON.stringify(payload));
    } catch {
      /* the next visit just starts from defaults */
    }
  }

  loadPrefs();

  function fail(message, fieldId) {
    formError.value = message;
    if (fieldId) document.getElementById(fieldId)?.focus();
  }

  function clearError() {
    formError.value = "";
  }

  const libraryReady = computed(() => {
    const current = config.value;
    if (!current) return false;
    return Boolean(current.unsplash_ready || current.pexels_ready || current.pixabay_ready);
  });

  const libraryNames = computed(() => {
    const current = config.value;
    if (!current) return "";
    const names = [];
    if (current.unsplash_ready) names.push("Unsplash");
    if (current.pexels_ready) names.push("Pexels");
    if (current.pixabay_ready) names.push("Pixabay");
    return names.join(" / ");
  });

  const submitLocked = computed(() => busySubmit.value || status.value?.cover_state === "running");

  const isReview = computed(() => status.value?.state === "review" && Boolean(status.value?.storyboard));
  const isActiveRunning = computed(() => status.value?.state === "running" || status.value?.state === "stopping");
  const isReady = computed(() => status.value?.state === "done" && Boolean(status.value?.has_output));
  const canReopen = computed(() => {
    const current = status.value;
    if (!current?.storyboard) return false;
    if (current.mode === "highlight") return false;
    return current.state === "done" || current.state === "failed";
  });

  const progress = computed(() => Math.round((Number(status.value?.progress) || 0) * 100));
  const logText = computed(() => {
    if (logLines.value.length) return logLines.value.join("\n");
    return (status.value?.notes || []).join("\n");
  });

  const stageTitle = computed(() => {
    if (!activeId.value) {
      if (mode.value === "create") return "手动调整";
      if (mode.value === "highlight") return "高光切片";
      return "自动成片";
    }
    if (isReview.value) return "确认分镜后再制作";
    return status.value?.title || status.value?.topic || activeId.value;
  });

  const statusError = computed(() => {
    const current = status.value;
    if (!current || formError.value) return "";
    if (current.state === "failed" || (current.state === "review" && current.error)) {
      return current.error || (current.state === "failed" ? "生成失败" : "");
    }
    return "";
  });

  const stageMeta = computed(() => {
    if (!activeId.value) return "";
    if (!status.value) return "正在读取";
    const bits = [STATE[status.value.state] || status.value.state || ""];
    bits.push(`${progress.value}%`);
    if (status.value.duration) bits.push(`${Math.round(Number(status.value.duration))} 秒`);
    if (status.value.aspect_ratio) bits.push(status.value.aspect_ratio);
    return bits.filter(Boolean).join(" · ");
  });

  const voices = computed(() => {
    const list = config.value?.voices;
    if (Array.isArray(list) && list.length) return list;
    return VOICES;
  });

  const specLine = computed(() => {
    if (isReview.value) {
      const count = review.scenes.length;
      return [
        count ? `${count} 镜` : "",
        status.value?.aspect_ratio || "",
        voiceLabel(review.voice, voices.value),
        QUALITY_LABEL[review.quality] || "",
      ].filter(Boolean).join(" · ");
    }
    if (activeId.value) return "";
    if (mode.value === "agent") {
      return confirmFirst.value ? "先确认分镜，再开始制作" : "直接生成成片";
    }
    const parts = [
      `${duration.value || 0} 秒`,
      aspect.value,
      QUALITY_LABEL[quality.value] || "",
    ];
    if (mode.value === "create") parts.push(voiceLabel(voice.value, voices.value));
    return parts.join(" · ");
  });

  const planChips = computed(() => {
    const plan = status.value?.plan;
    if (!plan?.summary) return [];
    const items = [
      ["时长", plan.duration ? `${Math.round(plan.duration)} 秒` : ""],
      ["比例", plan.aspect_ratio || ""],
      ["画面", SOURCE_LABEL[plan.visual_source] || ""],
      ["风格", STYLE_LABEL[plan.visual_style] || ""],
      ["配乐", plan.audio && plan.audio !== "auto" ? "" : ""],
    ];
    if (plan.audio && plan.audio !== "auto") {
      items[4][1] = plan.audio === "none" ? "无网络配乐" : "可商用配乐";
    }
    if (plan.caption_size) items.push(["字幕", String(plan.caption_size)]);
    items.push(["理解", plan.parser === "llm" ? "大模型" : "本地规则"]);
    return items.filter(([, value]) => value);
  });

  function captionValue(custom, size) {
    return custom ? Number(size) : 0;
  }

  function applyReview(next) {
    const board = next.storyboard || { title: "", scenes: [] };
    review.title = board.title || next.title || "";
    review.voice = next.voice || voice.value;
    review.source = next.visual_source || next.plan?.visual_source || "auto";
    review.quality = next.quality || quality.value || "draft";
    review.audio = next.audio || next.plan?.audio || "auto";
    const size = Number(next.caption_size || next.plan?.caption_size || 0);
    review.captionCustom = size >= 48 && size <= 140;
    if (review.captionCustom) review.captionSize = size;
    review.scenes = (board.scenes || []).map((scene) => ({
      narration: scene.narration || "",
      visual_style: ["cinematic", "documentary", "bright"].includes(scene.visual_style) ? scene.visual_style : "cinematic",
      keywords: (scene.search_keywords || []).join("，"),
    }));
    reviewBuiltFor.value = next.task_id;
  }

  function syncReview(next) {
    const reviewing = next.state === "review" && next.storyboard;
    if (reviewing && reviewBuiltFor.value !== next.task_id) applyReview(next);
    if (!reviewing) reviewBuiltFor.value = "";
  }

  function upsertJob(summary) {
    const row = { ...summary };
    delete row.storyboard;
    const index = jobs.value.findIndex((job) => job.task_id === row.task_id);
    if (index >= 0) jobs.value[index] = { ...jobs.value[index], ...row };
    else jobs.value.unshift(row);
  }

  async function loadConfig() {
    try {
      const response = await fetch("/config");
      if (!response.ok) throw new Error("config");
      config.value = await response.json();
      offline.value = false;
      if (config.value.voice) voice.value = allowed("voice", config.value.voice, voice.value);
      return true;
    } catch {
      offline.value = true;
      return false;
    }
  }

  async function loadJobs() {
    try {
      const response = await fetch("/tasks");
      if (!response.ok) throw new Error("tasks");
      const payload = await response.json();
      jobs.value = payload.tasks || [];
      offline.value = false;
      if (!activeId.value && !userChoseNew.value) {
        const running = jobs.value.find((job) => job.state === "running");
        if (running) openTask(running.task_id);
      }
    } catch {
      offline.value = true;
    }
  }

  async function refresh() {
    const id = activeId.value;
    if (!id) return;
    const [taskResponse, logResponse] = await Promise.all([
      fetch(`/tasks/${id}`),
      fetch(`/tasks/${id}/log`),
    ]);
    if (!taskResponse.ok) {
      const payload = await readJson(taskResponse);
      throw new Error(detailMessage(payload, "读取失败"));
    }
    const next = await taskResponse.json();
    if (activeId.value !== id) return;
    const previous = seenState;
    status.value = next;
    if (next.state === "running") formError.value = "";
    const logPayload = logResponse.ok ? await logResponse.json() : { lines: [] };
    logLines.value = logPayload.lines || [];
    syncReview(next);
    upsertJob(next);
    seenState = next.state || "";
    if (previous && previous !== next.state && next.state !== "running") loadJobs();
    document.title = `fastCut · ${next.title || next.topic || "工作台"}`;
  }

  function startTimer() {
    window.clearInterval(detailTimer);
    detailTimer = window.setInterval(() => {
      refresh().catch(() => {});
    }, 1500);
  }

  function stopTimer() {
    window.clearInterval(detailTimer);
    detailTimer = 0;
  }

  function openTask(id) {
    if (!id) return;
    if (activeId.value !== id) reviewBuiltFor.value = "";
    userChoseNew.value = false;
    activeId.value = id;
    seenState = "";
    clearError();
    stopTimer();
    refresh().catch((error) => fail(error.message || "读取失败"));
    startTimer();
  }

  async function removeJob(id) {
    if (!id) return;
    const response = await fetch(`/tasks/${id}`, { method: "DELETE" });
    const payload = await readJson(response);
    if (!response.ok && response.status !== 404) {
      fail(detailMessage(payload, "删除失败"));
      return false;
    }
    jobs.value = jobs.value.filter((job) => job.task_id !== id);
    if (activeId.value === id) startNew();
    return true;
  }

  function startNew() {
    userChoseNew.value = true;
    activeId.value = "";
    status.value = null;
    logLines.value = [];
    seenState = "";
    reviewBuiltFor.value = "";
    clearError();
    stopTimer();
    document.title = "fastCut";
  }

  function setMode(next) {
    mode.value = next;
    clearError();
  }

  function onVideo(event) {
    videoFile.value = event.target.files?.[0] || null;
    dragOver.value = false;
  }

  function onDrop(event) {
    dragOver.value = false;
    const file = event.dataTransfer?.files?.[0];
    if (file) videoFile.value = file;
  }

  async function send(url, options) {
    busySubmit.value = true;
    clearError();
    try {
      const response = await fetch(url, options);
      const payload = await readJson(response);
      if (!response.ok) {
        fail(detailMessage(payload, "提交失败"));
        return false;
      }
      await loadJobs();
      openTask(payload.task_id);
      return true;
    } finally {
      busySubmit.value = false;
    }
  }

  function durationError() {
    const value = Number(duration.value);
    if (value < 5 || value > 180) return "时长要在 5 到 180 秒之间";
    return "";
  }

  function blockIfBusy() {
    return busySubmit.value;
  }

  async function submitAgent() {
    if (blockIfBusy()) return;
    const text = brief.value.trim();
    if (!text) {
      fail("先写你想要的片子", "brief");
      return;
    }
    await send("/tasks/agent", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ brief: text, confirm: confirmFirst.value }),
    });
  }

  async function submitCreate() {
    if (blockIfBusy()) return;
    const text = topic.value.trim();
    if (!text) {
      fail("先写一个主题", "topic");
      return;
    }
    const problem = durationError();
    if (problem) {
      fail(problem, "duration");
      return;
    }
    await send("/tasks/draft", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        topic: text,
        duration: Number(duration.value),
        aspect_ratio: aspect.value,
        quality: quality.value,
        voice: voice.value,
        use_llm: useLlm.value,
        visual_source: visualSource.value,
        visual_style: visualStyle.value,
        audio: audio.value,
        caption_size: captionValue(captionCustom.value, captionSize.value),
        align: "auto",
      }),
    });
  }

  async function submitHighlight() {
    if (blockIfBusy()) return;
    if (!videoFile.value) {
      fail("先选择一个视频", "video");
      return;
    }
    const problem = durationError();
    if (problem) {
      fail(problem, "duration");
      return;
    }
    const body = new FormData();
    body.append("video", videoFile.value);
    body.append("duration", String(Number(duration.value)));
    body.append("aspect_ratio", aspect.value);
    body.append("quality", quality.value);
    body.append("use_llm", "true");
    await send("/tasks/highlight", { method: "POST", body });
  }

  async function produce() {
    if (blockIfBusy()) return;
    const scenes = review.scenes.map((scene) => ({
      narration: scene.narration.trim(),
      visual_style: scene.visual_style,
      search_keywords: splitKeywords(scene.keywords),
    }));
    if (!scenes.length || scenes.some((scene) => !scene.narration)) {
      const index = review.scenes.findIndex((scene) => !scene.narration.trim());
      fail("每一镜都要有文案", index >= 0 ? `scene-${index}` : "review-title");
      return;
    }
    await send(`/tasks/${activeId.value}/produce`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        title: review.title.trim() || "未命名",
        scenes,
        voice: review.voice,
        quality: review.quality,
        visual_source: review.source,
        audio: review.audio,
        caption_size: captionValue(review.captionCustom, review.captionSize),
        align: "auto",
      }),
    });
  }

  async function stop() {
    if (!activeId.value || status.value?.state === "stopping") return;
    const response = await fetch(`/tasks/${activeId.value}/stop`, { method: "POST" });
    const payload = await readJson(response);
    if (!response.ok) {
      fail(detailMessage(payload, "停止失败"));
      return;
    }
    refresh().catch(() => {});
  }

  async function reopen() {
    if (!activeId.value) return;
    const response = await fetch(`/tasks/${activeId.value}/reopen`, { method: "POST" });
    const payload = await readJson(response);
    if (!response.ok) {
      fail(detailMessage(payload, "退回分镜失败"));
      return;
    }
    reviewBuiltFor.value = "";
    refresh().catch(() => {});
  }

  async function makeCover() {
    if (!activeId.value) return;
    const response = await fetch(`/tasks/${activeId.value}/cover`, { method: "POST" });
    const payload = await readJson(response);
    if (!response.ok) {
      fail(detailMessage(payload, "封面生成失败"));
      return;
    }
    if (status.value) status.value = { ...status.value, cover_state: "running", cover_error: null };
    refresh().catch(() => {});
  }

  async function previewVoice(voiceName, text) {
    clearError();
    const response = await fetch("/voices/preview", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ voice: voiceName, text: String(text || "").slice(0, 80) }),
    });
    const payload = response.ok ? null : await readJson(response);
    if (!response.ok) {
      fail(detailMessage(payload, "试听失败"));
      return;
    }
    if (previewUrl.value) URL.revokeObjectURL(previewUrl.value);
    previewUrl.value = URL.createObjectURL(await response.blob());
  }

  function previewDraftVoice() {
    previewVoice(voice.value, "你好，这是当前音色的试听。");
  }

  function previewReviewVoice() {
    const sample = review.scenes.find((scene) => scene.narration.trim())?.narration || "";
    previewVoice(review.voice, sample);
  }

  async function openSettings() {
    const ok = await loadConfig();
    if (!ok) {
      fail("页面服务未连接");
      return;
    }
    settingsOpen.value = true;
  }

  function closeSettings() {
    settingsOpen.value = false;
  }

  function applySavedConfig(next) {
    config.value = next;
    offline.value = false;
    const list = Array.isArray(next?.voices) && next.voices.length ? next.voices : VOICES;
    if (next?.voice && list.some(([id]) => id === next.voice)) voice.value = next.voice;
    if (review.voice && !list.some(([id]) => id === review.voice)) review.voice = list[0][0];
  }

  function triggerPrimary() {
    if (settingsOpen.value) return;
    if (isReview.value) {
      produce();
      return;
    }
    if (activeId.value && isActiveRunning.value) {
      stop();
      return;
    }
    if (!activeId.value) {
      if (mode.value === "create") submitCreate();
      else if (mode.value === "highlight") submitHighlight();
      else submitAgent();
    }
  }

  function onKey(event) {
    if (settingsOpen.value) return;
    if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
      event.preventDefault();
      triggerPrimary();
    }
  }

  function onTitleEnter(event) {
    event.preventDefault();
    document.getElementById("scene-0")?.focus();
  }

  function onKeywordEnter(index, event) {
    event.preventDefault();
    const next = document.getElementById(`scene-${index + 1}`);
    if (next) next.focus();
    else document.getElementById("produce")?.focus();
  }

  watch(
    [mode, duration, aspect, quality, voice, visualSource, visualStyle, audio, useLlm, captionCustom, captionSize, confirmFirst],
    savePrefs,
  );

  onMounted(() => {
    loadConfig();
    loadJobs();
    listTimer = window.setInterval(loadJobs, 4000);
    window.addEventListener("keydown", onKey);
  });

  onUnmounted(() => {
    stopTimer();
    window.clearInterval(listTimer);
    window.removeEventListener("keydown", onKey);
    if (previewUrl.value) URL.revokeObjectURL(previewUrl.value);
  });

  return reactive({
    mode,
    brief,
    confirmFirst,
    topic,
    duration,
    aspect,
    quality,
    voice,
    visualSource,
    visualStyle,
    audio,
    useLlm,
    captionCustom,
    captionSize,
    videoFile,
    dragOver,
    config,
    offline,
    jobs,
    activeId,
    status,
    review,
    formError,
    statusError,
    busySubmit,
    settingsOpen,
    previewUrl,
    libraryReady,
    voices,
    libraryNames,
    submitLocked,
    isReview,
    isActiveRunning,
    isReady,
    canReopen,
    progress,
    logText,
    stageTitle,
    stageMeta,
    specLine,
    planChips,
    setMode,
    onVideo,
    onDrop,
    submitAgent,
    submitCreate,
    submitHighlight,
    produce,
    stop,
    reopen,
    makeCover,
    previewDraftVoice,
    previewReviewVoice,
    openSettings,
    closeSettings,
    applySavedConfig,
    openTask,
    removeJob,
    startNew,
    onTitleEnter,
    onKeywordEnter,
    triggerPrimary,
  });
}
