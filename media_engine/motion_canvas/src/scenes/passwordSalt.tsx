import {Layout, Rect, Txt, makeScene2D} from '@motion-canvas/2d';
import {all, createRef, easeOutCubic, waitFor} from '@motion-canvas/core';
import scene from '../data/video2-scene2.json';
import {CodeBlock, DatabaseNode, FlowConnector, TitleReveal} from '../primitives';
import {SD} from '../systemDecoded';

export default makeScene2D(function* (view) {
  view.fill(SD.bg);
  const title = createRef<Layout>();
  const shared = createRef<Rect>();
  const salts = createRef<Layout>();
  const outputs = createRef<Layout>();
  const database = createRef<Rect>();
  const accountA = scene.render_payload.accounts[0];
  const accountB = scene.render_payload.accounts[1];

  view.add(
    <Layout layout width={1080} height={1920} padding={[110, SD.margin, SD.captionSafeBottom, SD.margin]}
      direction={'column'} alignItems={'center'} gap={38}>
      <Layout ref={title} opacity={0} y={-28}>
        <TitleReveal eyebrow={scene.persisted_template_props.eyebrow}
          title={'SALTED PASSWORD HASH'}/>
      </Layout>

      <Rect ref={shared} opacity={0} scale={0.92} width={860} height={142} radius={24}
        fill={SD.panelRaised} stroke={SD.line} lineWidth={2} layout alignItems={'center'}
        justifyContent={'center'} gap={24}>
        <Txt text={'SAME PASSWORD'} fill={SD.muted} fontFamily={SD.font} fontSize={23}
          fontWeight={750} letterSpacing={3}/>
        <Txt text={scene.render_payload.example_password} fill={SD.white}
          fontFamily={'Consolas, monospace'} fontSize={34} fontWeight={800}/>
      </Rect>

      <FlowConnector label={'+'}/>

      <Layout layout ref={salts} opacity={0} y={32} width={920} justifyContent={'space-between'}>
        {[accountA, accountB].map((account) => (
          <Layout layout key={account.label} direction={'column'} alignItems={'center'} gap={18}>
            <Txt text={account.label} fill={SD.white} fontFamily={SD.font} fontSize={27} fontWeight={800}/>
            <CodeBlock label={`unique salt ${account.label.slice(-1)}`} value={account.salt}/>
          </Layout>
        ))}
      </Layout>

      <FlowConnector label={'SLOW HASH'}/>

      <Layout layout ref={outputs} opacity={0} y={32} width={920} justifyContent={'space-between'}>
        {[accountA, accountB].map((account) => (
          <Layout key={account.label}>
            <CodeBlock label={`different hash ${account.label.slice(-1)}`}
              value={account.verifier} accent={SD.success}/>
          </Layout>
        ))}
      </Layout>

      <Layout layout ref={database} opacity={0} y={24} direction={'column'} alignItems={'center'} gap={22}>
        <DatabaseNode label={scene.render_payload.result_label}/>
      </Layout>
    </Layout>,
  );

  const events = Object.fromEntries(scene.timing.events.map(event => [event.id, event.start]));
  const reveal = 0.26;
  let cursor = 0;

  yield* waitFor(Math.max(0, events.salted_hash - cursor));
  cursor = events.salted_hash;
  yield* all(title().opacity(1, reveal), title().y(0, reveal, easeOutCubic));
  title().opacity(1);
  title().y(0);
  cursor += reveal;

  yield* waitFor(Math.max(0, events.same_password - cursor));
  cursor = Math.max(cursor, events.same_password);
  yield* all(shared().opacity(1, reveal), shared().scale(1, reveal, easeOutCubic));
  shared().opacity(1);
  shared().scale(1);
  cursor += reveal;

  yield* waitFor(Math.max(0, events.unique_salt - cursor));
  cursor = events.unique_salt;
  yield* all(salts().opacity(1, reveal), salts().y(0, reveal, easeOutCubic));
  salts().opacity(1);
  salts().y(0);
  cursor += reveal;

  yield* waitFor(Math.max(0, events.different_outputs - cursor));
  cursor = events.different_outputs;
  yield* all(outputs().opacity(1, reveal), outputs().y(0, reveal, easeOutCubic));
  outputs().opacity(1);
  outputs().y(0);
  cursor += reveal;

  yield* waitFor(Math.max(0, events.each_account - cursor));
  cursor = events.each_account;
  yield* all(database().opacity(1, reveal), database().y(0, reveal, easeOutCubic));
  database().opacity(1);
  database().y(0);
  cursor += reveal;

  yield* waitFor(Math.max(0, scene.timing.audio_duration_seconds - cursor));
});
