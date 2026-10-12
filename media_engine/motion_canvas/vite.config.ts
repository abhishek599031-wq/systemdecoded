import {defineConfig} from 'vite';
import motionCanvasModule from '@motion-canvas/vite-plugin';

// 3.17.2 publishes CommonJS. Vite 5 exposes its default through a second
// `default` layer when this config is loaded as ESM.
const motionCanvas =
  (motionCanvasModule as unknown as {default?: typeof motionCanvasModule}).default ??
  motionCanvasModule;

export default defineConfig({
  plugins: [motionCanvas({project: './src/project.ts', output: './output-video2-production'})],
  server: {
    host: '0.0.0.0',
    port: 9000,
    allowedHosts: ['localhost', 'host.docker.internal'],
  },
  build: {outDir: 'dist'},
});
