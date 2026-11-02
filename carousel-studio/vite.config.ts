import { defineConfig } from 'vite';
import { fileURLToPath } from 'node:url';

export default defineConfig({
  base: '/carousel-studio/',
  plugins: [],
  server: {
    host: '127.0.0.1',
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8765',
        changeOrigin: true,
        configure(proxy) {
          // The Python app requires the local backend's exact Origin header.
          // Vite dev proxy is bound to loopback only, never public traffic.
          proxy.on('proxyReq', (request) => {
            request.setHeader('Origin', 'http://127.0.0.1:8765');
          });
        },
      },
    },
  },
  build: {
    outDir: fileURLToPath(new URL('../site/carousel-studio', import.meta.url)),
    emptyOutDir: true,
    assetsDir: 'assets',
  },
});
