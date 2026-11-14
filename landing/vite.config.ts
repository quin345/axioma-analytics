import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import path from "node:path";

// The public landing page, served by Flask at `/` (built assets at /assets).
// The terminal is a separate Vite app served at /terminal/.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  server: { port: 5174 },
  preview: { port: 4174 },
});
