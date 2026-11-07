import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = dirname(fileURLToPath(import.meta.url));
const host = 'http://127.0.0.1:8765';
const css = [
  'style', 'taxonomy-workspace', 'search-planner', 'workspace',
  'test-lab', 'sre-studio', 'group-a',
];

export default defineConfig({
  root,
  plugins: [react()],
  build: {
    outDir: '../site/react-dist',
    emptyOutDir: true,
    assetsDir: 'assets',
    sourcemap: false,
  },
  server: {
    host: '127.0.0.1',
    port: 5173,
    strictPort: true,
    proxy: {
      '/api': host,
      '/react-legacy': host,
      ...Object.fromEntries(css.map((name) => ['/' + name + '.css', host])),
    },
  },
});
