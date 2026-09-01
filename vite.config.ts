import { defineConfig } from 'vite';
import vue from '@vitejs/plugin-vue';
import { fileURLToPath, URL } from 'node:url';

// Hermes Portal 前端构建配置
// 生产构建产物输出到 dist/，由 Portal BFF 挂载在 /portal 路径下，
// 或由 Nginx 直接托管。开发时通过 proxy 把 /api 转发到 BFF。
export default defineConfig({
  plugins: [vue()],
  base: '/portal/',
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    host: '0.0.0.0',
    port: 5174,
    proxy: {
      // Portal BFF 自身接口
      '/api/portal': {
        target: 'http://localhost:9000',
        changeOrigin: true,
      },
      // 经 BFF 代理的下游 trader 接口
      '/api/trader': {
        target: 'http://localhost:9000',
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    assetsDir: 'assets',
    sourcemap: false,
    chunkSizeWarningLimit: 1200,
    rollupOptions: {
      output: {
        manualChunks: {
          vue: ['vue', 'vue-router', 'pinia'],
          echarts: ['echarts', 'vue-echarts'],
        },
      },
    },
  },
});
