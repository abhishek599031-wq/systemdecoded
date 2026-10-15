import {Renderer, Vector2} from '@motion-canvas/core';
import project from '../../../scene_templates/generic_scene_spec/generic-project.js';
import {computeLayout, type GenericSceneSpec, type LayoutReport} from './genericSceneSpec';

interface RuntimeWindow extends Window {
  __PROPS__?: {duration_seconds?: number};
  ready?: boolean;
  seek?: (progress: number) => Promise<void>;
  __LAYOUT_REPORT__?: LayoutReport;
}

const runtime = window as RuntimeWindow;
const duration = runtime.__PROPS__?.duration_seconds;
if (!duration || duration <= 0) throw new Error('A positive measured scene duration is required.');

const renderer = new Renderer(project);
const settings = {
  name: 'generic-scene-spec',
  range: [0, duration] as [number, number],
  fps: 30,
  exporter: {name: '@motion-canvas/core/image-sequence', options: {}},
  size: new Vector2(1080, 1920),
  resolutionScale: 1,
  colorSpace: 'srgb' as const,
  background: '#030712',
};

const canvas = renderer.stage.finalBuffer;
canvas.setAttribute('aria-label', 'SystemDecoded generic Motion Canvas scene');
document.body.append(canvas);

runtime.seek = async (progress: number) => {
  const bounded = Math.max(0, Math.min(1, Number(progress)));
  await renderer.renderFrame(settings, bounded * duration);
  if (canvas.width !== 1080 || canvas.height !== 1920) {
    throw new Error(`Unexpected Motion Canvas size ${canvas.width}x${canvas.height}`);
  }
};

void document.fonts.ready.then(async () => {
  runtime.__LAYOUT_REPORT__ = computeLayout(runtime.__PROPS__ as GenericSceneSpec).report;
  await runtime.seek!(0);
  runtime.ready = true;
});
