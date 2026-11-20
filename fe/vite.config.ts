import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev server proxy sang FastAPI để tránh CORS khi phát triển.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
});
