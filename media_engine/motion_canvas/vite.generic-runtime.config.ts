import {defineConfig} from 'vite';

export default defineConfig({
  root: 'generic_runtime',
  base: './',
  build: {
    outDir: '../../../scene_templates/generic_scene_spec',
    emptyOutDir: false,
  },
});
