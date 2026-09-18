import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The page calls /api and /media on its own address; Vite passes them on to Django.
const apiTarget = process.env.API_TARGET ?? "http://localhost:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": apiTarget,
      "/media": apiTarget,
    },
  },
});
