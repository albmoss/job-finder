import { defineConfig, type Plugin } from 'vite';
import react from '@vitejs/plugin-react';
import { readFileSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const SHAPE_FILE = fileURLToPath(new URL('./src/components/ui/fingerprint_shape.json', import.meta.url));

// Tylko `npm run dev`: laboratorium kształtu (fingerprint_lab.html) zapisuje suwaki do pliku.
// Przyjmuje wyłącznie klucze, które plik już ma, i tylko skończone liczby.
function fingerprintLab(): Plugin {
  return {
    name: 'fingerprint-lab',
    apply: 'serve',
    configureServer(server) {
      server.middlewares.use('/__lab/fingerprint', (req, res) => {
        if (req.method !== 'POST') {
          res.statusCode = 405;
          res.end();
          return;
        }
        let body = '';
        req.on('data', (chunk) => (body += chunk));
        req.on('end', () => {
          try {
            const current = JSON.parse(readFileSync(SHAPE_FILE, 'utf8')) as Record<string, number>;
            const next = JSON.parse(body) as Record<string, unknown>;
            const out: Record<string, number> = {};
            for (const key of Object.keys(current)) {
              const v = next[key];
              if (typeof v !== 'number' || !Number.isFinite(v)) throw new Error(`zła wartość: ${key}`);
              out[key] = Math.round(v * 1000) / 1000;
            }
            writeFileSync(SHAPE_FILE, JSON.stringify(out, null, 2) + '\n');
            res.setHeader('Content-Type', 'application/json');
            res.end(JSON.stringify(out));
          } catch (e) {
            res.statusCode = 400;
            res.end(String(e));
          }
        });
      });
    },
  };
}

export default defineConfig({
  plugins: [react(), fingerprintLab()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8501',
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
  },
});
