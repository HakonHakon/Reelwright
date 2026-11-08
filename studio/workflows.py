"""ComfyUI API-format graph builders for every Studio engine.

All model names come from settings, so the graphs track what's installed on a Windows PC.
Every graph is validated against /object_info before it is queued (comfy.Comfy.run).
"""

import copy
import re
import os

from . import blocking, settings as _settings


def _loras(wf: dict, model_ref: list, loras: list[dict], start_id: int) -> list:
    """Chain LoraLoaderModelOnly nodes; returns the final model link."""
    nid = start_id
    for lora in loras:
        if not lora.get("name"):
            continue
        wf[str(nid)] = {"class_type": "LoraLoaderModelOnly",
                        "inputs": {"lora_name": lora["name"], "strength_model": float(lora.get("strength", 1.0)),
                                   "model": model_ref}}
        model_ref = [str(nid), 0]
        nid += 1
    return model_ref


# ── Images ──────────────────────────────────────────────────────────────

def flux_image(s: dict, prompt: str, seed: int, width: int, height: int, batch: int = 1,
               loras: list[dict] | None = None, prefix: str = "studio/frame",
               init_image: str | None = None, denoise: float = 1.0) -> dict:
    f = s["flux"]
    wf = {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": f["unet"], "weight_dtype": f["weight_dtype"]}},
        "2": {"class_type": "DualCLIPLoader", "inputs": {"clip_name1": f["clip_l"], "clip_name2": f["t5"],
                                                         "type": "flux", "device": "default"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": f["vae"]}},
        "4": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["2", 0]}},
        "5": {"class_type": "FluxGuidance", "inputs": {"guidance": float(f["guidance"]), "conditioning": ["4", 0]}},
        "6": {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["4", 0]}},
    }
    model = _loras(wf, ["1", 0], loras or [], 30)
    latent = _latent(wf, s, "3", width, height, batch, init_image, use_sd3_latent=True)
    wf["8"] = {"class_type": "KSampler", "inputs": {
        "seed": int(seed), "steps": int(f["steps"]), "cfg": 1.0, "sampler_name": f["sampler"],
        "scheduler": f["scheduler"], "denoise": float(denoise), "model": model,
        "positive": ["5", 0], "negative": ["6", 0], "latent_image": latent}}
    wf["9"] = {"class_type": "VAEDecode", "inputs": {"samples": ["8", 0], "vae": ["3", 0]}}
    wf["20"] = {"class_type": "SaveImage", "inputs": {"filename_prefix": prefix, "images": ["9", 0]}}
    return wf


def zimage_image(s: dict, prompt: str, seed: int, width: int, height: int, batch: int = 1,
                 prefix: str = "studio/frame", init_image: str | None = None, denoise: float = 1.0) -> dict:
    z = s["zimage"]
    wf = {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": z["unet"], "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": z["clip"], "type": "lumina2", "device": "default"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": z["vae"]}},
        "4": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["2", 0]}},
        "6": {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["4", 0]}},
        "11": {"class_type": "ModelSamplingAuraFlow", "inputs": {"shift": float(z["shift"]), "model": ["1", 0]}},
    }
    latent = _latent(wf, s, "3", width, height, batch, init_image, use_sd3_latent=True)
    wf["8"] = {"class_type": "KSampler", "inputs": {
        "seed": int(seed), "steps": int(z["steps"]), "cfg": 1.0, "sampler_name": "res_multistep",
        "scheduler": "simple", "denoise": float(denoise), "model": ["11", 0],
        "positive": ["4", 0], "negative": ["6", 0], "latent_image": latent}}
    wf["9"] = {"class_type": "VAEDecode", "inputs": {"samples": ["8", 0], "vae": ["3", 0]}}
    wf["20"] = {"class_type": "SaveImage", "inputs": {"filename_prefix": prefix, "images": ["9", 0]}}
    return wf


def qwen_edit_image(s: dict, prompt: str, seed: int, width: int, height: int, batch: int,
                    ref_images: list[str], prefix: str = "studio/frame") -> dict:
    """Qwen Image Edit 2509 (+Lightning), conditioned on up to three ordered input images."""
    q = s["qwen_edit"]
    wf = {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": q["unet"], "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": q["clip"], "type": "qwen_image", "device": "default"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": q["vae"]}},
    }
    model = ["1", 0]
    if q.get("lora"):
        wf["10"] = {"class_type": "LoraLoaderModelOnly",
                    "inputs": {"lora_name": q["lora"], "strength_model": 1.0, "model": model}}
        model = ["10", 0]
    wf["11"] = {"class_type": "ModelSamplingAuraFlow", "inputs": {"shift": float(q["shift"]), "model": model}}
    wf["12"] = {"class_type": "CFGNorm", "inputs": {"strength": 1.0, "model": ["11", 0]}}
    te = {"clip": ["2", 0], "prompt": prompt, "vae": ["3", 0]}
    for i, name in enumerate(ref_images[:3]):
        wf[str(50 + i)] = {"class_type": "LoadImage", "inputs": {"image": name}}
        te[f"image{i + 1}"] = [str(50 + i), 0]
    wf["4"] = {"class_type": "TextEncodeQwenImageEditPlus", "inputs": te}
    wf["6"] = {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["4", 0]}}
    wf["7"] = {"class_type": "EmptySD3LatentImage", "inputs": {"width": int(width), "height": int(height),
                                                               "batch_size": int(batch)}}
    wf["8"] = {"class_type": "KSampler", "inputs": {
        "seed": int(seed), "steps": int(q["steps"]), "cfg": 1.0, "sampler_name": "euler", "scheduler": "simple",
        "denoise": 1.0, "model": ["12", 0], "positive": ["4", 0], "negative": ["6", 0], "latent_image": ["7", 0]}}
    wf["9"] = {"class_type": "VAEDecode", "inputs": {"samples": ["8", 0], "vae": ["3", 0]}}
    wf["20"] = {"class_type": "SaveImage", "inputs": {"filename_prefix": prefix, "images": ["9", 0]}}
    return wf


def qwen_correct_face(s: dict, source_image: str, ref_images: list[str], character: dict,
                      width: int, height: int, seed: int, prefix: str = "studio/face") -> dict:
    """Edit the source frame in place, with separate portrait identity conditioning."""
    prompt = (
        f"Edit image 1. Correct the person's facial identity to match {character['name']} "
        "in image 2. Image 1 is the performance frame; image 2 is the identity reference. "
        "Keep the exact head position, head size, perspective, gaze, expression, eyelids and mouth "
        "opening from image 1. Preserve the original body, hair placement, hat, glasses, clothing, "
        "hands, microphone, camera, background and stage lighting from image 1. "
        "Change the facial anatomy, nose, cheeks, jaw, lips, skin and distinguishing facial details "
        "to the person in image 2, naturally integrated into the original head and neck. "
        "Match the lighting, shadows and skin colour to image 1. Use image 2 only for identity; "
        "do not copy its pose, expression, hands, objects or background. "
        f"Character details: {character.get('appearance', '')}")
    return qwen_edit_image(s, prompt, seed, width, height, 1,
                           [source_image, *ref_images[:2]], prefix)


def qwen_frame_identity(s: dict, source_image: str, references: list[tuple[dict, dict, str]],
                        width: int, height: int, seed: int, prefix: str = "studio/frame") -> dict:
    """Transfer at most two identities onto an already composed frame (image 1)."""
    if not 1 <= len(references) <= 2:
        raise ValueError("A frame identity pass needs one or two character references")
    parts = ["Edit image 1. Image 1 is the composition frame."]
    for i, (character, role, _) in enumerate(references, 2):
        position = role.get("position") or ""
        position = f", {position}," if position else ""
        appearance = blocking.identity_description(character)
        parts.append(
            f"Give {character.get('name') or character['id']}{position} in image 1 the facial identity "
            f"of the person in image {i}. "
            + (f"Identify this character in image 1 by their appearance: {appearance}. " if appearance else "")
            + f"Use image {i} only for this person's face.")
    parts.append(
        "Keep every person's exact body pose, head position, head angle, gaze direction, expression "
        "and placement from image 1. Keep the framing, camera angle, clothing, hair placement, "
        "hands, props, background, lighting and shadows exactly as in image 1. "
        "Keep all occlusions and the visible portion of each face; leave hidden faces hidden. "
        "Preserve all other people and any identities already applied. "
        "Change only the specified facial identities, naturally lit to match image 1. "
        "Do not copy the portraits' pose, gaze, expression, framing, clothes or background. "
        "Do not turn anyone toward the camera or recompose the scene.")
    return qwen_edit_image(s, " ".join(parts), seed, width, height, 1,
                           [source_image, *(ref for _, _, ref in references)], prefix)


def qwen_frame_insert(s: dict, source_image: str, character: dict, subject: dict, clothes: str, target: str,
                      reference: str, width: int, height: int, seed: int, prefix: str = "studio/frame") -> dict:
    """Add one person (a layered subject: behind glass, in another room) into an already composed frame. `target`
    describes what they look at by its place in the picture: Qwen knows no character names. The portrait in image 2
    usually smiles into the lens, so the acting comes first and the portrait's expression and gaze are excluded."""
    name = character.get("name") or character["id"]
    expression = subject["expression"]
    if not re.search(r"\b(smil|grin|laugh)", expression, re.I):
        expression += "; not smiling, lips together"
    prompt = (f"Edit image 1. Add exactly one person to image 1, {subject['position']}, {subject['depth']}, "
              f"{subject['zone']} ({subject['space']}). Expression: {expression}. Her or his head and eyes turn toward "
              f"{target}, {subject['gaze_direction']}; the eyes do NOT look at the viewer or the camera. "
              f"{subject['pose']}. Facing {subject['facing']}. Visible: {subject['visibility'].replace('_', ' ')}. "
              f"Wearing {clothes}. {subject.get('wardrobe_state', '')} The face is the facial identity of the person in "
              "image 2 only: copy the face shape and features, NOT the portrait's smile, expression, head angle, gaze, "
              f"hair styling, clothes or background. This person is {name}. Match the scale, perspective, focus, light, "
              "reflections and glass of that spot in image 1. Keep everything else in image 1 exactly as it is: every "
              "other person, their pose, face and hands, every prop they hold, the framing and the lighting.")
    return qwen_edit_image(s, prompt, seed, width, height, 1, [source_image, reference], prefix)


def qwen_frame_performance(s: dict, source_image: str, who: str, subject: dict, target: str, width: int,
                           height: int, seed: int, prefix: str = "studio/frame") -> dict:
    """Text-only edit of one person's expression and gaze. An inserted person inherits the portrait's smile and
    lens gaze; with no portrait in the conditioning, Qwen changes the acting and keeps the identity."""
    expression = subject["expression"]
    if not re.search(r"\b(smil|grin|laugh)", expression, re.I):
        expression += "; mouth closed, no smile"
    prompt = (f"Edit image 1. Change ONLY {who}: expression {expression}. Head and eyes turn toward {target}, "
              f"{subject['gaze_direction']}; the eyes do NOT look at the viewer or the camera. Keep this person's "
              "identity, face shape, hair, clothes, size and position exactly. Keep everything else in image 1 exactly "
              "as it is: every other person and their pose, every prop, the framing and the lighting.")
    return qwen_edit_image(s, prompt, seed, width, height, 1, [source_image], prefix)


def _latent(wf, s, vae_id, width, height, batch, init_image, use_sd3_latent):
    if init_image:
        wf["40"] = {"class_type": "LoadImage", "inputs": {"image": init_image}}
        wf["41"] = {"class_type": "ImageScale", "inputs": {"upscale_method": "lanczos", "width": int(width),
                                                          "height": int(height), "crop": "center",
                                                          "image": ["40", 0]}}
        wf["42"] = {"class_type": "VAEEncode", "inputs": {"pixels": ["41", 0], "vae": [vae_id, 0]}}
        if batch > 1:
            wf["43"] = {"class_type": "RepeatLatentBatch", "inputs": {"samples": ["42", 0], "amount": int(batch)}}
            return ["43", 0]
        return ["42", 0]
    wf["7"] = {"class_type": "EmptySD3LatentImage", "inputs": {"width": int(width), "height": int(height),
                                                               "batch_size": int(batch)}}
    return ["7", 0]


# ── Video: LTX-2.3 image-to-video (two-stage, with audio) ───────────────

def lipsync_video(video: str, audio: str, steps=20, guidance=2.0, seed=1247,
                  prefix='studio/lipsync') -> dict:
    return {
        '1': {'class_type': 'LoadVideo', 'inputs': {'file': video}},
        '2': {'class_type': 'LoadAudio', 'inputs': {'audio': audio}},
        '3': {'class_type': 'ReelwrightLatentSync', 'inputs': {
            'video': ['1', 0], 'audio': ['2', 0], 'steps': steps, 'guidance': guidance, 'seed': seed}},
        '4': {'class_type': 'SaveVideo', 'inputs': {
            'video': ['3', 0], 'filename_prefix': prefix, 'format': 'mp4', 'codec': 'auto'}},
    }


def separate_vocals(audio: str, prefix='studio/vocals') -> dict:
    return {
        '1': {'class_type': 'LoadAudio', 'inputs': {'audio': audio}},
        '2': {'class_type': 'ReelwrightSeparateVocals', 'inputs': {'audio': ['1', 0]}},
        '3': {'class_type': 'SaveAudio', 'inputs': {'audio': ['2', 0], 'filename_prefix': prefix}},
    }

def ltx_i2v(s: dict, prompt: str, first_frame: str, frames: int, seed: int,
            end_frame: str | None = None, voice_audio: str | None = None,
            prefix: str = "studio/take") -> dict:
    lt = s["ltx"]
    tpl_path = lt["template"]
    if not os.path.isabs(tpl_path):
        tpl_path = os.path.join(_settings.REPO_ROOT, tpl_path)
    from .comfy import load_template
    wf = copy.deepcopy(load_template(tpl_path))
    v = s["video"]
    wf.pop("267:275", None)  # text preview node, not needed
    wf["267:266"]["inputs"]["value"] = prompt
    wf["267:247"]["inputs"]["text"] = lt["negative"]
    wf["269"]["inputs"]["image"] = first_frame
    wf["267:225"]["inputs"]["value"] = int(frames)
    wf["267:257"]["inputs"]["value"] = int(v["width"])
    wf["267:258"]["inputs"]["value"] = int(v["height"])
    wf["267:260"]["inputs"]["value"] = int(v["fps"])
    wf["267:201"]["inputs"]["value"] = False
    wf["267:249"]["inputs"]["strength"] = float(lt["first_frame_strength"])
    wf["267:216"]["inputs"]["noise_seed"] = int(seed)
    wf["267:237"]["inputs"]["noise_seed"] = int(seed) + 1
    wf["75"]["inputs"]["filename_prefix"] = prefix

    pos, neg, model = ["267:239", 0], ["267:239", 1], ["267:232", 0]

    if voice_audio:
        wf["290"] = {"class_type": "LoadAudio", "inputs": {"audio": voice_audio}}
        wf["291"] = {"class_type": "LoraLoaderModelOnly", "inputs": {
            "lora_name": lt["id_lora"], "strength_model": float(lt["id_lora_strength"]), "model": model}}
        wf["292"] = {"class_type": "LTXVReferenceAudio", "inputs": {
            "model": ["291", 0], "positive": pos, "negative": neg, "reference_audio": ["290", 0],
            "audio_vae": ["267:811", 0], "identity_guidance_scale": float(lt["identity_guidance_scale"]),
            "start_percent": 0.0, "end_percent": 1.0}}
        model, pos, neg = ["292", 0], ["292", 1], ["292", 2]

    if end_frame:
        wf["280"] = {"class_type": "LoadImage", "inputs": {"image": end_frame}}
        wf["281"] = copy.deepcopy(wf["267:238"])
        wf["281"]["inputs"]["input"] = ["280", 0]
        wf["282"] = copy.deepcopy(wf["267:235"])
        wf["282"]["inputs"]["images"] = ["281", 0]
        wf["283"] = copy.deepcopy(wf["267:248"])
        wf["283"]["inputs"]["image"] = ["282", 0]
        wf["284"] = {"class_type": "LTXVAddGuide", "inputs": {
            "positive": pos, "negative": neg, "vae": ["267:700", 2], "latent": ["267:249", 0],
            "image": ["283", 0], "frame_idx": -1, "strength": float(lt["end_frame_strength"])}}
        pos, neg = ["284", 0], ["284", 1]
        wf["267:222"]["inputs"]["video_latent"] = ["284", 2]
        wf["267:253"]["inputs"]["samples"] = ["267:212", 2]  # upsample the guide-cropped latent

    for gid in ("267:231", "267:213"):
        wf[gid]["inputs"]["model"] = model
    wf["267:231"]["inputs"]["positive"] = pos
    wf["267:231"]["inputs"]["negative"] = neg
    wf["267:212"]["inputs"]["positive"] = pos
    wf["267:212"]["inputs"]["negative"] = neg
    return wf


# ── Video: Wan 2.2 14B image-to-video, 4-step lightning (silent) ────────

def wan_i2v(s: dict, prompt: str, first_frame: str, frames: int, seed: int,
            character_loras: list[dict] | None = None, prefix: str = "studio/take") -> dict:
    w = s["wan"]
    steps = int(w["steps"])
    split = max(1, steps // 2)
    wf = {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": w["high"], "weight_dtype": "default"}},
        "2": {"class_type": "UNETLoader", "inputs": {"unet_name": w["low"], "weight_dtype": "default"}},
        "7": {"class_type": "CLIPLoader", "inputs": {"clip_name": w["text_encoder"], "type": "wan", "device": "default"}},
        "8": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["7", 0]}},
        "9": {"class_type": "CLIPTextEncode", "inputs": {"text": w["negative"], "clip": ["7", 0]}},
        "10": {"class_type": "VAELoader", "inputs": {"vae_name": w["vae"]}},
        "11": {"class_type": "LoadImage", "inputs": {"image": first_frame}},
        "12": {"class_type": "WanImageToVideo", "inputs": {
            "positive": ["8", 0], "negative": ["9", 0], "vae": ["10", 0], "start_image": ["11", 0],
            "width": int(w["width"]), "height": int(w["height"]), "length": int(frames), "batch_size": 1}},
    }
    extra = character_loras or []
    # Wan 2.2 LoRAs come as a pair: "..._high_noise" for the high-noise model, "..._low_noise" for the low one.
    extra_low = [{**l, "name": l["name"].replace("high_noise", "low_noise")} if l.get("name") else l for l in extra]
    high = _loras(wf, ["1", 0], [{"name": w["high_lightning"], "strength": 1.0}] + extra, 30)
    low = _loras(wf, ["2", 0], [{"name": w["low_lightning"], "strength": 1.0}] + extra_low, 50)
    wf["5"] = {"class_type": "ModelSamplingSD3", "inputs": {"shift": float(w["shift"]), "model": high}}
    wf["6"] = {"class_type": "ModelSamplingSD3", "inputs": {"shift": float(w["shift"]), "model": low}}
    common = {"cfg": 1.0, "sampler_name": "euler", "scheduler": "simple", "steps": steps,
              "positive": ["12", 0], "negative": ["12", 1]}
    wf["20"] = {"class_type": "KSamplerAdvanced", "inputs": {
        **common, "model": ["5", 0], "add_noise": "enable", "noise_seed": int(seed),
        "latent_image": ["12", 2], "start_at_step": 0, "end_at_step": split,
        "return_with_leftover_noise": "enable"}}
    wf["21"] = {"class_type": "KSamplerAdvanced", "inputs": {
        **common, "model": ["6", 0], "add_noise": "disable", "noise_seed": int(seed),
        "latent_image": ["20", 0], "start_at_step": split, "end_at_step": 10000,
        "return_with_leftover_noise": "disable"}}
    wf["22"] = {"class_type": "VAEDecode", "inputs": {"samples": ["21", 0], "vae": ["10", 0]}}
    wf["23"] = {"class_type": "CreateVideo", "inputs": {"images": ["22", 0], "fps": float(w["fps"])}}
    wf["24"] = {"class_type": "SaveVideo", "inputs": {"video": ["23", 0], "filename_prefix": prefix,
                                                      "format": "auto", "codec": "auto"}}
    return wf


# ── Frame counts ────────────────────────────────────────────────────────

def ltx_frames(seconds: float, fps: int) -> int:
    """LTX needs 8n+1 frames."""
    n = max(1, round(seconds * fps / 8))
    return 8 * n + 1


def wan_frames(seconds: float, fps: int) -> int:
    """Wan needs 4n+1 frames."""
    n = max(1, round(seconds * fps / 4))
    return 4 * n + 1


def minimax_h3_frames(seconds: float) -> int:
    """H3 uses a 17n+5 frame grid at 24 fps."""
    n = max(5, round(seconds * 24))
    return n + (5 - n % 17) % 17


def minimax_h3_i2v(s: dict, prompt: str, first_frame: str, frames: int, seed: int,
                   end_frame: str | None = None, prefix: str = "studio/take") -> dict:
    """Local H3 FL2VA with native audio; follows Comfy's official I2V template."""
    h = s["minimax_local"]
    wf = {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": h["unet"], "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": h["clip"], "type": "minimax", "device": "default"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": h["video_vae"]}},
        "4": {"class_type": "VAELoader", "inputs": {"vae_name": h["audio_vae"]}},
        "5": {"class_type": "LoadImage", "inputs": {"image": first_frame}},
        "6": {"class_type": "MiniMaxH3ImageToVideo", "inputs": {
            "clip": ["2", 0], "vae": ["3", 0], "prompt": prompt,
            "width": int(h["width"]), "height": int(h["height"]), "length": int(frames), "first_frame": ["5", 0]}},
        "7": {"class_type": "RandomNoise", "inputs": {"noise_seed": int(seed)}},
        "8": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "res_multistep"}},
        "9": {"class_type": "BasicScheduler", "inputs": {"model": ["1", 0], "scheduler": "simple", "steps": int(h["steps"]), "denoise": 1.0}},
        "10": {"class_type": "BasicGuider", "inputs": {"model": ["1", 0], "conditioning": ["6", 0]}},
        "11": {"class_type": "SamplerCustomAdvanced", "inputs": {
            "noise": ["7", 0], "guider": ["10", 0], "sampler": ["8", 0], "sigmas": ["9", 0], "latent_image": ["6", 1]}},
        "12": {"class_type": "VAEDecode", "inputs": {"samples": ["11", 0], "vae": ["3", 0]}},
        "13": {"class_type": "VAEDecodeAudio", "inputs": {"samples": ["11", 0], "vae": ["4", 0]}},
        "14": {"class_type": "CreateVideo", "inputs": {"images": ["12", 0], "fps": 24.0, "audio": ["13", 0]}},
        "15": {"class_type": "SaveVideo", "inputs": {"video": ["14", 0], "filename_prefix": prefix, "format": "auto", "codec": "auto"}},
    }
    if h.get("lora"):
        wf["16"] = {"class_type": "LoraLoaderModelOnly", "inputs": {
            "model": ["1", 0], "lora_name": h["lora"], "strength_model": 1.0}}
        wf["9"]["inputs"]["model"] = ["16", 0]
        wf["10"]["inputs"]["model"] = ["16", 0]
    if end_frame:
        wf["17"] = {"class_type": "LoadImage", "inputs": {"image": end_frame}}
        wf["6"]["inputs"]["last_frame"] = ["17", 0]
    return wf


def minimax_h3_reference_edit(s: dict, prompt: str, references: list[str], video: str,
                             frames: int, seed: int, width: int, height: int,
                             prefix: str = "studio/identity") -> dict:
    """Character portraits and source performance condition the Ref2VA model together."""
    cfg = copy.deepcopy(s)
    face = cfg["face_correction"]
    cfg["minimax_local"].update(unet=face["identity_unet"], lora=face["identity_turbo_lora"],
                               steps=8, width=width, height=height)
    wf = minimax_h3_i2v(cfg, prompt, references[0], frames, seed, prefix=prefix)
    inputs = {"clip": ["2", 0], "vae": ["3", 0], "prompt": prompt, "width": width,
              "height": height, "length": frames, "ref_image_size": "max",
              "ref_videos.ref_video_0": ["18", 0]}
    for i, image in enumerate(references):
        nid = "5" if i == 0 else str(50 + i)
        wf[nid] = {"class_type": "LoadImage", "inputs": {"image": image}}
        inputs[f"ref_images.ref_image_{i}"] = [nid, 0]
    wf["6"] = {"class_type": "MiniMaxH3ReferenceToVideo", "inputs": inputs}
    wf["17"] = {"class_type": "LoadVideo", "inputs": {"file": video}}
    wf["18"] = {"class_type": "GetVideoComponents", "inputs": {"video": ["17", 0]}}
    wf["19"] = {"class_type": "LoraLoaderModelOnly", "inputs": {
        "model": ["16", 0], "lora_name": face["identity_lora"], "strength_model": 1.0}}
    wf["9"]["inputs"].update(model=["19", 0], scheduler="normal")
    wf["10"]["inputs"]["model"] = ["19", 0]
    wf["8"]["inputs"]["sampler_name"] = "er_sde"
    # The original soundtrack is remuxed by the caller.
    wf.pop("4")
    wf.pop("13")
    wf["14"]["inputs"].pop("audio")
    return wf


def infinitetalk_size(s: dict) -> tuple[int, int]:
    """The project's shape at the 480p model's pixel budget, multiples of 16."""
    import math
    v = s["video"]; ratio = v["width"] / v["height"]; px = float(s["infinitetalk"]["pixels"])
    return (max(16, round(math.sqrt(px * ratio) / 16) * 16), max(16, round(math.sqrt(px / ratio) / 16) * 16))


def infinitetalk(s: dict, image: str, audio: str, prompt: str, frames: int, seed: int, prefix: str = "studio/take") -> dict:
    """Wan 2.1 I2V + InfiniteTalk (ComfyUI native), one speaker. Chunks of `chunk` frames, each continuing from the
    last `motion_frames` frames of the one before, so any length plays at the audio's pace (25 fps)."""
    it = s["infinitetalk"]; W, H = infinitetalk_size(s)
    LEN, MOTION = int(it["chunk"]), int(it["motion_frames"])
    neg = "字幕，文字，静止不动，模糊，畸形的脸，多余的手指，背景人很多"
    wf = {
        "unet": {"class_type": "UNETLoader", "inputs": {"unet_name": it["unet"], "weight_dtype": "default"}},
        "lora": {"class_type": "LoraLoaderModelOnly", "inputs": {"model": ["unet", 0], "lora_name": it["lora"], "strength_model": 1.0}},
        "shift": {"class_type": "ModelSamplingSD3", "inputs": {"model": ["lora", 0], "shift": float(it["shift"])}},
        "patch": {"class_type": "ModelPatchLoader", "inputs": {"name": it["patch"]}},
        "clip": {"class_type": "CLIPLoader", "inputs": {"clip_name": it["clip"], "type": "wan"}},
        "pos": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["clip", 0], "text": prompt}},
        "neg": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["clip", 0], "text": neg}},
        "vae": {"class_type": "VAELoader", "inputs": {"vae_name": it["vae"]}},
        "cvl": {"class_type": "CLIPVisionLoader", "inputs": {"clip_name": it["clip_vision"]}},
        "img": {"class_type": "LoadImage", "inputs": {"image": image}},
        "cve": {"class_type": "CLIPVisionEncode", "inputs": {"clip_vision": ["cvl", 0], "image": ["img", 0], "crop": "none"}},
        "ael": {"class_type": "AudioEncoderLoader", "inputs": {"audio_encoder_name": it["audio_encoder"]}},
        "aud": {"class_type": "LoadAudio", "inputs": {"audio": audio}},
        "ae": {"class_type": "AudioEncoderEncode", "inputs": {"audio_encoder": ["ael", 0], "audio": ["aud", 0]}},
    }
    chunks = 1 + max(0, -(-(frames - LEN) // (LEN - MOTION)))
    prev = None
    for k in range(chunks):
        node = {"mode": "single_speaker", "model": ["shift", 0], "model_patch": ["patch", 0], "positive": ["pos", 0],
                "negative": ["neg", 0], "vae": ["vae", 0], "width": W, "height": H, "length": LEN,
                "clip_vision_output": ["cve", 0], "start_image": ["img", 0], "audio_encoder_output_1": ["ae", 0],
                "motion_frame_count": MOTION, "audio_scale": 1.0}
        if prev:
            node["previous_frames"] = [prev, 0]
        wf[f"it{k}"] = {"class_type": "WanInfiniteTalkToVideo", "inputs": node}
        wf[f"ks{k}"] = {"class_type": "KSampler", "inputs": {"model": [f"it{k}", 0], "positive": [f"it{k}", 1],
                        "negative": [f"it{k}", 2], "latent_image": [f"it{k}", 3], "seed": int(seed) + k,
                        "steps": int(it["steps"]), "cfg": 1.0, "sampler_name": "euler", "scheduler": "simple", "denoise": 1.0}}
        wf[f"dec{k}"] = {"class_type": "VAEDecode", "inputs": {"samples": [f"ks{k}", 0], "vae": ["vae", 0]}}
        if prev is None:
            prev = f"dec{k}"
        else:
            wf[f"trim{k}"] = {"class_type": "ImageFromBatch", "inputs": {"image": [f"dec{k}", 0], "batch_index": MOTION, "length": LEN - MOTION}}
            wf[f"cat{k}"] = {"class_type": "ImageBatch", "inputs": {"image1": [prev, 0], "image2": [f"trim{k}", 0]}}
            prev = f"cat{k}"
    wf["vid"] = {"class_type": "CreateVideo", "inputs": {"images": [prev, 0], "fps": float(it["fps"])}}
    wf["save"] = {"class_type": "SaveVideo", "inputs": {"video": ["vid", 0], "filename_prefix": prefix, "format": "mp4", "codec": "auto"}}
    return wf
