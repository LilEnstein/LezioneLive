import { fileURLToPath, URL } from "node:url";

import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

const BACKEND = process.env.LEZIONELIVE_BACKEND ?? "http://127.0.0.1:8765";

export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    strictPort: true,
    proxy: {
      "/api": { target: BACKEND, changeOrigin: false },
      "/ws": { target: BACKEND.replace(/^http/, "ws"), ws: true, changeOrigin: false },
    },
  },
  build: {
    outDir: "dist",
    sourcemap: false,
    chunkSizeWarningLimit: 1500,
    rollupOptions: {
      // Hai trang: giao diện laptop + trang điện thoại đồng hành (/c, chế độ lớp học trực tiếp).
      input: {
        main: fileURLToPath(new URL("./index.html", import.meta.url)),
        companion: fileURLToPath(new URL("./companion.html", import.meta.url)),
      },
    },
  },
});
