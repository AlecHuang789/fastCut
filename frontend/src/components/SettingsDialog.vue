<script setup>
import { computed, nextTick, reactive, ref, watch } from "vue";
import Icon from "./Icon.vue";
import { detailMessage } from "../lib/labels";
import { useStudio } from "../composables/useStudio";

const studio = useStudio();
const dialogEl = ref(null);
const errorEl = ref(null);
const saving = ref(false);
const error = ref("");
const statusText = ref("");
const baseError = ref("");
const imageError = ref("");
const tab = ref("llm");

const form = reactive({
  llm_provider: "openai",
  openai_base_url: "",
  openai_model: "",
  openai_api_key: "",
  clear_openai: false,
  tts_provider: "edge",
  tts_model: "",
  tts_voice: "",
  tts_voice_custom: "",
  tts_api_key: "",
  clear_tts: false,
  image_provider: "off",
  image_api_url: "",
  image_model: "",
  image_api_key: "",
  clear_image: false,
  unsplash_access_key: "",
  clear_unsplash: false,
  pexels_api_key: "",
  clear_pexels: false,
  pixabay_api_key: "",
  clear_pixabay: false,
  freesound_api_key: "",
  clear_freesound: false,
});

const hints = reactive({
  unsplash: "",
  pexels: "",
  pixabay: "",
  freesound: "",
});

const tabs = [
  ["llm", "大模型", "llm_ready"],
  ["tts", "语音", "tts_ready"],
  ["image", "生图", "image_ready"],
  ["stock", "素材", "library"],
];

const stockFields = [
  ["set-unsplash", "Unsplash Access Key", "unsplash_access_key", "clear_unsplash", "unsplash"],
  ["set-pexels", "Pexels 密钥", "pexels_api_key", "clear_pexels", "pexels"],
  ["set-pixabay", "Pixabay 密钥", "pixabay_api_key", "clear_pixabay", "pixabay"],
  ["set-freesound", "Freesound API Key", "freesound_api_key", "clear_freesound", "freesound"],
];

function catalog(kind) {
  return studio.config?.providers?.[kind] || [];
}

function findProvider(kind, id) {
  return catalog(kind).find((item) => item.id === id) || null;
}

function grouped(kind) {
  const labels = { cn: "国内", intl: "国际", other: "其他" };
  return ["cn", "intl", "other"]
    .map((region) => ({
      region,
      label: labels[region],
      items: catalog(kind).filter((item) => item.region === region),
    }))
    .filter((group) => group.items.length);
}

const llmProvider = computed(() => findProvider("llm", form.llm_provider));
const ttsProvider = computed(() => findProvider("tts", form.tts_provider));
const imageProvider = computed(() => findProvider("image", form.image_provider));

const llmGroups = computed(() => grouped("llm"));
const ttsGroups = computed(() => grouped("tts"));
const imageGroups = computed(() => grouped("image"));

function modelPick(provider, value) {
  const models = provider?.models || [];
  if (!models.length) return "__custom";
  return models.some(([id]) => id === value) ? value : "__custom";
}

const llmModelPick = computed(() => modelPick(llmProvider.value, form.openai_model));
const ttsModelPick = computed(() => modelPick(ttsProvider.value, form.tts_model));
const imageModelPick = computed(() => modelPick(imageProvider.value, form.image_model));

function savedHint(kind, id) {
  return studio.config?.key_hints?.[kind]?.[id] || "";
}

const llmHint = computed(() => savedHint("llm", form.llm_provider));
const ttsHint = computed(() => savedHint("tts", form.tts_provider));
const imageHint = computed(() => savedHint("image", form.image_provider));

function placeholderFor(hint, required) {
  if (hint) return `已保存 ${hint}，留空则不修改`;
  return required ? "必填" : "选填";
}

function knownVoice(provider, voice) {
  return Boolean(provider?.voices?.some(([id]) => id === voice));
}

function fill(config) {
  form.llm_provider = config?.llm_provider || "openai";
  form.openai_base_url = config?.openai_base_url || "";
  form.openai_model = config?.openai_model || "";
  form.openai_api_key = "";
  form.clear_openai = false;
  form.tts_provider = config?.tts_provider || "edge";
  form.tts_model = config?.tts_model || "";
  form.tts_api_key = "";
  form.clear_tts = false;
  form.tts_voice_custom = "";
  const speech = (config?.providers?.tts || []).find((item) => item.id === form.tts_provider);
  const voice = config?.voice || "";
  if (speech && voice && !knownVoice(speech, voice) && speech.needs_key) {
    form.tts_voice = speech.voices?.[0]?.[0] || "";
    form.tts_voice_custom = voice;
  } else {
    form.tts_voice = voice || speech?.voices?.[0]?.[0] || "";
  }
  form.image_provider = config?.image_provider || "off";
  form.image_api_url = config?.image_api_url || "";
  form.image_model = config?.image_model || "";
  form.image_api_key = "";
  form.clear_image = false;
  form.unsplash_access_key = "";
  form.pexels_api_key = "";
  form.pixabay_api_key = "";
  form.freesound_api_key = "";
  form.clear_unsplash = false;
  form.clear_pexels = false;
  form.clear_pixabay = false;
  form.clear_freesound = false;
  hints.unsplash = config?.unsplash_access_key_hint || "";
  hints.pexels = config?.pexels_api_key_hint || "";
  hints.pixabay = config?.pixabay_api_key_hint || "";
  hints.freesound = config?.freesound_api_key_hint || "";
  error.value = "";
  statusText.value = "";
  baseError.value = "";
  imageError.value = "";
}

function checkUrl(value) {
  if (!value.trim() || /^https?:\/\//i.test(value.trim())) return "";
  return "地址要以 http:// 或 https:// 开头";
}

function secretPayload(value, clear, key) {
  if (value.trim()) return { [key]: value.trim() };
  if (clear) return { [`clear_${key}`]: true };
  return {};
}

function switchedSecret(kind, providerField, fallback, value, clear, key) {
  const previous = studio.config?.[providerField] || fallback;
  const moved = form[providerField] !== previous;
  if (moved && !value.trim() && !savedHint(kind, form[providerField])) return { [`clear_${key}`]: true };
  return secretPayload(value, clear, key);
}

function onModelPick(event, field, provider) {
  const value = event.target.value;
  if (value === "__custom") {
    const known = new Set((provider?.models || []).map(([id]) => id));
    if (known.has(form[field])) form[field] = "";
    return;
  }
  form[field] = value;
}

function applyLlmPreset() {
  const provider = findProvider("llm", form.llm_provider);
  form.openai_api_key = "";
  form.clear_openai = false;
  if (!provider || provider.id === "custom") return;
  form.openai_base_url = provider.base_url || "";
  form.openai_model = provider.default_model || "";
}

function applyTtsPreset() {
  const provider = findProvider("tts", form.tts_provider);
  form.tts_api_key = "";
  form.clear_tts = false;
  form.tts_voice_custom = "";
  if (!provider) return;
  form.tts_model = provider.default_model || "";
  form.tts_voice = provider.voices?.[0]?.[0] || "";
}

function applyImagePreset() {
  const provider = findProvider("image", form.image_provider);
  form.image_api_key = "";
  form.clear_image = false;
  if (!provider || provider.id === "off") {
    if (provider?.id === "off") form.image_api_url = "";
    return;
  }
  if (provider.id === "custom") return;
  form.image_api_url = provider.base_url || "";
  form.image_model = provider.default_model || "";
}

async function showError(message, nextTab) {
  error.value = message;
  if (nextTab) tab.value = nextTab;
  await nextTick();
  errorEl.value?.focus();
}

async function save() {
  const llm = llmProvider.value;
  const image = imageProvider.value;
  const speech = ttsProvider.value;
  baseError.value = llm?.id === "custom" ? checkUrl(form.openai_base_url) : "";
  imageError.value = image?.id === "custom" ? checkUrl(form.image_api_url) : "";
  error.value = "";
  const hasLlmKey = Boolean(form.openai_api_key.trim() || (llmHint.value && !form.clear_openai));
  if (!hasLlmKey) {
    await showError("大语言模型密钥是必填项。", "llm");
    return;
  }
  if (!(form.openai_model.trim() || llm?.default_model)) {
    await showError("请填写大语言模型名称或接入点 ID。", "llm");
    return;
  }
  if (llm?.id === "custom" && !form.openai_base_url.trim()) {
    baseError.value = "地址要以 http:// 或 https:// 开头";
    await showError("自定义大模型需要填写接口地址。", "llm");
    return;
  }
  if (image?.id === "custom" && !form.image_api_url.trim()) {
    imageError.value = "地址要以 http:// 或 https:// 开头";
    await showError("自定义生图需要填写接口地址。", "image");
    return;
  }
  if (baseError.value || imageError.value) {
    await showError("请修正标出的地址。", baseError.value ? "llm" : "image");
    return;
  }
  const voice = form.tts_voice_custom.trim() || form.tts_voice.trim();
  const body = {
    llm_provider: form.llm_provider,
    openai_base_url: llm?.id === "custom" ? form.openai_base_url.trim() : (llm?.base_url || form.openai_base_url.trim()),
    openai_model: form.openai_model.trim(),
    ...switchedSecret("llm", "llm_provider", "openai", form.openai_api_key, form.clear_openai, "openai_api_key"),
    tts_provider: form.tts_provider,
    tts_model: form.tts_model.trim() || speech?.default_model || "",
    tts_voice: voice,
    ...switchedSecret("tts", "tts_provider", "edge", form.tts_api_key, form.clear_tts, "tts_api_key"),
    image_provider: form.image_provider,
    image_api_url: image?.id === "off" ? "" : image?.id === "custom" ? form.image_api_url.trim() : (image?.base_url || ""),
    image_model: form.image_model.trim() || image?.default_model || "",
    ...switchedSecret("image", "image_provider", "off", form.image_api_key, form.clear_image, "image_api_key"),
    ...secretPayload(form.unsplash_access_key, form.clear_unsplash, "unsplash_access_key"),
    ...secretPayload(form.pexels_api_key, form.clear_pexels, "pexels_api_key"),
    ...secretPayload(form.pixabay_api_key, form.clear_pixabay, "pixabay_api_key"),
    ...secretPayload(form.freesound_api_key, form.clear_freesound, "freesound_api_key"),
  };
  saving.value = true;
  statusText.value = "正在保存…";
  try {
    const response = await fetch("/config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      statusText.value = "";
      await showError(detailMessage(payload, "保存失败"));
      return;
    }
    studio.applySavedConfig(payload);
    fill(payload);
    statusText.value = "已保存。接下来的片子会用这组密钥。";
  } finally {
    saving.value = false;
  }
}

function onClose() {
  if (studio.settingsOpen) studio.closeSettings();
  document.getElementById("open-settings")?.focus();
}

function stockReady(flag) {
  if (flag === "library") return studio.libraryReady || Boolean(studio.config?.freesound_ready);
  return Boolean(studio.config?.[flag]);
}

watch(() => studio.settingsOpen, async (open) => {
  const dialog = dialogEl.value;
  if (!dialog) return;
  if (open) {
    fill(studio.config);
    if (!dialog.open) dialog.showModal();
    await nextTick();
    document.getElementById("set-llm-provider")?.focus();
    return;
  }
  if (dialog.open) dialog.close();
});
</script>

<template>
  <dialog ref="dialogEl" class="keys" aria-labelledby="settings-title" @close="onClose">
    <div class="dialog-head">
      <h2 id="settings-title">密钥设置</h2>
      <button class="icon-button" type="button" aria-label="关闭设置" @click="studio.closeSettings()">
        <Icon name="close" />
      </button>
    </div>
    <form id="settings-form" @submit.prevent="save">
      <div class="key-tabs" role="tablist" aria-label="密钥分类">
        <button
          v-for="[id, label, flag] in tabs"
          :id="`tab-${id}`"
          :key="id"
          type="button"
          role="tab"
          :aria-selected="tab === id"
          :aria-controls="`panel-${id}`"
          @click="tab = id"
        >
          {{ label }}
          <i class="dot" :data-on="stockReady(flag)" />
        </button>
      </div>
      <div class="dialog-body">
        <p class="note">密钥只保存在这台电脑。大语言模型必填，配音、生图、图库和配乐都是选填。</p>

        <section v-show="tab === 'llm'" id="panel-llm" class="key-panel" role="tabpanel" aria-labelledby="tab-llm">
          <div class="key-head">
            <h3>大语言模型</h3>
            <span class="badge req">必填</span>
            <span class="state" :data-on="studio.config?.llm_ready">{{ studio.config?.llm_ready ? "就绪" : "未配置" }}</span>
          </div>
          <div class="key-grid">
            <div>
              <label for="set-llm-provider">服务</label>
              <select id="set-llm-provider" v-model="form.llm_provider" @change="applyLlmPreset">
                <optgroup v-for="group in llmGroups" :key="group.region" :label="group.label">
                  <option v-for="item in group.items" :key="item.id" :value="item.id">{{ item.name }}</option>
                </optgroup>
              </select>
            </div>
            <div>
              <label for="set-llm-model">模型</label>
              <select
                v-if="llmProvider && llmProvider.models.length"
                id="set-llm-model"
                :value="llmModelPick"
                @change="onModelPick($event, 'openai_model', llmProvider)"
              >
                <option v-for="[id, label] in llmProvider.models" :key="id" :value="id">{{ label }}</option>
                <option value="__custom">自定义</option>
              </select>
              <input
                v-else
                id="set-llm-model"
                v-model="form.openai_model"
                type="text"
                autocomplete="off"
                spellcheck="false"
                placeholder="接入点 ID，例如 ep-xxxx"
              />
            </div>
            <div v-if="llmProvider && llmProvider.models.length && llmModelPick === '__custom'" class="span-2">
              <label for="set-model">模型名称</label>
              <input id="set-model" v-model="form.openai_model" type="text" autocomplete="off" spellcheck="false" placeholder="填服务商要求的模型 ID" />
            </div>
            <div v-if="llmProvider?.id === 'custom'" class="span-2">
              <label for="set-base">接口地址</label>
              <input
                id="set-base"
                v-model="form.openai_base_url"
                type="url"
                inputmode="url"
                autocomplete="off"
                spellcheck="false"
                placeholder="https://example.com/v1"
                :aria-invalid="baseError ? 'true' : undefined"
                :aria-describedby="baseError ? 'base-error' : undefined"
                @blur="baseError = checkUrl(form.openai_base_url)"
              />
              <p v-if="baseError" id="base-error" class="field-error">{{ baseError }}</p>
            </div>
            <div class="span-2">
              <label for="set-openai">密钥 <span class="badge req">必填</span></label>
              <div class="key-row">
                <input id="set-openai" v-model="form.openai_api_key" type="password" autocomplete="off" spellcheck="false" :placeholder="placeholderFor(llmHint, true)" />
                <label v-if="llmHint" class="check" for="clear-openai"><input id="clear-openai" v-model="form.clear_openai" type="checkbox" /> 清除</label>
              </div>
            </div>
          </div>
          <p v-if="llmProvider?.base_url && llmProvider.id !== 'custom'" class="endpoint">{{ llmProvider.base_url }}</p>
          <p v-if="llmProvider?.hint" class="note">{{ llmProvider.hint }}</p>
        </section>

        <section v-show="tab === 'tts'" id="panel-tts" class="key-panel" role="tabpanel" aria-labelledby="tab-tts">
          <div class="key-head">
            <h3>语音合成</h3>
            <span class="badge opt">选填</span>
            <span class="state" :data-on="studio.config?.tts_ready">{{ studio.config?.tts_ready ? "就绪" : "未配置" }}</span>
          </div>
          <div class="key-grid">
            <div>
              <label for="set-tts-provider">服务</label>
              <select id="set-tts-provider" v-model="form.tts_provider" @change="applyTtsPreset">
                <optgroup v-for="group in ttsGroups" :key="group.region" :label="group.label">
                  <option v-for="item in group.items" :key="item.id" :value="item.id">{{ item.name }}</option>
                </optgroup>
              </select>
            </div>
            <div v-if="ttsProvider && ttsProvider.models.length">
              <label for="set-tts-model">模型</label>
              <select
                id="set-tts-model"
                :value="ttsModelPick"
                @change="onModelPick($event, 'tts_model', ttsProvider)"
              >
                <option v-for="[id, label] in ttsProvider.models" :key="id" :value="id">{{ label }}</option>
                <option value="__custom">自定义</option>
              </select>
            </div>
            <div v-if="ttsProvider && ttsProvider.models.length && ttsModelPick === '__custom'" class="span-2">
              <label for="set-tts-model-custom">模型名称</label>
              <input id="set-tts-model-custom" v-model="form.tts_model" type="text" autocomplete="off" spellcheck="false" />
            </div>
            <div v-if="ttsProvider && ttsProvider.voices.length">
              <label for="set-tts-voice">音色</label>
              <select id="set-tts-voice" v-model="form.tts_voice">
                <option v-for="[id, label] in ttsProvider.voices" :key="id" :value="id">{{ label }}</option>
              </select>
            </div>
            <div v-if="ttsProvider?.needs_key" class="span-2">
              <label for="set-tts-key">密钥 <span class="badge opt">选填</span></label>
              <div class="key-row">
                <input id="set-tts-key" v-model="form.tts_api_key" type="password" autocomplete="off" spellcheck="false" :placeholder="placeholderFor(ttsHint, false)" />
                <label v-if="ttsHint" class="check" for="clear-tts"><input id="clear-tts" v-model="form.clear_tts" type="checkbox" /> 清除</label>
              </div>
            </div>
            <div v-if="ttsProvider?.needs_key" class="span-2">
              <label for="set-tts-custom-voice">自定义音色 ID <span class="badge opt">选填</span></label>
              <input id="set-tts-custom-voice" v-model="form.tts_voice_custom" type="text" autocomplete="off" spellcheck="false" placeholder="留空则用上面的音色" />
            </div>
          </div>
          <p v-if="ttsProvider?.hint" class="note">{{ ttsProvider.hint }}</p>
        </section>

        <section v-show="tab === 'image'" id="panel-image" class="key-panel" role="tabpanel" aria-labelledby="tab-image">
          <div class="key-head">
            <h3>AI 生图</h3>
            <span class="badge opt">选填</span>
            <span class="state" :data-on="studio.config?.image_ready">{{ studio.config?.image_ready ? "就绪" : "未配置" }}</span>
          </div>
          <div class="key-grid">
            <div :class="{ 'span-2': !imageProvider || imageProvider.id === 'off' }">
              <label for="set-image-provider">服务</label>
              <select id="set-image-provider" v-model="form.image_provider" @change="applyImagePreset">
                <optgroup v-for="group in imageGroups" :key="group.region" :label="group.label">
                  <option v-for="item in group.items" :key="item.id" :value="item.id">{{ item.name }}</option>
                </optgroup>
              </select>
            </div>
            <template v-if="imageProvider && imageProvider.id !== 'off'">
              <div v-if="imageProvider.models.length">
                <label for="set-image-model">模型</label>
                <select
                  id="set-image-model"
                  :value="imageModelPick"
                  @change="onModelPick($event, 'image_model', imageProvider)"
                >
                  <option v-for="[id, label] in imageProvider.models" :key="id" :value="id">{{ label }}</option>
                  <option value="__custom">自定义</option>
                </select>
              </div>
              <div v-if="!imageProvider.models.length || imageModelPick === '__custom'">
                <label for="set-image-model-custom">模型名称</label>
                <input id="set-image-model-custom" v-model="form.image_model" type="text" autocomplete="off" spellcheck="false" placeholder="模型 ID" />
              </div>
              <div v-if="imageProvider.id === 'custom'" class="span-2">
                <label for="set-image-url">接口地址</label>
                <input
                  id="set-image-url"
                  v-model="form.image_api_url"
                  type="url"
                  inputmode="url"
                  autocomplete="off"
                  spellcheck="false"
                  placeholder="https://example.com/v1/images/generations"
                  :aria-invalid="imageError ? 'true' : undefined"
                  :aria-describedby="imageError ? 'image-error' : undefined"
                  @blur="imageError = checkUrl(form.image_api_url)"
                />
                <p v-if="imageError" id="image-error" class="field-error">{{ imageError }}</p>
              </div>
              <div class="span-2">
                <label for="set-image-key">密钥 <span class="badge opt">选填</span></label>
                <div class="key-row">
                  <input id="set-image-key" v-model="form.image_api_key" type="password" autocomplete="off" spellcheck="false" :placeholder="placeholderFor(imageHint, false)" />
                  <label v-if="imageHint" class="check" for="clear-image"><input id="clear-image" v-model="form.clear_image" type="checkbox" /> 清除</label>
                </div>
              </div>
            </template>
          </div>
          <p v-if="imageProvider?.base_url && imageProvider.id !== 'custom'" class="endpoint">{{ imageProvider.base_url }}</p>
          <p v-if="imageProvider?.hint" class="note">{{ imageProvider.hint }}</p>
        </section>

        <section v-show="tab === 'stock'" id="panel-stock" class="key-panel" role="tabpanel" aria-labelledby="tab-stock">
          <div class="key-head">
            <h3>图库和配乐</h3>
            <span class="badge opt">选填</span>
            <span class="state" :data-on="studio.libraryReady">{{ studio.libraryReady ? "图库就绪" : "图库未配置" }}</span>
          </div>
          <div class="key-grid">
            <div v-for="[id, label, model, clear, hint] in stockFields" :key="id">
              <label :for="id">{{ label }} <span class="badge opt">选填</span></label>
              <div class="key-row">
                <input :id="id" v-model="form[model]" type="password" autocomplete="off" spellcheck="false" :placeholder="placeholderFor(hints[hint], false)" />
                <label v-if="hints[hint]" class="check" :for="`clear-${hint}`"><input :id="`clear-${hint}`" v-model="form[clear]" type="checkbox" /> 清除</label>
              </div>
            </div>
          </div>
          <p class="note">图库用来找实拍素材。Freesound 只检索 CC0 和 CC BY，可免费商用，用到的曲子会在日志里署名。</p>
        </section>

        <p v-if="error" id="settings-error" ref="errorEl" class="alert" role="alert" tabindex="-1">{{ error }}</p>
        <p class="note" aria-live="polite">{{ statusText }}</p>
      </div>
      <div class="dialog-foot">
        <button class="btn btn-primary" type="submit" :disabled="saving">保存密钥</button>
      </div>
    </form>
  </dialog>
</template>
