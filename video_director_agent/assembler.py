# assembler.py — FFmpeg concat of approved clips + final continuity eval

import os
import subprocess
import tempfile
import logging

import config
from config import (
    OLLAMA_MODEL_FAST, EVAL_TOKEN_BUDGET, FFMPEG_PATH, FFPROBE_PATH,
    FINAL_TRANSITION_ENABLED, FINAL_TRANSITION_DURATION,
)
from evaluator import extract_frames
from llm_client import chat as llm_chat

log = logging.getLogger(__name__)


def _probe_duration(path: str) -> float:
    result = subprocess.run(
        [
            FFPROBE_PATH, "-v", "quiet",
            "-print_format", "json",
            "-show_format",
            path,
        ],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        return 0.0
    import json
    try:
        data = json.loads(result.stdout)
        return float((data.get("format") or {}).get("duration") or 0.0)
    except Exception:
        return 0.0


def _has_audio_stream(path: str) -> bool:
    result = subprocess.run(
        [
            FFPROBE_PATH, "-v", "quiet",
            "-select_streams", "a",
            "-show_entries", "stream=index",
            "-of", "csv=p=0",
            path,
        ],
        capture_output=True, text=True,
    )
    return result.returncode == 0 and bool(result.stdout.strip())


def _concat_with_transitions(scene_paths: list[str], output_path: str, transition_duration: float):
    inputs = []
    for path in scene_paths:
        inputs.extend(["-i", path])

    durations = [_probe_duration(path) for path in scene_paths]
    if any(duration <= 0 for duration in durations):
        raise RuntimeError("Could not probe all clip durations for transition assembly")

    max_safe_transition = min(durations) / 3.0
    fade = max(0.05, min(float(transition_duration), max_safe_transition, 2.0))

    video_labels = [f"[{idx}:v]" for idx in range(len(scene_paths))]
    include_audio = all(_has_audio_stream(path) for path in scene_paths)
    audio_labels = [f"[{idx}:a]" for idx in range(len(scene_paths))] if include_audio else []
    filter_parts = []

    cumulative_duration = durations[0]
    current_video = video_labels[0]
    current_audio = audio_labels[0] if include_audio else None

    for idx in range(1, len(scene_paths)):
        next_video = video_labels[idx]
        out_video = f"[vx{idx}]"
        offset = max(0.0, cumulative_duration - fade)
        filter_parts.append(
            f"{current_video}{next_video}xfade=transition=fade:duration={fade:.3f}:offset={offset:.3f}{out_video}"
        )
        current_video = out_video
        if include_audio:
            next_audio = audio_labels[idx]
            out_audio = f"[ax{idx}]"
            filter_parts.append(
                f"{current_audio}{next_audio}acrossfade=d={fade:.3f}:c1=tri:c2=tri{out_audio}"
            )
            current_audio = out_audio
        cumulative_duration = cumulative_duration + durations[idx] - fade

    filter_complex = ";".join(filter_parts)

    cmd = [
        FFMPEG_PATH, "-y",
        *inputs,
        "-filter_complex", filter_complex,
        "-map", current_video,
        "-c:v", "libx264", "-crf", "18", "-preset", "fast",
        "-movflags", "+faststart",
    ]
    if include_audio and current_audio:
        cmd.extend(["-map", current_audio, "-c:a", "aac", "-b:a", "192k"])
    else:
        cmd.append("-an")
    cmd.append(output_path)
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr[:800])


def concat_scenes(
    scene_paths: list[str],
    output_path: str,
    transition_enabled: bool | None = None,
    transition_duration: float | None = None,
):
    """Concatenate scene clips using FFmpeg, optionally with short crossfades."""
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    transition_enabled = FINAL_TRANSITION_ENABLED if transition_enabled is None else transition_enabled
    transition_duration = FINAL_TRANSITION_DURATION if transition_duration is None else transition_duration

    if transition_enabled and len(scene_paths) >= 2:
        log.info(
            "Assembling %d clips into %s with %.2fs crossfades",
            len(scene_paths), output_path, transition_duration,
        )
        try:
            _concat_with_transitions(scene_paths, output_path, transition_duration)
            log.info("Final film assembled with transitions: %s", output_path)
            return
        except Exception as exc:
            log.warning("Transition assembly failed, falling back to hard cuts: %s", exc)

    # Write concat list to temp file
    list_file = os.path.join(tempfile.gettempdir(), "concat_list.txt")
    with open(list_file, "w") as f:
        for path in scene_paths:
            abs_path = os.path.abspath(path).replace("\\", "/")
            f.write(f"file '{abs_path}'\n")

    log.info("Concatenating %d clips into %s", len(scene_paths), output_path)

    # First try lossless copy
    result = subprocess.run(
        [
            FFMPEG_PATH, "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", list_file,
            "-c", "copy",
            output_path,
        ],
        capture_output=True, text=True,
    )

    if result.returncode != 0:
        log.warning("Lossless concat failed, re-encoding: %s", result.stderr[:200])
        # Fallback: re-encode to ensure compatibility
        subprocess.run(
            [
                FFMPEG_PATH, "-y",
                "-f", "concat",
                "-safe", "0",
                "-i", list_file,
                "-c:v", "libx264", "-crf", "18", "-preset", "fast",
                "-c:a", "aac", "-b:a", "192k",
                "-movflags", "+faststart",
                output_path,
            ],
            capture_output=True, text=True, check=True,
        )

    log.info("Final film assembled: %s", output_path)


def probe_clip(path: str) -> dict:
    """Get clip metadata via ffprobe."""
    result = subprocess.run(
        [
            FFPROBE_PATH, "-v", "quiet",
            "-print_format", "json",
            "-show_streams",
            path,
        ],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        return {}
    import json
    return json.loads(result.stdout)


def final_continuity_eval(final_path: str, scenes: list[dict]) -> dict:
    """Sample frames across the full assembled video and check pacing/continuity."""
    frames = extract_frames(final_path, fps_sample=0.5)  # 1 frame every 2 seconds

    # Subsample to ~30 frames for the full film eval
    if len(frames) > 30:
        step = len(frames) // 30
        frames = frames[::step][:30]

    scene_summary = "\n".join(
        f"  Scene {s['scene_number']}: {s['description']} ({s['duration_seconds']}s)"
        for s in scenes
    )

    eval_prompt = f"""You are reviewing a completed AI-generated film assembled from {len(scenes)} scenes.

Scene plan:
{scene_summary}

These frames are sampled across the full video. Evaluate:
1. PACING: Does the film flow well between scenes?
2. CONTINUITY: Are there jarring visual jumps between scenes?
3. OVERALL_QUALITY: General impression.
4. SUGGESTIONS: Any scenes that should be regenerated?

Respond ONLY with valid JSON:
{{"pacing": "good|fair|poor", "continuity": "good|fair|poor",
"overall_quality": "good|fair|poor",
"notes": "brief overall impression",
"problem_scenes": [list of scene numbers that need work, or empty list]}}"""

    log.info("Running final continuity eval on assembled film (%d frames)...", len(frames))
    response = llm_chat(
        model=OLLAMA_MODEL_FAST,
        messages=[{"role": "user", "content": eval_prompt, "images": frames}],
        options={
            "num_predict": EVAL_TOKEN_BUDGET,
            **config.llm_reasoning_options(for_breakdown=False),
        },
    )

    raw = response["message"]["content"].strip()
    try:
        start = raw.find("{")
        end = raw.rfind("}") + 1
        if start >= 0 and end > start:
            raw = raw[start:end]
        import json
        return json.loads(raw)
    except Exception:
        log.warning("Final eval parse failed: %s", raw[:200])
        return {"pacing": "unknown", "continuity": "unknown", "overall_quality": "unknown",
                "notes": raw[:200], "problem_scenes": []}
