import { defineConfig } from 'vite';
import { resolve } from 'node:path';

export default defineConfig({
  base: '/carousel-studio/',
  plugins: [],
  server: {
    host: '127.0.0.1',
    port: 5173,
    proxy: { '/api': 'http://127.0.0.1:8765' },
  },
  build: {
    outDir: resolve(__dirname, '../site/carousel-studio'),
    emptyOutDir: true,
    assetsDir: 'assets',
  },
});
