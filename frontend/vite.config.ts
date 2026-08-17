import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    // `npm run dev` talks to the Python server on 7860 for everything real.
    // Set API_ORIGIN=https://localhost:7860 when the server is running with a
    // cert; `secure: false` is what lets the proxy accept the self-signed one.
    proxy: {
      "/api": {
        target: process.env.API_ORIGIN ?? "http://localhost:7860",
        changeOrigin: true,
        secure: false,
      },
    },
  },
  build: { outDir: "dist" },
});
