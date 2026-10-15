import {Circle, Line, Rect, Txt, makeScene2D} from '@motion-canvas/2d';
import {all, createRef, easeOutCubic, waitFor} from '@motion-canvas/core';
import {computeLayout, GenericSceneDiagram, type GenericSceneSpec} from '../genericSceneSpec';
import {SD} from '../systemDecoded';

interface TimingEvent {
  id: string;
  phrase: string;
  start: number;
  end: number;
}

interface RuntimeSpec extends GenericSceneSpec {
  duration_seconds: number;
  speech_duration_seconds: number;
  caption_safe_bottom: number;
  timing: {
    schema_version: 1;
    source: 'canonical-caption-word-timing';
    audio_duration_seconds: number;
    events: TimingEvent[];
  };
}

function runtimeSpec(): RuntimeSpec {
  const spec = (window as unknown as {__PROPS__?: RuntimeSpec}).__PROPS__;
  if (!spec) throw new Error('Generic Motion Canvas scene received no explicit payload.');
  return spec;
}

function revealTimes(spec: RuntimeSpec): number[] {
  const events = [...spec.timing.events].map(event => event.start).sort((a, b) => a - b);
  const count = spec.nodes.length;
  const first = Math.min(0.18, spec.speech_duration_seconds * 0.08);
  const last = Math.max(first, spec.speech_duration_seconds - 0.8);
  const times = spec.nodes.map((_node, index) =>
    first + (last - first) * (index / Math.max(1, count - 1)));
  events.forEach((event, index) => {
    const nodeIndex = Math.max(1, Math.round((index + 1) * (count - 1) / events.length));
    times[nodeIndex] = Math.max(first, Math.min(last, event));
  });
  const minimumGap = Math.min(0.68, (last - first) / Math.max(1, count - 1));
  times[count - 1] = Math.min(times[count - 1], last);
  for (let index = count - 2; index >= 0; index -= 1) {
    times[index] = Math.min(times[index], times[index + 1] - minimumGap);
  }
  times[0] = first;
  for (let index = 1; index < count; index += 1) {
    times[index] = Math.max(times[index], times[index - 1] + minimumGap);
  }
  return times;
}

export default makeScene2D(function* (view) {
  const spec = runtimeSpec();
  const layout = computeLayout(spec);
  view.fill(SD.bg);
  const heading = createRef<Txt>();
  const connectorLines = spec.connectors.map(() => createRef<Line>());
  const connectorLabels = spec.connectors.map(() => createRef<Rect>());
  const connectorSignals = spec.connectors.map(() => createRef<Circle>());
  const nodes = spec.nodes.map(() => createRef<Rect>());
  const endCard = createRef<Rect>();

  view.add(<GenericSceneDiagram spec={spec} refs={{
    heading,
    nodes,
    connectors: connectorLines,
    connectorLabels,
    connectorSignals,
    endCard,
  }}/>);

  let cursor = 0;
  const seek = function* (time: number) {
    const next = Math.max(cursor, Math.min(time, spec.duration_seconds));
    yield* waitFor(Math.max(0, next - cursor));
    cursor = next;
  };

  yield* all(
    heading().opacity(1, 0.24),
    heading().y(-620, 0).to(-640, 0.32, easeOutCubic),
  );
  cursor += 0.32;
  const times = revealTimes(spec);
  for (let nodeIndex = 0; nodeIndex < spec.nodes.length; nodeIndex += 1) {
    yield* seek(times[nodeIndex]);
    const animations = [
      nodes[nodeIndex]().opacity(1, 0.22),
      nodes[nodeIndex]().scale(1, 0.32, easeOutCubic),
    ];
    const activeEdges: number[] = [];
    for (let edgeIndex = 0; edgeIndex < spec.connectors.length; edgeIndex += 1) {
      const edge = spec.connectors[edgeIndex];
      const sourceIndex = spec.nodes.findIndex(node => node.id === edge.from);
      const targetIndex = spec.nodes.findIndex(node => node.id === edge.to);
      if (Math.max(sourceIndex, targetIndex) !== nodeIndex) continue;
      activeEdges.push(edgeIndex);
      animations.push(connectorLines[edgeIndex]().end(1, 0.32, easeOutCubic));
      animations.push(
        connectorSignals[edgeIndex]().opacity(1, 0.08),
        connectorSignals[edgeIndex]().position(layout.connectors[edgeIndex].points[1], 0.36, easeOutCubic),
      );
      const label = edge.label;
      if (label && layout.connectors[edgeIndex].label) {
        animations.push(connectorLabels[edgeIndex]().opacity(1, 0.22));
      }
    }
    yield* all(...animations);
    cursor += 0.36;
    for (const edgeIndex of activeEdges) {
      const edge = spec.connectors[edgeIndex];
      const targetIndex = spec.nodes.findIndex(node => node.id === edge.to);
      if (edge.kind === 'replacement' || edge.kind === 'rejection') {
        yield* nodes[targetIndex]().stroke(SD.danger, 0.12);
        cursor += 0.12;
      } else if (edge.kind === 'verification') {
        yield* nodes[targetIndex]().scale(1.04, 0.1).to(1, 0.1);
        cursor += 0.2;
      }
    }
    if (activeEdges.length) {
      yield* all(...activeEdges.map(index => connectorSignals[index]().opacity(0, 0.1)));
      cursor += 0.1;
    }
  }
  if (spec.end_card) {
    yield* seek(Math.max(cursor, spec.speech_duration_seconds + 0.08));
    yield* endCard().opacity(1, 0.4, easeOutCubic);
    cursor += 0.4;
  }
  yield* seek(spec.duration_seconds);
});
