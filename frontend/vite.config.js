import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";

export default defineConfig({
  plugins: [vue()],
  build: {
    outDir: "../web/dist",
    emptyOutDir: true,
  },
  server: {
    host: "127.0.0.1",
    port: 5173,
    strictPort: true,
    proxy: {
      "/config": "http://127.0.0.1:8000",
      "/tasks": "http://127.0.0.1:8000",
      "/voices": "http://127.0.0.1:8000",
    },
  },
});
