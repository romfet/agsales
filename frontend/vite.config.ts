import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

const target = process.env.VITE_PROXY_TARGET || "http://127.0.0.1:5050";

// In dev, proxy API calls to the FastAPI backend so the SPA can use same-origin
// paths. In prod, nginx serves the build and proxies /api to FastAPI.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": { target, changeOrigin: true },
      "/health": { target, changeOrigin: true },
    },
  },
});
