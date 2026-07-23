import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

const apiTarget = process.env.DASHBOARD_API_TARGET || 'http://127.0.0.1:8800';

export default defineConfig({
  root: 'dashboard',
  base: '/dashboard/assets/',
  plugins: [react()],
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    sourcemap: true,
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (id.includes('node_modules/echarts')) return 'echarts';
          if (id.includes('node_modules/@xyflow')) return 'react-flow';
          if (id.includes('node_modules')) return 'vendor';
        },
      },
    },
  },
  server: {
    host: '127.0.0.1',
    port: 5173,
    proxy: {
      '/api': {
        target: apiTarget,
        ws: true,
      },
    },
  },
  test: {
    environment: 'node',
    include: ['src/**/*.test.ts'],
  },
});
