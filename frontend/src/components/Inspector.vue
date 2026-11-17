<script setup>
import { computed, nextTick, ref, watch } from "vue";
import { ASPECTS, AUDIOS, QUALITIES, QUALITY_LABEL, SOURCES, STYLES, voiceLabel } from "../lib/labels";
import { useStudio } from "../composables/useStudio";

const studio = useStudio();
const audioEl = ref(null);

const reviewVoices = computed(() => {
  const list = [...(studio.voices || [])];
  if (studio.review.voice && !list.some(([value]) => value === studio.review.voice)) {
    list.push([studio.review.voice, studio.review.voice]);
  }
  return list;
});

const showCover = computed(() => {
  const current = studio.status;
  if (!studio.activeId || !current) return false;
  return current.state === "review" || current.state === "done" || current.has_cover || Boolean(current.cover_state);
});

const coverSrc = computed(() => {
  if (!studio.status?.has_cover) return "";
  return `/tasks/${studio.activeId}/cover?v=${studio.status.updated_at || ""}`;
});

const coverNote = computed(() => {
  const current = studio.status;
  if (!current) return "";
  if (current.cover_state === "running") return "封面生成中，通常要几十秒。";
  if (current.cover_error) return current.cover_error;
  if (current.has_cover) return "";
  return "封面用标题压在一张画面上，可以单独下载。";
});

const coverDisabled = computed(() => studio.status?.cover_state === "running" || studio.status?.state === "running");

watch(() => studio.previewUrl, async (url) => {
  if (!url) return;
  await nextTick();
  audioEl.value?.play?.().catch(() => {});
});
</script>

<template>
  <aside class="inspector" aria-label="参数">
    <h2>{{ studio.isReview ? "制作参数" : studio.activeId ? "这支片子" : "参数" }}</h2>

    <template v-if="!studio.activeId && studio.mode === 'agent'">
      <label class="check" for="agent-confirm">
        <input id="agent-confirm" v-model="studio.confirmFirst" type="checkbox" />
        先确认分镜，再开始制作
      </label>
      <p class="note">不勾选时，理解完主题就直接配音、找画面并合成。勾选后会停在分镜，改完再制作。</p>
      <p class="note">大模型、配音、生图和图库的密钥在右上角配置。大语言模型是必填的。</p>
    </template>

    <template v-else-if="!studio.activeId && studio.mode === 'create'">
      <div class="field">
        <label for="duration">目标时长（秒）</label>
        <input id="duration" v-model.number="studio.duration" form="create-form" type="number" inputmode="numeric" min="5" max="180" />
      </div>
      <div class="field">
        <span class="label" id="aspect-label">画面比例</span>
        <div class="choices" aria-labelledby="aspect-label">
          <button
            v-for="[value, label] in ASPECTS"
            :key="value"
            type="button"
            :aria-pressed="studio.aspect === value"
            @click="studio.aspect = value"
          >{{ label }}</button>
        </div>
      </div>
      <div class="field">
        <label for="quality">成片质量</label>
        <select id="quality" v-model="studio.quality" form="create-form">
          <option v-for="[value, label] in QUALITIES" :key="value" :value="value">{{ label }}</option>
        </select>
      </div>
      <div class="field">
        <label for="visual-source">画面来源</label>
        <select id="visual-source" v-model="studio.visualSource" form="create-form">
          <option v-for="[value, label] in SOURCES" :key="value" :value="value">{{ label }}</option>
        </select>
      </div>
      <div class="field">
        <label for="visual-style">画面风格</label>
        <select id="visual-style" v-model="studio.visualStyle" form="create-form">
          <option value="">由分镜决定</option>
          <option v-for="[value, label] in STYLES" :key="value" :value="value">{{ label }}</option>
        </select>
      </div>
      <div class="field">
        <label for="audio-bed">配乐</label>
        <select id="audio-bed" v-model="studio.audio" form="create-form">
          <option v-for="[value, label] in AUDIOS" :key="value" :value="value">{{ label }}</option>
        </select>
      </div>
      <div class="field">
        <label for="voice">配音</label>
        <div class="inline">
          <select id="voice" v-model="studio.voice" form="create-form">
            <option v-for="[value, label] in studio.voices" :key="value" :value="value">{{ label }}</option>
          </select>
          <button class="btn" type="button" @click="studio.previewDraftVoice()">试听</button>
        </div>
      </div>
      <label class="check" for="use-llm">
        <input id="use-llm" v-model="studio.useLlm" type="checkbox" />
        用大模型写分镜
      </label>
      <div class="field">
        <label for="caption-size">字幕大小</label>
        <div class="meter">
          <input
            id="caption-size"
            v-model.number="studio.captionSize"
            type="range"
            min="48"
            max="140"
            step="2"
            :disabled="!studio.captionCustom"
          />
          <output for="caption-size">{{ studio.captionCustom ? studio.captionSize : "默认" }}</output>
        </div>
        <label class="check" for="caption-custom">
          <input id="caption-custom" v-model="studio.captionCustom" type="checkbox" />
          手动指定字号，越大每屏字越少
        </label>
      </div>
    </template>

    <template v-else-if="!studio.activeId && studio.mode === 'highlight'">
      <div class="field">
        <label for="duration">成片时长（秒）</label>
        <input id="duration" v-model.number="studio.duration" form="highlight-form" type="number" inputmode="numeric" min="5" max="180" />
      </div>
      <div class="field">
        <span class="label" id="h-aspect-label">画面比例</span>
        <div class="choices" aria-labelledby="h-aspect-label">
          <button
            v-for="[value, label] in ASPECTS"
            :key="value"
            type="button"
            :aria-pressed="studio.aspect === value"
            @click="studio.aspect = value"
          >{{ label }}</button>
        </div>
      </div>
      <div class="field">
        <label for="h-quality">成片质量</label>
        <select id="h-quality" v-model="studio.quality" form="highlight-form">
          <option v-for="[value, label] in QUALITIES" :key="value" :value="value">{{ label }}</option>
        </select>
      </div>
      <p class="note">切片会按成片时长从原片里挑高光，比例只影响导出的画面。</p>
    </template>

    <template v-else-if="studio.isReview">
      <div class="field">
        <label for="review-voice">配音</label>
        <div class="inline">
          <select id="review-voice" v-model="studio.review.voice">
            <option v-for="[value, label] in reviewVoices" :key="value" :value="value">{{ label }}</option>
          </select>
          <button class="btn" type="button" @click="studio.previewReviewVoice()">试听这段</button>
        </div>
      </div>
      <div class="field">
        <label for="review-quality">成片质量</label>
        <select id="review-quality" v-model="studio.review.quality">
          <option v-for="[value, label] in QUALITIES" :key="value" :value="value">{{ label }}</option>
        </select>
      </div>
      <div class="field">
        <label for="review-source">画面来源</label>
        <select id="review-source" v-model="studio.review.source">
          <option v-for="[value, label] in SOURCES" :key="value" :value="value">{{ label }}</option>
        </select>
      </div>
      <div class="field">
        <label for="review-audio">配乐</label>
        <select id="review-audio" v-model="studio.review.audio">
          <option v-for="[value, label] in AUDIOS" :key="value" :value="value">{{ label }}</option>
        </select>
      </div>
      <div class="field">
        <label for="review-caption-size">字幕大小</label>
        <div class="meter">
          <input
            id="review-caption-size"
            v-model.number="studio.review.captionSize"
            type="range"
            min="48"
            max="140"
            step="2"
            :disabled="!studio.review.captionCustom"
          />
          <output for="review-caption-size">{{ studio.review.captionCustom ? studio.review.captionSize : "默认" }}</output>
        </div>
        <label class="check" for="review-caption-custom">
          <input id="review-caption-custom" v-model="studio.review.captionCustom" type="checkbox" />
          手动指定字号，越大每屏字越少
        </label>
      </div>
    </template>

    <template v-else-if="studio.activeId && studio.status">
      <dl class="facts">
        <div v-if="studio.status.aspect_ratio">
          <dt>比例</dt>
          <dd>{{ studio.status.aspect_ratio }}</dd>
        </div>
        <div v-if="studio.status.duration">
          <dt>时长</dt>
          <dd>{{ Math.round(Number(studio.status.duration)) }} 秒</dd>
        </div>
        <div v-if="studio.status.quality">
          <dt>质量</dt>
          <dd>{{ QUALITY_LABEL[studio.status.quality] || studio.status.quality }}</dd>
        </div>
        <div v-if="studio.status.voice">
          <dt>配音</dt>
          <dd>{{ voiceLabel(studio.status.voice, studio.voices) }}</dd>
        </div>
      </dl>
      <p v-if="studio.isActiveRunning" class="note">这一步结束前可以停止。停止后会回到分镜，已写好的文案还在。</p>
    </template>

    <audio v-show="studio.previewUrl" ref="audioEl" :src="studio.previewUrl" controls></audio>

    <section v-if="showCover" class="cover" aria-label="封面">
      <h3>封面</h3>
      <img v-if="coverSrc" :src="coverSrc" alt="视频封面" />
      <div class="inline">
        <button class="btn" type="button" :disabled="coverDisabled" @click="studio.makeCover()">
          {{ studio.status?.has_cover ? "重新生成封面" : "生成封面" }}
        </button>
        <a v-if="coverSrc" class="btn" :href="coverSrc" download>下载封面</a>
      </div>
      <p v-if="coverNote" class="note">{{ coverNote }}</p>
    </section>
  </aside>
</template>
