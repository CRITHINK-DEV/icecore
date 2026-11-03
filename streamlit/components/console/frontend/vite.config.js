import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

// Dev mode only: serve the prototype's mock data at /icecore-mock-data.js. The build never contains it.
const MOCK = fileURLToPath(new URL('../../../../docs/design/app-prototype/icecore-mock-data.js', import.meta.url));
const mock = () => ({ name: 'icecore-mock', configureServer(server) {
  server.middlewares.use('/icecore-mock-data.js', (req, res) => { res.setHeader('Content-Type', 'text/javascript'); res.end(readFileSync(MOCK)); });
} });

// Relative base: Streamlit serves the build directory under its own component URL. No CDN at runtime.
export default defineConfig({
  base: './',
  plugins: [react(), mock()],
  build: { outDir: '../build', emptyOutDir: true, assetsInlineLimit: 100000, chunkSizeWarningLimit: 2000,
           rollupOptions: { output: { entryFileNames: 'assets/console.js', assetFileNames: 'assets/console[extname]' } } },
});
