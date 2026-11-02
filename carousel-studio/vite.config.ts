import { defineConfig } from 'vite';
import { fileURLToPath } from 'node:url';

export default defineConfig({
  base: '/carousel-studio/',
  plugins: [],
  server: {
    host: '127.0.0.1',
    port: 5173,
    proxy: { '/api': 'http://127.0.0.1:8765' },
  },
  build: {
    outDir: fileURLToPath(new URL('../site/carousel-studio', import.meta.url)),
    emptyOutDir: true,
    assetsDir: 'assets',
  },
});
