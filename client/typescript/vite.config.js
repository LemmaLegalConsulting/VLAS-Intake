import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react-swc';

export default defineConfig(({ mode }) => {
    const env = loadEnv(mode, process.cwd(), '');
    return {
        base: "./", //Use relative paths so it works at any mount path
        plugins: [react()],
        server: {
            proxy: {
                '/ws': {
                    target: env.VITE_BACKEND_WS_URL || 'ws://localhost:8765',
                    changeOrigin: true,
                },
            },
        },
    };
});
