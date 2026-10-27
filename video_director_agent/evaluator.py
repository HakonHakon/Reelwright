# evaluator.py — Video frame extraction + Gemma multimodal evaluation

import base64
import json
import logging
import cv2

import config
from config import OLLAMA_MODEL_FAST, EVAL_FRAME_SAMPLE_RATE, EVAL_MAX_FRAMES
from llm_client import chat as llm_chat

log = logging.getLogger(__name__)


def extract_frames(video_path: str, fps_sample: int = EVAL_FRAME_SAMPLE_RATE) -> list[str]:
    """Extract frames at sample rate, return list of base64 JPEG strings."""
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise FileNotFoundError(f"Cannot open video: {video_path}")

    video_fps = cap.get(cv2.CAP_PROP_FPS)
    interval = max(1, int(video_fps / fps_sample))
    frames_b64 = []
    frame_idx = 0

    while True:
        ret, frame = cap.read()
        if not ret:
            break
        if frame_idx % interval == 0:
            _, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
            frames_b64.append(base64.b64encode(buf).decode("utf-8"))
        frame_idx += 1

    cap.release()
    log.info("Extracted %d frames from %s", len(frames_b64), video_path)
    return frames_b64


def evaluate_scene(video_path: str, scene: dict, prev_scene_path: str = None) -> dict:
    """Evaluate a generated video clip against the scene spec.

    Returns dict with: motion_quality, prompt_match, subject_consistency,
    continuity, verdict, fail_reason, retry_suggestion
    """
    frames = extract_frames(video_path, fps_sample=2)  # 2fps for better coverage

    # Cap frames but keep more than before for thorough review
    max_frames = min(EVAL_MAX_FRAMES, 30)
    if len(frames) > max_frames:
        step = len(frames) // max_frames
        frames = frames[::step][:max_frames]

    # Prepend last 3 frames from previous scene for continuity check
    continuity_note = ""
    if prev_scene_path:
        try:
            prev_frames = extract_frames(prev_scene_path, fps_sample=2)
            frames = prev_frames[-3:] + frames
            continuity_note = "The first 3 frames are from the PREVIOUS scene for continuity comparison. "
        except Exception as e:
            log.warning("Could not load prev scene for continuity: %s", e)

    # Build dialogue context
    dialogue = scene.get("dialogue", "")
    dialogue_section = ""
    if dialogue:
        dialogue_section = f"""
Expected dialogue: {dialogue}
- Does the character appear to be speaking/moving their mouth?
- Does the speaking cadence look natural for the dialogue length?"""

    eval_prompt = f"""{continuity_note}You are a STRICT quality control reviewer for AI-generated video clips.
Your job is to REJECT bad clips so they get regenerated. Do NOT rubber-stamp everything as PASS.
A clip that "looks okay" is NOT good enough — it must actually match what was requested.

SCENE REQUIREMENTS:
- Description: {scene['description']}
- Expected mood: {scene['mood']}
- Expected shot type: {scene['shot_type']}
- Continuity requirements: {scene.get('continuity_notes', 'none')}
- Audio/sound notes: {scene.get('audio_description', scene.get('audio_notes', 'none'))}
- Action: {scene.get('action_description', 'none')}{dialogue_section}

EVALUATE EACH CATEGORY — score as "good", "fair", or "poor":

1. SUBJECT_MATCH: Is the correct subject/character shown? Right appearance, clothing, setting?
   - FAIL if: wrong person, wrong setting, missing key elements from description

2. MOTION_QUALITY: Scan ALL frames in sequence. Is motion smooth and natural?
   - FAIL if: frozen/static frames, limbs bending wrong, face melting, flickering, teleporting objects

3. SUBJECT_CONSISTENCY: Does the subject look the SAME across all frames? Compare first, middle, and last frames.
   - FAIL if: face changes between frames, clothing changes color/style, body proportions shift

4. SHOT_TYPE: Does the camera angle match what was requested?
   - FAIL if: requested close-up but got wide shot, or vice versa

5. CONTINUITY: If previous scene frames are provided, does this scene connect visually?
   - FAIL if: completely different setting, lighting, or character appearance from previous scene

VERDICT RULES:
- ANY category scored "poor" = FAIL
- TWO OR MORE categories scored "fair" = FAIL
- Only PASS if the clip genuinely matches the scene requirements

Think through each category carefully before giving your verdict.

Respond with valid JSON:
{{"subject_match": "good|fair|poor", "motion_quality": "good|fair|poor",
"subject_consistency": "good|fair|poor", "shot_type_match": "good|fair|poor",
"continuity": "good|fair|poor", "verdict": "PASS|FAIL",
"fail_reason": "specific description of what's wrong, or null if PASS",
"retry_suggestion": "specific changes to make in the prompt to fix the issue, or null if PASS"}}"""

    log.info("Evaluating scene %d (%d frames, strict mode)...", scene["scene_number"], len(frames))
    response = llm_chat(
        model=OLLAMA_MODEL_FAST,
        messages=[{"role": "user", "content": eval_prompt, "images": frames}],
        options={
            "num_predict": 1024,   # More tokens for thorough reasoning
            "num_ctx": 8192,
            "temperature": 0.3,    # Low temp for consistent, analytical evaluation
            **config.llm_reasoning_options(for_breakdown=False),
        },
    )

    raw = response["message"]["content"].strip()
    try:
        result = _parse_eval_json(raw)
    except (ValueError, json.JSONDecodeError):
        log.warning("Eval returned invalid JSON, defaulting to FAIL for re-review: %s", raw[:300])
        result = {
            "subject_match": "unknown",
            "motion_quality": "unknown",
            "subject_consistency": "unknown",
            "shot_type_match": "unknown",
            "continuity": "unknown",
            "verdict": "FAIL",
            "fail_reason": "Evaluation failed to parse — clip needs manual review or regeneration",
            "retry_suggestion": "Simplify the prompt and try again",
        }

    # Enforce verdict rules programmatically in case the model is still too lenient
    result = _enforce_verdict_rules(result)

    log.info("Scene %d eval: %s — %s", scene["scene_number"], result["verdict"],
             result.get("fail_reason") or "all checks passed")
    return result


def _enforce_verdict_rules(result: dict) -> dict:
    """Programmatically enforce FAIL conditions even if the model said PASS."""
    categories = ["subject_match", "motion_quality", "subject_consistency",
                   "shot_type_match", "continuity"]

    poor_count = sum(1 for c in categories if result.get(c) == "poor")
    fair_count = sum(1 for c in categories if result.get(c) == "fair")

    should_fail = False
    reasons = []

    if poor_count > 0:
        should_fail = True
        poor_cats = [c for c in categories if result.get(c) == "poor"]
        reasons.append(f"poor scores in: {', '.join(poor_cats)}")

    if fair_count >= 2:
        should_fail = True
        fair_cats = [c for c in categories if result.get(c) == "fair"]
        reasons.append(f"multiple fair scores in: {', '.join(fair_cats)}")

    if should_fail and result.get("verdict") == "PASS":
        log.info("Overriding model PASS → FAIL due to: %s", "; ".join(reasons))
        result["verdict"] = "FAIL"
        existing_reason = result.get("fail_reason") or ""
        result["fail_reason"] = f"Auto-failed: {'; '.join(reasons)}. {existing_reason}".strip()
        if not result.get("retry_suggestion"):
            result["retry_suggestion"] = "Rephrase prompt to address the weak categories"

    return result


def _parse_eval_json(raw: str) -> dict:
    """Parse eval JSON, handling markdown fences and stray text."""
    if "```" in raw:
        lines = raw.split("\n")
        cleaned = []
        inside = False
        for line in lines:
            if line.strip().startswith("```"):
                inside = not inside
                continue
            if inside:
                cleaned.append(line)
        raw = "\n".join(cleaned)

    # Try to find JSON object in the response
    start = raw.find("{")
    end = raw.rfind("}") + 1
    if start >= 0 and end > start:
        raw = raw[start:end]

    result = json.loads(raw)
    # Normalize verdict
    result["verdict"] = result.get("verdict", "FAIL").upper()
    if result["verdict"] not in ("PASS", "FAIL"):
        result["verdict"] = "FAIL"
    return result
