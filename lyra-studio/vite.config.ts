import { defineConfig } from 'vite';
import { svelte } from '@sveltejs/vite-plugin-svelte';

// The memory engine's API key is added here, on the server side of the proxy,
// so it is never part of the code sent to the browser.
// Set MEMORY_API_KEY on the lyra-studio service to change it.
const MEMORY_API_KEY = process.env.MEMORY_API_KEY ?? 'lyra_multipass';

export default defineConfig({
  plugins: [svelte()],
  server: {
    host: '0.0.0.0',
    port: 8003,
    strictPort: true,
    watch: {
      usePolling: true 
    },
    proxy: {
      '/api/stm': {
        target: 'http://short-term-memory:8002',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api\/stm/, '')
      },
      '/api/memory': {
        target: 'http://memory-engine:8000',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api\/memory/, ''),
        headers: { 'x-api-key': MEMORY_API_KEY }
      }
    }
  }
});
