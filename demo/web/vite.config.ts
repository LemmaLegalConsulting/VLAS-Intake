import { defineConfig } from 'vite';

export default defineConfig({
  // AudioWorklet modules must have a same-origin URL under our CSP.
  build: { assetsInlineLimit: 0, rollupOptions: { input: { main: "index.html", compare: "compare/index.html" } } },
});
