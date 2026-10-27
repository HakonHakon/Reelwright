# Reelwright

Bible -> story -> shot list -> first frames -> animatic -> takes -> cut, with a review gate at each step.
Uses the repo .venv plus your ComfyUI and Ollama.

## Start
1. Start Comfy Desktop (port 8188) and Ollama.
2. Double-click `start-studio.bat` -> opens http://localhost:8020
   (first time: run `setup.bat` once).

## Workflow
1. **Series** - create one per show (e.g. "Sam"). The bible is shared by all its episodes.
2. **Style & props** - look, colour grade, default first-frame source, default video engine.
3. **Characters** - appearance (locked text), outfits, trigger word, Flux image LoRA (+ strength),
   optional Wan video LoRA, 5 s voice sample, speaking rate. Render a character sheet and
   approve the best images as references.
4. **Locations** - description, time-of-day variants, ambience; render or upload establishing plates.
5. **Episode -> Story** - paste a script or prose. "Draft bible from story" creates draft characters
   and locations you then review. "Plan scenes and shots" runs the planner (only bible ids allowed).
6. **Shot list** - edit any field, split / merge / reorder / delete / add, re-plan one scene,
   per-shot source (Flux + LoRA / Z-Image / Upload), engine (LTX / Wan), start mode
   (new / chain = last frame of previous take / reuse previous frame). Approve.
7. **First frames** - render candidates, vision check, select, refine (img2img with the LoRA or Z-Image),
   upload with crop (one shot) or bulk upload (files named `S01_SH02.png`), optional end frame, lock. Approve.
8. **Animatic** - plays selected frames at shot length with dialogue captions; export mp4. Approve.
9. **Takes** - render N takes per shot (LTX-2.3 with sound, or Wan 2.2 silent), QC flags, select,
   trim, "last frame -> next shot". Approve.
10. **Cut** - optional music bed with ducking -> final.mp4 + final.srt + final.edl (DaVinci Resolve).

## Notes
- Data lives in `studio_data/` (JSON + images/videos, editable by hand).
- Editing a character/location/style marks dependent shots **stale**; nothing re-renders automatically.
- Jobs run one at a time on the GPU; Ollama is unloaded before renders, ComfyUI models are freed
  when switching engine families.
- Model names live in Settings (JSON overrides). The status bar flags missing models.
- Experimental: LTX end-frame guide and voice reference (needs `ltx-2.3-id-lora-talkvid-3k`);
  both are only used when you set an end frame / a voice sample.
- Tests: `.venv\Scripts\python -m pytest tests\test_studio_e2e.py` (fake ComfyUI/Ollama, needs ffmpeg).
