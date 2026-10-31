"""Studio settings: one JSON file under the data root, with defaults for a Windows PC."""

import copy
import json
import os
import shutil
import threading

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_ROOT = os.environ.get("STUDIO_DATA", os.path.join(REPO_ROOT, "studio_data"))
SETTINGS_PATH = os.path.join(DATA_ROOT, "settings.json")
LEGACY_SETTINGS = os.path.join(REPO_ROOT, "video_director_agent", "user_settings.json")

DEFAULTS = {
    "comfy_host": "127.0.0.1:8188",
    "ollama_host": "http://localhost:11434",
    "planner_model": "bonsai-2",
    "vision_model": "bonsai-2",
    "vision_check": True,
    "ffmpeg": "",
    "ffprobe": "",
    "candidates_per_shot": 4,
    "takes_per_shot": 2,
    "image": {"width": 1344, "height": 768},
    "video": {"width": 1024, "height": 576, "fps": 24},
    "flux": {
        "unet": "flux1-dev.safetensors",
        "weight_dtype": "fp8_e4m3fn",
        "clip_l": "clip_l.safetensors",
        "t5": "t5xxl_fp8_e4m3fn.safetensors",
        "vae": "ae.safetensors",
        "steps": 24,
        "guidance": 3.5,
        "sampler": "euler",
        "scheduler": "simple",
        "second_lora_scale": 0.75,
    },
    "zimage": {
        "unet": "z_image_turbo_bf16.safetensors",
        "clip": "qwen_3_4b.safetensors",
        "vae": "ae.safetensors",
        "steps": 8,
        "shift": 3.0,
    },
    "qwen_edit": {
        # "Reference photos" frame source: Qwen Image Edit takes up to 3 images (the shot's characters' refs)
        "unet": "qwen_image_edit_2509_fp8_e4m3fn.safetensors",
        "clip": "qwen_2.5_vl_7b_fp8_scaled.safetensors",
        "vae": "qwen_image_vae.safetensors",
        "lora": "Qwen-Image-Edit-2509-Lightning-4steps-V1.0-bf16.safetensors",
        "steps": 4,
        "shift": 3.0,
        "max_refs": 3,
    },
    "ltx": {
        "template": "video_director_agent/i2v_template.json",
        "max_seconds": 10,
        "first_frame_strength": 1.0,
        "end_frame_strength": 0.8,
        "id_lora": "ltx-2.3-id-lora-talkvid-3k.safetensors",
        "id_lora_strength": 1.0,
        "identity_guidance_scale": 3.0,
        "negative": "blurry, low quality, still frame, frozen motion, watermark, subtitles, "
                    "text on screen, deformed hands, extra fingers, background music, scene cut, jump cut, "
                    "camera cut, transition, new scene, different location, different person, reframing",
    },
    "wan": {
        "high": "wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors",
        "low": "wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors",
        "high_lightning": "wan2.2_i2v_lightx2v_4steps_lora_v1_high_noise.safetensors",
        "low_lightning": "wan2.2_i2v_lightx2v_4steps_lora_v1_low_noise.safetensors",
        "text_encoder": "umt5_xxl_fp8_e4m3fn_scaled.safetensors",
        "vae": "wan_2.1_vae.safetensors",
        "width": 848,
        "height": 480,
        "fps": 16,
        "steps": 4,
        "shift": 5.0,
        "max_seconds": 5,
        "negative": "blurry, static, deformed, extra fingers, watermark, subtitles, text",
    },
    "minimax_local": {
        "unet": "minimax_h3_fl2va_pruned_int8_convrot.safetensors",
        "clip": "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors",
        "video_vae": "minimax_h3_video_vae_int8_convrot.safetensors",
        "audio_vae": "minimax_h3_audio_vae_fp32.safetensors",
        "lora": "minimax_h3_fl2v_turbo_4step_v1.0_768p_comfyui_bf16.safetensors",
        "steps": 4, "width": 608, "height": 352, "max_seconds": 15,
    },
    "minimax": {
        "model": "MiniMax-Hailuo-2.3", "resolution": "768P",
        "api_key_env": "MINIMAX_API_KEY", "base_url": "https://api.minimax.io/v1",
        "poll_seconds": 5, "timeout_seconds": 1800, "max_seconds": 10,
    },
    "face_correction": {
        "model": "qwen_identity", "keep_dir": "", "codeformer_dir": "",
        "python": "", "fidelity": 0.8,
        "identity_unet": "minimax_h3_ref2va_pruned_int8_convrot.safetensors",
        "identity_lora": "h3_character_swap_v1.5.safetensors",
        "identity_turbo_lora": "minimax_h3_fl2v_turbo_8step_v1.0_768p_comfyui_bf16.safetensors",
    },
    "loudness_lufs": -16,
    "continuity": {
        "punch_in_denoise": 0.32,   # low: keep the master's face, age and clothes; only re-sharpen
        # LTX tends to hard-cut to a new wide shot ~1 s into close-ups. Re-using the first frame as the
        # end guide pins the take to that picture (static-camera close/punch-in shots without an end frame).
        "anchor_end": True,
        "crop": {"medium": 0.62, "medium close-up": 0.46, "close-up": 0.34, "extreme close-up": 0.22,
                 "over-the-shoulder": 0.55, "two-shot": 0.7},
    },
    "location_ref": {
        "enabled": True,
        "denoise": 0.82,
        "framings": ["extreme wide", "wide", "full", "medium wide", "medium", "two-shot"],
    },
}

_lock = threading.Lock()


def _deep_merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _seed_from_legacy() -> dict:
    """First run: reuse what setup.ps1 already wrote for the legacy app."""
    seed = {}
    try:
        with open(LEGACY_SETTINGS, encoding="utf-8") as f:
            legacy = json.load(f)
    except (OSError, ValueError):
        return seed
    if legacy.get("comfyui_host"):
        seed["comfy_host"] = legacy["comfyui_host"]
    if legacy.get("ollama_host"):
        seed["ollama_host"] = legacy["ollama_host"]
    if legacy.get("ollama_model_creative"):
        seed["planner_model"] = legacy["ollama_model_creative"]
    if legacy.get("ollama_model_fast"):
        seed["vision_model"] = legacy["ollama_model_fast"]
    for src, dst in (("ffmpeg_path", "ffmpeg"), ("ffprobe_path", "ffprobe")):
        if legacy.get(src):
            seed[dst] = legacy[src]
    return seed


def load() -> dict:
    with _lock:
        os.makedirs(DATA_ROOT, exist_ok=True)
        user = {}
        if os.path.exists(SETTINGS_PATH):
            with open(SETTINGS_PATH, encoding="utf-8") as f:
                user = json.load(f)
        else:
            user = _seed_from_legacy()
            with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
                json.dump(user, f, indent=2)
        s = _deep_merge(DEFAULTS, user)
    s["ffmpeg"] = s["ffmpeg"] or shutil.which("ffmpeg") or "ffmpeg"
    s["ffprobe"] = s["ffprobe"] or shutil.which("ffprobe") or "ffprobe"
    return s


def save(user_values: dict) -> dict:
    """Persist only what the user set (the defaults stay in code)."""
    with _lock:
        os.makedirs(DATA_ROOT, exist_ok=True)
        with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(user_values, f, indent=2)
    return load()


def raw_user() -> dict:
    if os.path.exists(SETTINGS_PATH):
        with open(SETTINGS_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {}


def for_episode(s: dict, episode: dict) -> dict:
    """Apply a project's portrait/landscape output sizes without changing other projects."""
    if not episode.get('video') and not episode.get('image'):
        return s
    import math
    result = copy.deepcopy(s)
    for key in ('video', 'image'):
        if episode.get(key):
            result[key].update(episode[key])
    if episode.get('video'):
        ratio = result['video']['width'] / result['video']['height']
        h3 = result['minimax_local']
        pixels = h3['width'] * h3['height']
        h3['width'] = max(32, round(math.sqrt(pixels * ratio) / 32) * 32)
        h3['height'] = max(32, round(math.sqrt(pixels / ratio) / 32) * 32)
    return result
