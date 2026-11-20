"""Deterministic prompt assembly: bible text copied verbatim + shot fields.

The LLM never writes final prompts. Editing a bible entry changes every prompt that uses it.
"""

import re

from . import blocking, store

CLOSE_FRAMINGS = {"medium close-up", "close-up", "extreme close-up", "insert"}

POSITION_WORDS = {"left": "on the left of the frame", "right": "on the right of the frame",
                  "center": "in the centre of the frame", "background": "in the background",
                  "foreground": "in the foreground"}


def _char_text(c: dict, outfit: str | None, with_trigger: bool) -> str:
    outfit_key = outfit or c.get("default_outfit") or "default"
    outfit_desc = (c.get("outfits") or {}).get(outfit_key) or (c.get("outfits") or {}).get("default", "")
    parts = []
    if with_trigger and c.get("trigger"):
        parts.append(c["trigger"])
    parts.append(c.get("name", c["id"]))
    desc = ", ".join(p for p in [c.get("appearance", "").strip(), outfit_desc.strip()] if p)
    return f"{' '.join(parts)} ({desc})" if desc else " ".join(parts)


def _location_text(loc: dict | None, tod: str | None) -> str:
    if not loc:
        return ""
    variant = ""
    if tod:
        variant = (loc.get("variants") or {}).get(tod, "") or tod
    bits = [loc.get("description", "").strip(), variant.strip(), loc.get("palette", "").strip()]
    return ", ".join(b for b in bits if b)


def characters_in(shot: dict, bible: dict) -> list[tuple[dict, dict]]:
    out = []
    for ch in shot.get("characters", []):
        c = store.find_character(bible["characters"], ch.get("id"))
        if c:
            out.append((c, ch))
    return out


_PARTS = r"(?:shoes?|feet|foot|legs?|knees?|hands?|fingers?|fists?|arms?|wrists?|boots?|sneakers?|torso|chest)"
_PARTS_ONLY = re.compile(
    rf"\bonly (?P<what>(?:the |his |her |their |a |an )?(?:[\w'-]+ ){{0,4}}{_PARTS}\b[^.]{{0,40}}?)\s+(?:are |is )?"
    rf"(?:in frame|in the frame|in shot|visible|seen)|\b(?:no|not the|never the) (?:face|head)\b", re.I)
_LOWER_CLOTHES = re.compile(r"\b(trousers|pants|jeans|shorts|skirt|socks?|shoes?|boots?|sneakers?|trainers|"
                            r"leggings|tights|sandals)\b", re.I)
_LOW = re.compile(r"\b(shoes?|feet|foot|legs?|knees?|boots?|sneakers?)\b", re.I)


def body_parts_only(action: str) -> bool:
    """True when a shot's action says only hands, shoes, legs ... are in frame ('Only the shoes and lower legs
    are in frame'), so the character's face must not be described."""
    return bool(_PARTS_ONLY.search(action or ""))


def _insert_lead(action: str) -> str:
    """The first words of an insert prompt name what fills the frame: Z-Image composes from the opening words, and
    "Tight insert shot" alone still drew a whole child in a doorway (Hirad S02_SH01)."""
    m = _PARTS_ONLY.search(action or "")
    what = (m.group("what") or "").strip() if m and m.group("what") else ""
    if not what:
        return "Extreme close-up insert shot, camera right next to the subject"
    level = "at floor level, " if _LOW.search(what) else ""
    return f"Extreme close-up insert shot {level}filling the whole frame with {what}"


def _ensure_image_lora_triggers(prompt: str, shot: dict, bible: dict) -> str:
    """Always pair a selected image LoRA with its trained trigger in image prompts."""
    missing = []
    for character, _ in characters_in(shot, bible):
        if not (character.get("image_lora") or {}).get("name"):
            continue
        token = (character.get("trigger") or "").strip()
        if token and not re.search(rf"(?<!\w){re.escape(token)}(?!\w)", prompt):
            missing.append(token)
    return f"{', '.join(missing)}, {prompt}" if missing else prompt


def image_prompt(shot: dict, bible: dict, composition: bool = False) -> str:
    """First-frame prompt: a still moment, composition first."""
    if shot.get("first_frame") is not None:
        return _ensure_image_lora_triggers(blocking.image_prompt(shot, bible, composition=composition), shot, bible)
    if shot.get("image_prompt_override"):
        return _ensure_image_lora_triggers(shot["image_prompt_override"], shot, bible)
    style = bible["style"]
    loc = bible["locations"].get(shot.get("location_id") or "")
    framing = (shot.get("framing") or "").lower()
    close = framing in CLOSE_FRAMINGS
    parts_only = close and body_parts_only(shot.get("action", ""))
    chars = []
    for c, ch in characters_in(shot, bible):
        pos = "" if close else POSITION_WORDS.get((ch.get("position") or "").lower(), ch.get("position") or "")
        if parts_only:      # naming the person or describing the face makes Z-Image draw the whole person
            outfit = (c.get("outfits") or {}).get(ch.get("outfit") or c.get("default_outfit") or "default", "")
            if _LOW.search(shot.get("action", "")):      # a hoodie or backpack brings the torso back into frame
                outfit = ", ".join(x.strip() for x in outfit.split(",") if _LOWER_CLOTHES.search(x))
            if outfit:      # after the crop, so trousers and shoes match the other shots without re-adding a body
                chars.append(f"Clothes, only where the frame shows them: {outfit}")
            continue
        chars.append(f"{_char_text(c, ch.get('outfit'), True)}{' ' + pos if pos else ''}")
    # Close shots: framing + subject first, and only the light/palette of the location.
    # A full room description makes Flux pull the camera back to a wide shot.
    if loc and close:
        variant = (loc.get("variants") or {}).get(shot.get("time_of_day") or "", "")
        setting = ", ".join(x for x in [variant, loc.get("palette", "")] if x)
    else:
        setting = _location_text(loc, shot.get("time_of_day")) if loc else ""
    crop = ("The frame is cropped tight to what the moment names; the rest of the person is cut off by the frame "
            "edge: no face, no head, no upper body in the picture") if parts_only else ""
    lines = [
        _insert_lead(shot.get("action", "")) if parts_only else
        (f"{framing.capitalize()} shot" if framing else ""),
        f"Moment: {shot.get('action', '')}" if close and shot.get("action") else "",
        crop,
        "; ".join(chars),
        f"Setting: {setting}" if setting else "",
        "" if close else (f"Moment: {shot.get('action', '')}" if shot.get("action") else ""),
        # An insert drops the shot's extra notes and the look: their wardrobe, cast and "kid performances"
        # wording makes Z-Image draw the whole child again (measured on Hirad S02_SH01). The grade stays.
        "" if parts_only else shot.get("image_prompt_extra", ""),
        "" if parts_only else style.get("look", ""),
        style.get("grade", ""),
        crop,               # repeated last: the shot's extra notes (wardrobe, cast) pull the whole figure back in
    ]
    prompt = ". ".join(l.strip().rstrip(".") for l in lines if l and l.strip()) + "."
    return _ensure_image_lora_triggers(prompt, shot, bible)


def motion_prompt(shot: dict, bible: dict, engine: str = "ltx") -> str:
    """Video prompt. LTX gets dialogue and sound; Wan gets a short silent action prompt."""
    if shot.get("motion_prompt_override"):
        return shot["motion_prompt_override"]
    style = bible["style"]
    loc = bible["locations"].get(shot.get("location_id") or "")
    chars = [_char_text(c, ch.get("outfit"), False) for c, ch in characters_in(shot, bible)]
    if engine == "wan":
        # a character's video LoRA (applied to Wan renders) answers to its trigger word, e.g. "s4m"
        chars = [f"{_video_trigger(c)}, {t}" if _video_trigger(c) else t
                 for (c, _), t in zip(characters_in(shot, bible), chars)]
        previz = shot.get("previz") or {}
        previz_movement = (previz.get("movement") or "").strip()
        lines = [style.get("look", ""), "; ".join(chars), shot.get("action", ""),
                 f"Movement: {previz_movement}" if previz_movement else "",
                 f"Camera: {shot.get('camera', '')}" if shot.get("camera") else "",
                 shot.get("motion_prompt_extra", "")]
        return ". ".join(l.strip().rstrip(".") for l in lines if l and l.strip()) + "."

    camera = (shot.get("camera") or "").strip() or "static camera"
    framing = (shot.get("framing") or "").lower()
    # Previz movement: the approved 3D blockout describes what happens in the shot.
    previz = shot.get("previz") or {}
    previz_movement = (previz.get("movement") or "").strip()
    # The first frame already carries the room; repeating the full description makes LTX
    # re-compose the shot (a close-up drifts into a wide). Keep only light for close/derived shots.
    from_frame = shot.get("start_mode") in ("chain", "reuse", "punch_in") or framing in CLOSE_FRAMINGS
    if loc and from_frame:
        variant = (loc.get("variants") or {}).get(shot.get("time_of_day") or "", "")
        setting = variant
    else:
        setting = _location_text(loc, shot.get("time_of_day")) if loc else ""
    hold = "" if any(w in camera.lower() for w in ("push", "pull", "pan", "tilt", "track", "dolly", "handheld", "follow", "zoom", "orbit")) \
        else "The camera holds this exact framing and composition from the first frame, no reframing"
    lines = [
        f"{framing} shot, {camera}".strip(", "),
        hold,
        "Characters: " + "; ".join(chars) if chars else "",
        ("The same person stays on screen the whole shot; age, face, hair and clothing do not change"
         if chars else ""),
        f"Action: {shot.get('action', '')}" if shot.get("action") else "",
        f"Movement: {previz_movement}" if previz_movement else "",
        f"Light: {setting}" if from_frame and setting else (f"Setting: {setting}" if setting else ""),
        style.get("look", ""),
    ]
    for d in shot.get("dialogue", []) or []:
        c = store.find_character(bible["characters"], d.get("speaker"))
        who = c["name"] if c else d.get("speaker", "")
        voice = f" ({c['voice'].get('description')})" if c and (c.get("voice") or {}).get("description") else ""
        lines.append(f'{who} says{voice}: "{d.get("line", "").strip()}"')
    sound = ", ".join(x for x in [shot.get("sound", ""), (loc or {}).get("ambience", "")] if x)
    if sound:
        lines.append(f"Sound: {sound}")
    lines.append(shot.get("motion_prompt_extra", ""))
    return ". ".join(l.strip().rstrip(".") for l in lines if l and l.strip()) + "."


def image_loras(shot: dict, bible: dict, second_scale: float) -> list[dict]:
    """Character image LoRAs in frame order; extra characters are scaled down to limit bleed."""
    loras = []
    for i, (c, _) in enumerate(characters_in(shot, bible)):
        lora = c.get("image_lora") or {}
        if lora.get("name"):
            strength = float(lora.get("strength", 0.9)) * (1.0 if i == 0 else second_scale)
            loras.append({"name": lora["name"], "strength": round(strength, 3),
                          "model_family": lora.get("model_family", "flux1")})
    return loras


def _video_trigger(c: dict) -> str:
    lora = c.get("video_lora") or {}
    return (lora.get("trigger") or "").strip() if lora.get("name") else ""


def video_loras(shot: dict, bible: dict) -> list[dict]:
    loras = []
    for c, _ in characters_in(shot, bible):
        lora = c.get("video_lora") or {}
        if lora.get("name"):
            loras.append({"name": lora["name"], "strength": float(lora.get("strength", 0.8))})
    return loras


def frame_source(shot: dict, bible: dict) -> str:
    """Shot override > first character's setting > series default."""
    if shot.get("frame_source"):
        return shot["frame_source"]
    for c, _ in characters_in(shot, bible):
        if c.get("frame_source"):
            return c["frame_source"]
        if (c.get("image_lora") or {}).get("name"):     # a trained LoRA beats photos: keep Flux for those characters
            src = bible["style"].get("default_frame_source") or "flux_lora"
            return "flux_lora" if src in ("qwen_2511", "qwen_refs") else src
    src = bible["style"].get("default_frame_source") or "flux_lora"
    # Shots without any LoRA character gain nothing from Flux; Z-Image is faster.
    return "zimage" if src == "flux_lora" and not image_loras(shot, bible, 1.0) else src


def speaker_voice(shot: dict, bible: dict) -> str | None:
    """Voice sample of the first speaker in the shot (LTX reference audio)."""
    for d in shot.get("dialogue", []) or []:
        c = store.find_character(bible["characters"], d.get("speaker"))
        if c and (c.get("voice") or {}).get("file"):
            return c["voice"]["file"]
    return None


def character_sheet_prompts(c: dict, bible: dict) -> list[tuple[str, str]]:
    style = bible["style"]
    who = _char_text(c, None, True)
    base = f"{style.get('look', '')}. {who}. Plain neutral studio background, soft even light. {style.get('grade', '')}"
    return [
        ("front", f"Front view portrait, looking at camera. {base}"),
        ("three_quarter", f"Three-quarter view, head and shoulders. {base}"),
        ("full_body", f"Full body, standing, front view, whole outfit visible. {base}"),
        ("profile", f"Side profile view, head and shoulders. {base}"),
    ]


def qwen_sheet_prompts(c: dict) -> list[tuple[str, str, tuple[int, int]]]:
    """(view, Qwen Edit prompt, (width, height)) for a sheet redrawn from the character's photo (image 1)."""
    keep = (f"Image 1 is {c.get('name') or 'the person'}. Show this exact same person: identical face, facial "
            "features, hair, skin tone, age and body build. A new photograph, not a copy of image 1: plain light grey "
            "studio background, soft even light, neutral relaxed expression, photorealistic, sharp focus.")
    head, full = (1024, 1024), (832, 1216)
    return [
        ("front", f"{keep} Front view, head and shoulders, facing the camera, looking into the lens.", head),
        ("three_quarter", f"{keep} Three-quarter view: head and body turned 45 degrees to the left, head and "
                          "shoulders.", head),
        ("profile", f"{keep} Side profile: head turned 90 degrees to the left, we see one side of the face, "
                    "head and shoulders.", head),
        ("full_body", f"{keep} Full body from head to feet, standing upright, front view, arms relaxed, the "
                      "whole outfit visible.", full),
    ]


def location_plate_prompt(loc: dict, variant: str, bible: dict) -> str:
    style = bible["style"]
    return (f"{style.get('look', '')}. Wide establishing shot, no people. "
            f"{_location_text(loc, variant)}. {style.get('grade', '')}")


def infinitetalk_sing_prompt(shot: dict, bible: dict) -> str:
    """InfiniteTalk driven by the song's isolated vocals: the face sings along with its window of the song."""
    return (f"{shot.get('framing', '')} shot, camera: {shot.get('camera') or 'static'}. {shot.get('action', '')} "
            "The person facing the camera sings along with the song, lips following the sung words, and keeps "
            "the lips closed in the pauses. Same place, same light, same face and clothes as the first frame. "
            "Natural, heartfelt singing, natural blinking. Photorealistic, cinematic.")


def infinitetalk_prompt(shot: dict, bible: dict) -> str:
    """What the shot shows; the mouth comes from the audio, so no dialogue words (quoted words get painted as text)."""
    return (f"{shot.get('framing', '')} shot, camera: {shot.get('camera') or 'static'}. {shot.get('action', '')} "
            "The person facing the camera speaks only when the voice is heard and listens with lips closed otherwise. "
            "Same place, same light, same faces and clothes as the first frame. Restrained, natural acting, natural "
            "blinking. Photorealistic, cinematic.")
