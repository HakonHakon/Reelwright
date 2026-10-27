# Reelwright

A local studio for making short films with AI, one reviewed step at a time. You write a story, define your characters and locations once, and Reelwright carries them through shot list, first frames, animatic, video takes and final cut. Everything runs on your own machine with ComfyUI and Ollama: no cloud, no API keys.

![Projects](docs/img/home.png)

## Why it exists

Prompting a video model shot by shot loses your characters. Reelwright keeps a **bible** (characters, locations, style) and builds every prompt from it, so the same face, outfit and room show up in every shot. Each stage ends with a gate: you review and approve before the next, GPU-heavy stage starts.

```
Story -> Shot list -> First frames -> Animatic -> Takes -> Cut
```

![Frame candidates on a film strip, with the picked frame circled](docs/img/frames.png)

## Requirements

- Windows with an NVIDIA GPU (developed on an RTX 3090, 24 GB)
- [ComfyUI](https://www.comfy.org/) (Desktop or portable) on port 8188, with the models listed in **Settings**
- [Ollama](https://ollama.com/) for the story planner (the model is pulled automatically on first use)
- Python 3.10+ and ffmpeg

Model names are configurable in **Settings**. The sidebar shows whether ComfyUI and Ollama are reachable and flags any missing model.

## Install and start

```bat
git clone https://github.com/HakonHakon/Reelwright.git
cd Reelwright
setup.bat          :: once: creates .venv and installs dependencies
start-studio.bat   :: opens http://localhost:8020
```

`start-studio.bat` listens on all interfaces (`0.0.0.0:8020`) so other devices on your network can open it. To keep it on this machine only, change `--host 0.0.0.0` to `--host 127.0.0.1` in the file.

Your projects are saved in `studio_data/` next to the app. That folder is git-ignored, so your characters, photos and renders are never committed.

## Quick tour: make an episode

The repo includes a short script, [`examples/the_rain_boat.txt`](examples/the_rain_boat.txt), to try the flow with.

### 1. Create a project and its bible

Create a project (one per show). Then open it:

![Project overview](docs/img/series.png)

- **Style & props**: look, colour grade, default first-frame source and video engine.
- **Characters**: appearance text (locked into every prompt), outfits, optional trigger word and image LoRA, an optional video LoRA, a voice sample, and reference photos.
- **Locations**: description, time-of-day variants, and an establishing plate.

![Character page](docs/img/char.png)

Example character:

| Field | Value |
|---|---|
| Name | Sam |
| Appearance | small boy, about 5, round cheeks, messy brown hair |
| Outfit | teal raincoat with little yellow trees, red rubber boots |
| First-frame source | Reference photos (or Flux + LoRA if you trained one) |

### 2. Story

Paste a script or plain prose into **Story**. **Draft bible from story** creates draft characters and locations for you to review. **Plan scenes and shots** runs the planner, which may only use characters and locations that exist in the bible.

### 3. Shot list

Edit anything: framing, who is in the shot and where, action, dialogue, duration, first-frame source, video engine and start mode. Split, merge, reorder or add shots, then approve.

![Shot list](docs/img/shots.png)

Character cells use `id:position:outfit`, comma separated, and dialogue is one `id: line` per line:

```
characters:  sam:left, mona:right
dialogue:    sam: Boat!
```

Start modes: **new frame**, **chain** (start from the last frame of the previous take) and **reuse** (share the previous frame).

### 4. First frames

Each shot gets several candidates on a film strip. Pick one (it gets the red ring), refine it with img2img, upload your own, or set an optional end frame. Lock shots you are happy with.

Frame sources:

| Source | Use it when |
|---|---|
| **Reference photos** (Qwen Image Edit) | You have photos of your character and no LoRA. Up to 3 people plus the location plate go in as references. |
| **Flux + LoRA** | You trained an image LoRA for the character. |
| **Z-Image** | Fast text-to-image, no character identity. |
| **Auto** | Uses a LoRA if the character has one, otherwise reference photos, otherwise Z-Image. |
| **Upload** | You made the frame elsewhere. Bulk upload works with files named `S01_SH02.png`. |

A vision check flags frames where the characters don't match their description.

### 5. Animatic

Plays your picked frames at shot length with dialogue captions, so you can check pacing before spending GPU time. Export it as `animatic.mp4`.

### 6. Takes

Render several video takes per shot (LTX-2.3 with sound, or Wan 2.2 silent). Pick the best, trim it, and use **Last frame -> next shot** to continue a scene seamlessly. Takes get quality flags for cuts or drift inside the clip.

### 7. Cut

Optionally add a music bed that ducks under dialogue, then render `final.mp4` with `final.srt` subtitles and `final.edl` for DaVinci Resolve.

## Tips

- Press **Ctrl K** to search and jump anywhere. The activity button (top right) shows running jobs and logs.
- Jobs run one at a time on the GPU. Ollama is unloaded before renders.
- Editing a character, location or style marks the shots that depend on it as **stale**. Nothing re-renders automatically.
- Project data is plain JSON plus media files, so you can edit it by hand.

## Tests

```bat
.venv\Scripts\python -m pytest tests\test_studio_e2e.py
```

The tests use fake ComfyUI and Ollama servers and need ffmpeg. The screenshots above come from a throwaway demo project with generated placeholder images.

## Origins and license

Reelwright started as a fork of [KupkaProd Cinema Pipeline](https://github.com/Matticusnicholas/KupkaProd-Cinema-Pipeline) by Matt Kupka. The original autonomous pipeline is still in `video_director_agent/` (see [docs/ORIGINAL-README.md](docs/ORIGINAL-README.md)); the studio in `studio/` is the new interactive app.

The code is distributed under the original **non-commercial license**: see [LICENSE](LICENSE). Bundled fonts (Barlow, IBM Plex Mono) are under the SIL Open Font License; their licenses are in `studio/static/fonts/`.
