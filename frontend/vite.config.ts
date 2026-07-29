import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: {
    host: '127.0.0.1',
    port: 8733,
    proxy: {
      '/api': 'http://127.0.0.1:8732',
      '/health': 'http://127.0.0.1:8732',
    },
  },
})
