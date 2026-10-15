import {defineConfig} from 'vite';
import motionCanvasModule from '@motion-canvas/vite-plugin';

const motionCanvas =
  (motionCanvasModule as unknown as {default?: typeof motionCanvasModule}).default ??
  motionCanvasModule;

export default defineConfig({
  plugins: [motionCanvas({project: './src/genericProject.ts'})],
  build: {
    outDir: '../../scene_templates/generic_scene_spec',
    emptyOutDir: true,
    rollupOptions: {
      output: {entryFileNames: 'generic-project.js'},
    },
  },
});
