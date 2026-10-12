import {Layout, Line, Rect, Txt, makeScene2D} from '@motion-canvas/2d';
import {all, createRef, easeInOutCubic, easeOutCubic, waitFor} from '@motion-canvas/core';
import production from '../data/video2-production.json';
import {SD} from '../systemDecoded';

const reveal = 0.22;

function Label({text, accent = false, danger = false, fontSize = 40, width}: {
  text: string; accent?: boolean; danger?: boolean; fontSize?: number; width?: number;
}) {
  const color = danger ? SD.danger : accent ? SD.cyan : SD.white;
  return <Rect width={width} radius={28} padding={[28, 38]} fill={SD.panelRaised}
    stroke={danger ? SD.danger : accent ? SD.cyan : SD.line} lineWidth={4}
    shadowColor={'#00081488'} shadowBlur={22}>
    <Txt text={text} fill={color} fontFamily={SD.font} fontSize={fontSize}
      fontWeight={850} textAlign={'center'}/>
  </Rect>;
}

function SceneTitle({text, danger = false}: {text: string; danger?: boolean}) {
  return <Txt text={text} y={-600} fill={danger ? SD.danger : SD.white} fontFamily={SD.font}
    fontSize={64} fontWeight={900} width={960} textAlign={'center'} letterSpacing={1}/>;
}

function FlowArrow({from, to, color = SD.cyanDim}: {
  from: [number, number]; to: [number, number]; color?: string;
}) {
  return <Line points={[from, to]} stroke={color} lineWidth={7} endArrow arrowSize={20}
    lineCap={'round'} opacity={0.82}/>;
}

export default makeScene2D(function* (view) {
  view.fill(SD.bg);
  const groups = Array.from({length: 5}, () => createRef<Layout>());
  const password1 = createRef<Layout>(), rejected1 = createRef<Layout>();
  const verifier1 = createRef<Layout>(), databasePulse1 = createRef<Rect>();
  const password2 = createRef<Layout>(), salt2 = createRef<Layout>();
  const hash2 = createRef<Layout>(), verifier2 = createRef<Layout>();
  const accounts3 = createRef<Layout>(), salts3 = createRef<Layout>(), results3 = createRef<Layout>();
  const login3 = createRef<Layout>(), compare3 = createRef<Layout>(), match3 = createRef<Layout>();
  const breach4 = createRef<Layout>(), reverse4 = createRef<Layout>(), guesses4 = createRef<Layout>();
  const argon4 = createRef<Layout>(), cost4 = createRef<Layout>(), costFill4 = createRef<Rect>();
  const concept5 = createRef<Layout>(), password5 = createRef<Layout>(), removed5 = createRef<Layout>();
  const database5 = createRef<Layout>(), verifier5 = createRef<Layout>(), brand5 = createRef<Layout>();

  const shell = (ref: ReturnType<typeof createRef<Layout>>, children: any) =>
    <Layout ref={ref} opacity={0} width={1080} height={1420} y={-170}>{children}</Layout>;

  view.add(<>
    {shell(groups[0], <>
      <SceneTitle text={'PASSWORD ≠ STORED TEXT'}/>
      <Rect width={940} height={910} y={120} radius={42} fill={'#07101E'} stroke={SD.line} lineWidth={3}>
        <Txt text={'LOGIN'} y={-365} fill={SD.muted} fontFamily={SD.font} fontSize={28} fontWeight={800} letterSpacing={5}/>
        <Layout ref={password1} opacity={0} x={-410} y={-190}><Label text={'PASSWORD INPUT'} accent width={420}/></Layout>
        <FlowArrow from={[-215, -70]} to={[185, 75]}/>
        <Rect ref={databasePulse1} x={270} y={155} width={390} height={350} radius={38}
          fill={SD.panel} stroke={SD.line} lineWidth={5}>
          <Txt text={'DATABASE'} y={-115} fill={SD.muted} fontFamily={SD.font} fontSize={29} fontWeight={850} letterSpacing={4}/>
        </Rect>
        <Layout ref={rejected1} opacity={0} x={40} y={55} layout direction={'column'} alignItems={'center'} gap={14}>
          <Txt text={'READABLE TEXT'} fill={SD.white} fontFamily={SD.font} fontSize={36} fontWeight={850}/>
          <Rect height={7} width={340} fill={SD.danger} rotation={-7}/>
          <Txt text={'REJECTED'} fill={SD.danger} fontFamily={SD.font} fontSize={28} fontWeight={900}/>
        </Layout>
        <Layout ref={verifier1} opacity={0} x={270} y={185} scale={0.8}><Label text={'VERIFIER'} accent width={310} fontSize={43}/></Layout>
      </Rect>
    </>)}

    {shell(groups[1], <>
      <SceneTitle text={'BUILD THE VERIFIER'}/>
      <Rect width={940} height={930} y={120} radius={42} fill={'#07101E'} stroke={SD.line} lineWidth={3}>
        <FlowArrow from={[-250, -175]} to={[-80, 0]}/><FlowArrow from={[250, -175]} to={[80, 0]}/>
        <FlowArrow from={[0, 130]} to={[0, 330]}/>
        <Layout ref={password2} opacity={0} x={-255} y={-270}><Label text={'PASSWORD'} width={390}/></Layout>
        <Txt text={'+'} y={-270} fill={SD.cyan} fontFamily={SD.font} fontSize={70} fontWeight={900}/>
        <Layout ref={salt2} opacity={0} x={255} y={-270}><Label text={'UNIQUE SALT'} accent width={390}/></Layout>
        <Layout ref={hash2} opacity={0} y={50} scale={0.82}><Label text={'SLOW HASH'} width={440} fontSize={46}/></Layout>
        <Layout ref={verifier2} opacity={0} y={350} scale={0.8}><Label text={'VERIFIER'} accent width={470} fontSize={48}/></Layout>
      </Rect>
    </>)}

    {shell(groups[2], <>
      <Txt text={'SAME PASSWORD'} y={-600} fill={SD.white} fontFamily={SD.font} fontSize={58} fontWeight={900}/>
      <Txt text={'DIFFERENT STORED RESULTS'} y={-530} fill={SD.cyan} fontFamily={SD.font} fontSize={42} fontWeight={850}/>
      <Rect width={950} height={980} y={110} radius={42} fill={'#07101E'} stroke={SD.line} lineWidth={3}>
        <Layout ref={accounts3} opacity={0} y={-330} layout gap={70}><Label text={'ACCOUNT A'} width={405}/><Label text={'ACCOUNT B'} width={405}/></Layout>
        <Line points={[[-225, -235], [-225, 170]]} stroke={SD.cyanDim} lineWidth={6} endArrow/>
        <Line points={[[225, -235], [225, 170]]} stroke={SD.cyanDim} lineWidth={6} endArrow/>
        <Layout ref={salts3} opacity={0} y={-90} layout gap={70}><Label text={'SALT A'} accent width={405}/><Label text={'SALT B'} accent width={405}/></Layout>
        <Layout ref={results3} opacity={0} y={205} layout gap={70}><Label text={'91F…'} width={405} fontSize={48}/><Label text={'7C2…'} width={405} fontSize={48}/></Layout>
        <Layout ref={login3} opacity={0} y={405} x={-235}><Label text={'LOGIN INPUT'} width={405}/></Layout>
        <FlowArrow from={[-25, 405]} to={[90, 405]}/>
        <Layout ref={compare3} opacity={0} y={405} x={245} scale={0.85}><Label text={'COMPARE'} width={330}/></Layout>
        <Layout ref={match3} opacity={0} y={405} x={245} scale={0.72}><Label text={'MATCH'} accent width={330} fontSize={45}/></Layout>
      </Rect>
    </>)}

    {shell(groups[3], <>
      <SceneTitle text={'STOLEN DATABASE'} danger/>
      <Rect width={940} height={960} y={125} radius={42} fill={'#07101E'} stroke={SD.line} lineWidth={3}>
        <Layout ref={breach4} opacity={0} y={-315} scale={0.85}><Label text={'VERIFIERS EXPOSED'} danger width={610} fontSize={45}/></Layout>
        <Layout ref={reverse4} opacity={0} y={-135}><Label text={'NOT REVERSIBLE'} accent width={520}/></Layout>
        <Layout ref={guesses4} opacity={0} x={-340} y={55} layout gap={14}>
          <Label text={'GUESS 01'} width={245} fontSize={28}/><Label text={'GUESS 02'} width={245} fontSize={28}/><Label text={'GUESS 03'} width={245} fontSize={28}/>
        </Layout>
        <Txt text={'STILL GUESSABLE OFFLINE'} y={205} fill={SD.danger} fontFamily={SD.font} fontSize={38} fontWeight={900}/>
        <Layout ref={argon4} opacity={0} y={325} scale={0.82}><Label text={'ARGON2ID · EXAMPLE'} accent width={600}/></Layout>
        <Layout ref={cost4} opacity={0} y={485} layout direction={'column'} gap={18} alignItems={'center'}>
          <Txt text={'EACH GUESS COSTS'} fill={SD.white} fontFamily={SD.font} fontSize={36} fontWeight={900}/>
          <Rect width={790} height={42} radius={21} fill={SD.line} clip>
            <Rect ref={costFill4} width={70} height={42} radius={21} fill={SD.danger} x={-360}/>
          </Rect>
          <Txt text={'TIME + MEMORY'} fill={SD.muted} fontFamily={SD.font} fontSize={26} fontWeight={800}/>
        </Layout>
      </Rect>
    </>)}

    {shell(groups[4], <Layout ref={concept5} width={1080} height={1420}>
      <SceneTitle text={'WHAT THE DATABASE KEEPS'}/>
      <Rect width={940} height={900} y={130} radius={42} fill={'#07101E'} stroke={SD.line} lineWidth={3}>
        <Layout ref={password5} opacity={0} x={-400} y={-250}><Label text={'PLAINTEXT PASSWORD'} width={600} fontSize={42}/></Layout>
        <FlowArrow from={[-180, -115]} to={[120, 40]}/>
        <Layout ref={removed5} opacity={0} x={-80} y={-115} layout direction={'column'} alignItems={'center'} gap={10}>
          <Rect width={470} height={8} fill={SD.danger} rotation={-7}/>
          <Txt text={'REMOVED'} fill={SD.danger} fontFamily={SD.font} fontSize={30} fontWeight={900}/>
        </Layout>
        <Layout ref={database5} opacity={0} y={130}>
          <Rect width={720} height={360} radius={42} fill={SD.panel} stroke={SD.line} lineWidth={5}>
            <Txt text={'DATABASE'} y={-115} fill={SD.muted} fontFamily={SD.font} fontSize={30} fontWeight={850} letterSpacing={5}/>
          </Rect>
        </Layout>
        <Layout ref={verifier5} opacity={0} y={170} scale={0.82}><Label text={'VERIFIER, NOT PLAINTEXT'} accent width={650} fontSize={42}/></Layout>
      </Rect>
    </Layout>)}

    <Layout ref={brand5} opacity={0} scale={0.92} width={1080} height={1920} layout direction={'column'} alignItems={'center'} justifyContent={'center'} gap={30}>
      <Rect width={110} height={7} radius={4} fill={SD.cyan}/>
      <Txt text={'SYSTEMDECODED'} fill={SD.cyan} fontFamily={SD.font} fontSize={68} fontWeight={900} letterSpacing={10}/>
      <Txt text={'Complex Technology. Decoded.'} fill={SD.white} fontFamily={SD.font} fontSize={32} fontWeight={650} letterSpacing={2}/>
      <Txt text={'Follow for more decoded systems'} fill={SD.muted} fontFamily={SD.font} fontSize={24} fontWeight={600} marginTop={22}/>
    </Layout>
  </>);

  let cursor = 0;
  const seek = function* (time: number) {
    yield* waitFor(Math.max(0, time - cursor));
    cursor = Math.max(cursor, time);
  };
  const show = function* (index: number) {
    yield* seek(production.scenes[index].start);
    groups[index]().opacity(1);
  };
  const hide = function* (index: number) {
    const end = production.scenes[index].start + production.scenes[index].duration;
    yield* seek(end - 0.12);
    yield* groups[index]().opacity(0, 0.12);
    cursor += 0.12;
  };
  const cue = (index: number, name: string) => {
    const events = production.scenes[index].events as Record<string, number>;
    return production.scenes[index].start + events[name];
  };

  yield* show(0);
  yield* seek(cue(0, 'password'));
  yield* all(password1().opacity(1, reveal), password1().x(-230, 0.42, easeOutCubic)); cursor += 0.42;
  yield* seek(cue(0, 'readable'));
  yield* all(password1().x(90, 0.38, easeInOutCubic), password1().scale(0.82, 0.38), rejected1().opacity(1, 0.25)); cursor += 0.38;
  yield* all(password1().opacity(0, 0.2), rejected1().x(-40, 0.2)); cursor += 0.2;
  yield* seek(cue(0, 'verifier'));
  yield* all(verifier1().opacity(1, reveal), verifier1().scale(1, 0.36, easeOutCubic), databasePulse1().stroke(SD.cyan, 0.3)); cursor += 0.36;
  yield* hide(0);

  yield* show(1);
  yield* seek(cue(1, 'password'));
  yield* all(password2().opacity(1, reveal), password2().x(-225, 0.3)); cursor += 0.3;
  yield* seek(cue(1, 'salt'));
  yield* all(salt2().opacity(1, reveal), salt2().x(225, 0.3)); cursor += 0.3;
  yield* seek(cue(1, 'hash'));
  yield* all(password2().x(-70, 0.42, easeInOutCubic), salt2().x(70, 0.42, easeInOutCubic),
    password2().opacity(0.18, 0.42), salt2().opacity(0.18, 0.42), hash2().opacity(1, 0.28), hash2().scale(1, 0.42, easeOutCubic)); cursor += 0.42;
  yield* seek(cue(1, 'verifier'));
  yield* all(verifier2().opacity(1, reveal), verifier2().scale(1, 0.36, easeOutCubic), verifier2().y(330, 0.36)); cursor += 0.36;
  yield* hide(1);

  yield* show(2);
  yield* seek(cue(2, 'password'));
  yield* all(accounts3().opacity(1, reveal), accounts3().scale(1.03, 0.3)); cursor += 0.3;
  yield* seek(cue(2, 'salts'));
  yield* all(salts3().opacity(1, reveal), salts3().y(-60, 0.36, easeOutCubic)); cursor += 0.36;
  yield* seek(cue(2, 'results'));
  yield* all(results3().opacity(1, reveal), results3().y(225, 0.4, easeOutCubic)); cursor += 0.4;
  yield* seek(cue(2, 'login'));
  yield* all(login3().opacity(1, reveal), login3().x(-205, 0.34)); cursor += 0.34;
  yield* seek(cue(2, 'compare'));
  yield* all(login3().x(80, 0.42, easeInOutCubic), login3().opacity(0.15, 0.42), compare3().opacity(1, 0.28), compare3().scale(1, 0.4)); cursor += 0.42;
  yield* seek(cue(2, 'match'));
  yield* all(compare3().opacity(0, 0.2), match3().opacity(1, 0.25), match3().scale(1, 0.38, easeOutCubic)); cursor += 0.38;
  yield* hide(2);

  yield* show(3);
  yield* seek(cue(3, 'breach'));
  yield* all(breach4().opacity(1, reveal), breach4().scale(1, 0.38, easeOutCubic)); cursor += 0.38;
  yield* seek(cue(3, 'not_reversible'));
  yield* all(reverse4().opacity(1, reveal), reverse4().y(-115, 0.32)); cursor += 0.32;
  yield* seek(cue(3, 'guessing'));
  yield* all(guesses4().opacity(1, 0.18), guesses4().x(0, 0.62, easeInOutCubic), breach4().scale(1.04, 0.3)); cursor += 0.62;
  yield* seek(cue(3, 'argon2id'));
  yield* all(argon4().opacity(1, reveal), argon4().scale(1, 0.38, easeOutCubic)); cursor += 0.38;
  yield* seek(cue(3, 'cost'));
  yield* cost4().opacity(1, 0.18); cursor += 0.18;
  yield* all(costFill4().width(790, 0.7, easeInOutCubic), costFill4().x(0, 0.7, easeInOutCubic)); cursor += 0.7;
  yield* hide(3);

  yield* show(4);
  yield* seek(cue(4, 'password'));
  yield* all(password5().opacity(1, reveal), password5().x(-120, 0.46, easeOutCubic)); cursor += 0.46;
  yield* all(removed5().opacity(1, 0.2), password5().opacity(0.2, 0.32), password5().x(70, 0.32)); cursor += 0.32;
  yield* seek(cue(4, 'database'));
  yield* all(database5().opacity(1, reveal), database5().scale(1.03, 0.34)); cursor += 0.34;
  yield* seek(cue(4, 'verifier'));
  yield* all(verifier5().opacity(1, reveal), verifier5().scale(1, 0.4, easeOutCubic)); cursor += 0.4;

  const narrationEnd = production.scenes[4].start + production.scenes[4].speech_duration;
  yield* seek(narrationEnd + production.ending.concept_hold);
  yield* all(concept5().opacity(0, production.ending.diagram_transition), concept5().scale(0.94, production.ending.diagram_transition, easeInOutCubic));
  cursor += production.ending.diagram_transition;
  yield* all(brand5().opacity(1, production.ending.card_transition), brand5().scale(1, production.ending.card_transition, easeOutCubic));
  cursor += production.ending.card_transition;
  yield* seek(production.total_duration_seconds);
});
