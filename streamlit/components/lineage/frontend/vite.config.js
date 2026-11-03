import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Relative base: Streamlit serves the build directory under its own component URL. No CDN at runtime.
export default defineConfig({
  base: "./",
  plugins: [react()],
  build: { outDir: "../build", emptyOutDir: true, assetsInlineLimit: 100000, chunkSizeWarningLimit: 1500,
           rollupOptions: { output: { entryFileNames: "assets/lineage.js", assetFileNames: "assets/lineage[extname]" } } },
});
