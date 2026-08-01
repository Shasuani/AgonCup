import { defineConfig, type Plugin } from 'vite';
import react from '@vitejs/plugin-react';

// Electron loads the built renderer via loadFile (file:// origin). The `crossorigin`
// attribute Vite adds to module/preload tags makes file:// treat them as CORS and
// can block them under the app CSP — strip it. Relative base keeps asset URLs
// file://-resolvable.
function stripCrossorigin(): Plugin {
  return {
    name: 'strip-crossorigin',
    enforce: 'post',
    transformIndexHtml(html) {
      return html.replace(/\s+crossorigin(=(?:"[^"]*"|'[^']*'))?/g, '');
    },
  };
}

export default defineConfig({
  root: __dirname,
  base: './',
  plugins: [react(), stripCrossorigin()],
  build: {
    outDir: '../dist-renderer',
    emptyOutDir: true,
    target: 'chrome120',
    modulePreload: { polyfill: false },
  },
});
