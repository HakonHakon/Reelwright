"""Story -> bible draft, scenes and shots. The LLM is schema-constrained to bible ids."""

import math
import re

from . import llm, store

FRAMINGS = ["extreme wide", "wide", "full", "medium wide", "medium", "medium close-up", "close-up",
            "extreme close-up", "over-the-shoulder", "two-shot", "insert"]
POSITIONS = ["left", "right", "center", "background", "foreground"]
START_MODES = ["new", "chain", "reuse", "punch_in"]
WIDE_FRAMINGS = {"extreme wide", "wide", "full", "medium wide"}
PUNCH_FRAMINGS = {"medium", "medium close-up", "close-up", "extreme close-up", "over-the-shoulder", "two-shot"}


def _ids_schema(ids: list[str]) -> dict:
    return {"type": "string", "enum": ids} if ids else {"type": "string"}


# ── Bible draft ─────────────────────────────────────────────────────────

BIBLE_SCHEMA = {
    "type": "object",
    "properties": {
        "characters": {"type": "array", "items": {"type": "object", "properties": {
            "name": {"type": "string"},
            "appearance": {"type": "string", "description": "50-80 words: age, build, face, hair, skin, eyes"},
            "outfit": {"type": "string"},
            "voice": {"type": "string", "description": "how they sound"},
        }, "required": ["name", "appearance", "outfit"]}},
        "locations": {"type": "array", "items": {"type": "object", "properties": {
            "name": {"type": "string"},
            "description": {"type": "string", "description": "40-70 words: layout, materials, key objects"},
            "time_of_day": {"type": "string"},
            "ambience": {"type": "string"},
        }, "required": ["name", "description"]}},
    },
    "required": ["characters", "locations"],
}


def draft_bible(s: dict, sid: str, story: str) -> dict:
    existing_c = {c["name"].lower() for c in store.list_characters(sid)}
    existing_l = {l["name"].lower() for l in store.list_locations(sid)}
    data = llm.chat_json(
        s["ollama_host"], s["planner_model"],
        "You are a film pre-production assistant. Extract every recurring character and every location "
        "from the story. Write concrete visual descriptions a concept artist could draw from. "
        "Do not invent characters or places that are not in the story.",
        f"STORY:\n{story}", BIBLE_SCHEMA, temperature=0.4)
    added = {"characters": [], "locations": []}
    for c in data.get("characters", []):
        if not c.get("name") or c["name"].lower() in existing_c:
            continue
        ch = store.new_character(c["name"], appearance=c.get("appearance", ""),
                                 outfits={"default": c.get("outfit", "")}, draft=True)
        ch["voice"]["description"] = c.get("voice", "")
        added["characters"].append(store.save_character(sid, ch, bump=False)["id"])
    for l in data.get("locations", []):
        if not l.get("name") or l["name"].lower() in existing_l:
            continue
        tod = (l.get("time_of_day") or "day").strip().lower() or "day"
        loc = store.new_location(l["name"], description=l.get("description", ""),
                                 variants={tod: ""}, ambience=l.get("ambience", ""), draft=True)
        added["locations"].append(store.save_location(sid, loc, bump=False)["id"])
    return added


# ── Scene pass ──────────────────────────────────────────────────────────

def _bible_brief(b: dict) -> str:
    lines = ["CHARACTERS (use these ids exactly):"]
    for c in b["characters"].values():
        outfits = ", ".join((c.get("outfits") or {}).keys())
        lines.append(f"- {c['id']}: {c['name']}. {c.get('appearance', '')[:160]} Outfits: {outfits}")
    lines.append("LOCATIONS (use these ids exactly):")
    for l in b["locations"].values():
        variants = ", ".join((l.get("variants") or {}).keys())
        lines.append(f"- {l['id']}: {l['name']}. {l.get('description', '')[:160]} Times: {variants}")
    props = b.get("props") or []
    if props:
        lines.append("PROPS: " + "; ".join(f"{p.get('id')}: {p.get('description', '')}" for p in props))
    return "\n".join(lines)


def _scene_schema(b: dict) -> dict:
    cids, lids = list(b["characters"].keys()), list(b["locations"].keys())
    return {
        "type": "object",
        "properties": {"scenes": {"type": "array", "items": {"type": "object", "properties": {
            "location_id": _ids_schema(lids),
            "time_of_day": {"type": "string"},
            "summary": {"type": "string"},
            "characters": {"type": "array", "items": {"type": "object", "properties": {
                "id": _ids_schema(cids), "outfit": {"type": "string"},
                "condition": {"type": "string", "description": "e.g. wet, injured, holding umbrella; empty if none"},
            }, "required": ["id", "outfit"]}},
            "beats": {"type": "array", "items": {"type": "string"}},
            "dialogue": {"type": "array", "items": {"type": "object", "properties": {
                "speaker": _ids_schema(cids), "line": {"type": "string"}}, "required": ["speaker", "line"]}},
        }, "required": ["location_id", "summary", "characters", "beats", "dialogue"]}}},
        "required": ["scenes"],
    }


def plan_scenes(s: dict, b: dict, story: str) -> list[dict]:
    if not b["locations"]:
        raise ValueError("Add at least one location to the bible first (or use 'Draft bible from story').")
    data = llm.chat_json(
        s["ollama_host"], s["planner_model"],
        "You break stories into film scenes. A new scene starts when the location or time changes. "
        "Copy dialogue word for word from the story. Use only the given character and location ids. "
        "Beats are short visual actions in story order; include every visual moment of the story "
        "(arrivals, reveals, weather and light changes), each as its own beat, with any line spoken at that moment.",
        f"{_bible_brief(b)}\n\nSTORY:\n{story}", _scene_schema(b), temperature=0.5)
    scenes = []
    for i, sc in enumerate(data.get("scenes", []), 1):
        sc["id"] = f"S{i:02d}"
        scenes.append(sc)
    if not scenes:
        raise ValueError("The planner returned no scenes. Try a longer story or another model.")
    return scenes


# ── Shot pass ───────────────────────────────────────────────────────────

def _shot_schema(b: dict, scene: dict) -> dict:
    cids = [c["id"] for c in scene.get("characters", [])] or list(b["characters"].keys())
    return {
        "type": "object",
        "properties": {"shots": {"type": "array", "items": {"type": "object", "properties": {
            "framing": {"type": "string", "enum": FRAMINGS},
            "camera": {"type": "string", "description": "camera angle and movement, e.g. slow push-in, eye level"},
            "characters": {"type": "array", "items": {"type": "object", "properties": {
                "id": _ids_schema(cids), "position": {"type": "string", "enum": POSITIONS}},
                "required": ["id", "position"]}},
            "action": {"type": "string", "description": "one main visible action"},
            "dialogue": {"type": "array", "items": {"type": "object", "properties": {
                "speaker": _ids_schema(cids), "line": {"type": "string"}}, "required": ["speaker", "line"]}},
            "sound": {"type": "string"},
            "action_seconds": {"type": "number", "description": "seconds of action besides speech, 1-6"},
            "continuous_with_previous": {"type": "boolean"},
        }, "required": ["framing", "camera", "characters", "action", "dialogue", "action_seconds"]}}},
        "required": ["shots"],
    }


def plan_shots(s: dict, b: dict, scene: dict) -> list[dict]:
    rules = (
        "Rules: one main action per shot; at most two speaking characters in frame; "
        "split long dialogue across shots so no shot has more than about 20 words; "
        "open the scene with a wider shot; keep each character on the same side of frame within the scene; "
        "set continuous_with_previous only when the action flows across the cut. "
        "Every dialogue line of the scene must appear exactly once across the shots, word for word, "
        "and must sit in the shot that shows the moment it is spoken: a line said in reaction to something "
        "goes in the shot AFTER that something is visible (e.g. a greeting to an animal only after the animal "
        "has arrived on screen). Cover every beat with at least one shot, in story order, including visual "
        "moments without dialogue (weather changes, a reveal, a wide establishing view). Name concrete props "
        "consistently with the story (a paper boat stays a paper boat)."
    )
    data = llm.chat_json(
        s["ollama_host"], s["planner_model"],
        "You are a film director writing a shot list. " + rules,
        f"{_bible_brief(b)}\n\nSCENE {scene['id']} at {scene.get('location_id')} ({scene.get('time_of_day', '')}):\n"
        f"Summary: {scene.get('summary', '')}\nCharacters: {scene.get('characters')}\n"
        f"Beats: {scene.get('beats')}\nDialogue: {scene.get('dialogue')}",
        _shot_schema(b, scene), temperature=0.6)
    outfits = {c["id"]: c.get("outfit") for c in scene.get("characters", [])}
    shots = []
    for i, sh in enumerate(data.get("shots", []), 1):
        for ch in sh.get("characters", []):
            ch["outfit"] = _match_outfit(b, ch["id"], outfits.get(ch["id"]))
        shots.append(new_shot(
            scene_id=scene["id"], id=f"{scene['id']}_SH{i:02d}",
            location_id=scene.get("location_id"), time_of_day=_match_tod(b, scene),
            framing=sh.get("framing", "medium"), camera=sh.get("camera", ""),
            characters=sh.get("characters", []), action=sh.get("action", ""),
            dialogue=[d for d in sh.get("dialogue", []) if d.get("line", "").strip()],
            sound=sh.get("sound", ""), action_seconds=float(sh.get("action_seconds") or 2),
            start_mode="chain" if sh.get("continuous_with_previous") and i > 1 else "new"))
    return shots


def _match_outfit(b, cid, outfit_text):
    c = b["characters"].get(cid)
    if not c:
        return "default"
    keys = list((c.get("outfits") or {"default": ""}).keys())
    t = (outfit_text or "").lower()
    for k in keys:
        if k.lower() in t or t in k.lower():
            return k
    return c.get("default_outfit") or keys[0]


def _match_tod(b, scene):
    loc = b["locations"].get(scene.get("location_id") or "")
    t = (scene.get("time_of_day") or "").lower()
    if not loc:
        return t
    for k in (loc.get("variants") or {}):
        if k.lower() in t or t in k.lower():
            return k
    return next(iter(loc.get("variants") or {"day": ""}), t)


def new_shot(**fields) -> dict:
    shot = {
        "id": "", "scene_id": "", "order": 0, "location_id": "", "time_of_day": "",
        "framing": "medium", "camera": "", "characters": [], "action": "", "dialogue": [],
        "sound": "", "action_seconds": 2.0, "duration_s": 0.0, "duration_locked": False,
        "start_mode": "new", "frame_source": "", "engine": "", "end_frame": "",
        "image_prompt_extra": "", "motion_prompt_extra": "",
        "image_prompt_override": "", "motion_prompt_override": "",
        "frame": {"candidates": [], "selected": None, "locked": False, "bible_sig": ""},
        "takes": {"items": [], "selected": None, "locked": False, "trim_in": 0.0, "trim_out": 0.0},
        "notes": "",
    }
    shot.update(fields)
    return shot


# ── Durations ───────────────────────────────────────────────────────────

def compute_duration(shot: dict, b: dict, max_seconds: float) -> float:
    if shot.get("duration_locked") and shot.get("duration_s"):
        return float(shot["duration_s"])
    speech = 0.0
    for d in shot.get("dialogue", []) or []:
        c = b["characters"].get(d.get("speaker"))
        wpm = float(((c or {}).get("voice") or {}).get("wpm") or 140)
        words = len(re.findall(r"\w+", d.get("line", "")))
        speech += words / wpm * 60.0 + 0.3
    total = speech + float(shot.get("action_seconds") or 0) + 0.5
    return round(min(max(total, 2.0), max_seconds), 1)


def plan_episode(s: dict, sid: str, story: str, log=print) -> tuple[list, list]:
    b = store.bible(sid)
    log("Scene pass...")
    scenes = plan_scenes(s, b, story)
    log(f"  {len(scenes)} scenes")
    shots = []
    for sc in scenes:
        log(f"Shot pass for {sc['id']} ({sc.get('summary', '')[:60]})...")
        sc_shots = plan_shots(s, b, sc)
        log(f"  {len(sc_shots)} shots")
        shots.extend(sc_shots)
    for sh in shots:
        sh["duration_s"] = compute_duration(sh, b, s["ltx"]["max_seconds"])
    return scenes, shots


def replan_scene(s: dict, sid: str, scene: dict) -> list[dict]:
    b = store.bible(sid)
    shots = plan_shots(s, b, scene)
    for sh in shots:
        sh["duration_s"] = compute_duration(sh, b, s["ltx"]["max_seconds"])
    return shots


def runtime_seconds(shots: list[dict]) -> float:
    return round(sum(float(sh.get("duration_s") or 0) for sh in shots), 1)


def estimate_render_seconds(shots: list[dict], spf: dict, takes: int, default_spf: float = 2.5) -> float:
    total = 0.0
    for sh in shots:
        eng = sh.get("engine") or "ltx"
        frames = math.ceil(float(sh.get("duration_s") or 0) * 24)
        total += frames * float(spf.get(eng) or default_spf) * takes
    return total



def assign_continuity(shots: list[dict]) -> list[dict]:
    """Film-coverage continuity: one master per scene, closer shots punched in from it,
    returns to the master angle reuse it, continuous action chains. Locked shots are left alone."""
    master_of: dict[str, dict] = {}
    for sh in sorted(shots, key=lambda x: x.get("order", 0)):
        if sh.get("frame", {}).get("locked"):
            continue
        scene = sh.get("scene_id") or ""
        framing = (sh.get("framing") or "").lower()
        has_chars = bool(sh.get("characters"))
        master = master_of.get(scene)
        if master is None:
            sh["start_mode"] = "new"
            master_of[scene] = sh
            continue
        if sh.get("start_mode") == "chain":
            continue
        if not has_chars:
            sh["start_mode"] = "new"            # inserts / cutaways
        elif framing in WIDE_FRAMINGS:
            sh["start_mode"] = "reuse"          # back to the master angle
        elif framing in PUNCH_FRAMINGS:
            sh["start_mode"] = "punch_in"       # coverage cut from the master
        else:
            sh["start_mode"] = "new"
    return shots
