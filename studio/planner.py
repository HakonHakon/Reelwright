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
    data = llm.chat(
        s, "planner",
        "You are a film pre-production assistant. Extract every recurring character and every location "
        "from the story. Write concrete visual descriptions a concept artist could draw from. "
        "Do not invent characters. Besides the places the story names, add the in-between places a "
        "character must pass through when the story moves them from one place to another and a real film "
        "would show it (e.g. the lift and lobby of their building, the street outside, the back seat of a car), "
        "but only places implied by the geography of the story.",
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


TRANSITION_RULES = (
    "Geography: when a character leaves one location and appears in a different one, end the earlier scene "
    "with a clear exit beat (they cross to the door, leave) and open the later scene with an arrival or travel beat."
)


def plan_scenes(s: dict, b: dict, story: str) -> list[dict]:
    if not b["locations"]:
        raise ValueError("Add at least one location to the bible first (or use 'Draft bible from story').")
    data = llm.chat(
        s, "planner",
        "You break stories into film scenes. A new scene starts when the location or time changes. "
        "Copy dialogue word for word from the story. Use only the given character and location ids. "
        "Beats are short visual actions in story order; include every visual moment of the story "
        "(arrivals, reveals, weather and light changes), each as its own beat, with any line spoken at that moment. "
        + TRANSITION_RULES,
        f"{_bible_brief(b)}\n\nSTORY:\n{story}", _scene_schema(b), temperature=0.5)
    scenes = []
    for i, sc in enumerate(data.get("scenes", []), 1):
        sc["id"] = f"S{i:02d}"
        store.normalize_cast(b["characters"], sc)
        scenes.append(sc)
    if not scenes:
        raise ValueError("The planner returned no scenes. Try a longer story or another model.")
    return scenes


# ── Bridge pass: how did they get there? ────────────────────────────────

TIME_JUMP = re.compile(r"\b(later|next (day|morning|night|week)|the following|meanwhile|flashback|years? (ago|later)|"
                       r"weeks? later|days? later|hours? later|that (morning|evening|night)|elsewhere)\b", re.I)


MOVES = ["through one door or a few steps", "a real journey", "an intentional time jump"]
_GENERIC_PLACE_WORDS = {"interior", "exterior", "inside", "outside", "room", "street", "road", "house", "home",
                        "the", "and", "night", "day", "front", "back"}


def _place_words(loc: dict | None) -> set[str]:
    text = f"{(loc or {}).get('id', '')} {(loc or {}).get('name', '')}".lower()
    return {w.removesuffix("s") for w in re.findall(r"[a-z]{4,}", text)} - _GENERIC_PLACE_WORDS


def _same_place(b: dict, la: str, lb: str) -> bool:
    """'Maryam's Bakery' and 'Bakery Exterior' are two sides of one door."""
    return bool(_place_words(b["locations"].get(la)) & _place_words(b["locations"].get(lb)))


def _bridge_schema(lids: list[str]) -> dict:
    return {"type": "object", "properties": {
        "move": {"type": "string", "enum": MOVES, "description": "decide this first"},
        "location_id": {"type": "string", "enum": lids + ["new"]},
        "new_location_name": {"type": "string", "description": "only when location_id is 'new'"},
        "new_location_description": {"type": "string", "description": "40-70 words, only when location_id is 'new'"},
        "time_of_day": {"type": "string"},
        "summary": {"type": "string"},
        "beats": {"type": "array", "items": {"type": "string"}, "description": "1-3 short visual beats"},
    }, "required": ["move", "location_id", "summary", "beats"]}


def find_gaps(scenes: list[dict], b: dict | None = None) -> list[tuple[int, list[str]]]:
    """Indexes i where scene i -> i+1 moves a carried-over character to another place without a marked time jump."""
    bible, gaps = b, []
    for i in range(len(scenes) - 1):
        a, b = scenes[i], scenes[i + 1]
        if not a.get("location_id") or a.get("location_id") == b.get("location_id") or b.get("transition"):
            continue
        if bible and _same_place(bible, a["location_id"], b.get("location_id", "")):
            continue
        carried = [c["id"] for c in b.get("characters", []) if c["id"] in {x["id"] for x in a.get("characters", [])}]
        if not carried:
            continue
        opening = " ".join([b.get("summary", "")] + (b.get("beats") or [])[:1])
        if TIME_JUMP.search(opening) or TIME_JUMP.search(b.get("time_of_day", "")):
            continue
        gaps.append((i, carried))
    return gaps


def bridge_scenes(s: dict, sid: str, b: dict, scenes: list[dict], log=print) -> list[dict]:
    """Insert short transition scenes where a character jumps between distant places with nothing in between."""
    out, added = list(scenes), 0
    for i, carried in find_gaps(scenes, b):
        a, nxt = scenes[i], scenes[i + 1]
        names = ", ".join(b["characters"].get(c, {}).get("name", c) for c in carried)
        data = llm.chat(
            s, "planner",
            "You are a script supervisor checking geography between two scenes of a film. The audience must "
            "understand how a character got from one place to the next. First classify the move. Examples: "
            "a street outside a shop -> inside that shop = through one door or a few steps; inside a shop -> its own "
            "pavement = through one door or a few steps; a room -> the corridor outside it = through one door or a few "
            "steps; the top floor of a tower -> a street across the city = a real journey; home -> office = a real "
            "journey; a scene that opens 'the next morning' = an intentional time jump. Only for a real journey, write "
            "1-3 visual beats of the part the two scenes leave out (the first beat starts right after the last beat "
            "of FROM; the last beat ends right before the first beat of TO; never repeat or anticipate their beats). "
            "All beats happen in the ONE location you choose (it is what gets drawn): if the journey passes several "
            "places, pick the one that tells it best, e.g. the building's lobby or entrance when leaving matters, "
            "the street when the walk matters. Make the beats purposeful: show the character's state of mind or "
            "plant something the story pays off later; never routine filler. Do not contradict the two scenes "
            "(outfit, weather, props, who is present). No dialogue. Prefer an existing location; use 'new' only for "
            "a place the character must pass through that the bible lacks (a lift, a lobby, a building entrance).",
            f"{_bible_brief(b)}\n\nCHARACTER(S) MOVING: {names}\n"
            f"FROM scene {a['id']} at {a.get('location_id')} ({a.get('time_of_day', '')}): {a.get('summary', '')}\n"
            f"  Last beats: {(a.get('beats') or [])[-2:]}\n"
            f"TO scene {nxt['id']} at {nxt.get('location_id')} ({nxt.get('time_of_day', '')}): {nxt.get('summary', '')}\n"
            f"  First beats: {(nxt.get('beats') or [])[:2]}",
            _bridge_schema(list(b["locations"].keys())), temperature=0.5)
        beats = [x for x in (data.get("beats") or []) if str(x).strip()][:3]
        if data.get("move") != "a real journey" or not beats:
            continue
        lid = data.get("location_id")
        if lid == "new" or lid not in b["locations"]:
            name = (data.get("new_location_name") or "").strip()
            if not name:
                continue
            existing = next((l for l in b["locations"].values()
                             if l["name"].lower() == name.lower() or l["id"] == store.slug(name, "location")), None)
            if existing:
                lid = existing["id"]
            else:
                tod = (data.get("time_of_day") or a.get("time_of_day") or "day").strip().lower() or "day"
                loc = store.new_location(name, description=data.get("new_location_description", ""),
                                         variants={tod: ""}, draft=True)
                saved = store.save_location(sid, loc, bump=False)
                lid = saved["id"]
                b["locations"][lid] = saved
                log(f"  new draft location for the bridge: {name}")
        cast = [dict(c) for c in nxt.get("characters", []) if c["id"] in carried]
        out.insert(i + 1 + added, {
            "location_id": lid, "time_of_day": data.get("time_of_day") or a.get("time_of_day", ""),
            "summary": data.get("summary", ""), "characters": cast, "beats": beats, "dialogue": [],
            "transition": True})
        added += 1
        log(f"  bridge after {a['id']}: {data.get('summary', '')[:70]}")
    for k, sc in enumerate(out, 1):
        sc["id"] = f"S{k:02d}"
    return out


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
                "speaker": _ids_schema(cids), "line": {"type": "string"},
                "emotion": {"type": "string", "description": "acting direction for the voice, 3-8 words, e.g. thrilled, shouting with joy"}}, "required": ["speaker", "line"]}},
            "sound": {"type": "string"},
            "action_seconds": {"type": "number", "description": "seconds of action besides speech, 1-6"},
            "continuous_with_previous": {"type": "boolean"},
        }, "required": ["framing", "camera", "characters", "action", "dialogue", "action_seconds"]}}},
        "required": ["shots"],
    }


def _neighbour_brief(prev: dict | None, nxt: dict | None) -> str:
    def where(sc):
        return f"{sc.get('location_id')} ({sc.get('time_of_day', '')})"
    out = []
    if prev:
        beats = prev.get("beats") or []
        out.append(f"PREVIOUS SCENE {prev.get('id', '')} at {where(prev)}: {prev.get('summary', '')}"
                   + (f" Its last beat: {beats[-1]}" if beats else ""))
    if nxt:
        beats = nxt.get("beats") or []
        out.append(f"NEXT SCENE {nxt.get('id', '')} at {where(nxt)}: {nxt.get('summary', '')}"
                   + (f" Its first beat: {beats[0]}" if beats else ""))
    return ("\n" + "\n".join(out)) if out else ""


SHOT_CONTINUITY_RULES = (
    " Continuity across scenes: if the previous scene was somewhere else and a character continues from it, "
    "the first shots must show them arriving or entering (through the door, out of the car, walking up), unless "
    "the scene is an intentional time jump. If the next scene is somewhere else, the last shot must give a clean "
    "exit: the character leaves frame, goes through a door, or turns away and walks off, so the cut to the next "
    "place makes sense. Keep props and condition continuous across the cut (a jacket put on stays on, rain-soaked "
    "stays soaked). A transition scene is usually 1-3 shots: a wide or tracking shot of the character moving "
    "through the space, with sound carrying the mood."
)


def plan_shots(s: dict, b: dict, scene: dict, prev: dict | None = None, nxt: dict | None = None) -> list[dict]:
    rules = (
        "Rules: one main action per shot; at most two speaking characters in frame; "
        "split long dialogue across shots so no shot has more than about 20 words; "
        "open the scene with a wider shot; keep each character on the same side of frame within the scene; "
        "set continuous_with_previous only when the action flows across the cut. "
        "Every dialogue line of the scene must appear exactly once across the shots, word for word, "
        "and must sit in the shot that shows the moment it is spoken: a line said in reaction to something "
        "goes in the shot AFTER that something is visible (e.g. a greeting to an animal only after the animal "
        "has arrived on screen). Cover every beat with at least one shot, in story order, including visual "
        "moments without dialogue (weather changes, a reveal, a wide establishing view). Give every line an acting "
        "direction in 'emotion' (how it is said: e.g. 'frightened, calling from far away', 'shy but proud'). Name concrete props "
        "consistently with the story (a paper boat stays a paper boat)." + SHOT_CONTINUITY_RULES
    )
    data = llm.chat(
        s, "planner",
        "You are a film director writing a shot list. " + rules,
        f"{_bible_brief(b)}\n\nSCENE {scene['id']} at {scene.get('location_id')} ({scene.get('time_of_day', '')}):\n"
        f"Summary: {scene.get('summary', '')}\nCharacters: {scene.get('characters')}\n"
        f"Beats: {scene.get('beats')}\nDialogue: {scene.get('dialogue')}"
        + (f"\nThis is a transition scene." if scene.get("transition") else "")
        + _neighbour_brief(prev, nxt),
        _shot_schema(b, scene), temperature=0.6)
    outfits = {c["id"]: c.get("outfit") for c in scene.get("characters", [])}
    shots = []
    for i, sh in enumerate(data.get("shots", []), 1):
        store.normalize_cast(b["characters"], sh)
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
    c = store.find_character(b["characters"], cid)
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
        "first_frame": None, "screen_text": "",
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
        c = store.find_character(b["characters"], d.get("speaker"))
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
    if s.get("plan_bridges", True):
        log("Bridge pass (how characters get from place to place)...")
        scenes = bridge_scenes(s, sid, b, scenes, log)
    shots = []
    for i, sc in enumerate(scenes):
        log(f"Shot pass for {sc['id']} ({sc.get('summary', '')[:60]})...")
        sc_shots = plan_shots(s, b, sc, scenes[i - 1] if i else None, scenes[i + 1] if i + 1 < len(scenes) else None)
        log(f"  {len(sc_shots)} shots")
        shots.extend(sc_shots)
    for sh in shots:
        sh["duration_s"] = compute_duration(sh, b, s["ltx"]["max_seconds"])
    return scenes, shots


def replan_scene(s: dict, sid: str, scene: dict, scenes: list[dict] | None = None) -> list[dict]:
    b = store.bible(sid)
    scenes = scenes or []
    i = next((k for k, sc in enumerate(scenes) if sc.get("id") == scene.get("id")), None)
    prev = scenes[i - 1] if i else None
    nxt = scenes[i + 1] if i is not None and i + 1 < len(scenes) else None
    shots = plan_shots(s, b, scene, prev, nxt)
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
            if has_chars:                       # an establishing shot without people can't be a master to
                master_of[scene] = sh           # punch in on: the first shot WITH characters is (as frames expect)
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


# ── Dialogue blocking: two-shot, then over-the-shoulder per turn ─────────

def _look(b, cid, outfit):
    c = store.find_character(b["characters"], cid) or {"name": cid}
    wear = (c.get("outfits") or {}).get(outfit) or next(iter((c.get("outfits") or {"": ""}).values()), "")
    return c, f"{c.get('name', cid)} (exact face from the reference photo), {c.get('appearance', '')}, wearing {wear}".strip()


def _turns(lines):
    """Consecutive lines of one speaker are one turn."""
    turns = []
    for d in lines:
        if turns and turns[-1][0] == d.get("speaker"):
            turns[-1][1].append(d)
        else:
            turns.append((d.get("speaker"), [d]))
    return turns


_TEXTY = re.compile(r"\b(letter(ing|s)?|signs?|signage|menu|chalkboard|poster|logo|label|written|words?|neon)\b", re.I)


def setting_text(b, loc, time_of_day):
    """The place for an image prompt: description without its name (models paint names onto windows) or any
    clause that invites writing, plus the time-of-day variant and the palette."""
    parts = [x.strip() for x in re.split(r"[,;:]", loc.get("description", "")) if x.strip() and not _TEXTY.search(x)]
    variants = loc.get("variants") or {}
    t = (time_of_day or "").strip().lower()
    variant = variants.get(t) or next((v for k, v in variants.items() if k and (k in t or t in k)), "") if t else ""
    if not variant:      # a time the bible has no plate for (e.g. dawn): say the time itself, never another time's light
        variant = t or next(iter(variants.values()), "")
    out = ", ".join(parts).rstrip(".")
    if variant:
        out += f". Time and light: {variant}"
    if loc.get("palette"):
        out += f". Palette: {loc['palette']}"
    return out


def dialogue_blocking(episode, b, scene_id, sides=None):
    """Shots for a two-person dialogue scene the way it is usually shot: an opening two-shot that sets the geography,
    then one over-the-shoulder shot per turn on the speaker (the listener's shoulder in the foreground), every OTS on
    one speaker sharing one frame. Dialogue shots use InfiniteTalk (the mouth follows the voice). Returns new shots; the
    scene's old shots are the caller's to replace."""
    old = [s for s in sorted(episode["shots"], key=lambda x: x.get("order", 0)) if s.get("scene_id") == scene_id]
    lines = [dict(d) for s in old for d in s.get("dialogue") or [] if (d.get("line") or "").strip()]
    cast = []
    for s in old:
        for ch in s.get("characters", []):
            if ch["id"] not in [c["id"] for c in cast]:
                cast.append(dict(ch))
    if len(cast) != 2:
        raise ValueError(f"{scene_id}: dialogue blocking is for two people (found {len(cast)})")
    side = dict(sides or {})
    for s in [x for x in episode["shots"] if len(x.get("characters", [])) == 2]:
        for ch in s["characters"]:
            if ch.get("position") in ("left", "right"):
                side.setdefault(ch["id"], ch["position"])
    side.setdefault(cast[0]["id"], "left"); side.setdefault(cast[1]["id"], "left" if side[cast[0]["id"]] == "right" else "right")
    loc = b["locations"].get(old[0].get("location_id") or "") or {}
    where = setting_text(b, loc, old[0].get("time_of_day"))
    tail = ("No text, no letters, no signs anywhere. " + ((b.get("style") or {}).get("grade") or "") +
            ". Photorealistic premium cinematic drama, shallow depth of field, subtle film grain.")
    look = {c["id"]: _look(b, c["id"], c.get("outfit")) for c in cast}
    first = lambda cid: (look[cid][0].get("name") or cid).split()[0]
    pos = lambda: [{"id": c["id"], "position": side[c["id"]], "outfit": c.get("outfit", "default")} for c in cast]
    base = dict(scene_id=scene_id, location_id=old[0].get("location_id"), time_of_day=old[0].get("time_of_day"),
                frame_source="qwen_refs", engine="ltx_lipsync")
    a, c2 = cast[0]["id"], cast[1]["id"]
    shots = [new_shot(id=f"{scene_id}_TS01", framing="two-shot", camera="static, eye level", characters=pos(),
                      dialogue=[], duration_s=3.0, duration_locked=True, action_seconds=3.0,
                      action=(f"{first(a)} and {first(c2)} face each other across the room; nobody walks, nobody enters "
                              "or leaves; only small natural movements."),
                      image_prompt_override=(f"Vertical two-shot, eye level, static camera. Exactly two people in the whole picture, nobody else anywhere. "
                          f"{side[a].upper()}: {look[a][1]}, three-quarter profile turned toward frame {side[c2]}. "
                          f"{side[c2].upper()}: {look[c2][1]}, three-quarter profile turned toward frame {side[a]}. They "
                          f"look at each other. Both faces clearly visible. Setting: {where}. {tail}"), **base)]
    owner = {}
    for n, (spk, said) in enumerate(_turns(lines), 1):
        other = c2 if spk == a else a
        sid_ = f"{scene_id}_OT{n:02d}"
        shots.append(new_shot(
            id=sid_, framing="over-the-shoulder", camera=f"static, eye level, over {first(other)}'s shoulder",
            characters=pos(), dialogue=said, action_seconds=0.8, frame_from=owner.get(spk, ""), face=spk,
            action=(f"Over {first(other)}'s shoulder: {first(spk)} speaks to {first(other)}. {first(other)}, seen from "
                    "behind as a soft shoulder in the foreground, does not move. Nobody walks, enters or leaves."),
            image_prompt_override=(f"Vertical over-the-shoulder shot, eye level. In the {side[other].upper()} foreground, "
                f"out of focus and large, the back of the head and shoulder of {look[other][1]}, seen from behind. Facing "
                f"three-quarters toward the camera in the {side[spk]}-centre of the frame, sharp, from the chest up: "
                f"{look[spk][1]}, looking at {first(other)} toward frame {side[other]}, never into the lens. A candid film "
                f"still; the reference photos give only the faces, not the pose or gaze. Setting: {where}. {tail}"),
            **{**base, "engine": "infinitetalk"}))
        owner.setdefault(spk, sid_)
    return shots
