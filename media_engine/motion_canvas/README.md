# SystemDecoded Motion Canvas renderer

This project is the local Motion Canvas visual renderer for SystemDecoded
production. Video #2 consumes the immutable production timing payload in
`src/data/video2-production.json`; it contains no fallback or demo copy.

```powershell
npm install
npm run validate:isolation
npm run build
npm start
```

Open `http://localhost:9000`, select the only project, and render the five-scene
Video #2 sequence at 1080×1920 and 30 FPS. Visual events consume canonical
caption-word timing derived from the measured narration. Native frames are
written to `output-video2-production/`, which is intentionally ignored.

Persisted source identity:

- project: `01a11afb-aa75-7306-b2a2-e5e7af87b988`
- script v3: `01a12eae-17b5-7d77-b0b9-1a6002a9af06`
