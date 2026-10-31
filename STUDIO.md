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

## Models, face correction and deletion

The **Models** page lists local LTX/Wan and MiniMax Hailuo 2.3, 2.3 Fast and 02.
In **Takes**, choose a video model for the current render, or leave the per-shot/project
engine in effect. The override affects only that render; the take records its actual model.
MiniMax uses the hosted API, sends your selected first frame and prompt to MiniMax,
and requires a paid account. Set `MINIMAX_API_KEY` in the Studio server environment
and restart Studio (Windows PowerShell: `$env:MINIMAX_API_KEY = "your-key"`).
Keep the key out of settings JSON. Choose MiniMax model and resolution on **Models**.
Hailuo clips are 6 or 10 seconds (1080P: 6 seconds); use take trims to fit shorter shots.
This integration uses first frames only; end frames, local LoRAs and voice samples are
not passed to MiniMax. Cancelling stops local polling; an already submitted cloud job
may continue and incur charges.

**Correct faces** on each take creates an additional take without changing the original
or your selected take. Review it and use **Pick this** if it is better.
The default **KEEP** is intended for temporal consistency on a single main face;
**CodeFormer** supports multiple detected faces and an identity fidelity control.
Neither is a guaranteed improvement for stylised characters or identity errors.
Frame rate and original audio are retained.

Install the chosen upstream restorer in its own Python environment using its instructions:
- KEEP: https://github.com/jnjaby/KEEP (clone recursively, install dependencies and weights).
- CodeFormer: https://github.com/sczhou/CodeFormer (install dependencies and weights).

On **Models**, set the installation folder and the Python executable for that environment
(e.g. `C:\AI\KEEP\.venv\Scripts\python.exe`). If using both restorers, use an environment
with both dependency sets; alternatively change the executable when switching models.
These GPU packages are optional and are not installed into Studio automatically.
KEEP's upstream runner restores the main/largest face. CodeFormer fidelity defaults to
0.8 (higher values favour identity; lower values favour restoration).

**Select all** and **Delete selected** are available for projects, episodes/bible entries,
character references, shot rows, first-frame candidates and takes. Shot deletion is staged
until **Save shot list**. Project, episode, character and location deletion moves their
folder to a hidden `.deleted_*` sibling for manual recovery. Asset removal detaches the
entry but keeps the media on disk. Selection indices are adjusted after removal and
review gates/exports are invalidated. Finish or cancel active project jobs before deletion.

## MiniMax H3 local generation

Choose **MiniMax H3 · local, audio, no API fees** in Takes, or **MiniMax H3 (local)**
as the project's default engine in Style & props. Engine id: `minimax_local`;
model id: `MiniMax-H3`. This path runs ComfyUI nodes entirely on your machine.
It does not call MiniMax's hosted API, Context-IR service or 2K service.
The Hailuo cloud choices remain separate and require explicit selection and an API key.

The default local setup uses the Comfy-Org H3 pruned INT8 FL2VA model, NVFP4 Qwen3-VL
encoder, INT8 video VAE, FP32 audio VAE, and 4-step Turbo LoRA. Model files come from
https://huggingface.co/Comfy-Org/MiniMax-H3 . The upstream I2V template is at
https://github.com/Comfy-Org/workflow_templates/blob/main/templates/video_minimax_h3_i2v.json .
Weights retain the MiniMax H3 license: https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/LICENSE .

Resolution defaults to 608×352 to fit the RTX 3090; change `minimax_local.width` and
`minimax_local.height` in Settings to multiples of 32 for higher resolutions.
Clips use 24 fps and the model's 17n+5 frame grid, with a 4-second minimum and
15-second cap. An optional end frame is passed to H3. Native audio is retained.
The first run loads large models and can take several minutes.
