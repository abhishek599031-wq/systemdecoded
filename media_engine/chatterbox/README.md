# Chatterbox local narration

SystemDecoded uses Original Chatterbox for Video #2 local narration with CUDA,
no reference audio, and the persisted human-reviewed production controls. The
production generator is `generate_video2.py` and reuses one loaded model across
the five narration blocks.

The reference-free comparison and pace-tuning scripts remain local audition
tools. Their generated WAVs and benchmark metadata live under `media/pocs/`
and are intentionally ignored.
