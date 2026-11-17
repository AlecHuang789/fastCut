<script setup>
import { provideStudio } from "./composables/useStudio";
import JobRail from "./components/JobRail.vue";
import StageColumn from "./components/StageColumn.vue";
import Inspector from "./components/Inspector.vue";
import SettingsDialog from "./components/SettingsDialog.vue";
import Icon from "./components/Icon.vue";

const studio = provideStudio();
</script>

<template>
  <div class="app">
    <a class="skip" href="#stage">跳到工作区</a>
    <header class="topbar">
      <div class="brand">
        <span class="mark" aria-hidden="true">
          <svg viewBox="0 0 16 16">
            <rect x="1.75" y="3.5" width="12.5" height="9" />
            <path d="M6.4 6.15v3.7l3.4-1.85z" />
          </svg>
        </span>
        <span class="word">fastCut</span>
      </div>
      <p v-if="studio.offline" class="offline">页面服务未连接</p>
      <div v-else class="svcs" aria-label="服务状态">
        <span class="svc" :data-on="Boolean(studio.config?.llm_ready)">模型 {{ studio.config ? (studio.config.llm_ready ? "就绪" : "未配置") : "正在连接" }}</span>
        <span class="svc" :data-on="Boolean(studio.config?.tts_ready)">配音 {{ studio.config ? (studio.config.tts_ready ? "就绪" : "未配置") : "…" }}</span>
        <span class="svc" :data-on="Boolean(studio.config?.image_ready)">生图 {{ studio.config ? (studio.config.image_ready ? "就绪" : "未配置") : "…" }}</span>
        <span class="svc" :data-on="studio.libraryReady">图库 {{ studio.config ? (studio.libraryReady ? "就绪" : "未配置") : "…" }}</span>
        <span class="svc" :data-on="Boolean(studio.config?.freesound_ready)">配乐 {{ studio.config ? (studio.config.freesound_ready ? "就绪" : "未配置") : "…" }}</span>
      </div>
      <button id="open-settings" class="btn" type="button" @click="studio.openSettings()">
        <Icon name="gear" />
        密钥设置
      </button>
    </header>
    <div class="workspace">
      <JobRail />
      <StageColumn />
      <Inspector />
    </div>
    <SettingsDialog />
  </div>
</template>
