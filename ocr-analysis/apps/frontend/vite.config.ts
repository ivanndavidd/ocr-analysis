import { svelte } from '@sveltejs/vite-plugin-svelte';
import { defineConfig } from 'vite';

// Local only: the dev server listens on 127.0.0.1 and forwards /api to the backend, keeping the Host header so the
// backend's host allowlist still applies.
export default defineConfig({
  plugins: [svelte()],
  server: {
    host: '127.0.0.1',
    port: 5173,
    strictPort: true,
    proxy: { '/api': { target: 'http://127.0.0.1:3000' } }
  },
  preview: { host: '127.0.0.1', port: 4173, strictPort: true },
});
