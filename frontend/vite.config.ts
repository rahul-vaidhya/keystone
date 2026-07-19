import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/auth": { target: "http://127.0.0.1:8010", changeOrigin: true },
      "/health": { target: "http://127.0.0.1:8010", changeOrigin: true },
      "/documents": { target: "http://127.0.0.1:8010", changeOrigin: true },
      "/notebooks": { target: "http://127.0.0.1:8010", changeOrigin: true },
      "/chat": { target: "http://127.0.0.1:8010", changeOrigin: true },
      "/access-roles": { target: "http://127.0.0.1:8010", changeOrigin: true },
      "/retrieval": { target: "http://127.0.0.1:8010", changeOrigin: true },
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
  },
});
