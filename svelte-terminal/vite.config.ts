import { svelte } from '@sveltejs/vite-plugin-svelte';
import tailwindcss from '@tailwindcss/vite';
import path from 'path';
import { fileURLToPath } from 'url';
import { defineConfig } from 'vite';
import * as dataPluginModule from './vite-plugin-data';

// Tolerant load: if the plugin file is wrong or incomplete, Vite still boots (the app runs from its
// local cache) and logs why, instead of the container crash-looping.
const dataPlugin = (dataPluginModule as any).default;
if (typeof dataPlugin !== 'function') {
  console.error('[data-api] vite-plugin-data.ts has no default export. /api/data is DISABLED. Replace that file with the correct version.');
}

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

export default defineConfig({
  plugins: [svelte(), tailwindcss(), ...(typeof dataPlugin === 'function' ? [dataPlugin()] : [])],
  resolve: {
    alias: {
      $lib: path.resolve(__dirname, './src/lib'),
    },
  },
  server: {
    host: '0.0.0.0',
    port: 8100,
    strictPort: true,
    // Saving a card or lorebook writes into ./data; don't treat that as a source change.
    watch: { ignored: ['**/data/**'] },
    proxy: {
      '/v1': {
        target: process.env.MEMORY_PROXY_URL || 'http://localhost:8401',
        changeOrigin: true,
      },
      '/api/stm': {
        target: process.env.STM_SERVICE_URL || 'http://localhost:8002',
        changeOrigin: true,
        rewrite: (p) => p.replace(/^\/api\/stm/, ''),
      },
      '/api/memory': {
        target: process.env.MEMORY_ENGINE_URL || 'http://localhost:8000',
        changeOrigin: true,
        rewrite: (p) => p.replace(/^\/api\/memory/, ''),
      },
      '/api/prometheus': {
        target: process.env.PROMETHEUS_URL || 'http://localhost:8500',
        changeOrigin: true,
        rewrite: (p) => p.replace(/^\/api\/prometheus/, ''),
      },
    },
  },
  preview: {
    host: '0.0.0.0',
    port: 8100,
    strictPort: true,
  },
});
