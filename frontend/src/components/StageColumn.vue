<script setup>
import { computed, nextTick, ref, watch } from "vue";
import { MODES, MODE_HINT, STEPS, STYLES, formatBytes, stepLabel } from "../lib/labels";
import { useStudio } from "../composables/useStudio";
import Icon from "./Icon.vue";

const studio = useStudio();
const errorEl = ref(null);
const logEl = ref(null);
const logOpen = ref(false);
const icons = { agent: "spark", create: "rows", highlight: "film" };

const stepIndex = computed(() => STEPS.findIndex(([key]) => key === (studio.status?.step || "storyboard")));

const downloadHref = computed(() => (studio.activeId ? `/tasks/${studio.activeId}/download?download=1` : "#"));
const capcutHref = computed(() => (studio.activeId ? `/tasks/${studio.activeId}/capcut` : "#"));
const playerSrc = computed(() => (studio.isReady ? `/tasks/${studio.activeId}/download` : ""));

const actionHint = computed(() => {
  if (!studio.activeId) return "Ctrl + Enter";
  if (studio.isReview) return "改完文案后，Ctrl + Enter 开始制作";
  return "";
});

const RUNNING_STEP = {
  storyboard: "正在写分镜",
  review: "正在整理分镜",
  voice: "正在配音",
  transcribe: "正在生成字幕",
  assets: "正在准备画面",
  cut: "正在切片",
  overlay: "正在叠加字幕和画面",
  encode: "正在合成成片",
  done: "正在收尾",
};

const runningHeadline = computed(() => {
  if (studio.status?.state === "stopping") return "正在停止";
  return RUNNING_STEP[studio.status?.step] || (stepLabel(studio.status?.step) ? `正在${stepLabel(studio.status?.step)}` : "正在生成");
});

const runningDetail = computed(() => {
  if (studio.status?.state === "stopping") return "正在停下来。停完会回到分镜，已写好的文案还在。";
  return "这一步还在进行，进度会自动更新，不用刷新页面。";
});

const capcutNote = computed(() => {
  if (!studio.isReady) return "";
  return studio.status?.capcut_note
    || "分镜、旁白、配乐和字幕会拆成剪映轨道。这台电脑装了剪映或 CapCut 时，草稿会写进软件的草稿列表。";
});

watch(() => studio.formError, async (message) => {
  if (!message) return;
  await nextTick();
  errorEl.value?.focus();
});

watch(() => [studio.activeId, studio.status?.state], ([id, state], previous) => {
  const prevId = previous?.[0];
  const prevState = previous?.[1];
  if (id !== prevId) {
    logOpen.value = state === "running" || state === "stopping" || state === "failed";
    return;
  }
  if ((state === "running" || state === "stopping" || state === "failed") && state !== prevState) {
    logOpen.value = true;
  }
});

watch(() => studio.logText, async () => {
  const el = logEl.value;
  if (!el) return;
  const stick = el.scrollTop + el.clientHeight >= el.scrollHeight - 24;
  await nextTick();
  if (stick) el.scrollTop = el.scrollHeight;
});

function onLogToggle(event) {
  logOpen.value = event.target.open;
}
</script>

<template>
  <section id="stage" class="stage" aria-label="工作区">
    <header class="stage-head">
      <div class="stage-id">
        <h1>{{ studio.stageTitle }}</h1>
        <p v-if="studio.activeId" class="stage-meta">{{ studio.stageMeta }}</p>
        <p v-else class="stage-meta">{{ MODE_HINT[studio.mode] }}</p>
      </div>
      <div
        v-if="!studio.activeId"
        class="modes"
        role="tablist"
        aria-label="制作方式"
      >
        <button
          v-for="[value, label] in MODES"
          :key="value"
          type="button"
          role="tab"
          :aria-selected="studio.mode === value"
          @click="studio.setMode(value)"
        >
          <Icon :name="icons[value]" />
          {{ label }}
        </button>
      </div>
    </header>

    <ol
      v-if="studio.activeId && studio.status"
      class="pipeline"
      :data-live="studio.isActiveRunning ? 'true' : 'false'"
      aria-label="制作步骤"
    >
      <li
        v-for="([key, label], index) in STEPS"
        :key="key"
        :data-state="key === studio.status.step ? 'current' : stepIndex > index ? 'done' : 'wait'"
        :aria-current="key === studio.status.step ? 'step' : undefined"
      >
        <span class="node"></span>
        <span>{{ label }}</span>
      </li>
    </ol>

    <div class="stage-body">
      <p
        v-if="studio.formError"
        ref="errorEl"
        class="alert"
        role="alert"
        tabindex="-1"
      >{{ studio.formError }}</p>
      <p v-else-if="studio.statusError" class="alert" role="alert">{{ studio.statusError }}</p>

      <form
        v-if="!studio.activeId && studio.mode === 'agent'"
        id="agent-form"
        @submit.prevent="studio.submitAgent()"
      >
        <label for="brief">你想要什么样的片子</label>
        <textarea
          id="brief"
          v-model="studio.brief"
          rows="4"
          placeholder="例如：帮我生成一段视频，是关于城市夜景的，大概 1 分钟左右，使用网络图片，电影风格，字幕大一点"
        ></textarea>
      </form>

      <form
        v-else-if="!studio.activeId && studio.mode === 'create'"
        id="create-form"
        @submit.prevent="studio.submitCreate()"
      >
        <label for="topic">主题</label>
        <textarea
          id="topic"
          v-model="studio.topic"
          rows="4"
          placeholder="例如：世界上只有一种英雄主义，就是看清生活的真相之后依然热爱生活"
        ></textarea>
      </form>

      <form
        v-else-if="!studio.activeId && studio.mode === 'highlight'"
        id="highlight-form"
        @submit.prevent="studio.submitHighlight()"
      >
        <label for="video">长视频</label>
        <div
          class="drop"
          :data-over="studio.dragOver"
          @dragover.prevent="studio.dragOver = true"
          @dragleave="studio.dragOver = false"
          @drop.prevent="studio.onDrop($event)"
        >
          <input id="video" type="file" accept="video/*" @change="studio.onVideo($event)" />
          <span v-if="studio.videoFile" class="drop-file">
            <strong>{{ studio.videoFile.name }}</strong>
            <span>{{ formatBytes(studio.videoFile.size) }}</span>
          </span>
          <span v-else class="drop-empty">把长视频拖到这里，或点击选择</span>
        </div>
      </form>

      <template v-else-if="studio.activeId">
        <p v-if="!studio.status" class="empty">正在读取这条片子。</p>
        <template v-else>
          <div v-if="studio.isActiveRunning" class="running" role="status">
            <span class="running-dot" aria-hidden="true"></span>
            <div class="running-copy">
              <strong>{{ runningHeadline }}</strong>
              <p>{{ runningDetail }}</p>
            </div>
            <span class="running-pct">{{ studio.progress }}%</span>
          </div>

          <div v-if="studio.planChips.length" class="plan">
            <p>{{ studio.status.plan.summary }}</p>
            <div class="chips">
              <span v-for="[label, value] in studio.planChips" :key="label" class="chip">{{ label }} {{ value }}</span>
            </div>
          </div>

          <form v-if="studio.isReview" id="review-form" @submit.prevent="studio.produce()">
            <label for="review-title">标题</label>
            <input
              id="review-title"
              v-model="studio.review.title"
              type="text"
              @keydown.enter="studio.onTitleEnter($event)"
            />
            <div class="scenes">
              <article v-for="(scene, index) in studio.review.scenes" :key="index" class="scene">
                <span class="scene-no">{{ String(index + 1).padStart(2, "0") }}</span>
                <label class="sr-only" :for="`scene-${index}`">第 {{ index + 1 }} 镜文案</label>
                <textarea
                  :id="`scene-${index}`"
                  v-model="scene.narration"
                  rows="3"
                ></textarea>
                <label class="sr-only" :for="`style-${index}`">第 {{ index + 1 }} 镜风格</label>
                <select :id="`style-${index}`" v-model="scene.visual_style">
                  <option v-for="[value, label] in STYLES" :key="value" :value="value">{{ label }}</option>
                </select>
                <label class="sr-only" :for="`keys-${index}`">第 {{ index + 1 }} 镜关键词</label>
                <input
                  :id="`keys-${index}`"
                  v-model="scene.keywords"
                  class="keywords"
                  type="text"
                  placeholder="画面关键词，逗号分隔"
                  @keydown.enter="studio.onKeywordEnter(index, $event)"
                />
              </article>
            </div>
          </form>

          <div v-if="studio.isReady" class="monitor" :data-aspect="studio.status.aspect_ratio || '9:16'">
            <video :src="playerSrc" controls playsinline></video>
          </div>
          <p v-if="capcutNote" class="note">{{ capcutNote }}</p>

          <details class="log" :open="logOpen" @toggle="onLogToggle">
            <summary>运行记录</summary>
            <pre ref="logEl">{{ studio.logText || "还没有记录。" }}</pre>
          </details>
        </template>
      </template>
    </div>

    <div class="actionbar">
      <p v-if="studio.specLine || actionHint || studio.isActiveRunning" class="action-note">
        <span v-if="studio.isActiveRunning" class="live-note">
          <span class="running-dot" aria-hidden="true"></span>
          {{ runningHeadline }} · {{ studio.progress }}%
        </span>
        <span v-if="studio.specLine">{{ studio.specLine }}</span>
        <span v-if="actionHint" class="kbd">{{ actionHint }}</span>
      </p>
      <div class="action-buttons">
        <button
          v-if="!studio.activeId && studio.mode === 'agent'"
          class="btn btn-primary"
          type="submit"
          form="agent-form"
          :disabled="studio.submitLocked"
        >开始生成</button>
        <button
          v-else-if="!studio.activeId && studio.mode === 'create'"
          class="btn btn-primary"
          type="submit"
          form="create-form"
          :disabled="studio.submitLocked"
        >生成分镜</button>
        <button
          v-else-if="!studio.activeId && studio.mode === 'highlight'"
          class="btn btn-primary"
          type="submit"
          form="highlight-form"
          :disabled="studio.submitLocked"
        >开始切高光</button>
        <button
          v-else-if="studio.isReview"
          id="produce"
          class="btn btn-primary"
          type="submit"
          form="review-form"
          :disabled="studio.submitLocked"
        >确认并开始制作</button>
        <button
          v-else-if="studio.isActiveRunning"
          class="btn btn-danger"
          type="button"
          :disabled="studio.status?.state === 'stopping'"
          @click="studio.stop()"
        >{{ studio.status?.state === "stopping" ? "正在停止…" : "停止生成" }}</button>
        <a v-if="studio.isReady" class="btn btn-primary" :href="downloadHref" download>下载成片</a>
        <a v-if="studio.isReady" class="btn" :href="capcutHref" download>导入剪映</a>
        <button
          v-if="studio.canReopen"
          class="btn"
          type="button"
          @click="studio.reopen()"
        >退回分镜重新生成</button>
      </div>
    </div>
  </section>
</template>
