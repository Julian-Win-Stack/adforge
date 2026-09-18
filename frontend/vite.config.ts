import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// The page calls /api and /media on its own address; Vite passes them on to Django.
const apiTarget = process.env.API_TARGET ?? "http://localhost:8000";

export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
  },
  server: {
    proxy: {
      "/api": apiTarget,
      "/media": apiTarget,
    },
  },
});
