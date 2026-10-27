# KupkaProd on a Windows PC (RTX 3090, Comfy Desktop)

## Run once
    setup.bat            (or: powershell -ExecutionPolicy Bypass -File setup.ps1 [-CreativeModel bonsai-2:27b] [-FastModel bonsai-2])

Creates `.venv` (Python 3.12 via uv), installs deps, installs FFmpeg via winget if missing,
verifies the Ollama models (default `bonsai-2` for both roles), writes `video_director_agent/user_settings.json`,
and validates all templates against the running ComfyUI.

## Daily use
1. Start Comfy Desktop (port 8188).
2. `start.bat` (Tk UI) or `start-web.bat` (http://localhost:8010).

## Test renders (before a long run)
    .venv\Scripts\python tools\check_comfy.py --run keyframe
    .venv\Scripts\python tools\check_comfy.py --run video

## Changes vs upstream
- Video/i2v templates use the models already on D:\ComfySharedModels:
  `checkpoints/ltx-2.3-22b-dev-fp8` + `loras/ltx_2.3_22b_distilled_1.1_lora_dynamic_fro09_avg_rank_111_bf16` (strength 1.0)
  + `text_encoders/gemma_3_12B_it_fp4_mixed`; audio VAE / text projection / video VAE come from the checkpoint
  (core nodes `LTXVAudioVAELoader`, `LTXAVTextEncoderLoader`). No KJNodes or extra downloads needed.
- Keyframe template: removed an orphan LTX text-encoder node that referenced a missing file.
- Outputs are fetched over ComfyUI's `/view` API when not found on disk, so the ComfyUI output folder doesn't matter.
- FFmpeg lookup: settings > PATH > ComfyUI root (no more full-tree walk of the ComfyUI install on each settings load).
- Clear error if ComfyUI is down and no launcher is configured; startup timeout 240 s.
- Removed committed junk: another user's `user_settings.json` (pointed at a LAN LLM), `state/`, `__pycache__/`.
- LLM: Ollama `bonsai-2` for planning + evaluation; thinking is sent as `think=false` (toggle via `llm_enable_thinking`);
  preflight fails fast if a configured model isn't installed.
- 3090 defaults: 2 takes/scene, max 12 s scenes, 1024x576 video, 1536x832 keyframes.
