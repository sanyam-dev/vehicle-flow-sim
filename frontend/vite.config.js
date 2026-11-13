import { defineConfig, loadEnv } from 'vite';
import { svelte } from '@sveltejs/vite-plugin-svelte';

// Dev server proxies /api to the FastAPI backend (VITE_DEV_API, default :8000),
// so the app can use same-origin URLs locally.
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '');
  return {
    plugins: [svelte()],
    server: {
      proxy: { '/api': env.VITE_DEV_API || 'http://localhost:8000' },
    },
  };
});
