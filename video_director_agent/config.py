# config.py — All settings in one place.
# User-specific paths are loaded from user_settings.json (auto-created on first run).

import json
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_SETTINGS_PATH = os.path.join(os.path.dirname(__file__), "user_settings.json")

# --- Defaults (overridden by user_settings.json) ---
_DEFAULTS = {
    "comfyui_root": r"/opt/llm/comfyui",
    "comfyui_output_dir": "",
    "project_output_root": os.path.join(BASE_DIR, "output"),
    "comfyui_host": "127.0.0.1:8188",
    "comfyui_hosts": ["127.0.0.1:8188"],
    "comfyui_launcher": "run_nvidia_gpu.bat",
    "default_brief": "",
    "default_is_script": False,
    "llm_provider": "ollama",
    "llm_base_url": "http://localhost:11434",
    "llm_api_key": "",
    "llm_enable_thinking": False,
    "llm_reasoning_breakdown_only": False,
    "llm_reasoning_format": "none",
    "llm_creative_drafting_mode": False,
    "ollama_host": "http://localhost:11434",
    "ollama_model_creative": "bonsai-2",
    "ollama_model_fast": "bonsai-2",
    "lazy_mode": False,
    "t2v_only": False,
    "takes_per_scene": 3,
    "scene_min_sec": 2,
    "scene_max_sec": 30,
    "skip_kf_eval": True,
    "subtitle_safe_mode": False,
    "final_transition_enabled": False,
    "final_transition_duration": 0.35,
    "character_focus": 68,
    "action_focus": 62,
    "environment_weight": 58,
    "establishing_shot_bias": 32,
    "natural_dialogue": False,
    "no_dialogue": False,
    "zit_direct_prompt": "",
    "kf_prompt_node_id": "57:27",
    "kf_prompt_input_name": "text",
    "kf_width": 2048,
    "kf_height": 1024,
    "video_width": 1024,
    "video_height": 432,
}


def _load_user_settings() -> dict:
    if os.path.exists(_SETTINGS_PATH):
        with open(_SETTINGS_PATH) as f:
            return json.load(f)
    return {}


def save_user_settings(settings: dict):
    merged = _load_user_settings()
    merged.update({k: v for k, v in settings.items() if v is not None})
    with open(_SETTINGS_PATH, "w") as f:
        json.dump(merged, f, indent=2)
    load_runtime_settings()


_user = _load_user_settings()


def _get(key: str) -> str:
    return _user.get(key, _DEFAULTS[key])


def _get_bool(key: str) -> bool:
    value = _user.get(key, _DEFAULTS[key])
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _get_list(key: str) -> list[str]:
    value = _user.get(key, _DEFAULTS[key])
    if isinstance(value, list):
        items = value
    else:
        items = str(value).splitlines()
    cleaned = []
    seen = set()
    for item in items:
        text = str(item).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        cleaned.append(text)
    return cleaned


def _normalize_comfyui_hosts() -> tuple[str, list[str]]:
    hosts = _get_list("comfyui_hosts")
    active = str(_user.get("comfyui_host", _DEFAULTS["comfyui_host"])).strip()
    if not active and hosts:
        active = hosts[0]
    if not hosts and active:
        hosts = [active]
    if active and active not in hosts:
        hosts = [active] + hosts
    if not hosts:
        hosts = [str(_DEFAULTS["comfyui_host"]).strip()]
    if not active:
        active = hosts[0]
    return active, hosts


def _resolve_path(path_value: str) -> str:
    path = os.path.expanduser(str(path_value).strip())
    if not os.path.isabs(path):
        path = os.path.join(BASE_DIR, path)
    return os.path.abspath(path)


def _resolve_comfyui_output_dir(comfyui_root: str, configured_value: str = "") -> str:
    text = str(configured_value or "").strip()
    if not text:
        return os.path.abspath(os.path.join(comfyui_root, "ComfyUI", "output"))
    path = os.path.expanduser(text)
    if not os.path.isabs(path):
        path = os.path.join(comfyui_root, path)
    return os.path.abspath(path)


def get_settings_snapshot() -> dict:
    comfyui_host, comfyui_hosts = _normalize_comfyui_hosts()
    comfyui_root = _get("comfyui_root")
    configured_output_dir = str(_user.get("comfyui_output_dir", _DEFAULTS["comfyui_output_dir"])).strip()
    return {
        "comfyui_root": comfyui_root,
        "comfyui_output_dir": configured_output_dir or _resolve_comfyui_output_dir(comfyui_root, configured_output_dir),
        "comfyui_output_dir_resolved": _resolve_comfyui_output_dir(comfyui_root, configured_output_dir),
        "project_output_root": _resolve_path(_get("project_output_root")),
        "comfyui_host": comfyui_host,
        "comfyui_hosts": comfyui_hosts,
        "comfyui_hosts_text": "\n".join(comfyui_hosts),
        "comfyui_launcher": _get("comfyui_launcher"),
        "default_brief": _get("default_brief"),
        "default_is_script": _get_bool("default_is_script"),
        "llm_provider": _get("llm_provider"),
        "llm_base_url": _get("llm_base_url"),
        "llm_api_key": _get("llm_api_key"),
        "llm_enable_thinking": _get_bool("llm_enable_thinking"),
        "llm_reasoning_breakdown_only": _get_bool("llm_reasoning_breakdown_only"),
        "llm_reasoning_format": _get("llm_reasoning_format"),
        "llm_creative_drafting_mode": _get_bool("llm_creative_drafting_mode"),
        "ollama_host": _get("ollama_host"),
        "ollama_model_creative": _get("ollama_model_creative"),
        "ollama_model_fast": _get("ollama_model_fast"),
        "lazy_mode": _get_bool("lazy_mode"),
        "t2v_only": _get_bool("t2v_only"),
        "takes_per_scene": int(_get("takes_per_scene")),
        "scene_min_sec": int(_get("scene_min_sec")),
        "scene_max_sec": int(_get("scene_max_sec")),
        "skip_kf_eval": _get_bool("skip_kf_eval"),
        "subtitle_safe_mode": _get_bool("subtitle_safe_mode"),
        "final_transition_enabled": _get_bool("final_transition_enabled"),
        "final_transition_duration": float(_get("final_transition_duration")),
        "character_focus": int(_get("character_focus")),
        "action_focus": int(_get("action_focus")),
        "environment_weight": int(_get("environment_weight")),
        "establishing_shot_bias": int(_get("establishing_shot_bias")),
        "natural_dialogue": _get_bool("natural_dialogue"),
        "no_dialogue": _get_bool("no_dialogue"),
        "zit_direct_prompt": _get("zit_direct_prompt"),
        "kf_prompt_node_id": _get("kf_prompt_node_id"),
        "kf_prompt_input_name": _get("kf_prompt_input_name"),
        "kf_width": int(_get("kf_width")),
        "kf_height": int(_get("kf_height")),
        "video_width": int(_get("video_width")),
        "video_height": int(_get("video_height")),
    }


def _discover_ffmpeg_paths(comfyui_root: str) -> tuple[str, str]:
    """Locate ffmpeg/ffprobe: explicit settings > PATH > well-known spots under the ComfyUI root.

    The old implementation walked the entire ComfyUI tree (including .venv and models),
    which takes minutes on a Comfy Desktop install and runs on every settings save.
    """
    import shutil

    def _pick(setting_key: str, exe: str) -> str:
        configured = str(_user.get(setting_key, "") or "").strip()
        if configured and os.path.exists(configured):
            return configured
        on_path = shutil.which(exe)
        if on_path:
            return on_path
        if comfyui_root:
            for candidate in (
                os.path.join(comfyui_root, f"{exe}.exe"),
                os.path.join(comfyui_root, "ffmpeg", f"{exe}.exe"),
                os.path.join(comfyui_root, "ffmpeg", "bin", f"{exe}.exe"),
            ):
                if os.path.exists(candidate):
                    return candidate
        return exe

    return _pick("ffmpeg_path", "ffmpeg"), _pick("ffprobe_path", "ffprobe")


def load_runtime_settings() -> dict:
    global _user, _comfyui_root
    global COMFYUI_HOST, COMFYUI_OUTPUT_DIR, COMFYUI_LAUNCHER, COMFYUI_STARTUP_TIMEOUT
    global FFMPEG_PATH, FFPROBE_PATH, OUTPUT_DIR
    global LLM_PROVIDER, LLM_BASE_URL, LLM_API_KEY, LLM_ENABLE_THINKING, LLM_REASONING_BREAKDOWN_ONLY, LLM_REASONING_FORMAT, LLM_CREATIVE_DRAFTING_MODE, OLLAMA_HOST
    global OLLAMA_MODEL_CREATIVE, OLLAMA_MODEL_FAST, OLLAMA_MODEL
    global SCENE_MIN_SEC, SCENE_MAX_SEC, TAKES_PER_SCENE, USE_KEYFRAMES, LAZY_MODE, SKIP_KF_EVAL, SUBTITLE_SAFE_MODE
    global FINAL_TRANSITION_ENABLED, FINAL_TRANSITION_DURATION
    global NATURAL_DIALOGUE, NO_DIALOGUE, ZIT_DIRECT_PROMPT
    global KF_PROMPT_NODE_ID, KF_PROMPT_INPUT_NAME
    global KF_WIDTH, KF_HEIGHT, VIDEO_WIDTH, VIDEO_HEIGHT

    _user = _load_user_settings()
    _comfyui_root = _get("comfyui_root")

    OUTPUT_DIR = _resolve_path(_get("project_output_root"))
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    COMFYUI_HOST, _configured_hosts = _normalize_comfyui_hosts()
    COMFYUI_OUTPUT_DIR = _resolve_comfyui_output_dir(
        _comfyui_root,
        _user.get("comfyui_output_dir", _DEFAULTS["comfyui_output_dir"]),
    )
    COMFYUI_LAUNCHER = os.path.join(_comfyui_root, _get("comfyui_launcher"))
    COMFYUI_STARTUP_TIMEOUT = int(_user.get("comfyui_startup_timeout", 240))

    FFMPEG_PATH, FFPROBE_PATH = _discover_ffmpeg_paths(_comfyui_root)

    LLM_PROVIDER = _get("llm_provider")
    LLM_BASE_URL = _get("llm_base_url")
    LLM_API_KEY = _get("llm_api_key")
    LLM_ENABLE_THINKING = _get_bool("llm_enable_thinking")
    LLM_REASONING_BREAKDOWN_ONLY = _get_bool("llm_reasoning_breakdown_only")
    LLM_REASONING_FORMAT = _get("llm_reasoning_format")
    LLM_CREATIVE_DRAFTING_MODE = _get_bool("llm_creative_drafting_mode")
    OLLAMA_HOST = _get("ollama_host")
    OLLAMA_MODEL_CREATIVE = _get("ollama_model_creative")
    OLLAMA_MODEL_FAST = _get("ollama_model_fast")
    OLLAMA_MODEL = OLLAMA_MODEL_FAST

    SCENE_MIN_SEC = int(_get("scene_min_sec"))
    SCENE_MAX_SEC = int(_get("scene_max_sec"))
    TAKES_PER_SCENE = int(_get("takes_per_scene"))
    USE_KEYFRAMES = not _get_bool("t2v_only")
    LAZY_MODE = _get_bool("lazy_mode")
    SKIP_KF_EVAL = _get_bool("skip_kf_eval")
    SUBTITLE_SAFE_MODE = _get_bool("subtitle_safe_mode")
    FINAL_TRANSITION_ENABLED = _get_bool("final_transition_enabled")
    FINAL_TRANSITION_DURATION = float(_get("final_transition_duration"))
    NATURAL_DIALOGUE = _get_bool("natural_dialogue")
    NO_DIALOGUE = _get_bool("no_dialogue")
    ZIT_DIRECT_PROMPT = _get("zit_direct_prompt")
    KF_PROMPT_NODE_ID = _get("kf_prompt_node_id")
    KF_PROMPT_INPUT_NAME = _get("kf_prompt_input_name") or "text"

    KF_WIDTH = int(_get("kf_width"))
    KF_HEIGHT = int(_get("kf_height"))
    VIDEO_WIDTH = int(_get("video_width"))
    VIDEO_HEIGHT = int(_get("video_height"))

    return get_settings_snapshot()


def llm_reasoning_options(for_breakdown: bool = False) -> dict:
    """Return effective llama.cpp reasoning settings for the given pipeline phase."""
    if LLM_REASONING_BREAKDOWN_ONLY:
        enabled = bool(for_breakdown)
    else:
        enabled = bool(LLM_ENABLE_THINKING)
    return {
        "enable_thinking": enabled,
        "reasoning_format": LLM_REASONING_FORMAT if enabled else "none",
    }


def llm_creative_drafting_enabled() -> bool:
    return bool(LLM_CREATIVE_DRAFTING_MODE)


# --- Derived paths from ComfyUI root ---
load_runtime_settings()


def get_output_root() -> str:
    return OUTPUT_DIR


def project_dir(project_name: str) -> str:
    return os.path.join(OUTPUT_DIR, project_name)


def project_state_path(project_name: str) -> str:
    return os.path.join(project_dir(project_name), "state.json")

# --- LTX-AV (video generation) ---
LTX_FPS = 24

# --- Scene duration limits (seconds) ---
SCENE_MIN_SEC = int(_get("scene_min_sec"))
SCENE_MAX_SEC = int(_get("scene_max_sec"))
SCENE_SWEET_SPOT_SEC = 15

# --- Video workflow node IDs (t2v transformer pipeline) ---
PROMPT_NODE_ID = "153:132"
NEG_PROMPT_NODE_ID = "153:123"
FRAMES_NODE_ID = "153:125"
SEED_NODE_ID_PASS1 = "153:151"
SEED_NODE_ID_PASS2 = "153:127"

VIDEO_RES_NODE_ID = "153:124"   # EmptyImage node that sets video resolution

# --- Keyframe image workflow node IDs (Z-Image Turbo) ---
KF_PROMPT_NODE_ID = _get("kf_prompt_node_id")
KF_PROMPT_INPUT_NAME = _get("kf_prompt_input_name") or "text"
KF_SEED_NODE_ID = "57:3"
KF_LATENT_NODE_ID = "57:13"

# --- Video resolution ---

# --- Agent behavior ---
TAKES_PER_SCENE = int(_get("takes_per_scene"))
USE_KEYFRAMES = not _get_bool("t2v_only")
KF_CANDIDATES = 4
LAZY_MODE = _get_bool("lazy_mode")                # AI auto-selects best keyframes and takes (no manual review)
SKIP_KF_EVAL = _get_bool("skip_kf_eval")          # Skip AI evaluation of keyframes (experimental)
SUBTITLE_SAFE_MODE = _get_bool("subtitle_safe_mode")  # Avoid literal quoted dialogue in prompts to reduce burned-in captions
FINAL_TRANSITION_ENABLED = _get_bool("final_transition_enabled")
FINAL_TRANSITION_DURATION = float(_get("final_transition_duration"))
NATURAL_DIALOGUE = _get_bool("natural_dialogue")
NO_DIALOGUE = _get_bool("no_dialogue")
ZIT_DIRECT_PROMPT = _get("zit_direct_prompt")
EVAL_FRAME_SAMPLE_RATE = 1
EVAL_MAX_FRAMES = 20
EVAL_TOKEN_BUDGET = 1024

# --- Negative prompt ---
NEGATIVE_PROMPT = (
    "background music, soundtrack, musical score, "
    "blurry, low quality, still frame, frozen motion, watermark, overlay, "
    "titles, subtitles, text on screen, deformed hands, extra fingers"
)

VISUAL_NEGATIVE_PROMPT = (
    "blurry, low quality, still frame, frozen motion, watermark, overlay, "
    "titles, subtitles, text on screen, deformed hands, extra fingers"
)

NO_DIALOGUE_NEGATIVE_PROMPT = (
    "speech, talking, dialogue, spoken words, narration, narrator, voice-over, "
    "voiceover, announcer, presenter, podcast, interview, monologue, whispering, "
    "murmuring, mouth-synced speech, lip-sync, lip sync, YouTube voice, "
    "advertisement voice, product placement, infomercial, commercial announcer"
)


def effective_negative_prompt() -> str:
    """Return the negative prompt that should be injected into ComfyUI."""
    if _get_bool("no_dialogue"):
        return f"{VISUAL_NEGATIVE_PROMPT}, {NO_DIALOGUE_NEGATIVE_PROMPT}"
    return NEGATIVE_PROMPT
