import fs from 'node:fs';

const paths = [
  new URL('../src/data/video2-production.json', import.meta.url),
  new URL('../src/scenes/video2Production.tsx', import.meta.url),
];
const payload = JSON.parse(fs.readFileSync(paths[0], 'utf8'));
const serialized = paths.map(path => fs.readFileSync(path, 'utf8')).join('\n').toLowerCase();
const forbidden = ['totp', '418902', 'six-digit', 'six digit', '30-second', 'countdown', 'never communicate'];
for (const token of forbidden) {
  if (serialized.includes(token)) throw new Error(`Video #2 payload contains forbidden carryover: ${token}`);
}
if (payload.project_id !== '01a11afb-aa75-7306-b2a2-e5e7af87b988') {
  throw new Error('Unexpected project id');
}
if (payload.script_id !== '01a12eae-17b5-7d77-b0b9-1a6002a9af06') {
  throw new Error('Unexpected production script id');
}
console.log(`isolation-ok project=${payload.project_id} script=${payload.script_id}`);
