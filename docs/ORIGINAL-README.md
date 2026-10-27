# KupkaProd Cinema Pipeline

**Powered by LTX 2.3**

An autonomous AI movie studio that turns a text prompt or screenplay into a fully produced video — entirely local, no cloud, no subscriptions.

Give it a script, go to sleep, wake up to a movie.

### Demo: 10-Minute Nature Documentary from a Single Sentence

This entire video was generated from a prompt under 50 words. The system is fully agentic — give it as little or as much detail as you want. It writes the script, plans every scene, generates character descriptions, and produces the full video autonomously.


*\*Light trimming done in post-production to clean up sentence cutoffs.*

---

## What It Does

KupkaProd Cinema Pipeline is a Python application that orchestrates multiple AI models to produce videos from text. It works like a miniature production studio:

1. **Script Analysis** — A local LLM (Gemma via Ollama) reads your prompt or screenplay, breaks it into scenes, writes detailed character descriptions, plans camera angles, lighting, and dialogue timing
2. **Storyboarding** — Generates keyframe images for every scene using Z-Image Turbo, then lets you review and approve them before committing to expensive video generation
3. **Video Production** — Generates multiple takes of each scene through ComfyUI's LTX-AV pipeline (synchronized audio + video), with different seeds for variety
4. **Editing** — A built-in take reviewer lets you watch each take, pick your favorites scene-by-scene, and assemble the final film with one click

The entire pipeline runs on your local machine. No API keys, no cloud compute, no per-minute billing.

---

## Features

- **Script or Prompt** — Paste a full screenplay (auto-detected) or just describe what you want ("make a 5 minute video about...")
- **T2V or Keyframe Mode** — Go straight to video generation (T2V Only) or generate storyboard keyframes first for review
- **Intelligent Scene Planning** — Calculates scene duration from actual dialogue word count at character-appropriate speaking rates
- **Character Consistency** — Generates detailed physical descriptions during planning and injects them verbatim into every scene prompt
- **Storyboard Review** — Approve keyframe images before video generation starts. Reject with notes and regenerate
- **Adjustable Takes** — 1-10 video takes per scene (default 3). Set to 1 for fast iteration, crank it up for overnight runs
- **Adjustable Resolution** — Image and video resolution sliders in the GUI with automatic snapping to valid dimensions
- **Adjustable Scene Duration** — Set min/max scene length from the GUI (default 2-30 seconds)
- **Full World Reconstruction** — Every prompt rebuilds the entire scene from scratch (character, setting, lighting, camera) because the video model has no memory between scenes
- **Resume Support** — State saved after every step. Crash overnight? Resume from where you left off
- **Auto-Launch** — Starts ComfyUI automatically if it's not running. Auto-restarts Ollama on each production run to prevent hangs
- **Configurable LLM** — Uses Gemma 4 E4B by default. Supports any Ollama model — swap in Qwen, Mistral, or whatever you prefer in Settings
- **Modern Dark UI** — Windows 11-style dark theme via Sun Valley (falls back gracefully if not installed)
- **Open Source Portable** — First-run setup wizard. No hardcoded paths

---

## What You Need

- **GPU**: NVIDIA with 12GB+ VRAM (tested on RTX 4090 Laptop 16GB)
- **RAM**: 32GB+ recommended
- **Storage**: ~50GB for AI models
- **Python**: 3.10 or newer
- **ComfyUI**: Installed and working ([download here](https://github.com/comfyanonymous/ComfyUI))
- **Ollama**: Installed ([download here](https://ollama.ai/))

---

## Setup (Step by Step)

### Step 1: Download This Repo

```bash
git clone https://github.com/matticusnicholas/KupkaProd-Cinema-Pipeline.git
cd KupkaProd-Cinema-Pipeline
```

Or just download the ZIP from GitHub and extract it somewhere.

### Step 2: Install Python Dependencies

Double-click **`setup.bat`** and let it finish.

Or run this yourself:
```bash
pip install -r requirements.txt
```

### Step 3: Install Ollama and the AI Brain

KupkaProd uses a local AI model to write scripts, plan scenes, and direct your movie. Here's how to set that up:

1. Download and install Ollama from [ollama.ai](https://ollama.ai/)
2. Open a terminal and pull the model:
```bash
ollama pull gemma4:e4b
```
This downloads Gemma 4 (~5GB). It's the brain that writes all the scene descriptions and dialogue.

### Step 4: Download the Video and Image Models for ComfyUI

These are the AI models that actually generate the video and images. You need to download them and put them in the right folders inside your ComfyUI installation.

Your ComfyUI models folder is at something like:
- `C:\ComfyUI\ComfyUI_windows_portable\ComfyUI\models\` (Windows portable)
- `ComfyUI/models/` (manual install)

#### Video Models (LTX-AV — this makes the actual video)

Download these from the [ComfyUI-LTXVideo releases](https://github.com/Lightricks/ComfyUI-LTXVideo):

| File | Put it in |
|------|-----------|
| `ltx-2.3-22b-distilled` (the video model) | `ComfyUI/models/checkpoints/` |
| `LTX23_video_vae_bf16.safetensors` (video decoder) | `ComfyUI/models/vae/` |
| `LTX23_audio_vae_bf16.safetensors` (audio decoder) | `ComfyUI/models/vae/` |
| `gemma_3_12B_it_fp4_mixed.safetensors` (text encoder) | `ComfyUI/models/text_encoders/` |

#### Image Model (for storyboard keyframes)

You also need a fast image model for the storyboard preview step. Z-Image Turbo is recommended — download it from [Tongyi-MAI/Z-Image](https://github.com/Tongyi-MAI/Z-Image) and put the checkpoint in `ComfyUI/models/checkpoints/`.

Any fast image model works (Flux, SDXL Turbo, etc.). This step just generates quick preview images before the expensive video generation starts.

### Step 5: Install ComfyUI Custom Nodes

You need two custom node packs installed in ComfyUI. The easiest way is through [ComfyUI Manager](https://github.com/ltdrdata/ComfyUI-Manager), or you can clone them directly into `ComfyUI/custom_nodes/`:

- **[ComfyUI-LTXVideo](https://github.com/Lightricks/ComfyUI-LTXVideo)** — The LTX-AV video generation nodes
- **[ComfyUI-VideoHelperSuite](https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite)** — Video output and preview nodes

After installing, restart ComfyUI.

### Step 6: Test the Workflows (Dry Run)

This is the most important step. KupkaProd comes with two ComfyUI workflows in the **`workflows/`** folder. You need to load each one in ComfyUI and make sure it actually works before KupkaProd can use it.

#### Test the Video Workflow

1. Open ComfyUI in your browser (`http://localhost:8188`)
2. Drag and drop **`workflows/ltx2.3t2v.json`** into the ComfyUI window
3. If you see any **red nodes**, you're missing custom nodes — install them and restart ComfyUI
4. Make sure all the model paths look right (ComfyUI should find your downloaded models automatically)
5. Click **Queue Prompt** and let it generate a test video
6. If it works, you're good! If not, fix any errors before continuing

#### Test the Keyframe Workflow

1. Drag and drop **`workflows/keyframegenerator.json`** into ComfyUI
2. Same deal — fix any red nodes, make sure the image model is loaded
3. Click **Queue Prompt** and let it generate a test image
4. If it works, you're ready

#### Test the Image-to-Video Workflow (Optional but Recommended)

If you want KupkaProd to use your approved storyboard keyframes as the starting frame for each scene (instead of generating video purely from text), set up the i2v workflow too:

1. Drag and drop **`workflows/ltxv_i2v_transformersCUSTOMSIGMAS.json`** into ComfyUI
2. Fix any red nodes, load a test image into the LoadImage node
3. Click **Queue Prompt** and let it generate a test video from the image
4. If it works, export it in the next step

If you skip this, KupkaProd will use text-to-video for all scenes. It still works, but your keyframes only serve as a storyboard preview rather than actually guiding the video generation.

#### Already Included: API Templates

KupkaProd ships with pre-exported API templates (`workflow_template.json` and `keyframe_template.json`) that match the included workflows. **If the dry runs above worked without changes, you're done — skip to Step 7.**

If you had to modify the workflows (different models, custom nodes, etc.), re-export them so KupkaProd picks up your changes. Run the workflow in ComfyUI, then immediately run the matching export command:

**Video workflow:**
```bash
python -c "import json, urllib.request; r = urllib.request.urlopen('http://127.0.0.1:8188/history?max_items=1'); history = json.loads(r.read()); wf = list(history.values())[0]['prompt'][2]; open('video_director_agent/workflow_template.json','w').write(json.dumps(wf,indent=2)); print(f'Saved video workflow ({len(wf)} nodes)')"
```

**Keyframe workflow:**
```bash
python -c "import json, urllib.request; r = urllib.request.urlopen('http://127.0.0.1:8188/history?max_items=1'); history = json.loads(r.read()); wf = list(history.values())[0]['prompt'][2]; open('video_director_agent/keyframe_template.json','w').write(json.dumps(wf,indent=2)); print(f'Saved keyframe workflow ({len(wf)} nodes)')"
```

**I2V workflow** (optional — only if you set up image-to-video above):
```bash
python -c "import json, urllib.request; r = urllib.request.urlopen('http://127.0.0.1:8188/history?max_items=1'); history = json.loads(r.read()); wf = list(history.values())[0]['prompt'][2]; open('video_director_agent/i2v_template.json','w').write(json.dumps(wf,indent=2)); print(f'Saved i2v workflow ({len(wf)} nodes)')"
```

KupkaProd auto-detects which nodes to control in your workflows — no need to edit any code.

### Step 7: Launch KupkaProd

Double-click **`start.bat`**.

Or run:
```bash
python video_director_agent/gui.py
```

On first launch, a setup dialog asks for:
- **ComfyUI root folder** — where ComfyUI is installed (e.g. `C:\ComfyUI\ComfyUI_windows_portable`)
- **Launch script** — the `.bat` file you normally use to start ComfyUI
- **LLM models** — which Ollama models to use (the defaults are fine)

These settings are saved and can be changed anytime via the Settings button.

### Optional: Launch the Web Interface

If you prefer using the pipeline through a browser instead of the Tk desktop UI, install the extra web dependencies from `requirements.txt` and run:

```bash
uvicorn video_director_agent.web_app:app --host 0.0.0.0 --port 8000
```

Then open `http://localhost:8000` in your browser, or forward that port over SSH if the pipeline runs on another machine.

Current scope of the web UI:
- Start or resume a production run
- Watch live logs in the browser
- Review storyboard keyframes
- Review generated takes and assemble the final film

Current limitation:
- The first web version supports only one active production job at a time

### Step 8: Make a Movie

Type a prompt like *"make a 2 minute nature documentary about ocean life"* and click **Start Production**.

Or paste in a full screenplay — KupkaProd auto-detects scripts with scene headings, character names, and dialogue.

Go get coffee. Come back to a movie.

---

## How It Works

### The Pipeline

```
[Your Script/Prompt]
        |
        v
PHASE 1: SCENE BREAKDOWN (Gemma via Ollama)
  - Parse script or generate scenes from prompt
  - Write character descriptions (50-80 words each)
  - Plan settings, lighting, camera angles per scene
  - Calculate duration from dialogue word count + action time
  - Unload heavy model from VRAM
        |
        v
PHASE 2: STORYBOARD (Z-Image Turbo) [skipped in T2V Only mode]
  - Generate keyframe candidates per scene (stops early on first PASS)
  - AI evaluates each against character descriptions
  - >>> YOU REVIEW: approve/reject keyframes <<<
        |
        v
PHASE 3: VIDEO PRODUCTION (LTX-AV via ComfyUI)
  - 1-10 takes per scene (configurable), different seeds
  - Full 2-pass pipeline with latent upsampling
  - >>> YOU REVIEW: pick best take per scene <<<
        |
        v
FINAL ASSEMBLY (FFmpeg)
  - Stitch selected takes into final video
  - Lossless concat (no re-encode)
```

### Script Auto-Detection

The system automatically detects screenplays by looking for:
- Scene headings (`INT.` / `EXT.`)
- Character names in ALL CAPS
- Stage directions in parentheses
- Transitions (`FADE IN`, `CUT TO`)

If detected, it preserves all dialogue word-for-word and converts stage directions to visual descriptions.

### Prompt Writing Philosophy

Every video prompt is **fully self-contained** because the video model has zero memory between scenes. Each prompt includes:
- Complete character physical description (copied verbatim from the planning phase)
- Full setting/environment description
- Lighting direction and atmosphere
- Camera angle and movement
- Exact dialogue in quotes
- Sound effects and ambient audio
- 200-400 words per prompt

### Duration Calculation

Scene duration is calculated from actual content, not guessed:
- Dialogue words counted and divided by character-appropriate WPM
- Known speaking rates: Trump (170 WPM), Obama (130 WPM), default (140 WPM)
- Action time added on top of dialogue time
- Scenes range from 2 seconds (quick cutaway) to 30 seconds (long take)

---

## Project Structure

```
KupkaProd-Cinema-Pipeline/
├── README.md
├── LICENSE
├── trump_standup.txt              # Example script
├── workflows/
│   ├── ltx2.3t2v.json             # Video workflow (load in ComfyUI)
│   ├── keyframegenerator.json     # Keyframe workflow (load in ComfyUI)
│   └── ltxv_i2v_transformersCUSTOMSIGMAS.json  # Image-to-video workflow
├── video_director_agent/
│   ├── agent.py                   # Main orchestrator + CLI
│   ├── director.py                # Scene planning + prompt writing (Gemma)
│   ├── comfyui_client.py          # ComfyUI API client (WebSocket + REST)
│   ├── keyframe_gen.py            # Keyframe image generation + evaluation
│   ├── evaluator.py               # Video frame evaluation
│   ├── assembler.py               # FFmpeg video assembly
│   ├── gui.py                     # Main GUI (Tkinter)
│   ├── storyboard.py              # Storyboard review GUI
│   ├── reviewer.py                # Take selection GUI
│   ├── config.py                  # Settings (loads from user_settings.json)
│   ├── user_settings.json         # Your local paths (git-ignored)
│   ├── workflow_template.json     # LTX-AV video workflow (API format, included)
│   ├── keyframe_template.json     # Z-Image Turbo workflow (API format, included)
│   ├── i2v_template.json          # Image-to-video workflow (API format, user-exported)
│   ├── logs/                      # Per-run log files
│   └── output/                    # Generated projects
│       └── [project_name]/
│           ├── state.json         # Full project state (resumable)
│           ├── keyframes/         # Storyboard images
│           ├── scenes/            # Video takes
│           └── final.mp4          # Assembled film
```

---

## Configuration

All settings are in `video_director_agent/config.py` with user overrides in `user_settings.json`.

### Key Settings

| Setting | Default | Description |
|---------|---------|-------------|
| `TAKES_PER_SCENE` | 3 | Video takes generated per scene (1-10, adjustable in GUI) |
| `KF_CANDIDATES` | 4 | Keyframe image candidates per scene (stops early on first PASS) |
| `USE_KEYFRAMES` | True | Enable storyboard phase (or use T2V Only checkbox in GUI) |
| `SCENE_MIN_SEC` | 2 | Minimum scene duration in seconds (adjustable in GUI) |
| `SCENE_MAX_SEC` | 30 | Maximum scene duration in seconds (adjustable in GUI) |
| `LTX_FPS` | 24 | Video frame rate |
| `KF_WIDTH` | 2048 | Keyframe image width (adjustable in GUI, snaps to multiples of 64) |
| `KF_HEIGHT` | 1024 | Keyframe image height (adjustable in GUI, snaps to multiples of 64) |
| `VIDEO_WIDTH` | 1024 | Video resolution width (adjustable in GUI, snaps to multiples of 32) |
| `VIDEO_HEIGHT` | 432 | Video resolution height (adjustable in GUI, snaps to multiples of 32) |
| `OLLAMA_MODEL_CREATIVE` | gemma4:e4b | Model for planning/writing (configurable in Settings) |
| `OLLAMA_MODEL_FAST` | gemma4:e4b | Model for evaluation (configurable in Settings) |

### Workflow Node IDs

KupkaProd auto-detects which nodes to control in your ComfyUI workflows. If you're using the included example workflows, everything works out of the box.

If you're using a custom workflow and auto-detection isn't finding the right nodes, you can manually set them in `config.py`. This is an advanced option — most users won't need it.

---

## Tips

- **Start small** — Test with a 1-minute video first before attempting a 30-minute film
- **Fast iteration** — Set takes to 1 and enable T2V Only mode to skip keyframes. Great for testing prompts
- **Overnight runs** — Crank takes to 5-10 for maximum variety. Long productions (10+ minutes) can take hours. The resume system handles crashes
- **Model swapping** — If Gemma 26B is too slow, use `gemma4:e4b` for both creative and eval. Quality will be lower but it's much faster
- **Resolution** — Image dimensions must be divisible by 64, video by 32. The GUI sliders snap automatically
- **VRAM management** — The agent automatically unloads the heavy LLM and restarts Ollama before starting ComfyUI generation

---

## Troubleshooting

**ComfyUI won't start:** Make sure the launcher `.bat` path is correct in Settings. Check that ComfyUI runs normally when launched manually.

**"Node not found" errors:** Your workflow uses nodes from custom node packs that aren't installed. Install the required custom nodes in ComfyUI.

**JSON parse errors:** The Gemma model sometimes outputs malformed JSON. The parser handles most cases automatically. If it persists, try using a different model or reducing the scene count.

**Out of memory:** Reduce video resolution in the workflow template, or generate shorter scenes (lower `SCENE_MAX_SEC`).

**Keyframes all failing:** Check that Z-Image Turbo works manually in ComfyUI at the configured resolution. Dimensions must be divisible by 32.

---

## License

Free for non-commercial use. See [LICENSE](LICENSE) for details.

Commercial use requires a separate license. Contact **matt.kupka@gmail.com** for commercial licensing.

---

## Acknowledgments

- [ComfyUI](https://github.com/comfyanonymous/ComfyUI) — The backbone for all image/video generation
- [LTX-Video](https://github.com/Lightricks/LTXVideo) — Text-to-video with synchronized audio
- [Z-Image Turbo](https://github.com/Tongyi-MAI/Z-Image) — Fast image generation for storyboarding
- [Ollama](https://ollama.ai/) — Local LLM inference
- [Gemma](https://ai.google.dev/gemma) — Scene planning and evaluation

KupkaProd Cinema Pipeline — Built with Claude Code.
