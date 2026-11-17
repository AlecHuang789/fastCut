<script setup>
defineProps({
  name: { type: String, required: true },
});
</script>

<template>
  <svg viewBox="0 0 16 16" aria-hidden="true">
    <template v-if="name === 'spark'">
      <path d="M8 1.5v2.2M8 12.3v2.2M1.5 8h2.2M12.3 8h2.2" />
      <path d="m3.4 3.4 1.5 1.5M11.1 11.1l1.5 1.5M12.6 3.4l-1.5 1.5M4.9 11.1 3.4 12.6" />
    </template>
    <template v-else-if="name === 'rows'">
      <path d="M2.5 4.5h11M2.5 8h7.5M2.5 11.5h5" />
    </template>
    <template v-else-if="name === 'film'">
      <rect x="1.75" y="3.25" width="12.5" height="9.5" rx="1.2" />
      <path d="M6.2 3.25v9.5M10.2 3.25v9.5" />
    </template>
    <template v-else-if="name === 'gear'">
      <circle cx="8" cy="8" r="2" />
      <path d="M8 1.8v1.6M8 12.6v1.6M1.8 8h1.6M12.6 8h1.6M3.5 3.5l1.1 1.1M11.4 11.4l1.1 1.1M12.5 3.5l-1.1 1.1M4.6 11.4l-1.1 1.1" />
    </template>
    <template v-else-if="name === 'plus'">
      <path d="M8 3.25v9.5M3.25 8h9.5" />
    </template>
    <template v-else-if="name === 'close'">
      <path d="m4 4 8 8M12 4 4 12" />
    </template>
    <template v-else-if="name === 'trash'">
      <path d="M3.25 4.25h9.5" />
      <path d="M6.25 4.25V2.75h3.5v1.5" />
      <path d="M4.5 4.25l.55 9h5.9l.55-9" />
    </template>
  </svg>
</template>
