import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Backend runs on :8000 (FastAPI). Proxy API + SSE during dev.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/runs": { target: "http://localhost:8000", changeOrigin: true },
      "/companies": { target: "http://localhost:8000", changeOrigin: true },
      "/corpus": { target: "http://localhost:8000", changeOrigin: true },
      "/evals": { target: "http://localhost:8000", changeOrigin: true },
      "/health": { target: "http://localhost:8000", changeOrigin: true },
    },
  },
});
