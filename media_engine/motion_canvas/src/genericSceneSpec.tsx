/** Responsive, data-only portrait SceneSpec interpreter. */
import {Circle, Layout, Line, Rect, Txt} from '@motion-canvas/2d';
import type {Reference} from '@motion-canvas/core';
import {SD} from './systemDecoded';

export type VisualType =
  | 'TITLE_REVEAL' | 'FLOW' | 'PARALLEL_FLOW' | 'PROCESS' | 'COMPARISON'
  | 'DATABASE_FLOW' | 'NETWORK_FLOW' | 'TIMELINE' | 'CODE_REVEAL' | 'CALLOUT';

export interface GenericNode { id: string; label: string; emphasis: 'normal' | 'accent' | 'danger' | 'success'; }
export type ConnectorKind = 'normal' | 'injection' | 'replacement' | 'verification' | 'rejection';
export interface GenericConnector { from: string; to: string; label?: string; kind?: ConnectorKind; }
export interface GenericSceneSpec {
  schema_version: 1;
  visual_type: VisualType;
  labels: string[];
  nodes: GenericNode[];
  connectors: GenericConnector[];
  timing_anchors: Record<string, string>;
  animation_intent: string;
  end_card?: boolean;
}

export interface GenericDiagramRefs {
  heading?: Reference<Txt>;
  nodes?: Reference<Rect>[];
  connectors?: Reference<Line>[];
  connectorLabels?: Reference<Rect>[];
  connectorSignals?: Reference<Circle>[];
  endCard?: Reference<Rect>;
}

type Point = [number, number];
interface NodeLayout {id: string; position: Point; width: number; height: number;}
interface Bounds {left: number; right: number; top: number; bottom: number;}
interface ConnectorLayout {
  points: [Point, Point];
  label?: {
    position: Point;
    width: number;
    height: number;
    fontSize: number;
    measuredTextWidth: number;
    placement: 'beside' | 'interrupted';
  };
}
export interface LayoutReport {
  schema_version: 1;
  canvas: {width: number; height: number};
  safe_region: {left: number; right: number; top: number; bottom: number};
  heading: {left: number; right: number; top: number; bottom: number};
  layout: 'vertical' | 'wrapped' | 'comparison';
  nodes: Array<{id: string; left: number; right: number; top: number; bottom: number; width: number; height: number}>;
  connectors: Array<{from: string; to: string; kind: ConnectorKind}>;
  connector_labels: Array<{
    from: string;
    to: string;
    text: string;
    visible: boolean;
    bounds?: Bounds;
    font_size: number;
    measured_text_width: number;
    measurement_source: 'browser-canvas-font-metrics' | 'fallback-estimate';
    placement?: 'beside' | 'interrupted';
    line_collision: boolean;
    arrowhead_collision: boolean;
    node_collision: boolean;
    label_collision: boolean;
    omitted_reason?: string;
  }>;
  caption_region_top: number;
  has_end_card: boolean;
  end_card_brand?: {name: string; tagline: string};
}

const colorFor = (emphasis: GenericNode['emphasis']) =>
  emphasis === 'danger' ? SD.danger : emphasis === 'success' ? SD.success :
  emphasis === 'accent' ? SD.cyan : SD.white;

const intersects = (a: Bounds, b: Bounds, padding = 0) =>
  a.left < b.right + padding && a.right > b.left - padding &&
  a.top < b.bottom + padding && a.bottom > b.top - padding;

function measureLabel(text: string, fontSize: number) {
  if (typeof document !== 'undefined') {
    const canvas = document.createElement('canvas');
    const context = canvas.getContext('2d');
    if (context) {
      context.font = `800 ${fontSize}px ${SD.font}`;
      const metrics = context.measureText(text);
      const height = Math.ceil(
        (metrics.actualBoundingBoxAscent || fontSize * 0.8) +
        (metrics.actualBoundingBoxDescent || fontSize * 0.25),
      );
      return {width: Math.ceil(metrics.width), height, source: 'browser-canvas-font-metrics' as const};
    }
  }
  return {width: Math.ceil(text.length * fontSize * 0.62), height: fontSize,
    source: 'fallback-estimate' as const};
}

export function computeLayout(spec: GenericSceneSpec): {
  nodes: NodeLayout[];
  connectors: ConnectorLayout[];
  report: LayoutReport;
} {
  const comparison = (spec.visual_type === 'COMPARISON' || spec.visual_type === 'PARALLEL_FLOW') && spec.nodes.length >= 3;
  const wrapped = spec.nodes.length >= 5;
  let layout: LayoutReport['layout'] = 'vertical';
  let positions: Point[];
  let sizes: Array<[number, number]>;
  if (comparison) {
    layout = 'comparison';
    positions = [[0, -340], ...spec.nodes.slice(1).map((_node, index) =>
      [index % 2 === 0 ? -220 : 220, -70 + Math.floor(index / 2) * 235] as Point)];
    sizes = [[760, 150], ...spec.nodes.slice(1).map(() => [400, 160] as [number, number])];
  } else if (wrapped) {
    layout = 'wrapped';
    positions = spec.nodes.map((_node, index) =>
      [index % 2 === 0 ? -220 : 220, -330 + Math.floor(index / 2) * 250]);
    sizes = spec.nodes.map(() => [400, 160]);
  } else {
    const gap = 230;
    const first = -((spec.nodes.length - 1) * gap) / 2;
    positions = spec.nodes.map((_node, index) => [0, first + index * gap]);
    sizes = spec.nodes.map(() => [760, 150]);
  }
  const nodes = spec.nodes.map((node, index) => ({
    id: node.id,
    position: positions[index],
    width: sizes[index][0],
    height: sizes[index][1],
  }));
  const nodeBounds = nodes.map(node => ({
    left: node.position[0] - node.width / 2,
    right: node.position[0] + node.width / 2,
    top: node.position[1] - node.height / 2,
    bottom: node.position[1] + node.height / 2,
  }));
  const byId = new Map(nodes.map(node => [node.id, node]));
  const acceptedLabels: Bounds[] = [];
  const connectorLabels: LayoutReport['connector_labels'] = [];
  const connectors = spec.connectors.map(edge => {
    const points = boundaryPoint(byId.get(edge.from)!, byId.get(edge.to)!);
    const layout: ConnectorLayout = {points};
    if (!edge.label) return layout;

    const text = edge.label.toUpperCase();
    const fontSize = 28;
    const measured = measureLabel(text, fontSize);
    const width = measured.width + 28;
    const height = Math.max(42, measured.height + 16);
    const dx = points[1][0] - points[0][0];
    const dy = points[1][1] - points[0][1];
    const distance = Math.max(1, Math.hypot(dx, dy));
    const ux = dx / distance;
    const uy = dy / distance;
    const normal: Point = [-uy, ux];
    const middle: Point = [(points[0][0] + points[1][0]) / 2, (points[0][1] + points[1][1]) / 2];
    const normalExtent = Math.abs(normal[0]) * width / 2 + Math.abs(normal[1]) * height / 2;
    const safe: Bounds = {left: -468, right: 468, top: -760, bottom: 500};
    const arrow: Bounds = {
      left: points[1][0] - 28, right: points[1][0] + 28,
      top: points[1][1] - 28, bottom: points[1][1] + 28,
    };
    const candidates: Array<{position: Point; placement: 'beside' | 'interrupted'}> = [
      {position: [middle[0] + normal[0] * (normalExtent + 42), middle[1] + normal[1] * (normalExtent + 42)], placement: 'beside'},
      {position: [middle[0] - normal[0] * (normalExtent + 42), middle[1] - normal[1] * (normalExtent + 42)], placement: 'beside'},
    ];
    const alongExtent = Math.abs(ux) * width / 2 + Math.abs(uy) * height / 2;
    if (distance / 2 - alongExtent >= 32) {
      candidates.push({position: middle, placement: 'interrupted'});
    }
    let reason = 'no collision-free label region';
    for (const candidate of candidates) {
      const bounds: Bounds = {
        left: candidate.position[0] - width / 2,
        right: candidate.position[0] + width / 2,
        top: candidate.position[1] - height / 2,
        bottom: candidate.position[1] + height / 2,
      };
      const outside = bounds.left < safe.left || bounds.right > safe.right ||
        bounds.top < safe.top || bounds.bottom > safe.bottom;
      const nodeCollision = nodeBounds.some(box => intersects(bounds, box, 10));
      const labelCollision = acceptedLabels.some(box => intersects(bounds, box, 8));
      const arrowCollision = intersects(bounds, arrow, 8);
      if (outside || nodeCollision || labelCollision || arrowCollision) {
        reason = outside ? 'outside portrait safe region' : nodeCollision ? 'node collision' :
          labelCollision ? 'connector-label collision' : 'arrowhead collision';
        continue;
      }
      acceptedLabels.push(bounds);
      layout.label = {
        position: candidate.position, width, height, fontSize,
        measuredTextWidth: measured.width, placement: candidate.placement,
      };
      connectorLabels.push({
        from: edge.from, to: edge.to, text, visible: true, bounds,
        font_size: fontSize, measured_text_width: measured.width,
        measurement_source: measured.source, placement: candidate.placement,
        line_collision: false, arrowhead_collision: false,
        node_collision: false, label_collision: false,
      });
      return layout;
    }
    connectorLabels.push({
      from: edge.from, to: edge.to, text, visible: false,
      font_size: fontSize, measured_text_width: measured.width,
      measurement_source: measured.source, line_collision: false,
      arrowhead_collision: false, node_collision: false, label_collision: false,
      omitted_reason: reason,
    });
    return layout;
  });
  return {nodes, connectors, report: {
    schema_version: 1,
    canvas: {width: 1080, height: 1920},
    safe_region: {left: -468, right: 468, top: -760, bottom: 500},
    heading: {left: -440, right: 440, top: -700, bottom: -580},
    layout,
    nodes: nodes.map(node => ({
      id: node.id,
      left: node.position[0] - node.width / 2,
      right: node.position[0] + node.width / 2,
      top: node.position[1] - node.height / 2,
      bottom: node.position[1] + node.height / 2,
      width: node.width,
      height: node.height,
    })),
    connectors: spec.connectors.map(edge => ({
      from: edge.from, to: edge.to, kind: edge.kind ?? 'normal',
    })),
    connector_labels: connectorLabels,
    caption_region_top: 500,
    has_end_card: Boolean(spec.end_card),
    end_card_brand: spec.end_card ? {
      name: 'SYSTEMDECODED', tagline: 'Complex Technology. Decoded.',
    } : undefined,
  }};
}

function boundaryPoint(from: NodeLayout, to: NodeLayout): [Point, Point] {
  const dx = to.position[0] - from.position[0];
  const dy = to.position[1] - from.position[1];
  const distance = Math.max(1, Math.hypot(dx, dy));
  const ux = dx / distance;
  const uy = dy / distance;
  const inset = (node: NodeLayout) => Math.min(
    Math.abs(ux) < 0.001 ? Number.POSITIVE_INFINITY : node.width / 2 / Math.abs(ux),
    Math.abs(uy) < 0.001 ? Number.POSITIVE_INFINITY : node.height / 2 / Math.abs(uy),
  ) + 12;
  const fromInset = inset(from);
  const toInset = inset(to);
  return [
    [from.position[0] + ux * fromInset, from.position[1] + uy * fromInset],
    [to.position[0] - ux * toInset, to.position[1] - uy * toInset],
  ];
}

export function GenericSceneDiagram({spec, refs}: {spec: GenericSceneSpec; refs?: GenericDiagramRefs}) {
  const plan = computeLayout(spec);
  return <Layout width={1080} height={1920}>
    <Txt ref={refs?.heading} position={[0, -640]} opacity={refs?.heading ? 0 : 1}
      text={spec.labels.join(' · ')} fill={SD.cyan} fontFamily={SD.font} fontSize={46}
      fontWeight={900} textAlign={'center'} width={880} lineHeight={54}/>
    {spec.connectors.map((edge, index) => {
      const connector = plan.connectors[index];
      const target = spec.nodes.find(node => node.id === edge.to)!;
      const kind = edge.kind ?? 'normal';
      const stroke = kind === 'injection' || kind === 'replacement' || kind === 'rejection'
        ? SD.danger : target.emphasis === 'success' ? SD.success : SD.cyanDim;
      const lineDash = kind === 'injection' ? [18, 12] : kind === 'replacement' ? [10, 10] : undefined;
      return <Layout key={`${edge.from}-${edge.to}-${index}`}>
        <Line ref={refs?.connectors?.[index]} points={connector.points} stroke={stroke}
          lineDash={lineDash} lineWidth={8} end={refs?.connectors ? 0 : 1}
          endArrow arrowSize={20}/>
        <Circle ref={refs?.connectorSignals?.[index]} position={connector.points[0]}
          width={kind === 'replacement' ? 24 : 18}
          height={kind === 'replacement' ? 24 : 18}
          fill={stroke} shadowColor={stroke} shadowBlur={18}
          opacity={0}/>
        {connector.label && <Rect ref={refs?.connectorLabels?.[index]}
          position={connector.label.position} width={connector.label.width}
          height={connector.label.height} radius={10} fill={SD.bg} stroke={SD.line}
          lineWidth={2} opacity={refs?.connectorLabels ? 0 : 1}
          layout alignItems={'center'} justifyContent={'center'}>
          <Txt text={edge.label!.toUpperCase()} fill={SD.white} fontFamily={SD.font}
            fontSize={connector.label.fontSize} fontWeight={800}/>
        </Rect>}
      </Layout>;
    })}
    {spec.nodes.map((node, index) => {
      const box = plan.nodes[index];
      return <Rect key={node.id} ref={refs?.nodes?.[index]}
        position={box.position} opacity={refs?.nodes ? 0 : 1} scale={refs?.nodes ? 0.9 : 1}
        width={box.width} height={box.height} radius={30} fill={SD.panelRaised}
        stroke={colorFor(node.emphasis)} lineWidth={4} layout alignItems={'center'} justifyContent={'center'}>
        <Txt text={node.label} fill={colorFor(node.emphasis)} fontFamily={SD.font}
          fontSize={box.width < 500 ? 38 : 46} fontWeight={900} textAlign={'center'}
          width={box.width - 54} lineHeight={48}/>
      </Rect>;
    })}
    {spec.end_card && <Rect ref={refs?.endCard} opacity={0} width={1080} height={1920}
      fill={SD.bg} layout direction={'column'} alignItems={'center'} justifyContent={'center'} gap={28}>
      <Txt text={'SYSTEMDECODED'} fill={SD.cyan} fontFamily={SD.font}
        fontSize={68} fontWeight={950}/>
      <Rect width={180} height={8} radius={4} fill={SD.cyan}/>
      <Txt text={'Complex Technology. Decoded.'} fill={SD.white}
        fontFamily={SD.font} fontSize={32} fontWeight={750}/>
    </Rect>}
  </Layout>;
}
