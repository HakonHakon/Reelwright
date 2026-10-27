# keyframe_gen.py -- Keyframe image generation + AI evaluation via Z-Image Turbo

import json
import logging
import os
import random
import shutil
import base64
import copy
import ast
import re

import config
from comfyui_client import resolve_or_fetch_output
from config import (
    COMFYUI_OUTPUT_DIR, OLLAMA_MODEL_CREATIVE, OLLAMA_MODEL_FAST,
    KF_PROMPT_NODE_ID, KF_PROMPT_INPUT_NAME, KF_SEED_NODE_ID, KF_LATENT_NODE_ID,
    KF_CANDIDATES, KF_WIDTH, KF_HEIGHT, SKIP_KF_EVAL, ZIT_DIRECT_PROMPT,
)
from llm_client import chat as llm_chat

log = logging.getLogger(__name__)

_KEYFRAME_ACCEPT_MIN_WORDS = 45
_KEYFRAME_ACCEPT_MAX_WORDS = 240
_KEYFRAME_PROMPT_POLICY_VERSION = "zit-immutable-core-refiner-v5"

KEYFRAME_PROMPT_SYSTEM = """You are a cinematic keyframe prompt refiner for Z-Image Turbo.

Write ONE objective still-image prompt only. Internally identify the immutable visual core before writing:
subject identity, quantity, required action, body pose, hand placement, gaze, object contact, object state, location, and essential continuity details.

Rules:
- The final prompt must begin with the immutable visual core: what happens in the image, who does it, and what physical contact or pose makes it readable
- Convert directorial instructions into concrete still-image language; do not copy words like must, always, every scene, soll, muss, or in jeder Szene
- Preserve the scene's story beat without adding new story events, props, characters, locations, symbols, or text-bearing objects
- Keep clothing, body, hair, and accessories consistent, but do not let optional details control framing or replace the action
- Improve composition, setting, lighting, texture, color, depth, and cinematic clarity only after the action is clear
- Do not write abstract intent, motivation, subtext, dialogue function, or emotional analysis; show posture, expression, object state, spatial relation, and motion cues instead
- Do not describe visible filming equipment, optical glass, a photographer, an operator, or a visible observer; for POV, use an unseen foreground viewpoint
- Do not include dialogue quotes, captions, subtitles, speech bubbles, labels, signs, UI text, poster text, readable writing, markdown, JSON, labels, or meta-tags
- If reasoning is enabled, keep it internal and output only the final image prompt paragraph
- Aim for 70-140 words, one dense paragraph, concrete and image-model-ready
"""


_KEYFRAME_META_MARKERS = (
    "style lock",
    "prompt priorities",
    "immutable visual core",
    "framing support",
    "setting / light",
    "style support",
    "body / hair continuity",
    "wardrobe continuity",
    "character focus",
    "action focus",
    "environment",
    "establishing",
    "hero moment",
    "scene description",
    "shot type",
    "subject:",
    "framing:",
    "style:",
    "lighting:",
    "constraints",
    "single frame?",
    "one prompt only?",
    "drafting the final",
    "refining",
    "final polish",
    "wait,",
    "the prompt says",
    "check against",
    "the prompt is ready",
)

_VISUAL_CUE_HINTS = (
    "hand", "hands", "finger", "arm", "shoulder", "torso", "waist", "hip",
    "leg", "legs", "knee", "foot", "feet", "gaze", "eyes", "looking",
    "stare", "expression", "smile", "smirk", "mouth", "head", "tilt",
    "lean", "posture", "pose", "seated", "standing", "sitting", "chair",
    "glass", "cup", "water", "coffee", "phone", "door", "table", "fabric",
    "gown", "jacket", "prop", "object", "rests", "touches", "holds",
    "grips", "points", "reaches", "pulls", "pushes", "raises", "lowers",
    "turns", "shadow", "light", "sweat", "hair", "flower", "planter",
)

_VISIBLE_ACTION_TERMS = (
    "raises", "raised", "raising", "lifts", "lifted", "lifting", "lowers",
    "lowered", "reaches", "reaching", "touches", "touching", "holds",
    "holding", "grips", "gripping", "rests", "resting", "leans", "leaning",
    "turns", "turning", "tilts", "tilted", "looks", "looking", "gaze",
    "stares", "smiles", "smirk", "points", "pointing", "adjusts",
    "adjusting", "pulls", "pulling", "pushes", "pushing", "sits", "sitting",
    "stands", "standing", "steps", "walking", "hand", "hands", "fingers",
    "arm", "arms", "eyes", "head", "shoulder", "torso", "knees", "feet",
    "glass", "cup", "door", "chair", "table", "phone", "prop contact",
    "body pose",
)

_USER_ACTION_PROP_HINTS = (
    "hold", "holds", "holding", "grab", "grabs", "grabbing", "carry", "carries",
    "carrying", "point", "points", "pointing", "touch", "touches", "touching",
    "reach", "reaches", "reaching", "pick", "picks", "picking", "take", "takes",
    "taking", "drink", "drinks", "drinking", "sip", "sips", "sipping", "eat",
    "eats", "eating", "enter", "enters", "entering", "sit", "sits", "sitting",
    "stand", "stands", "standing", "walk", "walks", "walking", "run", "runs",
    "running", "dance", "dances", "dancing", "lean", "leans", "leaning",
    "glass", "cup", "mug", "coffee", "water", "phone", "book", "bag", "door",
    "chair", "table", "umbrella", "weapon", "flower", "cigarette", "bottle",
    "hält", "halten", "trägt", "tragen", "zeigt", "zeigen", "fasst", "greift",
    "nimmt", "nehmen", "trinkt", "trinken", "isst", "essen", "kommt rein",
    "betritt", "setzt sich", "sitzt", "steht", "läuft", "geht", "rennt",
    "tanzt", "lehnt", "glas", "tasse", "kaffee", "wasser", "telefon", "buch",
    "tasche", "tür", "stuhl", "tisch", "schirm", "blume", "flasche",
)

_USER_REQUIREMENT_MARKERS = (
    "must", "always", "every scene", "each scene", "in every scene", "throughout",
    "visible", "show", "shows", "appear", "appears", "holding", "holds", "wearing",
    "wears", "doing", "does", "walking", "sitting", "standing", "talking",
    "muss", "immer", "jede szene", "jeder szene", "in jeder szene", "ständig",
    "sichtbar", "zeigen", "zeige", "auftauchen", "taucht", "hält", "trägt",
    "macht", "tut", "sitzt", "steht", "läuft", "geht",
)

_VISUAL_REQUIREMENT_STOPWORDS = {
    "with", "that", "this", "from", "into", "through", "every", "scene", "each",
    "must", "always", "visible", "there", "their", "woman", "person", "story",
    "create", "write", "fictional", "film", "filmed", "duration", "minutes",
    "seconds", "language", "talks", "speaks", "german", "english", "french",
    "eine", "einer", "einem", "einen", "jeder", "jede", "szene", "immer",
    "sichtbar", "film", "sprache", "deutsch", "englisch", "französisch",
}

_ABSTRACT_STORY_HINTS = (
    "intent", "purpose", "subtext", "obstacle", "wants", "want", "desire",
    "motivation", "relationship", "tension", "conflict", "decision",
    "reveal", "escalation", "payoff", "story", "dialogue", "spoken",
    "asks", "requests", "tries to", "trying to", "needs to", "means to",
    "anliegen", "absicht", "will ", "möchte", "subtext", "spannung",
)

_FORBIDDEN_INTENT_PATTERNS = (
    r"\btries to\b", r"\btrying to\b", r"\bwants to\b", r"\bintends to\b",
    r"\bthe purpose\b", r"\bpurpose is\b", r"\bintent is\b", r"\bsubtext\b",
    r"\bdialogue intent\b", r"\bdialogue obstacle\b", r"\bdas anliegen\b",
    r"\banliegen ist\b", r"\bdie absicht\b", r"\babsicht ist\b",
    r"\bm[oö]chte\b", r"\bwill ein(?:e|en|em|er)?\b",
)

_WARDROBE_TERMS = (
    "wears", "wearing", "worn", "dressed", "outfit", "wardrobe", "attire",
    "clothing", "costume", "uniform", "jacket", "coat", "blazer", "shirt",
    "t-shirt", "tee", "top", "blouse", "sweater", "hoodie", "dress", "skirt",
    "pants", "trousers", "jeans", "shorts", "leggings", "bra", "corset",
    "vest", "tie", "scarf", "hat", "cap", "gloves", "belt", "boots", "shoes",
    "heels", "sneakers", "sandals", "leather", "lace", "denim", "silk",
)

_FOOTWEAR_TERMS = (
    "boots", "boot", "shoes", "shoe", "heels", "heel", "sneakers", "sneaker",
    "sandals", "sandals", "pumps", "high-heeled", "highheel", "highheels",
    "footwear", "turnschuhe", "schuhe", "stiefel",
)

_BODY_HAIR_TERMS = (
    "age", "year-old", "woman", "man", "girl", "boy", "person", "body",
    "build", "built", "height", "tall", "short", "statuesque", "slender",
    "athletic", "muscular", "curvy", "round", "soft", "broad", "narrow",
    "legs", "leg", "thighs", "thigh", "hips", "hip", "waist", "chest",
    "shoulders", "torso", "arms", "hands", "skin", "skin tone", "face",
    "facial", "cheekbones", "cheeks", "eyes", "nose", "mouth", "jaw",
    "hair", "hairstyle", "ponytail", "braid", "curls", "curly", "straight",
    "blonde", "brown", "brunette", "black-haired", "red-haired", "gray",
    "grey", "bald", "beard", "mustache", "moustache", "scars", "scar",
    "tattoo", "freckles", "wrinkles",
)


def _contains_wardrobe_term(text: str) -> bool:
    for term in _WARDROBE_TERMS:
        pattern = r"(?<![a-z0-9])" + re.escape(term.lower()) + r"(?![a-z0-9])"
        if re.search(pattern, text):
            return True
    return False


def _contains_footwear_term(text: str) -> bool:
    for term in _FOOTWEAR_TERMS:
        pattern = r"(?<![a-z0-9])" + re.escape(term.lower()) + r"(?![a-z0-9])"
        if re.search(pattern, text):
            return True
    return False


def _contains_body_hair_term(text: str) -> bool:
    for term in _BODY_HAIR_TERMS:
        pattern = r"(?<![a-z0-9])" + re.escape(term.lower()) + r"(?![a-z0-9])"
        if re.search(pattern, text):
            return True
    return False


def _crop_to_video_ar(image_path: str, target_w: int = 1024, target_h: int = 432):
    """Crop a square image to the video's widescreen aspect ratio (center crop)."""
    from PIL import Image as PILImage
    img = PILImage.open(image_path)
    w, h = img.size

    # Calculate crop box for target aspect ratio
    target_ar = target_w / target_h  # e.g. 2.37:1
    current_ar = w / h

    if current_ar < target_ar:
        # Image is too tall, crop top and bottom
        new_h = int(w / target_ar)
        top = (h - new_h) // 2
        box = (0, top, w, top + new_h)
    else:
        # Image is too wide, crop sides
        new_w = int(h * target_ar)
        left = (w - new_w) // 2
        box = (left, 0, left + new_w, h)

    cropped = img.crop(box)
    cropped.save(image_path)


def load_keyframe_template(path: str = None) -> dict:
    if path is None:
        path = os.path.join(os.path.dirname(__file__), "keyframe_template.json")
    with open(path) as f:
        return json.load(f)


def _detect_keyframe_nodes(wf: dict) -> dict:
    """Auto-detect keyframe workflow node IDs from the template."""
    detected = {
        "prompt": KF_PROMPT_NODE_ID,
        "seed": KF_SEED_NODE_ID,
        "latent": KF_LATENT_NODE_ID,
    }

    all_present = all(k in wf for k in [KF_PROMPT_NODE_ID, KF_SEED_NODE_ID, KF_LATENT_NODE_ID])
    if all_present:
        return detected

    log.info("Default keyframe node IDs not found — auto-detecting...")

    # Find prompt node (CLIPTextEncode)
    clip_nodes = [nid for nid, n in wf.items()
                  if isinstance(n, dict) and n.get("class_type") == "CLIPTextEncode"]
    if clip_nodes:
        detected["prompt"] = clip_nodes[0]

    # Find seed node (KSampler or KSamplerAdvanced)
    for cls in ["KSampler", "KSamplerAdvanced", "RandomNoise"]:
        seed_nodes = [nid for nid, n in wf.items()
                      if isinstance(n, dict) and n.get("class_type") == cls]
        if seed_nodes:
            detected["seed"] = seed_nodes[0]
            break

    # Find latent node (EmptySD3LatentImage, EmptyLatentImage, etc.)
    for cls in ["EmptySD3LatentImage", "EmptyLatentImage", "EmptyImage"]:
        latent_nodes = [nid for nid, n in wf.items()
                        if isinstance(n, dict) and n.get("class_type") == cls]
        if latent_nodes:
            detected["latent"] = latent_nodes[0]
            break

    log.info("Auto-detected keyframe node IDs: %s", detected)
    return detected


def _set_node_text_input(wf: dict, node_id: str, input_name: str, value: str):
    node = wf.get(node_id)
    if not isinstance(node, dict):
        raise KeyError(f"Keyframe prompt node '{node_id}' not found in workflow")
    inputs = node.setdefault("inputs", {})
    field = (input_name or "text").strip() or "text"
    if field not in inputs and "text" in inputs:
        log.warning(
            "Configured keyframe prompt input '%s' not found on node %s; falling back to 'text'.",
            field,
            node_id,
        )
        field = "text"
    inputs[field] = value


def build_keyframe_workflow(template: dict, prompt_text: str, seed: int,
                            width: int = 1024, height: int = 432) -> dict:
    """Build a keyframe image workflow with the given prompt and seed.
    Auto-detects node IDs if the configured defaults don't match the template."""
    wf = copy.deepcopy(template)
    nodes = _detect_keyframe_nodes(wf)
    _set_node_text_input(wf, nodes["prompt"], KF_PROMPT_INPUT_NAME, prompt_text)
    wf[nodes["seed"]]["inputs"]["seed"] = seed
    wf[nodes["latent"]]["inputs"]["width"] = width
    wf[nodes["latent"]]["inputs"]["height"] = height
    return wf


def get_image_output_path(history: dict, output_dir: str | None = None) -> str:
    """Extract image path from ComfyUI history."""
    output_dir = output_dir or COMFYUI_OUTPUT_DIR
    outputs = history.get("outputs", {})
    for node_id, node_output in outputs.items():
        if "images" in node_output:
            for item in node_output["images"]:
                if not isinstance(item, dict):
                    continue
                filename = item.get("filename", "")
                if filename.endswith((".png", ".jpg", ".jpeg", ".webp")):
                    subfolder = item.get("subfolder", "")
                    return resolve_or_fetch_output(
                        output_dir, subfolder, filename, item.get("type", "output"))
    raise ValueError("No image output found in history")


def image_to_base64(image_path: str) -> str:
    """Read an image file and return base64 string."""
    with open(image_path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def evaluate_keyframe(image_path: str, scene: dict, characters: dict) -> dict:
    """Thoroughly evaluate a keyframe image against the scene + character descriptions.

    Returns dict with scores and verdict.
    """
    img_b64 = image_to_base64(image_path)

    # Build character checklist
    chars_in_scene = scene.get("characters_in_scene", [])
    char_checklist = ""
    if chars_in_scene and characters:
        char_checklist = "\n\nCHARACTER CHECKLIST -- verify EACH of these against the image:\n"
        for char_id in chars_in_scene:
            desc = characters.get(char_id, "")
            if desc:
                char_checklist += f"\n{char_id}:\n{desc}\n"
                char_checklist += "Check: face shape? skin tone? hair color/style? clothing match? age range? build?\n"

    eval_prompt = f"""You are a STRICT storyboard quality control reviewer. Your job is to REJECT images that don't match the character descriptions or scene requirements. Do NOT approve mediocre images.

SCENE REQUIREMENTS:
- Description: {scene['description']}
- Shot type: {scene.get('shot_type', 'not specified')}
- Setting: {scene.get('setting_description', 'not specified')}
- Lighting: {scene.get('lighting_description', 'not specified')}
- Mood: {scene.get('mood', 'not specified')}
{char_checklist}

EVALUATE EACH CATEGORY -- score as "good", "fair", or "poor":

1. CHARACTER_ACCURACY: Does each character match their description?
   - Compare face, skin tone, hair, age, build against the description POINT BY POINT
   - Is the clothing exactly right? (color, style, accessories)
   - FAIL if: wrong face features, wrong hair color, wrong clothing, wrong age range
   - FAIL if: the person is clearly not who they're supposed to be

2. SETTING_ACCURACY: Does the environment match?
   - Background, room type, props, furniture
   - FAIL if: completely wrong location or setting

3. COMPOSITION: Is the shot type correct?
   - Camera angle, framing, subject placement
   - FAIL if: requested close-up but got wide shot, etc.

4. LIGHTING_MOOD: Does the lighting/atmosphere match?
   - Color temperature, shadow direction, mood
   - Minor variations are OK

5. IMAGE_QUALITY: Technical quality
   - No distorted faces, extra limbs, text artifacts, blurriness
   - FAIL if: deformed features, extra fingers, melted faces

VERDICT RULES:
- CHARACTER_ACCURACY "poor" = automatic FAIL (this is the most important)
- ANY other category "poor" = FAIL
- TWO or more "fair" = FAIL

Think carefully about each point before scoring.

Respond with valid JSON:
{{"character_accuracy": "good|fair|poor", "setting_accuracy": "good|fair|poor",
"composition": "good|fair|poor", "lighting_mood": "good|fair|poor",
"image_quality": "good|fair|poor", "verdict": "PASS|FAIL",
"fail_reason": "specific description of what's wrong, or null",
"character_notes": "what specifically matches or doesn't match the character description"}}"""

    response = llm_chat(
        model=OLLAMA_MODEL_FAST,
        messages=[{"role": "user", "content": eval_prompt, "images": [img_b64]}],
        options={
            "num_predict": 1024,
            "temperature": 0.2,  # Very analytical
            **config.llm_reasoning_options(for_breakdown=False),
        },
    )

    raw = response["message"]["content"].strip()
    try:
        # Find JSON in response
        start = raw.find("{")
        end = raw.rfind("}") + 1
        if start >= 0 and end > start:
            result = json.loads(raw[start:end])
        else:
            raise ValueError("No JSON found")

        result["verdict"] = result.get("verdict", "FAIL").upper()
        if result["verdict"] not in ("PASS", "FAIL"):
            result["verdict"] = "FAIL"

        # Enforce rules programmatically
        result = _enforce_keyframe_rules(result)
        return result

    except (json.JSONDecodeError, ValueError):
        log.warning("Keyframe eval parse failed: %s", raw[:200])
        return {
            "character_accuracy": "unknown", "setting_accuracy": "unknown",
            "composition": "unknown", "lighting_mood": "unknown",
            "image_quality": "unknown", "verdict": "FAIL",
            "fail_reason": "Evaluation failed to parse",
            "character_notes": None,
        }


def _enforce_keyframe_rules(result: dict) -> dict:
    """Programmatically enforce FAIL conditions."""
    categories = ["character_accuracy", "setting_accuracy", "composition",
                   "lighting_mood", "image_quality"]

    # Character accuracy poor = instant fail
    if result.get("character_accuracy") == "poor":
        result["verdict"] = "FAIL"
        if not result.get("fail_reason"):
            result["fail_reason"] = "Character doesn't match description"

    poor_count = sum(1 for c in categories if result.get(c) == "poor")
    fair_count = sum(1 for c in categories if result.get(c) == "fair")

    if poor_count > 0 or fair_count >= 2:
        if result.get("verdict") == "PASS":
            reasons = []
            if poor_count:
                poor_cats = [c for c in categories if result.get(c) == "poor"]
                reasons.append(f"poor: {', '.join(poor_cats)}")
            if fair_count >= 2:
                fair_cats = [c for c in categories if result.get(c) == "fair"]
                reasons.append(f"multiple fair: {', '.join(fair_cats)}")
            result["verdict"] = "FAIL"
            result["fail_reason"] = f"Auto-failed: {'; '.join(reasons)}"

    return result


def _rewrite_keyframe_prompt(scene: dict, fail_reasons: list[str], original_prompt: str, brief: str = "") -> tuple[str, dict]:
    """Ask the LLM to rewrite the image prompt based on what kept failing."""
    reasons_text = "\n".join(f"  - Attempt {i+1}: {r}" for i, r in enumerate(fail_reasons))
    user_visual_requirements = _extract_user_visual_requirements(brief)
    user_visual_prompt = _image_model_user_requirement_text(user_visual_requirements)
    user_visual_block = (
        "\nDIRECTORIAL USER REQUIREMENT TO TRANSLATE AND KEEP VISIBLE:\n"
        f"{user_visual_requirements}\n"
        f"Useful still-image fallback: {user_visual_prompt or user_visual_requirements}\n"
        if user_visual_requirements else ""
    )

    rewrite_request = f"""The following image prompt was used to generate keyframe images for a scene, but it FAILED evaluation {len(fail_reasons)} times in a row.

ORIGINAL PROMPT:
{original_prompt}

FAILURE REASONS:
{reasons_text}
{user_visual_block}

SCENE DESCRIPTION: {scene['description']}

Rewrite the prompt to fix these issues. Focus on:
- The scene description is the primary source of truth
- Translate mandatory user requirements into concrete visible still-frame language, then keep those facts in the opening sentence
- Do NOT copy raw instruction words like must, always, every scene, soll, muss, or in jeder Szene into the rewritten prompt
- Do NOT invent new story beats, characters, props, locations, or actions beyond the provided scene context
- Explicitly preserve the requested shot type/framing in the rewritten prompt
- Explicitly preserve the requested visual style in the rewritten prompt
- If characters didn't match: emphasize their physical description more prominently
- If setting was wrong: be more explicit about the environment
- If composition was bad: specify the camera angle and framing more clearly
- Simplify overly complex descriptions that the image model can't handle
- Keep it to one clear moment — don't describe sequential actions
- This is a SILENT storyboard image, not a video/audio prompt
- Do NOT include dialogue quotes, captions, subtitles, speech bubbles, lower thirds, or any readable text
- Return one concise, image-model-friendly paragraph rather than a checklist or scratchpad

Respond with ONLY the rewritten prompt text, nothing else."""

    response = llm_chat(
        model=OLLAMA_MODEL_FAST,
        messages=[{"role": "user", "content": rewrite_request}],
        options={
            "num_predict": 2048,
            "temperature": 0.5,
            **config.llm_reasoning_options(for_breakdown=False),
        },
    )
    rewritten = response["message"]["content"].strip()
    return rewritten, {
        "prompt_policy_version": _KEYFRAME_PROMPT_POLICY_VERSION,
        "rewrite_request": rewrite_request,
        "raw_response": rewritten,
        "fail_reasons": fail_reasons,
    }


_LEGACY_KEYFRAME_PROMPT_SUFFIX = (
    "\n\nSTORYBOARD FRAME RULES:\n"
    "- This is a single silent storyboard frame for an image model.\n"
    "- Show only the visual moment, composition, characters, setting, lighting, and action.\n"
    "- Do NOT render subtitles, captions, speech bubbles, lower thirds, signs, readable labels, or any other readable text.\n"
    "- Do NOT show written dialogue anywhere in the image.\n"
)


def _current_zit_direct_prompt() -> str:
    return str(ZIT_DIRECT_PROMPT or "").strip()


def _append_zit_direct_prompt(prompt_body: str, direct_prompt: str | None = None) -> str:
    body = _base_prompt_without_suffix(prompt_body)
    extra = str(_current_zit_direct_prompt() if direct_prompt is None else direct_prompt).strip()
    if not extra:
        return body
    if extra in body:
        return body
    if body:
        return f"{body}\n\n{extra}".strip()
    return extra


def _finalize_keyframe_prompt(prompt_body: str, direct_prompt: str | None = None) -> str:
    body = _sanitize_visible_camera_terms_for_zit(prompt_body)
    body = _append_zit_direct_prompt(body, direct_prompt=direct_prompt)
    return body


def _style_for_keyframe(brief: str = "") -> str:
    from director import get_style_anchor

    style = (get_style_anchor() or "").strip()
    if style:
        return style
    if "pixar" in brief.lower() or "animation" in brief.lower() or "anime" in brief.lower():
        return brief.strip()
    return "Photorealistic cinematic still frame, natural lighting, realistic textures, grounded composition"


def _location_for_keyframe(scene: dict) -> tuple[str, str]:
    from director import get_location_anchors

    location_id = (scene.get("location_id") or "").strip()
    if not location_id:
        return "", ""
    anchor = (get_location_anchors() or {}).get(location_id, "").strip()
    return location_id, anchor


def _is_pov_only_composition_lock(brief: str = "") -> bool:
    from director import get_composition_lock

    lock = _normalize_prompt_text(get_composition_lock(brief))
    return lock == "keep a pov viewer angle looking at the subject"


def _keyframe_bias_controls() -> dict:
    from director import get_prompt_bias_controls

    return get_prompt_bias_controls()


def _brief_explicitly_requests_closeup(brief: str = "") -> bool:
    lowered = (brief or "").lower()
    return any(term in lowered for term in ("close-up", "close up", "closeup", "extreme close", "macro shot"))


def _avoid_face_closeups_for_keyframe(brief: str = "") -> bool:
    controls = _keyframe_bias_controls()
    if _brief_explicitly_requests_closeup(brief):
        return False
    return controls.get("action_focus", 0) >= 55 or controls.get("environment_weight", 0) >= 40 or controls.get("character_focus", 0) <= 65


def _keyframe_framing_guidance(scene: dict, brief: str = "") -> str:
    controls = _keyframe_bias_controls()
    guidance = []
    if controls.get("action_focus", 0) >= 60:
        guidance.append("Frame the character with enough upper body, arms, hands, and touched props visible so the physical action reads clearly.")
    if controls.get("environment_weight", 0) >= 40:
        guidance.append("Keep a recognizable slice of the location in frame.")
    if controls.get("establishing_shot_bias", 0) >= 55:
        guidance.append("Prefer a wider composition that shows the character in the surrounding place.")
    elif _avoid_face_closeups_for_keyframe(brief):
        guidance.append("Use an immersive medium foreground viewpoint or waist-up composition with hands, props, and background context visible.")
    if controls.get("character_focus", 0) <= 55:
        guidance.append("The person remains important, but the frame should also show pose, gesture, props, and spatial context.")
    return " ".join(dict.fromkeys(guidance))


def _looks_like_face_closeup(text: str) -> bool:
    normalized = _normalize_prompt_text(text)
    close_terms = ("close-up", "close up", "closeup", "very close", "face close", "close to the lens", "close toward the lens", "leaning in close")
    face_terms = ("face", "eyes", "portrait", "lens")
    return any(term in normalized for term in close_terms) and any(term in normalized for term in face_terms)


def _soften_closeup_language(text: str, brief: str = "") -> str:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    if not text or not _avoid_face_closeups_for_keyframe(brief):
        return text
    replacements = (
        (r"\bslow\s+zoom\s+in\s+to\s+a\s+close-up\b", "medium POV hold with upper body and hands visible"),
        (r"\bsettling\s+into\s+a\s+close-up\b", "settling into a medium POV frame"),
        (r"\bstatic\s+close-up\b", "static medium POV frame"),
        (r"\bclose-up\s+on\s+her\s+face\b", "medium POV frame with her face, upper body, hands, and props visible"),
        (r"\bclose-up\s+on\s+his\s+face\b", "medium POV frame with his face, upper body, hands, and props visible"),
        (r"\bclose-up\b", "medium POV frame"),
        (r"\bclose up\b", "medium POV frame"),
        (r"\bcloseup\b", "medium POV frame"),
        (r"\bher\s+face\s+very\s+close\s+to\s+the\s+lens\b", "her upper body leaning toward the foreground"),
        (r"\bhis\s+face\s+very\s+close\s+to\s+the\s+lens\b", "his upper body leaning toward the foreground"),
        (r"\bface\s+very\s+close\s+to\s+the\s+lens\b", "upper body leaning toward the foreground"),
        (r"\bleaning\s+in\s+close\s+to\s+the\s+lens\b", "leaning toward the foreground while upper body and hands remain visible"),
        (r"\bmoving\s+closer\s+to\s+the\s+camera\b", "leaning slightly toward the foreground in a medium immersive frame"),
    )
    for pattern, replacement in replacements:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", text).strip(" ,.;")


def _sanitize_visible_camera_terms_for_zit(text: str) -> str:
    """Avoid ZiT rendering a literal camera/lens while preserving POV intent."""
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    if not text:
        return text

    replacements = (
        (r"\bwith\s+static\s+POV\s+Preserve\s+the\s+POV\s+viewer\s+angle\.?\s*", "Static immersive foreground-view composition. "),
        (r"\bwith\s+([^.;]*?)\s+Preserve\s+the\s+POV\s+viewer\s+angle\.?\s*", r"\1. "),
        (r"\bPreserve\s+the\s+POV\s+viewer\s+angle\.?\s*", ""),
        (r"\bfrom the perspective of a viewer sitting ([^.,;]+)", r"from an unseen seated foreground viewpoint \1"),
        (r"\bfrom the viewer's perspective\b", "from an unseen foreground viewpoint"),
        (r"\bfrom the perspective of a seated viewer\b", "from an unseen seated foreground viewpoint"),
        (r"\bviewer sitting\b", "unseen foreground position"),
        (r"\bviewer\b", "unseen foreground presence"),
        (r"\bcamera lens\b", "foreground viewpoint"),
        (r"\bthe lens\b", "the foreground"),
        (r"\blens\b", "foreground viewpoint"),
        (r"\blooks directly at the camera\b", "looks directly toward the foreground"),
        (r"\blooking directly at the camera\b", "looking directly toward the foreground"),
        (r"\blooking at the camera\b", "looking toward the foreground"),
        (r"\btoward the camera\b", "toward the foreground"),
        (r"\btoward the lens\b", "toward the foreground"),
        (r"\bpointing at the camera\b", "pointing toward the foreground"),
        (r"\bpoints at the camera\b", "points toward the foreground"),
        (r"\bhand reaching toward the camera\b", "hand reaching toward the foreground"),
        (r"\breaches toward the camera\b", "reaches toward the foreground"),
        (r"\bextended directly toward the camera\b", "extended directly toward the foreground"),
        (r"\bcamera movement\b", "viewpoint movement"),
        (r"\bcamera angle\b", "viewpoint angle"),
        (r"\bcamera timing\b", "viewpoint timing"),
        (r"\bcamera\b", "viewpoint"),
        (r"\bPOV viewer angle\b", "immersive foreground viewpoint"),
        (r"\bPOV angle\b", "immersive foreground viewpoint"),
        (r"\bPOV shot\b", "immersive foreground-view shot"),
        (r"\bPOV medium shot\b", "medium immersive foreground-view shot"),
        (r"\bMedium POV shot\b", "Medium immersive foreground-view shot"),
        (r"\bstatic POV\b", "static immersive foreground view"),
        (r"\bCinematic POV style\b", "cinematic first-person style"),
        (r"\bPOV cinematic style\b", "cinematic first-person style"),
        (r"\bPOV style\b", "first-person style"),
    )
    for pattern, replacement in replacements:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    text = re.sub(r"\bwith\s+([^.;]*?)\s+Preserve the immersive foreground viewpoint\.?", r"\1.", text, flags=re.IGNORECASE)
    text = re.sub(r"\bPreserve the immersive foreground viewpoint\.?", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\bmedium POV frame\b", "medium immersive foreground-view frame", text, flags=re.IGNORECASE)
    text = re.sub(r"\bPOV frame\b", "immersive foreground-view frame", text, flags=re.IGNORECASE)
    text = re.sub(r"\bPOV unseen foreground presence\b", "unseen foreground presence", text, flags=re.IGNORECASE)
    text = re.sub(r"\bPOV unseen foreground presence angle\b", "immersive foreground viewpoint", text, flags=re.IGNORECASE)
    text = re.sub(r"\bunseen foreground presence angle\b", "immersive foreground viewpoint", text, flags=re.IGNORECASE)
    text = re.sub(r"\bPOV immersive foreground viewpoint\b", "immersive foreground viewpoint", text, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", text).strip(" ,.;")


def _zit_safe_visual_text(text: str) -> str:
    return _sanitize_visible_camera_terms_for_zit(text)


def _clean_legacy_static_framing(text: str, brief: str = "") -> str:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    if not text or not _is_pov_only_composition_lock(brief):
        return text
    text = re.sub(
        r"\bstatic\s+pov\s+shot\s+with\s+original\s+medium/seated\s+framing\b",
        "POV shot",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\bstatic\s+camera;\s*preserve\s+original\s+framing,\s*subject\s+scale,\s*and\s*pov\s+angle\.?",
        "",
        text,
        flags=re.IGNORECASE,
    )
    return re.sub(r"\s+", " ", text).strip(" ,.;")


def _keyframe_shot_type(scene: dict, brief: str = "") -> str:
    shot_type = _clean_legacy_static_framing(scene.get("shot_type") or "cinematic", brief) or "cinematic"
    if _avoid_face_closeups_for_keyframe(brief) and "close" in shot_type.lower():
        if "pov" in (brief or "").lower() or "pov" in shot_type.lower():
            return "medium POV shot"
        return "medium shot"
    return shot_type


def _keyframe_camera_action(scene: dict, brief: str = "") -> str:
    return _clean_legacy_static_framing(scene.get("camera_action") or "", brief)


def _keyframe_prompt_context(scene: dict, characters: dict, brief: str = "") -> str:
    from director import (
        _compact_anchor,
        get_composition_lock,
        get_prompt_bias_section,
        _scene_camera_action,
        _scene_comic_hook,
        _scene_hero_moment,
        _scene_object_interaction,
        _scene_pose_details,
        _scene_subject_action,
    )

    style = _style_for_keyframe(brief)
    composition_lock = get_composition_lock(brief)
    if _is_pov_only_composition_lock(brief):
        composition_lock = ""
    location_id, location_anchor = _location_for_keyframe(scene)
    hero_moment = _zit_safe_visual_text(_soften_closeup_language(_visible_only_field(_scene_hero_moment(scene)), brief))
    subject_action = _zit_safe_visual_text(_soften_closeup_language(_visible_only_field(_scene_subject_action(scene)), brief))
    visible_action_anchor = _zit_safe_visual_text(_infer_visible_action_from_scene(scene))
    camera_action = _zit_safe_visual_text(_soften_closeup_language(_keyframe_camera_action(scene, brief) or _scene_camera_action(scene), brief))
    comic_hook = _scene_comic_hook(scene)
    pose_details = _zit_safe_visual_text(_soften_closeup_language(_scene_pose_details(scene), brief))
    object_interaction = _zit_safe_visual_text(_soften_closeup_language(_scene_object_interaction(scene), brief))
    visual_cues = _zit_safe_visual_text(_keyframe_visual_continuity_cues(scene))
    user_visual_requirements = _extract_user_visual_requirements(brief)
    user_visual_prompt = _image_model_user_requirement_text(user_visual_requirements)
    body_hair_lock = _keyframe_body_hair_lock(scene, characters)
    wardrobe_lock = _keyframe_wardrobe_lock(scene, characters, prose=True, brief=brief)
    framing_guidance = _keyframe_framing_guidance(scene, brief)
    lines = []

    core_lines = []
    if user_visual_requirements:
        core_lines.append(
            "user requirement translated into visible image facts: "
            f"{(user_visual_prompt or user_visual_requirements).rstrip('.')}"
        )
    if subject_action:
        core_lines.append(f"main subject action / pose: {subject_action}")
    if visible_action_anchor and visible_action_anchor != subject_action and visible_action_anchor != hero_moment:
        core_lines.append(f"mandatory action anchor: {visible_action_anchor}")
    if object_interaction:
        core_lines.append(f"object contact / prop state: {object_interaction}")
    if pose_details:
        core_lines.append(f"body pose / gaze / expression: {pose_details}")
    if hero_moment:
        core_lines.append(f"best frozen instant: {hero_moment}")
    visible_description = _zit_safe_visual_text(_keyframe_visible_scene_description(scene, hero_moment, subject_action))
    if visible_description:
        core_lines.append(f"visible scene context: {visible_description}")
    if comic_hook:
        core_lines.append(f"specific memorable visible detail: {_zit_safe_visual_text(comic_hook)}")
    action = _zit_safe_visual_text(_soften_closeup_language(_visible_only_field(scene.get("action_description") or ""), brief))
    if action and action != subject_action:
        core_lines.append(f"additional physical action: {action}")
    if core_lines:
        lines.append(
            "IMMUTABLE VISUAL CORE (final prompt begins with these facts; preserve action before style):\n"
            + "\n".join(f"- {line}" for line in core_lines)
        )

    viewpoint_lines = [
        f"shot / framing: {_keyframe_shot_type(scene, brief)}",
        f"mood: {(scene.get('mood') or 'natural').strip()}",
    ]
    if framing_guidance:
        viewpoint_lines.append(f"action-readable composition: {framing_guidance}")
    if camera_action:
        viewpoint_lines.append(f"viewpoint energy to imply: {_sanitize_visible_camera_terms_for_zit(camera_action)}")
        lines.append("FRAMING SUPPORT (do not render visible filming equipment):\n" + "\n".join(f"- {line}" for line in viewpoint_lines))

    setting = (scene.get("setting_description") or "").strip()
    lighting = (scene.get("lighting_description") or "").strip()
    place_lines = []
    if setting:
        place_lines.append(f"scene setting: {setting}")
    if location_anchor:
        place_lines.append(f"canonical location texture: {_compact_anchor(location_anchor, 45)}")
    if lighting:
        place_lines.append(f"lighting: {lighting}")
    if place_lines:
        lines.append("SETTING / LIGHT (supporting context, not the opening):\n" + "\n".join(f"- {line}" for line in place_lines))

    if visual_cues:
        lines.append(
            "VISUAL CONTINUITY CUES (concrete image facts only):\n"
            f"{visual_cues}"
        )

    lines.append(get_prompt_bias_section())
    if style:
        lines.append(f"STYLE SUPPORT (apply after the visual core is clear):\n{style}")
    if composition_lock:
        lines.append(
            "COMPOSITION / POSE LOCK (supporting continuity, do not mention a visible camera):\n"
            f"{_sanitize_visible_camera_terms_for_zit(composition_lock)}"
        )

    continuity = _zit_safe_visual_text((scene.get("continuity_notes") or "").strip())
    if continuity and continuity.lower() != "none":
        lines.append(f"CONTINUITY:\n{continuity}")

    if body_hair_lock:
        lines.append(
            "BODY / HAIR CONTINUITY (preserve after the action core; do not let it replace the pose/action):\n"
            f"{body_hair_lock}"
        )

    if wardrobe_lock:
        lines.append(
            "WARDROBE CONTINUITY (keep consistent, but do not let clothing dominate framing unless action-relevant):\n"
            f"{wardrobe_lock}"
        )

    chars_in_scene = scene.get("characters_in_scene", []) or []
    if chars_in_scene:
        char_lines = []
        for char_id in chars_in_scene:
            desc = (characters or {}).get(char_id, "").strip()
            if desc:
                char_lines.append(f"{char_id}: {desc}")
            else:
                char_lines.append(f"{char_id}: physically specific realistic character design")
        lines.append("CHARACTERS:\n" + "\n".join(char_lines))

    lines.append(
        "IMPORTANT:\n"
        "- single still storyboard frame\n"
        "- internally identify the immutable visual core before writing\n"
        "- final prompt opens with action, pose, object contact, and subject identity before style or worldbuilding\n"
        "- translate directorial user requirements into picture language before writing the final prompt\n"
        "- translated user visuals outrank style, beauty, atmosphere, wardrobe, and generic portrait composition\n"
        "- never pass raw instruction words like must, always, every scene, soll, muss, or in jeder Szene to ZiT\n"
        "- preserve style, location, body, hair, wardrobe, and composition as supporting details after the core action is clear\n"
        "- keep accessories and clothing consistent without letting them force full-body fashion framing unless action-relevant\n"
        "- prioritize the hero moment, visible subject pose, and object contact over generic environment coverage\n"
        "- the image should make the main action legible in one frame\n"
        "- obey the ZiT framing / action guidance; do not collapse the frame into a face-only close-up when action or environment is prioritized\n"
        "- the final prompt must include the mandatory visible action anchor when provided\n"
        "- describe visible physical facts, not what the character intends, wants, says, or means\n"
        "- turn requests or dialogue goals into visible gestures, object contact, gaze, or body position\n"
        "- preserve exact hand placement, gaze direction, limb positioning, and prop contact when provided\n"
        "- preserve explicit body shape, facial structure, handedness, and other bodily traits from the context exactly\n"
        "- if a comic_hook or oddly specific detail is provided, keep it visibly in the frame instead of smoothing it away\n"
        "- no visible filming equipment, optical glass, photographer, operator, or visible observer\n"
        "- no speech bubbles\n"
        "- no captions or subtitles\n"
        "- no comic panels\n"
        "- no readable text"
    )
    return "\n\n".join(lines)


def _base_prompt_without_suffix(prompt: str) -> str:
    return prompt.split(_LEGACY_KEYFRAME_PROMPT_SUFFIX, 1)[0].strip()


def _strip_code_fences(text: str) -> str:
    cleaned = (text or "").strip()
    cleaned = re.sub(r"^\s*```(?:json|text)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```\s*$", "", cleaned)
    return cleaned.strip()


def _extract_keyframe_prompt_text(raw: str) -> str | None:
    raw = (raw or "").strip()
    if not raw:
        return None

    cleaned = _strip_code_fences(raw)
    text_candidate = re.sub(
        r'^\s*(?:prompt|storyboard prompt|image prompt)\s*:\s*',
        "",
        cleaned,
        flags=re.IGNORECASE,
    ).strip()
    if len(text_candidate) >= 2 and text_candidate[0] == text_candidate[-1] and text_candidate[0] in {"'", '"'}:
        try:
            literal = ast.literal_eval(text_candidate)
            if isinstance(literal, str) and literal.strip():
                return _base_prompt_without_suffix(literal.strip())
        except (ValueError, SyntaxError):
            text_candidate = text_candidate[1:-1].strip()
    if text_candidate:
        return _base_prompt_without_suffix(text_candidate)
    return None


def _looks_like_keyframe_meta(text: str) -> bool:
    lowered = (text or "").strip().lower()
    if not lowered:
        return True
    return any(marker in lowered for marker in _KEYFRAME_META_MARKERS)


def _looks_like_visual_cue(text: str) -> bool:
    clean = re.sub(r"\s+", " ", (text or "").lower()).strip()
    lowered = f" {clean} "
    if not lowered.strip():
        return False
    abstract_hits = sum(1 for hint in _ABSTRACT_STORY_HINTS if hint in lowered)
    visual_hits = sum(1 for hint in _VISUAL_CUE_HINTS if hint in lowered)
    return visual_hits > 0 and abstract_hits <= 1


def _contains_prompt_term(text: str, term: str) -> bool:
    lowered = (text or "").lower()
    clean_term = (term or "").lower().strip()
    if not clean_term:
        return False
    if " " in clean_term:
        return clean_term in lowered
    return bool(re.search(r"(?<![\w-])" + re.escape(clean_term) + r"(?![\w-])", lowered))


def _contains_user_requirement_marker(text: str) -> bool:
    return any(_contains_prompt_term(text, marker) for marker in _USER_REQUIREMENT_MARKERS)


def _contains_user_action_or_prop_hint(text: str) -> bool:
    return any(_contains_prompt_term(text, hint) for hint in _USER_ACTION_PROP_HINTS)


def _has_forbidden_intent_language(text: str) -> bool:
    lowered = re.sub(r"\s+", " ", (text or "").lower()).strip()
    return any(re.search(pattern, lowered) for pattern in _FORBIDDEN_INTENT_PATTERNS)


def _visible_only_field(text: str) -> str:
    clean = re.sub(r"\s+", " ", str(text or "")).strip()
    if clean and _has_forbidden_intent_language(clean):
        return ""
    return clean


def _extract_user_visual_requirements(brief: str = "", max_words: int = 70) -> str:
    """Extract user-specified visual/action requirements that ZiT must not lose.

    This intentionally focuses on hard user wishes and visible actions/props. Body
    and hair continuity are handled by their own lock so ZiT does not turn every
    frame into an appearance checklist.
    """
    clean = re.sub(r"\s+", " ", str(brief or "")).strip()
    if not clean:
        return ""

    chunks = re.split(r"(?<=[.!?])\s+|[\n\r]+|(?<=;)\s+", clean)
    kept = []
    for chunk in chunks:
        chunk = chunk.strip(" .;")
        if not chunk:
            continue
        lowered = chunk.lower()
        has_requirement_marker = _contains_user_requirement_marker(chunk)
        has_action_or_prop = _contains_user_action_or_prop_hint(chunk)
        is_optional_footwear = _contains_footwear_term(lowered) and any(
            term in lowered
            for term in (
                "sometimes", "occasionally", "optional", "unimportant", "not important",
                "manchmal", "gelegentlich", "unwichtig", "nicht wichtig",
            )
        )
        if is_optional_footwear:
            continue
        is_visual = (
            has_requirement_marker
            or has_action_or_prop
            or _contains_wardrobe_term(lowered)
        )
        is_nonvisual_runtime = any(term in lowered for term in ("duration", "minutes", "seconds", "language", "sprache"))
        if is_visual and not is_nonvisual_runtime:
            kept.append(chunk)

    text = ". ".join(kept)
    words = text.split()
    if len(words) > max_words:
        text = " ".join(words[:max_words]).rstrip(",;:")
    return text.strip()


def _user_visual_requirement_keywords(requirements: str) -> list[str]:
    keywords = []
    for token in re.findall(r"[A-Za-zÄÖÜäöüß0-9-]+", requirements or ""):
        lowered = token.lower()
        if len(lowered) < 3 or lowered in _VISUAL_REQUIREMENT_STOPWORDS:
            continue
        if lowered not in keywords:
            keywords.append(lowered)
    return keywords[:12]


def _prompt_satisfies_user_visual_requirements(prompt: str, requirements: str) -> bool:
    keywords = _user_visual_requirement_keywords(requirements)
    if not keywords:
        return True
    normalized = _normalize_prompt_text(prompt)
    hits = sum(1 for keyword in keywords if _normalize_prompt_text(keyword) in normalized)
    if len(keywords) <= 2:
        return hits >= len(keywords)
    return hits >= max(2, int(len(keywords) * 0.45))


def _image_model_user_requirement_text(requirements: str) -> str:
    """Turn user instructions into positive still-image facts for ZiT."""
    text = re.sub(r"\s+", " ", str(requirements or "")).strip()
    if not text:
        return ""
    text = re.sub(r"\b(she)\s+must\s+hold\b", r"\1 holds", text, flags=re.IGNORECASE)
    text = re.sub(r"\b(he)\s+must\s+hold\b", r"\1 holds", text, flags=re.IGNORECASE)
    text = re.sub(r"\b(they)\s+must\s+hold\b", r"\1 hold", text, flags=re.IGNORECASE)
    text = re.sub(r"\b(she)\s+must\s+point\b", r"\1 points", text, flags=re.IGNORECASE)
    text = re.sub(r"\b(he)\s+must\s+point\b", r"\1 points", text, flags=re.IGNORECASE)
    text = re.sub(r"\b(they)\s+must\s+point\b", r"\1 point", text, flags=re.IGNORECASE)
    text = re.sub(r"\b(she|he)\s+must\s+sit\b", r"\1 sits", text, flags=re.IGNORECASE)
    text = re.sub(r"\b(she|he)\s+must\s+stand\b", r"\1 stands", text, flags=re.IGNORECASE)
    text = re.sub(
        r"\bno\s+(?:face\s+)?close[- ]?ups?\b",
        "medium framing with hands, props, and body action visible",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\bkeine?\s+(?:gesichts)?nahaufnahmen?\b",
        "mittlere Kadrierung mit sichtbaren Händen, Requisiten und Körperaktion",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"^\s*(?:in\s+)?(?:every|each)\s+scene\s+", "", text, flags=re.IGNORECASE)
    text = re.sub(r"^\s*in\s+jeder\s+szene\s+", "", text, flags=re.IGNORECASE)
    text = re.sub(r"^\s*(?:soll|sollen|muss|müssen)\s+", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\b([A-ZÄÖÜ][\wÄÖÜäöüß-]+)\s+(?:muss|soll)\s+", r"\1 ", text)
    text = re.sub(
        r"(^|\bund\s+)([A-ZÄÖÜ][\wÄÖÜäöüß-]+)\s+([^.;,]+?)\s+im\s+Gesicht\s+haben\b",
        r"\1\2 hat \3 im Gesicht",
        text,
    )
    text = re.sub(r"\b(eine?\s+[^.;,]+?)\s+im\s+([^.;,]+?)\s+sitzen\b", r"\1 sitzt im \2", text, flags=re.IGNORECASE)
    text = re.sub(r"\bkommt rein\b", "tritt sichtbar durch die Tür", text, flags=re.IGNORECASE)
    text = re.sub(r"\bsetzt sich\b", "sitzt gerade auf einem Stuhl", text, flags=re.IGNORECASE)
    text = re.sub(r"\bnimmt einen Schluck Kaffee\b", "hält eine Kaffeetasse am Mund", text, flags=re.IGNORECASE)
    text = re.sub(r"\b(she|he)\s+holds\b([^.;]+?)\s+and\s+point\b", r"\1 holds\2 and points", text, flags=re.IGNORECASE)
    text = re.sub(r"\bmust\s+be\s+visible\b", "is clearly visible", text, flags=re.IGNORECASE)
    text = re.sub(r"\bmust\s+appear\b", "appears", text, flags=re.IGNORECASE)
    text = re.sub(r"\bmust\s+be\b", "is", text, flags=re.IGNORECASE)
    text = re.sub(r"\bmust\s+", "", text, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", text).strip(" .;:")


def _prompt_frontloads_user_visual_requirements(prompt: str, requirements: str) -> bool:
    if not requirements:
        return True
    first_words = " ".join(str(prompt or "").split()[:35])
    return _prompt_satisfies_user_visual_requirements(first_words, requirements)


def _enforce_user_visual_requirements(prompt_body: str, brief: str = "") -> str:
    prompt_body = re.sub(r"\s+", " ", str(prompt_body or "")).strip()
    requirements = _extract_user_visual_requirements(brief)
    if not prompt_body or not requirements:
        return prompt_body
    if _prompt_frontloads_user_visual_requirements(prompt_body, requirements):
        return prompt_body
    visible_requirements = _image_model_user_requirement_text(requirements) or requirements
    sentence = f"Visible required image detail: {visible_requirements.rstrip('.')}."
    return f"{sentence} {prompt_body}".strip()


def _keyframe_visual_continuity_cues(scene: dict) -> str:
    """Keep ZiT continuity concrete; avoid story-purpose and dialogue-intent language."""
    candidates = []
    for key in ("continuity_notes", "new_information_or_turn", "callback_or_setup"):
        value = re.sub(r"\s+", " ", str(scene.get(key, "") or "")).strip()
        if not value or value.lower() == "none":
            continue
        if _looks_like_visual_cue(value):
            candidates.append(value.rstrip("."))

    seen = set()
    kept = []
    for candidate in candidates:
        normalized = candidate.lower()
        if normalized in seen:
            continue
        seen.add(normalized)
        kept.append(candidate)
    return "; ".join(kept[:3])


def _keyframe_visible_scene_description(scene: dict, hero_moment: str = "", subject_action: str = "") -> str:
    description = re.sub(r"\s+", " ", str(scene.get("description", "") or "")).strip()
    if description and not _has_forbidden_intent_language(description):
        return description
    fallback_parts = [
        str(hero_moment or "").strip(),
        str(subject_action or "").strip(),
        str(scene.get("action_description", "") or "").strip(),
    ]
    return " ".join(part.rstrip(".") + "." for part in fallback_parts if part).strip()


def _infer_visible_action_from_scene(scene: dict) -> str:
    """Convert weak intent fields into concrete still-image action anchors for ZiT."""
    fields = (
        scene.get("hero_moment", ""),
        scene.get("subject_action", ""),
        scene.get("action_description", ""),
        scene.get("pose_details", ""),
        scene.get("object_interaction", ""),
        scene.get("description", ""),
        scene.get("dialogue_intent", ""),
        scene.get("dialogue_obstacle", ""),
        scene.get("new_information_or_turn", ""),
    )
    joined = re.sub(r"\s+", " ", " ".join(str(v or "") for v in fields)).strip()
    lowered = joined.lower()

    for value in fields[:5]:
        clean = re.sub(r"\s+", " ", str(value or "")).strip()
        if clean and _looks_like_visual_cue(clean):
            return clean

    if any(term in lowered for term in ("glass", "water", "wasser", "drink", "order", "bestell")):
        return "One hand is lifted in a small requesting gesture, eyes looking off-frame toward service, with an empty glass or drink-related prop near the other hand."
    if any(term in lowered for term in ("coffee", "kaffee", "cup", "mug")):
        return "One hand rests near a coffee cup while the character's gaze and raised fingers signal a small request or reaction."
    if any(term in lowered for term in ("enter", "enters", "kommt rein", "walks in", "door")):
        return "The character is frozen at the doorway or threshold, one hand near the doorframe, body angled into the room."
    if any(term in lowered for term in ("sit", "sits", "setzt", "chair", "stuhl", "sessel")):
        return "The character is seated with a clear body angle, hands placed on the chair or nearby surface, gaze directed toward the scene partner."
    if any(term in lowered for term in ("hold", "holds", "holding", "hält", "trägt", "hand")):
        return "The relevant object is visibly held or touched, with fingers wrapped around it and the character's gaze connected to that object."
    if any(term in lowered for term in ("ask", "asks", "request", "bittet", "fragt", "wants", "möchte", "anliegen")):
        return "The character uses a small readable gesture: one hand slightly raised, eyes directed toward the other person, posture leaning into the request."
    return "The character is frozen in a clear readable gesture, with visible hand placement, gaze direction, body angle, and object contact tied to the scene beat."


def _has_visible_action_language(text: str) -> bool:
    clean = re.sub(r"\s+", " ", (text or "").lower()).strip()
    lowered = f" {clean} "
    return any(term in lowered for term in _VISIBLE_ACTION_TERMS)


def _optional_footwear_guidance(brief: str = "") -> str:
    from director import get_optional_footwear_guidance

    return get_optional_footwear_guidance(brief)


def _remove_optional_footwear_clauses(sentence: str) -> str:
    parts = re.split(r",\s+|\s+;\s+|\s+\band\b\s+", sentence)
    kept = []
    for part in parts:
        clean = part.strip(" .")
        if not clean:
            continue
        if _contains_footwear_term(clean.lower()):
            continue
        kept.append(clean)
    if not kept:
        return ""
    return ", ".join(kept).strip()


def _extract_wardrobe_from_description(desc: str, brief: str = "") -> str:
    """Pull clothing-specific sentences out of a character anchor for ZiT continuity."""
    desc = re.sub(r"\s+", " ", (desc or "")).strip()
    if not desc:
        return ""

    optional_footwear = bool(_optional_footwear_guidance(brief))
    wardrobe_parts = []
    for sentence in re.split(r"(?<=[.!?])\s+", desc):
        sentence = sentence.strip()
        if not sentence:
            continue
        lowered = sentence.lower()
        if _contains_wardrobe_term(lowered):
            if optional_footwear and _contains_footwear_term(lowered):
                sentence = _remove_optional_footwear_clauses(sentence)
                if not sentence:
                    continue
            wardrobe_parts.append(sentence.rstrip("."))

    if not wardrobe_parts:
        return ""

    text = ". ".join(wardrobe_parts)
    footwear_note = _optional_footwear_guidance(brief)
    if footwear_note:
        text = f"{text}. {footwear_note}"
    words = text.split()
    if len(words) > 80:
        text = " ".join(words[:80]).rstrip(",;:")
    return text.strip()


def _extract_body_hair_from_description(desc: str) -> str:
    """Pull body, face, and hairstyle facts out of a character anchor."""
    desc = re.sub(r"\s+", " ", (desc or "")).strip()
    if not desc:
        return ""

    appearance_parts = []
    for sentence in re.split(r"(?<=[.!?])\s+", desc):
        sentence = sentence.strip()
        if not sentence:
            continue
        lowered = sentence.lower()
        if _contains_body_hair_term(lowered) and not _contains_wardrobe_term(lowered):
            appearance_parts.append(sentence.rstrip("."))

    if not appearance_parts:
        first_sentences = re.split(r"(?<=[.!?])\s+", desc)[:2]
        appearance_parts = [s.strip().rstrip(".") for s in first_sentences if s.strip()]

    text = ". ".join(appearance_parts)
    words = text.split()
    if len(words) > 85:
        text = " ".join(words[:85]).rstrip(",;:")
    return text.strip()


def _character_display_name(char_id: str) -> str:
    return " ".join(part.capitalize() for part in re.split(r"[_\-\s]+", str(char_id)) if part)


def _character_names_for_scene(scene: dict) -> str:
    names = [_character_display_name(char_id) for char_id in scene.get("characters_in_scene", []) or []]
    return ", ".join(name for name in names if name)


def _keyframe_body_hair_lock(scene: dict, characters: dict, *, prose: bool = False) -> str:
    locks = []
    for char_id in scene.get("characters_in_scene", []) or []:
        desc = (characters or {}).get(char_id, "").strip()
        body_hair = _extract_body_hair_from_description(desc)
        if body_hair:
            label = _character_display_name(char_id) if prose else char_id
            locks.append(f"{label}: {body_hair}")
    return "; ".join(locks[:3])


def _keyframe_wardrobe_lock(scene: dict, characters: dict, *, prose: bool = False, brief: str = "") -> str:
    locks = []
    for char_id in scene.get("characters_in_scene", []) or []:
        desc = (characters or {}).get(char_id, "").strip()
        wardrobe = _extract_wardrobe_from_description(desc, brief=brief)
        if wardrobe:
            label = _character_display_name(char_id) if prose else char_id
            locks.append(f"{label}: {wardrobe}")
    return "; ".join(locks[:3])


def _enforce_keyframe_wardrobe(prompt_body: str, scene: dict, characters: dict, brief: str = "") -> str:
    prompt_body = re.sub(r"\s+", " ", (prompt_body or "")).strip()
    wardrobe_lock = _keyframe_wardrobe_lock(scene, characters, prose=True, brief=brief)
    if not prompt_body or not wardrobe_lock:
        return prompt_body

    normalized_prompt = _normalize_prompt_text(prompt_body)
    normalized_lock = _normalize_prompt_text(wardrobe_lock)
    lock_terms = [
        token for token in re.findall(r"[a-zA-Z0-9-]+", normalized_lock)
        if len(token) >= 4 and token not in {"wears", "worn", "with", "that", "into", "their", "feet"}
    ]
    if lock_terms:
        hits = sum(1 for token in dict.fromkeys(lock_terms) if token in normalized_prompt)
        if hits >= max(4, int(len(dict.fromkeys(lock_terms)) * 0.65)):
            return prompt_body

    sentence = _wardrobe_lock_sentence(wardrobe_lock, brief)
    return f"{prompt_body.rstrip('.')} . {sentence}".replace(" .", ".").strip()


def _wardrobe_lock_sentence(wardrobe_lock: str, brief: str = "") -> str:
    wardrobe_lock = (wardrobe_lock or "").strip().rstrip(".")
    if not wardrobe_lock:
        return ""
    if _optional_footwear_guidance(brief):
        return f"For visual continuity, keep the core wardrobe unchanged without reframing for low-priority lower-frame details: {wardrobe_lock}."
    return f"For visual continuity, keep the exact wardrobe unchanged: {wardrobe_lock}."


def _enforce_keyframe_body_hair(prompt_body: str, scene: dict, characters: dict) -> str:
    prompt_body = re.sub(r"\s+", " ", (prompt_body or "")).strip()
    body_hair_lock = _keyframe_body_hair_lock(scene, characters, prose=True)
    if not prompt_body or not body_hair_lock:
        return prompt_body

    normalized_prompt = _normalize_prompt_text(prompt_body)
    normalized_lock = _normalize_prompt_text(body_hair_lock)
    lock_terms = [
        token for token in re.findall(r"[a-zA-Z0-9-]+", normalized_lock)
        if len(token) >= 4 and token not in {"with", "that", "into", "from", "face"}
    ]
    if lock_terms:
        unique_terms = list(dict.fromkeys(lock_terms))
        hits = sum(1 for token in unique_terms if token in normalized_prompt)
        if hits >= max(4, int(len(unique_terms) * 0.55)):
            return prompt_body

    sentence = f"Body and hair continuity: {body_hair_lock}."
    return f"{prompt_body.rstrip('.')} . {sentence}".replace(" .", ".").strip()


def _enforce_keyframe_composition(prompt_body: str, brief: str = "") -> str:
    from director import get_composition_lock

    prompt_body = re.sub(r"\s+", " ", (prompt_body or "")).strip()
    composition_lock = get_composition_lock(brief)
    if not prompt_body or not composition_lock:
        return prompt_body
    if _is_pov_only_composition_lock(brief):
        return prompt_body

    normalized_prompt = _normalize_prompt_text(prompt_body)
    normalized_lock = _normalize_prompt_text(composition_lock)
    if "pov viewer angle" in normalized_lock and not any(
        phrase in normalized_lock
        for phrase in ("original input-image", "subject scale", "medium seated", "no zoom", "do not zoom", "unseen areas")
    ):
        if "pov" in normalized_prompt or "point of view" in normalized_prompt:
            return prompt_body

    required_terms = [
        "stable", "pov", "framing", "composition", "seated", "chair",
        "crossed", "legs", "subject scale",
    ]
    hits = sum(1 for term in required_terms if _normalize_prompt_text(term) in normalized_prompt)
    if hits >= 4:
        return prompt_body

    sentence = f"Composition continuity: {composition_lock}"
    return f"{sentence}. {prompt_body}".strip()


def _enforce_keyframe_action(prompt_body: str, scene: dict) -> str:
    prompt_body = re.sub(r"\s+", " ", (prompt_body or "")).strip()
    action_anchor = _infer_visible_action_from_scene(scene)
    if not prompt_body or not action_anchor:
        return prompt_body
    if _has_visible_action_language(prompt_body):
        return prompt_body
    sentence = f"Visible action anchor: {action_anchor}."
    return f"{sentence} {prompt_body}".strip()


def _compact_character_bits(scene: dict, characters: dict) -> str:
    bits = []
    for char_id in scene.get("characters_in_scene", []) or []:
        desc = (characters or {}).get(char_id, "").strip()
        if not desc:
            continue
        first_sentence = re.split(r"(?<=[.!?])\s+", desc, maxsplit=1)[0].strip()
        if first_sentence:
            bits.append(first_sentence.rstrip("."))
    return "; ".join(bits[:2])


def _normalize_prompt_text(text: str) -> str:
    lowered = (text or "").lower()
    lowered = lowered.replace("over the shoulder", "over-the-shoulder")
    lowered = re.sub(r"[^a-z0-9\s-]+", " ", lowered)
    return re.sub(r"\s+", " ", lowered).strip()


def _normalized_contains(haystack: str, needle: str) -> bool:
    normalized_needle = _normalize_prompt_text(needle)
    if not normalized_needle:
        return True
    return normalized_needle in _normalize_prompt_text(haystack)


def _style_keywords(style: str) -> list[str]:
    stopwords = {
        "with", "and", "the", "that", "this", "from", "into", "over", "under",
        "style", "visual", "lighting", "color", "colour", "tones", "tone",
        "natural", "realistic", "cinematic", "frame", "still", "image",
        "look", "rendering", "palette", "quality", "film", "photo",
    }
    keywords = []
    for word in re.findall(r"[a-zA-Z0-9-]+", style or ""):
        token = word.lower()
        if len(token) < 5 or token in stopwords:
            continue
        if token not in keywords:
            keywords.append(token)
    return keywords[:4]


def _prompt_mentions_style(prompt: str, style: str) -> bool:
    normalized_prompt = _normalize_prompt_text(prompt)
    normalized_style = _normalize_prompt_text(style)
    if normalized_style and normalized_style in normalized_prompt:
        return True
    if "photorealistic cinematic" in normalized_style:
        if "photorealistic" in normalized_prompt and ("cinematic" in normalized_prompt or "film" in normalized_prompt):
            return True
    keywords = _style_keywords(style)
    if not keywords:
        return False
    hits = sum(1 for keyword in keywords if keyword in normalized_prompt)
    return hits >= min(2, len(keywords))


def _prompt_mentions_shot_type(prompt: str, shot_type: str) -> bool:
    normalized_prompt = _normalize_prompt_text(prompt)
    normalized_shot = _normalize_prompt_text(shot_type)
    if not normalized_shot:
        return True
    if normalized_shot in normalized_prompt:
        return True

    aliases = {
        "wide": ("wide shot", "wide frame", "wide composition"),
        "medium": ("medium shot", "medium frame"),
        "close-up": ("close-up", "close up", "tight close-up"),
        "close": ("close-up", "close up"),
        "extreme close-up": ("extreme close-up", "extreme close up"),
        "pov": ("pov", "point of view"),
        "over-the-shoulder": ("over-the-shoulder", "over the shoulder"),
        "tracking": ("tracking shot", "tracking frame"),
        "establishing": ("establishing shot", "establishing frame"),
    }
    for alias in aliases.get(normalized_shot, ()):
        if _normalize_prompt_text(alias) in normalized_prompt:
            return True
    return False


def _photographic_camera_for_keyframe(shot_type: str, camera_action: str = "") -> str:
    text = f"{shot_type} {camera_action}".lower()
    if "extreme close" in text or "macro" in text:
        return "Extreme macro-style framing with very shallow depth of field."
    if "close" in text:
        return "Portrait-style close framing with shallow depth of field."
    if "medium" in text or "over-the-shoulder" in text:
        return "Natural medium-depth perspective with waist-up composition."
    if "wide" in text or "establishing" in text:
        return "Natural wide perspective with medium-deep depth of field, showing the subject and environment."
    if "pov" in text:
        return "Natural first-person foreground perspective with medium depth of field and immersive framing."
    return "Natural medium-depth perspective with balanced cinematic composition."


def _photographic_grade_for_keyframe(style: str, brief: str = "") -> str:
    text = f"{style} {brief}".lower()
    if "black and white" in text or "noir" in text:
        return "Shot on Kodak Tri-X with pronounced grain, high contrast, and controlled highlight roll-off."
    if "documentary" in text or "handheld" in text:
        return "Shot on Kodak Portra 400 with natural color grading, fine grain, and soft contrast."
    if "cool" in text or "clinical" in text:
        return "Shot on digital medium format with cool neutral color grading, precise exposure, and clean shadow detail."
    if "warm" in text or "golden" in text or "cafe" in text:
        return "Shot on Kodak Portra 400 with warm natural color grading, fine grain, soft shadows, and gentle highlight roll-off."
    return "Shot on Kodak Portra 400 with warm neutral color grading, fine grain, natural skin texture, and soft shadows."


def _is_default_keyframe_style(style: str) -> bool:
    return _normalize_prompt_text(style).startswith("photorealistic cinematic still frame")


def _lighting_sentence_for_keyframe(lighting: str, setting: str, mood: str) -> str:
    lighting = re.sub(r"\s+", " ", (lighting or "")).strip().rstrip(".")
    if lighting:
        return f"Shot with {lighting}."
    setting_text = f"{setting} {mood}".lower()
    if "night" in setting_text or "bar" in setting_text:
        return "Shot with warm practical side lighting from the left and subtle fill, creating soft shadows and intimate contrast."
    if "morning" in setting_text or "window" in setting_text:
        return "Shot during soft morning natural light from a window, with gentle directional shadows and muted highlights."
    return "Shot with soft directional natural light and subtle fill, preserving realistic shadows and material texture."


def _enforce_keyframe_anchors(prompt_body: str, scene: dict, brief: str = "") -> str:
    prompt_body = re.sub(r"\s+", " ", (prompt_body or "")).strip()
    if not prompt_body:
        return prompt_body

    style = _style_for_keyframe(brief)
    shot_type = _keyframe_shot_type(scene, brief).strip()
    camera_action = _zit_safe_visual_text(_soften_closeup_language(_keyframe_camera_action(scene, brief), brief)).strip()

    prefix_parts = []
    suffix_parts = []
    if shot_type and not _prompt_mentions_shot_type(prompt_body, shot_type):
        prefix_parts.append(f"{shot_type.capitalize()} storyboard frame")
    if style and not _prompt_mentions_style(prompt_body, style):
        suffix_parts.append(f"Visual style: {style}.")
    if camera_action:
        normalized_prompt = _normalize_prompt_text(prompt_body)
        normalized_camera = _normalize_prompt_text(camera_action)
        if normalized_camera and normalized_camera not in normalized_prompt:
            prefix_parts.append(f"with {camera_action}")

    if not prefix_parts and not suffix_parts:
        return prompt_body

    result = prompt_body
    if prefix_parts:
        anchor_sentence = ", ".join(prefix_parts).strip()
        if not anchor_sentence.endswith("."):
            anchor_sentence += "."
        result = f"{anchor_sentence} {result}".strip()
    if suffix_parts:
        result = f"{result.rstrip()} {' '.join(suffix_parts)}".strip()
    return result


def _build_structured_keyframe_prompt(scene: dict, characters: dict, brief: str = "") -> str:
    from director import (
        _scene_camera_action,
        _scene_comic_hook,
        _scene_hero_moment,
        _scene_object_interaction,
        _scene_pose_details,
        _scene_subject_action,
        get_composition_lock,
    )

    style = _style_for_keyframe(brief)
    composition_lock = get_composition_lock(brief)
    if _is_pov_only_composition_lock(brief):
        composition_lock = ""
    shot_type = _keyframe_shot_type(scene, brief).strip()
    mood = (scene.get("mood") or "").strip()
    hero_moment = _zit_safe_visual_text(_soften_closeup_language(_visible_only_field(_scene_hero_moment(scene)), brief))
    subject_action = _zit_safe_visual_text(_soften_closeup_language(_visible_only_field(_scene_subject_action(scene)), brief))
    visible_action_anchor = _zit_safe_visual_text(_infer_visible_action_from_scene(scene))
    pose_details = _zit_safe_visual_text(_soften_closeup_language(_scene_pose_details(scene), brief))
    object_interaction = _zit_safe_visual_text(_soften_closeup_language(_scene_object_interaction(scene), brief))
    camera_action = _zit_safe_visual_text(_soften_closeup_language(_keyframe_camera_action(scene, brief) or _scene_camera_action(scene), brief))
    comic_hook = _zit_safe_visual_text(_scene_comic_hook(scene))
    visual_cues = _zit_safe_visual_text(_keyframe_visual_continuity_cues(scene))
    user_visual_requirements = _extract_user_visual_requirements(brief)
    user_visual_prompt = _image_model_user_requirement_text(user_visual_requirements)
    setting = (scene.get("setting_description") or "").strip()
    lighting = (scene.get("lighting_description") or "").strip()
    location_id, location_anchor = _location_for_keyframe(scene)
    character_bits = _compact_character_bits(scene, characters)
    body_hair_lock = _keyframe_body_hair_lock(scene, characters, prose=True)
    wardrobe_lock = _keyframe_wardrobe_lock(scene, characters, prose=True, brief=brief)
    framing_guidance = _keyframe_framing_guidance(scene, brief)

    subject_parts = []
    action_parts = []
    visible_description = _zit_safe_visual_text(_keyframe_visible_scene_description(scene, hero_moment, subject_action))
    character_label = _character_names_for_scene(scene)

    if user_visual_requirements:
        action_parts.append((user_visual_prompt or user_visual_requirements).rstrip("."))
    if subject_action:
        action_parts.append(subject_action.rstrip("."))
    if visible_action_anchor and not _normalized_contains(" ".join(action_parts), visible_action_anchor):
        action_parts.append(visible_action_anchor.rstrip("."))
    if object_interaction and not _normalized_contains(" ".join(action_parts), object_interaction):
        action_parts.append(object_interaction.rstrip("."))
    if pose_details and not _normalized_contains(" ".join(action_parts), pose_details):
        action_parts.append(pose_details.rstrip("."))
    if hero_moment and not _normalized_contains(" ".join(action_parts), hero_moment):
        action_parts.append(hero_moment.rstrip("."))

    if body_hair_lock and character_label:
        subject_parts.append(character_label)
    elif character_bits:
        subject_parts.append(character_bits)
    elif visible_description:
        subject_parts.append(visible_description.rstrip("."))
    if framing_guidance:
        subject_parts.append(framing_guidance.rstrip("."))
    if comic_hook:
        action_parts.append(comic_hook.rstrip("."))

    if action_parts:
        action_clause = "; ".join(part for part in action_parts if part)
        subject_sentence = f"{shot_type.capitalize()} photograph showing {action_clause}"
        if subject_parts:
            subject_sentence += f", with {', '.join(part for part in subject_parts if part)}"
    else:
        subject_sentence = f"{shot_type.capitalize()} photograph of " + ", ".join(part for part in subject_parts if part)
    if setting:
        subject_sentence += f", in {setting.rstrip('.')}"
    elif location_anchor:
        subject_sentence += f", in {' '.join(location_anchor.split()[:24]).rstrip('.')}"
    if mood:
        subject_sentence += f", {mood}"
    subject_sentence = subject_sentence.rstrip(".") + "."

    lighting_sentence = _lighting_sentence_for_keyframe(lighting, setting or location_anchor, mood)
    camera_sentence = _photographic_camera_for_keyframe(shot_type, camera_action)
    grade_sentence = _photographic_grade_for_keyframe(style, brief)
    if style and not _is_default_keyframe_style(style):
        grade_sentence = f"{grade_sentence.rstrip('.')} with {style.rstrip('.')}."

    parts = [subject_sentence, lighting_sentence, camera_sentence, grade_sentence]
    if composition_lock:
        parts.insert(1, f"Composition continuity: {composition_lock}.")
    if body_hair_lock:
        parts.insert(1, f"Body and hair continuity: {body_hair_lock}.")
    if wardrobe_lock:
        parts.insert(1, _wardrobe_lock_sentence(wardrobe_lock, brief))
    if visual_cues:
        parts.append(f"Concrete visual continuity cues: {visual_cues.rstrip('.')}.")
    return " ".join(parts)


def _extract_compact_keyframe_prose(raw: str) -> str | None:
    text = _strip_code_fences(raw or "")
    if not text:
        return None

    quoted_candidates = []
    for match in re.finditer(r'"([^"\n]{80,2000})"', text, flags=re.DOTALL):
        candidate = " ".join(match.group(1).split()).strip()
        if candidate and not _looks_like_keyframe_meta(candidate):
            quoted_candidates.append(candidate)
    if quoted_candidates:
        quoted_candidates.sort(key=len, reverse=True)
        return quoted_candidates[0]

    lines = []
    for raw_line in text.splitlines():
        line = raw_line.strip(" *-\t")
        line = re.sub(r"^[A-Za-z][A-Za-z /_-]{0,40}:\s*", "", line).strip()
        if len(line) < 40:
            continue
        if _looks_like_keyframe_meta(raw_line) or _looks_like_keyframe_meta(line):
            continue
        lines.append(line)
    if lines:
        joined = " ".join(lines)
        joined = re.sub(r"\s+", " ", joined).strip()
        return joined
    return None


def _refine_keyframe_prompt(prompt_body: str, scene: dict, characters: dict, brief: str = "") -> str:
    prompt_body = _base_prompt_without_suffix(prompt_body)
    extracted = _extract_compact_keyframe_prose(prompt_body)
    word_count = len((prompt_body or "").split())
    avoid_closeup = _avoid_face_closeups_for_keyframe(brief)
    if extracted:
        extracted_words = len(extracted.split())
        if (
            _KEYFRAME_ACCEPT_MIN_WORDS <= extracted_words <= _KEYFRAME_ACCEPT_MAX_WORDS
            and not _has_forbidden_intent_language(extracted)
            and _has_visible_action_language(extracted)
            and not (avoid_closeup and _looks_like_face_closeup(extracted))
            and _prompt_satisfies_user_visual_requirements(extracted, _extract_user_visual_requirements(brief))
        ):
            anchored = _enforce_keyframe_anchors(extracted, scene, brief)
            anchored = _enforce_user_visual_requirements(anchored, brief)
            anchored = _enforce_keyframe_action(anchored, scene)
            anchored = _enforce_keyframe_wardrobe(anchored, scene, characters, brief=brief)
            anchored = _enforce_keyframe_body_hair(anchored, scene, characters)
            return _enforce_keyframe_composition(anchored, brief)
    if (
        _KEYFRAME_ACCEPT_MIN_WORDS <= word_count <= _KEYFRAME_ACCEPT_MAX_WORDS
        and not _looks_like_keyframe_meta(prompt_body)
        and not _has_forbidden_intent_language(prompt_body)
        and _has_visible_action_language(prompt_body)
        and not (avoid_closeup and _looks_like_face_closeup(prompt_body))
        and _prompt_satisfies_user_visual_requirements(prompt_body, _extract_user_visual_requirements(brief))
    ):
        anchored = _enforce_keyframe_anchors(prompt_body, scene, brief)
        anchored = _enforce_user_visual_requirements(anchored, brief)
        anchored = _enforce_keyframe_action(anchored, scene)
        anchored = _enforce_keyframe_wardrobe(anchored, scene, characters, brief=brief)
        anchored = _enforce_keyframe_body_hair(anchored, scene, characters)
        return _enforce_keyframe_composition(anchored, brief)
    anchored = _enforce_keyframe_anchors(_build_structured_keyframe_prompt(scene, characters, brief), scene, brief)
    anchored = _enforce_user_visual_requirements(anchored, brief)
    anchored = _enforce_keyframe_action(anchored, scene)
    anchored = _enforce_keyframe_wardrobe(anchored, scene, characters, brief=brief)
    anchored = _enforce_keyframe_body_hair(anchored, scene, characters)
    return _enforce_keyframe_composition(anchored, brief)


def _parse_keyframe_prompt_response(raw: str) -> str | None:
    raw = (raw or "").strip()
    if not raw:
        return None

    cleaned = _strip_code_fences(raw)

    candidates = [cleaned, raw]
    start = raw.find("{")
    end = raw.rfind("}") + 1
    if start >= 0 and end > start:
        candidates.insert(0, raw[start:end])
    start = cleaned.find("{")
    end = cleaned.rfind("}") + 1
    if start >= 0 and end > start:
        candidates.insert(0, cleaned[start:end])

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            try:
                parsed = ast.literal_eval(candidate)
            except (ValueError, SyntaxError):
                continue

        if isinstance(parsed, dict):
            for key in ("prompt", "keyframe_prompt", "image_prompt"):
                value = parsed.get(key)
                if isinstance(value, str) and value.strip():
                    return _base_prompt_without_suffix(value.strip())
        elif isinstance(parsed, str) and parsed.strip():
            return _base_prompt_without_suffix(parsed.strip())

    return _extract_keyframe_prompt_text(raw)


def _reasoning_options(temperature: float) -> dict:
    opts = {
        "num_predict": 3072 if config.llm_creative_drafting_enabled() else 2048,
        "temperature": temperature,
    }
    opts.update(config.llm_reasoning_options(for_breakdown=False))
    return opts


def _is_valid_keyframe_prompt(prompt: str) -> bool:
    body = _base_prompt_without_suffix(prompt)
    words = [word for word in body.split() if any(ch.isalpha() for ch in word)]
    if len(words) < 25:
        return False
    if len(body) < 160:
        return False
    lowered = body.lower()
    if lowered in {"a", "an", "image", "storyboard", "frame"}:
        return False
    return True


def _fallback_keyframe_prompt(scene: dict, characters: dict, brief: str = "") -> str:
    """Emergency fallback if the LLM repeatedly returns unusable output."""
    from director import (
        _scene_camera_action,
        _scene_comic_hook,
        _scene_hero_moment,
        _scene_object_interaction,
        _scene_pose_details,
        _scene_subject_action,
        get_composition_lock,
    )

    parts = []
    style = _style_for_keyframe(brief)
    composition_lock = get_composition_lock(brief)
    location_id, location_anchor = _location_for_keyframe(scene)
    hero_moment = _zit_safe_visual_text(_soften_closeup_language(_visible_only_field(_scene_hero_moment(scene)), brief))
    subject_action = _zit_safe_visual_text(_soften_closeup_language(_visible_only_field(_scene_subject_action(scene)), brief))
    visible_action_anchor = _zit_safe_visual_text(_infer_visible_action_from_scene(scene))
    camera_action = _zit_safe_visual_text(_soften_closeup_language(_keyframe_camera_action(scene, brief) or _scene_camera_action(scene), brief))
    comic_hook = _zit_safe_visual_text(_scene_comic_hook(scene))
    pose_details = _zit_safe_visual_text(_soften_closeup_language(_scene_pose_details(scene), brief))
    object_interaction = _zit_safe_visual_text(_soften_closeup_language(_scene_object_interaction(scene), brief))
    visual_cues = _zit_safe_visual_text(_keyframe_visual_continuity_cues(scene))
    user_visual_requirements = _extract_user_visual_requirements(brief)
    user_visual_prompt = _image_model_user_requirement_text(user_visual_requirements)
    body_hair_lock = _keyframe_body_hair_lock(scene, characters, prose=True)
    wardrobe_lock = _keyframe_wardrobe_lock(scene, characters, prose=True, brief=brief)
    framing_guidance = _keyframe_framing_guidance(scene, brief)
    if user_visual_requirements:
        parts.append(f"Visible required image detail: {(user_visual_prompt or user_visual_requirements).rstrip('.')}.")
    if subject_action:
        parts.append(f"The subject is visibly {subject_action.rstrip('.')}.")
    if visible_action_anchor and visible_action_anchor not in " ".join(parts):
        parts.append(f"The main visible action is {visible_action_anchor.rstrip('.')}.")
    if object_interaction:
        parts.append(f"Object contact is clear: {object_interaction}.")
    if pose_details:
        parts.append(f"Body pose is clear: {pose_details}.")
    if style:
        parts.append(f"Visual style: {style}.")
    if composition_lock:
        parts.append(f"Composition continuity: {composition_lock}.")
    if framing_guidance:
        parts.append(f"Framing guidance: {framing_guidance}.")
    if body_hair_lock:
        parts.append(f"Body and hair continuity: {body_hair_lock}.")
    if location_anchor:
        parts.append(f"Canonical location ({location_id}): {location_anchor}.")
    parts.append(f"{_keyframe_shot_type(scene, brief).capitalize()} storyboard frame.")
    if hero_moment and hero_moment not in " ".join(parts):
        parts.append(f"Hero moment: {hero_moment}.")
    if comic_hook:
        parts.append(f"Memorable comic or ironic detail to keep visible: {comic_hook}.")
    if pose_details or object_interaction:
        parts.append("These body-part and prop-contact details are non-negotiable and must be clearly visible in the frame.")
    if wardrobe_lock:
        parts.append(_wardrobe_lock_sentence(wardrobe_lock, brief))
    if camera_action:
        parts.append(f"Camera action: {camera_action}.")
    if visual_cues:
        parts.append(f"Concrete visual continuity cues: {visual_cues}.")
    visible_description = _zit_safe_visual_text(_keyframe_visible_scene_description(scene, hero_moment, subject_action))
    if visible_description:
        parts.append(visible_description)
    action = _zit_safe_visual_text(_soften_closeup_language(_visible_only_field(scene.get("action_description", "")), brief))
    if action:
        parts.append(f"Visible action: {action}.")
    if scene.get("setting_description"):
        parts.append(f"Environment: {str(scene['setting_description']).strip()}.")
    if scene.get("lighting_description"):
        parts.append(f"Lighting: {str(scene['lighting_description']).strip()}.")
    chars_in_scene = scene.get("characters_in_scene", []) or []
    if chars_in_scene:
        char_bits = []
        for char_id in chars_in_scene:
            desc = (characters or {}).get(char_id, "").strip()
            if desc:
                char_bits.append(f"{char_id}: {desc}")
        if char_bits:
            parts.append("Characters: " + " ".join(char_bits) + ".")
    parts.append("Single realistic film frame, no comic layout, no manga paneling, no illustrated speech bubbles, no poster design.")
    return " ".join(parts)


def _build_keyframe_prompt(scene: dict, characters: dict, brief: str = "") -> tuple[str, str, dict]:
    """Create a visual-only prompt for keyframe generation, written by the LLM when possible."""
    request = _keyframe_prompt_context(scene, characters, brief)
    temperatures = (1.0, 0.8, 0.65)
    debug = {
        "prompt_policy_version": _KEYFRAME_PROMPT_POLICY_VERSION,
        "system_prompt": KEYFRAME_PROMPT_SYSTEM,
        "request_context": request,
        "direct_prompt_append": _current_zit_direct_prompt(),
        "attempts": [],
        "rewrite_attempts": [],
    }

    for attempt, temperature in enumerate(temperatures, start=1):
        messages = [
            {"role": "system", "content": KEYFRAME_PROMPT_SYSTEM},
            {"role": "user", "content": request},
        ]
        response = llm_chat(
            model=OLLAMA_MODEL_CREATIVE,
            messages=messages,
            options=_reasoning_options(temperature),
        )
        raw = response["message"]["content"].strip()
        prompt_body = _parse_keyframe_prompt_response(raw)
        attempt_debug = {
            "attempt": attempt,
            "temperature": temperature,
            "raw_response": raw,
            "reasoning_content": response["message"].get("reasoning_content"),
        }
        if prompt_body:
            refined_body = _refine_keyframe_prompt(prompt_body, scene, characters, brief)
            prompt = _finalize_keyframe_prompt(refined_body)
            attempt_debug["parsed_prompt"] = prompt_body
            attempt_debug["refined_prompt"] = refined_body
            if _is_valid_keyframe_prompt(prompt):
                attempt_debug["result"] = "accepted"
                debug["attempts"].append(attempt_debug)
                debug["final_prompt"] = prompt
                debug["final_source"] = "llm"
                return prompt, "llm", debug
            attempt_debug["result"] = "too_short"
            log.warning("Keyframe prompt attempt %d was too short after parsing final answer: %r", attempt, prompt_body[:160])
        else:
            attempt_debug["result"] = "invalid_final_answer"
            log.warning("Keyframe prompt attempt %d returned unusable final answer: %r", attempt, raw[:160])

        repair_messages = messages + [
            {"role": "assistant", "content": raw},
            {
                "role": "user",
                "content": (
                    "That final answer was unusable. Think if needed, but return ONLY the final storyboard prompt text. "
                    "No JSON, no markdown fences, no labels, no explanation, no surrounding quotes."
                ),
            },
        ]
        repair_response = llm_chat(
            model=OLLAMA_MODEL_CREATIVE,
            messages=repair_messages,
            options=_reasoning_options(0.1),
        )
        repair_raw = repair_response["message"]["content"].strip()
        repaired_body = _parse_keyframe_prompt_response(repair_raw)
        attempt_debug["repair_raw_response"] = repair_raw
        attempt_debug["repair_reasoning_content"] = repair_response["message"].get("reasoning_content")
        if repaired_body:
            refined_repair = _refine_keyframe_prompt(repaired_body, scene, characters, brief)
            prompt = _finalize_keyframe_prompt(refined_repair)
            attempt_debug["repair_parsed_prompt"] = repaired_body
            attempt_debug["repair_refined_prompt"] = refined_repair
            if _is_valid_keyframe_prompt(prompt):
                attempt_debug["repair_result"] = "accepted"
                debug["attempts"].append(attempt_debug)
                debug["final_prompt"] = prompt
                debug["final_source"] = "llm"
                return prompt, "llm", debug
            attempt_debug["repair_result"] = "too_short"
            log.warning("Keyframe prompt repair %d was still too short: %r", attempt, repaired_body[:160])
        else:
            attempt_debug["repair_result"] = "invalid_final_answer"
            log.warning("Keyframe prompt repair %d still returned unusable final answer: %r", attempt, repair_raw[:160])
        debug["attempts"].append(attempt_debug)

    log.warning("Falling back to deterministic keyframe prompt after repeated malformed LLM responses.")
    fallback_prompt = _finalize_keyframe_prompt(_fallback_keyframe_prompt(scene, characters, brief))
    debug["final_prompt"] = fallback_prompt
    debug["final_source"] = "fallback"
    return fallback_prompt, "fallback", debug


def _needs_keyframe_prompt_refresh(scene: dict) -> bool:
    prompt = scene.get("keyframe_prompt", "")
    if not prompt:
        return True
    debug = scene.get("keyframe_prompt_debug") or {}
    if debug.get("prompt_policy_version") != _KEYFRAME_PROMPT_POLICY_VERSION:
        return True
    if scene.get("keyframe_prompt_source") not in {"llm", "llm_rewrite"}:
        return True
    stripped = _base_prompt_without_suffix(prompt).strip()
    if len(stripped) < 80:
        return True
    return False


def _run_keyframe_round(client, template: dict, scene: dict, characters: dict,
                        keyframe_dir: str, prompt: str, candidates: list,
                        max_attempts: int, scene_num: int) -> bool:
    """Run a round of keyframe generation attempts. Returns True if one passed."""
    seed = random.randint(0, 2**32 - 1)
    start_idx = len(candidates)

    for i in range(max_attempts):
        candidate_num = start_idx + i + 1
        log.info("  Keyframe %d for scene %d (seed %d)...",
                 candidate_num, scene_num, seed)

        workflow = build_keyframe_workflow(
            template, prompt, seed,
            width=KF_WIDTH, height=KF_HEIGHT,
        )

        try:
            prompt_id = client.queue_prompt(workflow)
            history = client.wait_for_completion(prompt_id, timeout=120)
            raw_path = get_image_output_path(history)
        except Exception as e:
            log.error("  Keyframe gen failed: %s", e)
            candidates.append({"candidate": candidate_num, "status": "failed", "error": str(e)})
            seed = random.randint(0, 2**32 - 1)
            continue

        kf_filename = f"scene_{scene_num:03d}_kf_{candidate_num}.png"
        kf_path = os.path.join(keyframe_dir, kf_filename)
        shutil.copy2(raw_path, kf_path)

        if SKIP_KF_EVAL:
            log.info("  Keyframe %d generated (evaluation skipped)", candidate_num)
            eval_result = {"verdict": "PASS", "notes": "Evaluation skipped"}
        else:
            log.info("  Evaluating keyframe %d...", candidate_num)
            eval_result = evaluate_keyframe(kf_path, scene, characters)
            log.info("  Keyframe %d: %s -- %s",
                     candidate_num, eval_result["verdict"],
                     eval_result.get("fail_reason") or eval_result.get("character_notes", "OK"))

        candidates.append({
            "candidate": candidate_num,
            "status": "generated",
            "path": kf_path,
            "seed": seed,
            "eval": eval_result,
        })

        if eval_result["verdict"] == "PASS":
            log.info("  Keyframe PASSED -- moving on from scene %d", scene_num)
            return True

        seed = random.randint(0, 2**32 - 1)

    return False


def generate_keyframes(client, scene: dict, characters: dict,
                       project_dir: str, brief: str = "") -> list[dict]:
    """Generate keyframe candidates for a scene with prompt rewriting on failure.

    Round 1: Try KF_CANDIDATES attempts with the original prompt.
    If all fail, rewrite the prompt based on failure reasons and try again.
    """
    template = load_keyframe_template()
    scene_num = scene["scene_number"]

    # Write an image-specific prompt that strips dialogue to avoid burned-in captions.
    if _needs_keyframe_prompt_refresh(scene):
        (
            scene["keyframe_prompt"],
            scene["keyframe_prompt_source"],
            scene["keyframe_prompt_debug"],
        ) = _build_keyframe_prompt(scene, characters, brief=brief)
    elif not scene.get("keyframe_prompt_debug"):
        scene["keyframe_prompt_debug"] = {
            "prompt_policy_version": _KEYFRAME_PROMPT_POLICY_VERSION,
            "system_prompt": KEYFRAME_PROMPT_SYSTEM,
            "request_context": _keyframe_prompt_context(scene, characters, brief),
            "direct_prompt_append": _current_zit_direct_prompt(),
            "attempts": [],
            "rewrite_attempts": [],
            "final_prompt": scene.get("keyframe_prompt", ""),
            "final_source": scene.get("keyframe_prompt_source", "existing_state"),
        }

    scene["keyframe_prompt"] = _finalize_keyframe_prompt(scene.get("keyframe_prompt", ""))
    scene.setdefault("keyframe_prompt_debug", {})
    scene["keyframe_prompt_debug"]["prompt_policy_version"] = _KEYFRAME_PROMPT_POLICY_VERSION
    scene["keyframe_prompt_debug"]["direct_prompt_append"] = _current_zit_direct_prompt()
    scene["keyframe_prompt_debug"]["final_prompt"] = scene["keyframe_prompt"]

    prompt = scene["keyframe_prompt"]
    log.info("  Image prompt (%d words):", len(prompt.split()))
    for line in prompt.split("\n"):
        log.info("    | %s", line)

    keyframe_dir = os.path.join(project_dir, "keyframes")
    os.makedirs(keyframe_dir, exist_ok=True)

    candidates = []

    # Round 1: original prompt
    log.info("  Round 1: trying %d candidates with original prompt", KF_CANDIDATES)
    if _run_keyframe_round(client, template, scene, characters,
                           keyframe_dir, prompt, candidates,
                           KF_CANDIDATES, scene_num):
        return candidates

    # All failed — collect failure reasons and rewrite the prompt
    fail_reasons = []
    for c in candidates:
        reason = c.get("eval", {}).get("fail_reason") or c.get("error", "unknown")
        if reason:
            fail_reasons.append(reason)

    log.info("  All %d candidates failed. Rewriting prompt based on failures...", KF_CANDIDATES)
    new_prompt, rewrite_debug = _rewrite_keyframe_prompt(scene, fail_reasons, prompt, brief=brief)
    new_prompt = _finalize_keyframe_prompt(_refine_keyframe_prompt(new_prompt, scene, characters, brief))
    scene["keyframe_prompt"] = new_prompt
    scene["keyframe_prompt_source"] = "llm_rewrite"
    scene.setdefault("keyframe_prompt_debug", {})
    scene["keyframe_prompt_debug"]["prompt_policy_version"] = _KEYFRAME_PROMPT_POLICY_VERSION
    scene["keyframe_prompt_debug"].setdefault("rewrite_attempts", [])
    rewrite_debug["direct_prompt_append"] = _current_zit_direct_prompt()
    rewrite_debug["final_prompt"] = new_prompt
    scene["keyframe_prompt_debug"]["rewrite_attempts"].append(rewrite_debug)
    scene["keyframe_prompt_debug"]["direct_prompt_append"] = _current_zit_direct_prompt()
    scene["keyframe_prompt_debug"]["final_prompt"] = new_prompt
    scene["keyframe_prompt_debug"]["final_source"] = "llm_rewrite"

    log.info("  Rewritten prompt (%d words):", len(new_prompt.split()))
    for line in new_prompt.split("\n"):
        log.info("    | %s", line)

    # Round 2: rewritten prompt
    log.info("  Round 2: trying %d candidates with rewritten prompt", KF_CANDIDATES)
    _run_keyframe_round(client, template, scene, characters,
                        keyframe_dir, new_prompt, candidates,
                        KF_CANDIDATES, scene_num)

    return candidates
