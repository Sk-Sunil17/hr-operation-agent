import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// The dashboard runs on 5173 and forwards /api to the agent on 4000.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { '/api': 'http://127.0.0.1:4000' },
  },
});
