import {Layout, Line, Rect, Txt} from '@motion-canvas/2d';
import {SD} from './systemDecoded';

export interface TitleRevealProps {
  eyebrow: string;
  title: string;
}

export function TitleReveal({eyebrow, title}: TitleRevealProps) {
  return (
    <Layout layout direction={'column'} gap={22} width={936} alignItems={'center'}>
      <Txt text={eyebrow.toUpperCase()} fill={SD.cyan} fontFamily={SD.font} fontSize={28}
        fontWeight={800} letterSpacing={7}/>
      <Txt text={title} fill={SD.white} fontFamily={SD.font} fontSize={64}
        fontWeight={850} textAlign={'center'} lineHeight={78} width={930}/>
    </Layout>
  );
}

export interface CodeBlockProps {
  label: string;
  value: string;
  accent?: string;
}

export function CodeBlock({label, value, accent = SD.cyan}: CodeBlockProps) {
  return (
    <Rect width={390} height={136} radius={22} fill={SD.panelRaised} stroke={SD.line} lineWidth={2}
      layout direction={'column'} padding={[20, 24]} gap={10}>
      <Txt text={label.toUpperCase()} fill={SD.muted} fontFamily={SD.font} fontSize={20}
        fontWeight={700} letterSpacing={3}/>
      <Txt text={value} fill={accent} fontFamily={'Consolas, monospace'} fontSize={31} fontWeight={750}/>
    </Rect>
  );
}

export function DatabaseNode({label}: {label: string}) {
  return (
    <Rect width={860} height={176} radius={28} fill={SD.panel} stroke={SD.cyanDim} lineWidth={3}
      layout alignItems={'center'} justifyContent={'center'} gap={32}>
      <Rect width={72} height={86} radius={14} stroke={SD.cyan} lineWidth={5}/>
      <Txt text={label} fill={SD.white} fontFamily={SD.font} fontSize={42} fontWeight={800}/>
    </Rect>
  );
}

export function FlowConnector({label}: {label: string}) {
  return (
    <Layout layout direction={'column'} alignItems={'center'} gap={8}>
      <Line points={[[0, -26], [0, 26]]} stroke={SD.cyanDim} lineWidth={5} endArrow arrowSize={14}/>
      <Txt text={label} fill={SD.muted} fontFamily={SD.font} fontSize={22} fontWeight={650}/>
    </Layout>
  );
}

export function Callout({text}: {text: string}) {
  return (
    <Rect radius={999} fill={'#0A2A31'} stroke={SD.cyanDim} lineWidth={2} padding={[18, 34]}>
      <Txt text={text} fill={SD.cyan} fontFamily={SD.font} fontSize={28} fontWeight={800}/>
    </Rect>
  );
}
