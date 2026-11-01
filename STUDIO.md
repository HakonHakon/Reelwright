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
9. **Takes** - generate candidates and edit a saved timeline: preview, reorder, trim, split,
   duplicate, delete, mute and add music. Choose lip-sync clips and render the edited MP4.
   The collapsible take library retains generation, correction and review controls.
10. **Cut** - render the saved Takes timeline and download MP4/EDL. Projects without a saved
    timeline retain the legacy music-bed, subtitle and final-cut workflow.

## Editing in Takes

The timeline starts with picked takes, or segments from an existing selected lip-sync final
when its cut metadata is available. Viewing does not change the project; edits autosave with
revision checks so another window cannot silently overwrite them. Save before closing the
page. A conflicting edit stays open until you reload or resolve it.

Drag a clip's top grip to reorder. Drag its edges or set **Source in / out** in Settings to
trim at the project frame rate. Move the playhead inside a selected clip and choose **Split**.
Delete closes the gap without removing its source file. Undo/redo lasts for the current editor
session; reload starts a new history. **Use picked takes** replaces the edit with current
library choices; **Use shot lengths** shortens clips to their shot durations.

**Add music / voice** uploads a track and offers lip-sync setup. Choose **Not now** to mix
the audio only, or choose clips for one or both characters. Vocal isolation guides lip-sync;
the export uses the full music mix. Each clip uses its actual edited position in that mix.
Sync is currently for single-character shots. Each clip's original audio has independent mute
and gain controls. The music inspector sets source in, timeline start, length, volume, mute and
looping; dragging its waveform changes the start. Music beyond the picture is cut off.

**Generate take for this shot** uses the selected clip's shot and chosen video model. New
results appear in the library, where **Add to timeline** appends them and **Replace selected
clip** retains the selected clip's length where possible. Approved sources remain available.

**Render timeline** runs through Studio's job queue. Cancel from Activity. A successful export
is frame-count checked before becoming the current final; a failed/cancelled export leaves the
previous final selected. Each version retains its edit snapshot, MP4 and EDL. Watch/download
links appear in Takes, with the current result also in Cut. Completed lip-sync clips are cached
on disk; re-submit after an interruption to reuse them. The queue itself is in memory, so a
server restart requires re-submitting an unfinished job.

On phones, **Settings** toggles the inspector and **Expand editor** opens a larger workspace.
The timeline scrolls horizontally, while grips and trim edges support touch dragging. This
first editor supports one ripple video track and one music track; it does not add overlays,
transitions or separate overlapping audio lanes.

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

### Lip-sync after uploading music

For saved timelines, use **Takes → Add music / voice → Choose clips → Render timeline**.
The instructions below describe the legacy Cut workflow before a timeline is saved.

In **Cut**, upload a song or voice recording and choose **Save music settings**.
Studio asks whether to set up lip-sync. **Not now** saves the audio without running
any model. **Choose shots** opens the character and shot picker. You can also use
**Lip-sync to uploaded audio** later. Select single-character shots, set the start
time in the recording, and choose **Run lip-sync**. Demucs vocal isolation is on by
default for music; turn it off for an already clean voice recording.

LatentSync 1.6 runs each chosen shot against the corresponding audio segment.
Other shots retain their picture. The export uses the full uploaded music mix at
full volume, replacing take audio; bed volume and ducking apply only to the ordinary
final-cut render. Short recordings are padded with silence, long recordings are
trimmed. Review the separate lip-sync preview and choose **Use as final** to select
it. Original takes and the previous final remain available. A missing face or model
failure leaves the original final selected. Singing, profiles and microphones
covering the mouth can reduce accuracy; inspect the preview.

Install locally with Python 3.10 and an NVIDIA GPU (LatentSync 1.6 needs about 18 GB
VRAM; this installation uses an RTX 3090):

```sh
python scripts/install_lipsync.py --python /path/to/python3.10 --comfy /path/to/ComfyUI
```

The installer creates an isolated `.runtime/lipsync-env`, downloads the
[ByteDance LatentSync 1.6](https://github.com/bytedance/LatentSync) checkpoint,
Whisper tiny, SD VAE and face detector, and caches the
[Demucs htdemucs](https://github.com/facebookresearch/demucs) vocal separator.
Restart ComfyUI and Studio after installation. No paid API or media upload to a
remote service is involved. Model dependencies do not modify ComfyUI's Python environment.

The ComfyUI nodes are **Lip sync · LatentSync 1.6** and **Isolate singing voice · Demucs**.
`workflows/lipsync_latentsync_api.json` is a ready API graph: replace its video/audio
filenames with uploaded ComfyUI input names. Its vocal guide drives the model, and
the optional **soundtrack** socket retains the full music in the output.
For clean speech, connect LoadAudio directly to LatentSync's audio socket.

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

Face correction defaults to **Qwen Image Edit · every frame**. In First frames,
**Correct face · Qwen** edits an existing image using its shot's uploaded character
portrait. In Takes, **Correct faces** edits every decoded frame with Qwen and
reassembles the clip with the source audio copied unchanged. Both operations add
reviewable candidates and retain the original selection. Qwen correction requires
a single-character shot with an uploaded portrait. It uses the `qwen_edit` model
settings (2509 FP8, Qwen-VL encoder, VAE and four-step Lightning adapter) and runs
locally through ComfyUI. A full take is substantially slower than a still image.

**Correct faces** on each take creates an additional take without changing the original
or your selected take. Review it and use **Pick this** if it is better.
**KEEP** is intended for temporal consistency on a single main face;
**CodeFormer** supports multiple detected faces and an identity fidelity control.
Neither is a guaranteed improvement for stylised characters or identity errors.
Frame rate and original audio are retained.

For an incorrect character identity, choose **Match character references · MiniMax H3**
on Takes. This conditions a local video edit on the character's uploaded portraits
and the source performance, rather than sharpening the existing face. It supports
single-character takes up to 15 seconds. The original soundtrack and take are retained;
review the new version for likeness, motion and foreground objects before selecting it.
This generative edit can alter expressions or scene details.

In addition to the local H3 text encoder and video VAE, install these in ComfyUI:
- `diffusion_models/minimax_h3_ref2va_pruned_int8_convrot.safetensors` from
  [Comfy-Org/MiniMax-H3](https://huggingface.co/Comfy-Org/MiniMax-H3).
- `loras/h3_character_swap_v1.5.safetensors` from
  [akatz-ai/MiniMax-H3-Character-Swap-LoRA](https://huggingface.co/akatz-ai/MiniMax-H3-Character-Swap-LoRA).
- `loras/minimax_h3_fl2v_turbo_8step_v1.0_768p_comfyui_bf16.safetensors` from
  [lightx2v/Minimax-h3-Turbo](https://huggingface.co/lightx2v/Minimax-h3-Turbo).

Their names can be configured under **Models → Local character reference files**.

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
