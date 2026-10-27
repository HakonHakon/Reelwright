"""Deterministic prompt assembly: bible text copied verbatim + shot fields.

The LLM never writes final prompts. Editing a bible entry changes every prompt that uses it.
"""

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
        c = bible["characters"].get(ch.get("id"))
        if c:
            out.append((c, ch))
    return out


def image_prompt(shot: dict, bible: dict) -> str:
    """First-frame prompt: a still moment, composition first."""
    if shot.get("image_prompt_override"):
        return shot["image_prompt_override"]
    style = bible["style"]
    loc = bible["locations"].get(shot.get("location_id") or "")
    framing = (shot.get("framing") or "").lower()
    close = framing in CLOSE_FRAMINGS
    chars = []
    for c, ch in characters_in(shot, bible):
        pos = "" if close else POSITION_WORDS.get((ch.get("position") or "").lower(), ch.get("position") or "")
        chars.append(f"{_char_text(c, ch.get('outfit'), True)}{' ' + pos if pos else ''}")
    # Close shots: framing + subject first, and only the light/palette of the location.
    # A full room description makes Flux pull the camera back to a wide shot.
    if loc and close:
        variant = (loc.get("variants") or {}).get(shot.get("time_of_day") or "", "")
        setting = ", ".join(x for x in [variant, loc.get("palette", "")] if x)
    else:
        setting = _location_text(loc, shot.get("time_of_day")) if loc else ""
    lines = [
        f"{framing.capitalize()} shot" if framing else "",
        f"Moment: {shot.get('action', '')}" if close and shot.get("action") else "",
        "; ".join(chars),
        f"Setting: {setting}" if setting else "",
        "" if close else (f"Moment: {shot.get('action', '')}" if shot.get("action") else ""),
        shot.get("image_prompt_extra", ""),
        style.get("look", ""),
        style.get("grade", ""),
    ]
    return ". ".join(l.strip().rstrip(".") for l in lines if l and l.strip()) + "."


def motion_prompt(shot: dict, bible: dict, engine: str = "ltx") -> str:
    """Video prompt. LTX gets dialogue and sound; Wan gets a short silent action prompt."""
    if shot.get("motion_prompt_override"):
        return shot["motion_prompt_override"]
    style = bible["style"]
    loc = bible["locations"].get(shot.get("location_id") or "")
    chars = [_char_text(c, ch.get("outfit"), False) for c, ch in characters_in(shot, bible)]
    if engine == "wan":
        lines = [style.get("look", ""), "; ".join(chars), shot.get("action", ""),
                 f"Camera: {shot.get('camera', '')}" if shot.get("camera") else "",
                 shot.get("motion_prompt_extra", "")]
        return ". ".join(l.strip().rstrip(".") for l in lines if l and l.strip()) + "."

    camera = (shot.get("camera") or "").strip() or "static camera"
    framing = (shot.get("framing") or "").lower()
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
        f"Light: {setting}" if from_frame and setting else (f"Setting: {setting}" if setting else ""),
        style.get("look", ""),
    ]
    for d in shot.get("dialogue", []) or []:
        c = bible["characters"].get(d.get("speaker"))
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
            loras.append({"name": lora["name"], "strength": round(strength, 3)})
    return loras


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
        if (c.get("image_lora") or {}).get("name"):
            return bible["style"].get("default_frame_source") or "flux_lora"
    src = bible["style"].get("default_frame_source") or "flux_lora"
    # Shots without any LoRA character gain nothing from Flux; Z-Image is faster.
    return "zimage" if src == "flux_lora" and not image_loras(shot, bible, 1.0) else src


def speaker_voice(shot: dict, bible: dict) -> str | None:
    """Voice sample of the first speaker in the shot (LTX reference audio)."""
    for d in shot.get("dialogue", []) or []:
        c = bible["characters"].get(d.get("speaker"))
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


def location_plate_prompt(loc: dict, variant: str, bible: dict) -> str:
    style = bible["style"]
    return (f"{style.get('look', '')}. Wide establishing shot, no people. "
            f"{_location_text(loc, variant)}. {style.get('grade', '')}")
