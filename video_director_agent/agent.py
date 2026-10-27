#!/usr/bin/env python3
"""
Video Director Agent — Autonomous LTX-AV video creation via Gemma + ComfyUI API.

Usage:
    python agent.py "make a 2 minute short film about a lighthouse keeper"
    python agent.py "..." --project lighthouse_keeper
    python agent.py --resume lighthouse_keeper
    python agent.py --test-scene "crashing waves at sunset, 10 seconds"
    python agent.py "..." --model qwen3:30b
"""

import argparse
import json
import logging
import os
import random
import re
import shutil
import sys
import tempfile
import time
from datetime import datetime

# Add this directory to path so config imports work
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config
from config import (
    COMFYUI_HOST, TAKES_PER_SCENE, COMFYUI_OUTPUT_DIR,
    OLLAMA_MODEL, LTX_FPS, COMFYUI_LAUNCHER, COMFYUI_STARTUP_TIMEOUT,
    FFMPEG_PATH, USE_KEYFRAMES, project_state_path,
)
from comfyui_client import (
    ComfyUIClient, load_workflow_template, build_workflow, calc_frames,
    load_i2v_template, build_i2v_workflow,
)
from llm_client import ensure_model_available, is_model_installed, is_ollama_provider, provider_label, unload_model
import director
import evaluator
import assembler

log = logging.getLogger(__name__)

# ── Logging ────────────────────────────────────────────────────────────────

def setup_logging(project_name: str):
    log_dir = os.path.join(os.path.dirname(__file__), "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, f"{project_name}_{datetime.now():%Y%m%d_%H%M%S}.log")

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(log_file, encoding="utf-8"),
        ],
    )
    return logging.getLogger("agent")


# ── State Management ───────────────────────────────────────────────────────

def state_path(project_name: str) -> str:
    return project_state_path(project_name)


def _state_backup_path(path: str) -> str:
    return f"{path}.bak"


def _load_json_file(path: str, retries: int = 3, delay: float = 0.05) -> dict | None:
    last_error = None
    for attempt in range(retries + 1):
        try:
            with open(path, encoding="utf-8") as handle:
                raw = handle.read()
            if not raw.strip():
                raise json.JSONDecodeError("Empty JSON file", raw, 0)
            return json.loads(raw)
        except (OSError, json.JSONDecodeError) as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(delay * (attempt + 1))

    backup_path = _state_backup_path(path)
    if os.path.exists(backup_path):
        try:
            with open(backup_path, encoding="utf-8") as handle:
                raw = handle.read()
            if raw.strip():
                log.warning("Recovered state from backup after JSON read failure: %s", path)
                return json.loads(raw)
        except (OSError, json.JSONDecodeError):
            pass

    if isinstance(last_error, json.JSONDecodeError):
        log.warning("State file is unreadable JSON: %s", path)
        return None
    raise last_error


def _save_json_file(path: str, payload: dict):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    backup_path = _state_backup_path(path)
    fd, tmp_path = tempfile.mkstemp(prefix=".state_", suffix=".tmp", dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        if os.path.exists(path):
            try:
                shutil.copy2(path, backup_path)
            except OSError:
                pass
        os.replace(tmp_path, path)
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass


def load_state(project_name: str) -> dict | None:
    path = state_path(project_name)
    if os.path.exists(path):
        return _load_json_file(path)
    return None


def save_state(state: dict):
    path = state_path(state["project_name"])
    _save_json_file(path, state)


def _llm_runtime_snapshot() -> dict:
    snapshot = config.get_settings_snapshot()
    return {
        "provider": snapshot.get("llm_provider"),
        "base_url": snapshot.get("llm_base_url"),
        "enable_thinking": snapshot.get("llm_enable_thinking"),
        "reasoning_breakdown_only": snapshot.get("llm_reasoning_breakdown_only"),
        "reasoning_format": snapshot.get("llm_reasoning_format"),
        "creative_drafting_mode": snapshot.get("llm_creative_drafting_mode"),
        "model_creative": snapshot.get("ollama_model_creative"),
        "model_fast": snapshot.get("ollama_model_fast"),
    }


def create_state(project_name: str, brief: str) -> dict:
    return {
        "project_name": project_name,
        "brief": brief,
        "created_at": datetime.now().isoformat(),
        "llm_runtime": _llm_runtime_snapshot(),
        "total_scenes": 0,
        "scenes": [],
    }


# ── Model Management ────────────────────────────────────────────────────���

def _unload_model(log):
    """Unload the heavy creative model from VRAM to free it for ComfyUI."""
    from config import OLLAMA_MODEL_CREATIVE
    try:
        log.info("Unloading %s from VRAM to free GPU for ComfyUI...", OLLAMA_MODEL_CREATIVE)
        unload_model(OLLAMA_MODEL_CREATIVE, log)
        log.info("Model unloaded.")
    except Exception as e:
        log.warning("Could not unload model: %s (not critical)", e)


# ── Preflight Checks ──────────────────────────────────────────────────────

def launch_comfyui(log):
    """Launch ComfyUI in the background and wait until it's reachable."""
    import subprocess as _sp
    if not COMFYUI_LAUNCHER or not os.path.isfile(COMFYUI_LAUNCHER):
        raise RuntimeError(
            f"ComfyUI is not reachable at {COMFYUI_HOST} and the launcher '{COMFYUI_LAUNCHER}' "
            "does not exist. Start ComfyUI (Comfy Desktop) manually or fix 'comfyui_launcher' in Settings."
        )
    log.info("Launching ComfyUI: %s", COMFYUI_LAUNCHER)
    kwargs = {"cwd": os.path.dirname(COMFYUI_LAUNCHER)}
    if os.name == "nt":
        kwargs["creationflags"] = _sp.CREATE_NEW_PROCESS_GROUP
    _sp.Popen(COMFYUI_LAUNCHER, **kwargs)
    # Poll until the API responds
    client = ComfyUIClient()
    deadline = time.time() + COMFYUI_STARTUP_TIMEOUT
    while time.time() < deadline:
        if client.check_alive():
            log.info("ComfyUI is ready.")
            return
        time.sleep(3)
    raise RuntimeError(f"ComfyUI did not start within {COMFYUI_STARTUP_TIMEOUT}s")


def preflight(client: ComfyUIClient, log):
    """Verify everything is ready before starting. Auto-launches ComfyUI if needed."""
    # Check ComfyUI — launch if not running
    if not client.check_alive():
        log.info("ComfyUI is not running, launching automatically...")
        launch_comfyui(log)
    else:
        log.info("ComfyUI is running.")

    # Check LLM backend + model
    try:
        resolved_model = ensure_model_available(OLLAMA_MODEL)
        if resolved_model != OLLAMA_MODEL:
            log.info("%s reachable. Using model '%s' (requested '%s').",
                     provider_label(), resolved_model, OLLAMA_MODEL)
        else:
            log.info("%s model '%s' is available.", provider_label(), resolved_model)
    except RuntimeError:
        raise
    except Exception as e:
        raise RuntimeError(f"Cannot connect to {provider_label()}: {e}")

    if is_ollama_provider():
        from config import OLLAMA_MODEL_CREATIVE, OLLAMA_MODEL_FAST
        for role, name in (("creative", OLLAMA_MODEL_CREATIVE), ("fast", OLLAMA_MODEL_FAST)):
            if not is_model_installed(name):
                raise RuntimeError(
                    f"Ollama model '{name}' ({role}) is not installed. Run: ollama pull {name} "
                    "or change the model in Settings."
                )
        log.info("Ollama models OK: creative=%s, fast=%s", OLLAMA_MODEL_CREATIVE, OLLAMA_MODEL_FAST)

    # Check workflow template
    try:
        load_workflow_template()
        log.info("Workflow template loaded OK.")
    except FileNotFoundError:
        raise RuntimeError("workflow_template.json not found. Export from ComfyUI (API format).")

    # Check ffmpeg
    import subprocess
    try:
        subprocess.run([FFMPEG_PATH, "-version"], capture_output=True, check=True)
        log.info("FFmpeg is available at %s", FFMPEG_PATH)
    except FileNotFoundError:
        raise RuntimeError(f"FFmpeg not found at {FFMPEG_PATH}")


# ── Project Name ───────────────────────────────────────────────────────────

def slugify(text: str) -> str:
    """Turn a brief into a filesystem-safe project name."""
    slug = re.sub(r"[^\w\s-]", "", text.lower())
    slug = re.sub(r"[\s-]+", "_", slug)
    return slug[:50].strip("_")


# ── Main Pipeline ──────────────────────────────────────────────────────────

def run(brief: str, project_name: str, log, is_script: bool = False, lazy: bool = False, should_cancel=None):
    """Full autonomous pipeline."""
    state = load_state(project_name)
    if state:
        state.setdefault("project_name", project_name)
        state.setdefault("brief", brief)
        state.setdefault("scenes", [])
        state.setdefault("total_scenes", len(state.get("scenes", [])))
        state.setdefault("is_script", is_script)
        state.pop("cancel_requested_at", None)
        state.pop("cancelled_at", None)
        state["llm_runtime"] = _llm_runtime_snapshot()
        log.info("Resuming project '%s' (%d scenes planned)", project_name, state["total_scenes"])
    else:
        state = create_state(project_name, brief)
        state["is_script"] = is_script
        state.pop("cancel_requested_at", None)
        state.pop("cancelled_at", None)
        log.info("New project '%s'", project_name)

    def cancel_if_requested(where: str) -> bool:
        if should_cancel and should_cancel():
            state["cancelled_at"] = datetime.now().isoformat()
            save_state(state)
            log.info("Cancellation requested. Stopping pipeline at %s.", where)
            return True
        return False

    client = ComfyUIClient()
    client.connect()
    template = load_workflow_template()
    i2v_template = load_i2v_template()  # None if i2v_template.json not available

    if cancel_if_requested("startup"):
        client.disconnect()
        return

    # ── Phase 1: Scene Breakdown ───────────────────────────────────────
    if not state["scenes"]:
        log.info("=" * 60)
        log.info("PHASE 1: Scene Breakdown")
        log.info("=" * 60)
        scenes = director.breakdown(brief, force_script=state.get("is_script", False))
        state["scenes"] = scenes
        state["total_scenes"] = len(scenes)
        # Store character + voice + style descriptions from breakdown
        from director import (
            get_character_descriptions,
            get_last_planning_debug,
            get_location_anchors,
            get_story_world,
            get_voice_descriptions,
            get_style_anchor,
        )
        chars = get_character_descriptions()
        if chars:
            state["characters"] = chars
            log.info("Stored %d character descriptions.", len(chars))
        voices = get_voice_descriptions()
        if voices:
            state["voices"] = voices
            log.info("Stored %d voice descriptions.", len(voices))
        locations = get_location_anchors()
        if locations:
            state["locations"] = locations
            log.info("Stored %d location anchors.", len(locations))
        style = get_style_anchor()
        if style:
            state["style"] = style
            log.info("Stored style anchor: %s", style[:80])
        story_world = get_story_world()
        if story_world:
            state["story_world"] = story_world
            log.info("Stored story world anchor: %s", story_world[:100])
        planning_debug = get_last_planning_debug()
        if planning_debug:
            state["planning_debug"] = planning_debug
            log.info("Stored planning debug in state.json")
        save_state(state)
        log.info("Planned %d scenes.", len(scenes))

        # Unload the heavy creative model to free VRAM for ComfyUI
        _unload_model(log)
    else:
        scenes = state["scenes"]
        done = sum(1 for s in scenes if s.get("takes_done"))
        log.info("Resuming: %d/%d scenes have takes.", done, len(scenes))

    project_root = config.project_dir(project_name)
    characters = state.get("characters", {})

    # Restore character + voice + style descriptions into director module (needed for prompt writing on resume)
    if characters:
        director._current_characters = characters
    voices = state.get("voices", {})
    if voices:
        director._current_voices = voices
    locations = state.get("locations", {})
    if locations:
        director._current_locations = locations
    style = state.get("style", "")
    if style:
        director._current_style = style
    story_world = state.get("story_world", "")
    if story_world:
        director._current_story_world = story_world

    # ── Phase 2: Storyboard (keyframe generation) ─────────────────────
    if USE_KEYFRAMES and not state.get("storyboard_approved"):
        log.info("=" * 60)
        log.info("PHASE 2: Storyboard -- Keyframe Generation")
        log.info("=" * 60)

        from keyframe_gen import generate_keyframes

        for i, scene in enumerate(scenes):
            if cancel_if_requested(f"storyboard scene {scene.get('scene_number')}"):
                client.disconnect()
                return
            if scene.get("keyframe_candidates") and not scene.get("rejection_notes"):
                continue  # Already generated and not rejected

            scene_num = scene["scene_number"]
            log.info("-" * 40)
            log.info("Keyframes for scene %d/%d: %s",
                     scene_num, len(scenes), scene["description"][:80])
            chars_in = scene.get("characters_in_scene", [])
            if chars_in:
                log.info("  Characters: %s", ", ".join(chars_in))
            log.info("  Setting: %s", scene.get("setting_description", "not specified")[:80])
            log.info("  Shot: %s | Mood: %s", scene.get("shot_type", "?"), scene.get("mood", "?"))

            candidates = generate_keyframes(
                client, scene, characters, project_root, brief=brief
            )
            scene["keyframe_candidates"] = candidates
            scene.pop("rejection_notes", None)  # Clear rejection after regen
            save_state(state)

            # Log results
            passed = sum(1 for c in candidates if c.get("eval", {}).get("verdict") == "PASS")
            log.info("  %d/%d candidates passed AI eval", passed, len(candidates))

        state["storyboard_generated_at"] = datetime.now().isoformat()

        if lazy:
            # Auto-select best keyframe per scene (prefer PASS, then best eval)
            log.info("LAZY MODE: Auto-selecting best keyframes...")
            for scene in scenes:
                cands = scene.get("keyframe_candidates", [])
                generated = [c for c in cands if c.get("status") == "generated"]
                if not generated:
                    continue
                # Prefer PASS verdicts, then pick first available
                passed = [c for c in generated if c.get("eval", {}).get("verdict") == "PASS"]
                best = passed[0] if passed else generated[0]
                scene["selected_keyframe"] = best["path"]
                scene["keyframe_approved"] = True
                log.info("  Scene %d: auto-selected keyframe %d (%s)",
                         scene["scene_number"], best["candidate"],
                         best.get("eval", {}).get("verdict", "?"))
            state["storyboard_approved"] = True
            save_state(state)
        else:
            log.info("=" * 60)
            log.info("STORYBOARD COMPLETE -- Review and approve keyframes:")
            log.info("  python storyboard.py %s", project_name)
            log.info("Or use the GUI: python agent.py --gui")
            log.info("=" * 60)
            save_state(state)
            client.disconnect()
            return

    # ── Phase 3: Video Generation (multiple takes per scene) ──────────
    log.info("=" * 60)
    log.info("PHASE 3: Video Production (%d takes per scene)", TAKES_PER_SCENE)
    if i2v_template:
        log.info("  I2V workflow available — scenes with approved keyframes will use image-to-video")
    else:
        log.info("  No i2v_template.json found — using text-to-video for all scenes")
    log.info("=" * 60)

    scenes_dir = os.path.join(project_root, "scenes")
    os.makedirs(scenes_dir, exist_ok=True)

    import shutil
    for i, scene in enumerate(scenes):
        if cancel_if_requested(f"video scene {scene.get('scene_number')}"):
            client.disconnect()
            return
        if scene.get("takes_done"):
            continue

        scene_num = scene["scene_number"]
        log.info("-" * 40)
        log.info("Scene %d/%d: %s (%ds)",
                 scene_num, len(scenes), scene["description"][:60], scene["duration_seconds"])

        prev_scene = scenes[i - 1] if i > 0 else None

        # Write video prompt once per scene
        if getattr(config, "NO_DIALOGUE", False):
            director._strip_dialogue_from_scenes([scene])
        if not scene.get("ltx_prompt"):
            scene["ltx_prompt"] = director.write_prompt(scene, prev_scene, brief=brief)

        scene["status"] = "in_progress"
        scene["takes"] = scene.get("takes", [])
        save_state(state)

        frames = calc_frames(scene["duration_seconds"], LTX_FPS)

        # Check if this scene has an approved keyframe for i2v
        keyframe_path = scene.get("selected_keyframe")
        use_i2v = i2v_template and keyframe_path and os.path.exists(keyframe_path)
        uploaded_kf_name = None
        if use_i2v:
            log.info("  Using I2V mode with keyframe: %s", os.path.basename(keyframe_path))
            try:
                uploaded_kf_name = client.upload_image(keyframe_path)
            except Exception as e:
                log.warning("  Failed to upload keyframe: %s — falling back to T2V", e)
                use_i2v = False

        for take_num in range(1, TAKES_PER_SCENE + 1):
            if cancel_if_requested(f"scene {scene_num} before take {take_num}"):
                client.disconnect()
                return
            if take_num <= len(scene["takes"]):
                continue

            seed = random.randint(0, 2**32 - 1)
            log.info("  Take %d/%d: %d frames, seed %d (%s)",
                     take_num, TAKES_PER_SCENE, frames, seed,
                     "i2v" if use_i2v else "t2v")

            if use_i2v:
                workflow = build_i2v_workflow(
                    i2v_template, scene["ltx_prompt"], frames, seed, uploaded_kf_name
                )
            else:
                workflow = build_workflow(template, scene["ltx_prompt"], frames, seed)

            try:
                prompt_id = client.queue_prompt(workflow)
                history = client.wait_for_completion(prompt_id, timeout=900)
                raw_output = client.get_output_path(history)
            except (RuntimeError, TimeoutError) as e:
                log.error("  Take %d failed: %s", take_num, e)
                scene["takes"].append({"take": take_num, "status": "failed", "error": str(e)})
                save_state(state)
                time.sleep(5)
                continue

            take_filename = f"scene_{scene_num:03d}_take_{take_num}.mp4"
            take_path = os.path.join(scenes_dir, take_filename)
            shutil.copy2(raw_output, take_path)
            log.info("  Take %d saved: %s", take_num, take_path)

            scene["takes"].append({
                "take": take_num,
                "status": "generated",
                "path": take_path,
                "seed": seed,
            })
            save_state(state)

        scene["takes_done"] = True
        save_state(state)

    state["generation_completed_at"] = datetime.now().isoformat()

    if lazy:
        # Auto-select first successful take per scene and assemble
        log.info("=" * 60)
        log.info("LAZY MODE: Auto-selecting takes and assembling...")
        log.info("=" * 60)

        scene_paths = []
        for scene in scenes:
            takes = [t for t in scene.get("takes", []) if t.get("status") == "generated"]
            if takes:
                # Just pick the first good take
                scene["selected_take"] = takes[0]["path"]
                scene["status"] = "approved"
                scene_paths.append(takes[0]["path"])
                log.info("  Scene %d: auto-selected take %d", scene["scene_number"], takes[0]["take"])

        if scene_paths:
            final_dir = config.project_dir(project_name)
            final_path = os.path.join(final_dir, "final.mp4")
            assembler.concat_scenes(scene_paths, final_path)
            state["final_path"] = final_path
            state["completed_at"] = datetime.now().isoformat()

            total_dur = sum(s.get("duration_seconds", 0) for s in scenes)
            log.info("=" * 60)
            log.info("COMPLETE (LAZY MODE)")
            log.info("  Final film: %s", final_path)
            log.info("  Scenes: %d", len(scene_paths))
            log.info("  Duration: ~%ds (%.1f min)", total_dur, total_dur / 60)
            log.info("=" * 60)

        save_state(state)
        client.disconnect()
    else:
        log.info("=" * 60)
        log.info("ALL TAKES GENERATED")
        log.info("=" * 60)
        log.info("Run the reviewer to pick your favorite takes and assemble:")
        log.info("  python reviewer.py %s", project_name)

        save_state(state)
        client.disconnect()


# ── Test Single Scene ──────────────────────────────────────────────────────

def test_scene(description: str, log):
    """Generate and evaluate a single test scene."""
    client = ComfyUIClient()
    client.connect()
    template = load_workflow_template()

    # Parse duration from description if present (e.g. "10 seconds")
    dur_match = re.search(r"(\d+)\s*(?:sec|seconds?)", description, re.IGNORECASE)
    duration = int(dur_match.group(1)) if dur_match else 10

    scene = {
        "scene_number": 1,
        "description": description,
        "duration_seconds": duration,
        "shot_type": "establishing",
        "mood": "cinematic",
        "audio_notes": "ambient sound matching the scene",
        "continuity_notes": "none - test scene",
        "status": "pending",
    }

    prompt = director.write_prompt(scene)
    scene["ltx_prompt"] = prompt
    log.info("Prompt: %s", prompt)

    frames = calc_frames(duration, LTX_FPS)
    seed = random.randint(0, 2**32 - 1)
    log.info("Frames: %d, Seed: %d", frames, seed)

    workflow = build_workflow(template, prompt, frames, seed)
    prompt_id = client.queue_prompt(workflow)
    log.info("Queued: %s", prompt_id)

    history = client.wait_for_completion(prompt_id)
    output_path = client.get_output_path(history)
    log.info("Output: %s", output_path)

    eval_result = evaluator.evaluate_scene(output_path, scene)
    log.info("Eval: %s", json.dumps(eval_result, indent=2))

    client.disconnect()
    return output_path, eval_result


# ── CLI ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Video Director Agent")
    parser.add_argument("brief", nargs="?", help="The video brief / description")
    parser.add_argument("--project", "-p", help="Project name (default: auto from brief)")
    parser.add_argument("--resume", "-r", help="Resume a project by name")
    parser.add_argument("--test-scene", help="Test a single scene description")
    parser.add_argument("--model", "-m", help="Override Ollama model")
    parser.add_argument("--script", "-s", help="Path to a script/screenplay file to parse")
    parser.add_argument("--gui", action="store_true", help="Launch GUI mode")
    args = parser.parse_args()

    if args.gui:
        from gui import DirectorGUI
        app = DirectorGUI()
        app.run()
        return

    # Override model if specified
    if args.model:
        import config
        config.OLLAMA_MODEL = args.model
        director.OLLAMA_MODEL = args.model

    if args.test_scene:
        log = setup_logging("test_scene")
        log.info("Test scene mode: %s", args.test_scene)
        client = ComfyUIClient()
        preflight(client, log)
        test_scene(args.test_scene, log)
        return

    is_script = False
    if args.resume:
        project_name = args.resume
        state = load_state(project_name)
        if not state:
            print(f"No state found for project '{project_name}'")
            sys.exit(1)
        brief = state["brief"]
        log = setup_logging(project_name)
        log.info("Resuming project: %s", project_name)
    elif args.script:
        # Read script from file
        with open(args.script, encoding="utf-8") as f:
            brief = f.read()
        is_script = True
        project_name = args.project or slugify(os.path.basename(args.script))
        log = setup_logging(project_name)
        log.info("Script mode: loaded %d chars from %s", len(brief), args.script)
    elif args.brief:
        brief = args.brief
        project_name = args.project or slugify(brief)
        log = setup_logging(project_name)
    else:
        parser.print_help()
        sys.exit(1)

    client = ComfyUIClient()
    preflight(client, log)
    # Store is_script flag so breakdown knows to force script parsing
    run(brief, project_name, log, is_script=is_script)


if __name__ == "__main__":
    main()
