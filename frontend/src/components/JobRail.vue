<script setup>
import { computed, ref } from "vue";
import Icon from "./Icon.vue";
import { STATE, formatWhen } from "../lib/labels";
import { useStudio } from "../composables/useStudio";

const studio = useStudio();
const pendingId = ref("");

const countLabel = computed(() => (studio.jobs.length ? String(studio.jobs.length) : ""));

function jobName(job) {
  return job.title || job.topic || job.task_id;
}

function busy(job) {
  if (job.live === false) return false;
  return job.state === "running" || job.state === "stopping";
}

function openJob(id) {
  pendingId.value = "";
  studio.openTask(id);
}

async function remove(job) {
  if (busy(job)) return;
  if (pendingId.value !== job.task_id) {
    pendingId.value = job.task_id;
    return;
  }
  const removed = await studio.removeJob(job.task_id);
  if (removed) pendingId.value = "";
}
</script>

<template>
  <aside class="rail" aria-label="最近的片子">
    <div class="rail-head">
      <div class="rail-title">
        <h2>最近的片子</h2>
        <span v-if="countLabel" class="count">{{ countLabel }}</span>
      </div>
      <button
        class="btn"
        type="button"
        :aria-pressed="!studio.activeId"
        @click="studio.startNew()"
      >
        <Icon name="plus" />
        新片子
      </button>
    </div>
    <div class="rail-list">
      <p v-if="!studio.jobs.length" class="empty">还没有成片。</p>
      <div
        v-for="job in studio.jobs"
        :key="job.task_id"
        class="job"
        :class="{ active: job.task_id === studio.activeId }"
        :data-confirm="pendingId === job.task_id ? 'true' : undefined"
      >
        <button
          class="job-open"
          type="button"
          :aria-current="job.task_id === studio.activeId ? 'true' : undefined"
          @click="openJob(job.task_id)"
        >
          <span class="job-top">
            <span class="job-name">{{ jobName(job) }}</span>
            <span class="job-state" :data-state="job.state">{{ STATE[job.state] || job.state }}</span>
          </span>
          <span class="job-meta">
            <span v-if="job.aspect_ratio">{{ job.aspect_ratio }}</span>
            <span v-if="job.duration">{{ Math.round(Number(job.duration)) }} 秒</span>
            <span v-if="formatWhen(job.updated_at)">{{ formatWhen(job.updated_at) }}</span>
          </span>
          <span v-if="busy(job)" class="job-bar" aria-hidden="true">
            <span :style="{ width: `${Math.round((job.progress || 0) * 100)}%` }"></span>
          </span>
        </button>
        <button
          class="job-delete"
          type="button"
          :disabled="busy(job)"
          :aria-pressed="pendingId === job.task_id"
          :aria-label="pendingId === job.task_id ? `确认删除 ${jobName(job)}` : `删除 ${jobName(job)}`"
          :title="busy(job) ? '还在处理，先停止再删除' : pendingId === job.task_id ? '再点一次就会删除' : '删除这条片子'"
          @click="remove(job)"
          @keydown.esc.stop="pendingId = ''"
        >
          <span v-if="pendingId === job.task_id">确认删除</span>
          <Icon v-else name="trash" />
        </button>
      </div>
    </div>
  </aside>
</template>
