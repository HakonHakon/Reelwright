"""Professional writing pipeline: story spine -> scene cards -> shot design -> script-supervisor review.

Each layer is written with the whole layer above in view, so a shot knows the episode's theme, what its scene must
change, which props pay off later, where everyone is, and what the video models cannot render.

    spine  (episode)   logline, theme, each character's want / need / secret / arc, setups -> payoffs, ledger
    cards  (scenes)    value turn, whose scene, entrances / exits, what the audience learns, plants / pays, beats
    shots  (per scene) designed from spine + card + neighbouring cards + ledger + film grammar + renderer limits
    review (per scene) a second pass checks every shot against a checklist and returns the fixed shot list

The user approves cards before shots are designed (job_plan_cards -> job_plan_shots); everything is stored on the
episode (spine, scenes, shots, review) so it can be read and edited on the Story / Shot list pages.
"""
import copy
import json

from . import blocking, llm, planner, prompts, store

# ── Craft (distilled from the installed screenwriting skills: McKee, Field, Hicks, Walter, Lu Jun) ─────────

CRAFT = (
    "Craft rules for every scene: a scene exists to turn a value (trust, safety, hope, a secret...) from one charge to "
    "another; if nothing turns, it is exposition and must be folded into another scene. Every scene both moves the plot "
    "with new information and reveals more of the protagonist. Enter late, leave early: start as close to the turn as "
    "possible and leave before the conflict is fully resolved. Behaviour over talk: people pursue what they want through "
    "actions; subtext over on-the-nose lines; give the actors something physical to do that carries the story. Props "
    "matter only if a character acts on them at a turn; each later appearance must change hands, state or meaning. "
    "Prefer telling the audience a secret before the characters learn it (suspense) to surprising them. Geography: the "
    "audience must always know where people are and how they got there."
)

GRAMMAR = (
    "Film grammar for the shot list: open a scene or a change of place with one shot that sets the geography (often a "
    "two-shot or a wide); play dialogue as shot / reverse shot over the listener's shoulder, cutting on a turn of the "
    "conversation or a change in power, not on every line; when one person's reaction matters more than the other "
    "person's words, HOLD on the listener while the other speaks off-screen; use a close-up only for a value turn or a "
    "decision; use an insert only for a prop that is a setup or a payoff; hold silent shots long enough to breathe. "
    "Keep each character on the same side of frame for the whole scene and their eyelines consistent with it. "
    "Pace: stay inside the scene's time budget; aim for an average shot of 4-6 seconds; a quick exchange of short "
    "lines plays in ONE shot held on the person whose reaction matters (the other speaks off-screen), not in a "
    "reverse per line; one silent shot per action, 2-4 seconds unless the turn needs a longer hold; never two shots "
    "in a row that show the same thing from the same setup."
)

LIMITS = (
    "What the video models can and cannot render (design around this, never against it): each shot is one continuous "
    "take of at most 15 seconds; at most ONE face speaks in a shot, and it must be large and turned at least three-"
    "quarters to the camera (over-the-shoulder or close-up); a wide shot with two small faces must not carry dialogue; "
    "nobody can walk across the set, enter, leave, sit down, or hand an object over inside a take - show the before and "
    "the after and cut on the move, or keep the camera on someone watching it happen off-screen; complex hand business "
    "needs its own insert; no on-screen text EXCEPT a phone screen, letter or document whose words the story depends "
    "on - show it in an insert and give its exact words in 'screen_text'; characters do not change clothes inside a take."
)

BLOCKING = (
    "Plan space before rendering. The scene geography fixes the set layout, inside/outside barriers, landmarks "
    "and camera axis. For EVERY shot supply first_frame: one frozen opening moment, camera location, layout, "
    "lighting, subjects, props and constraints. For EVERY cast member explicitly specify screen position, depth, "
    "zone, pose, facing, gaze_target (a declared character or prop id, offscreen, or camera), gaze_direction, "
    "expression, visibility and wardrobe_state. Include offscreen subjects explicitly; never duplicate a person "
    "inside and outside. Declare prop location, state and holder; use holder none for scenery. "
    "Subjects must exactly match the shot characters. Gaze follows the opening story beat, never character order "
    "or an automatic look-at-each-other rule. Keep first_frame separate from action: do not show the turn, exit "
    "or handover before it happens. Specify the current window/door/blind state, who holds each object, who is "
    "inside or outside, and which faces are actually visible. Copy the scene's geography consistently between shots."
)

# ── Schemas ──────────────────────────────────────────────────────────────


def _ids(xs):
    return planner._ids_schema(list(xs))


def _spine_schema(b):
    cids = list(b["characters"].keys())
    return {"type": "object", "properties": {
        "logline": {"type": "string"},
        "theme": {"type": "string", "description": "what the episode argues, as a sentence"},
        "characters": {"type": "array", "items": {"type": "object", "properties": {
            "id": _ids(cids), "want": {"type": "string"}, "need": {"type": "string"},
            "secret": {"type": "string"}, "arc": {"type": "string", "description": "where they start -> where they end"}},
            "required": ["id", "want", "need", "arc"]}},
        "setups": {"type": "array", "items": {"type": "object", "properties": {
            "id": {"type": "string"}, "what": {"type": "string"},
            "planted": {"type": "string", "description": "how and where it is planted"},
            "payoff": {"type": "string", "description": "how and where it pays off"}},
            "required": ["id", "what", "planted", "payoff"]}},
        "ledger": {"type": "array", "description": "continuity state per character per scene, in story order",
                   "items": {"type": "object", "properties": {
                       "scene": {"type": "string", "description": "a short scene label in story order"},
                       "character": _ids(cids), "wardrobe": {"type": "string"},
                       "condition": {"type": "string", "description": "wet, tired, injured...; empty if none"},
                       "holding": {"type": "string"}},
                       "required": ["scene", "character", "wardrobe"]}},
    }, "required": ["logline", "theme", "characters", "setups", "ledger"]}


def _cards_schema(b):
    cids, lids = list(b["characters"].keys()), list(b["locations"].keys())
    return {"type": "object", "properties": {"scenes": {"type": "array", "items": {"type": "object", "properties": {
        "location_id": _ids(lids), "time_of_day": {"type": "string"},
        "summary": {"type": "string"},
        "geography": {"type": "string", "description": "Stable set layout, barriers, landmarks and camera axis; no transient cast actions"},
        "purpose": {"type": "string", "description": "why this scene exists"},
        "value": {"type": "string", "description": "the value at stake"},
        "turn": {"type": "string", "description": "its charge at the start -> at the end"},
        "pov": _ids(cids),
        "entrances": {"type": "array", "items": {"type": "string"}, "description": "who enters and how they got here"},
        "exits": {"type": "array", "items": {"type": "string"}, "description": "who leaves, where to and how"},
        "audience_learns": {"type": "string"},
        "plants": {"type": "array", "items": {"type": "string"}, "description": "setup ids planted here"},
        "pays": {"type": "array", "items": {"type": "string"}, "description": "setup ids paid off here"},
        "transition": {"type": "boolean"},
        "characters": {"type": "array", "items": {"type": "object", "properties": {
            "id": _ids(cids), "outfit": {"type": "string"},
            "condition": {"type": "string"}}, "required": ["id", "outfit"]}},
        "beats": {"type": "array", "items": {"type": "string"}},
        "dialogue": {"type": "array", "items": {"type": "object", "properties": {
            "speaker": _ids(cids), "line": {"type": "string"},
            "emotion": {"type": "string"}}, "required": ["speaker", "line"]}},
    }, "required": ["location_id", "summary", "geography", "purpose", "value", "turn", "pov", "characters", "beats", "dialogue"]}}},
        "required": ["scenes"]}


FRAMINGS = ["wide", "two-shot", "over-the-shoulder", "medium", "medium close-up", "close-up", "insert", "point-of-view"]


def _shots_schema(b, card):
    cids = [c["id"] for c in card.get("characters", [])] or list(b["characters"].keys())
    return {"type": "object", "properties": {
        "issues": {"type": "array", "items": {"type": "string"}, "description": "review only: what you fixed and why"},
        "shots": {"type": "array", "items": {"type": "object", "properties": {
            "purpose": {"type": "string", "description": "the one job this shot does in the scene"},
            "framing": {"type": "string", "enum": FRAMINGS},
            "camera": {"type": "string"},
            "characters": {"type": "array", "items": {"type": "object", "properties": {
                "id": _ids(cids), "position": {"type": "string", "enum": planner.POSITIONS}}, "required": ["id", "position"]}},
            "face": {"type": "string", "description": "id of the one character whose face is large enough to speak; empty if none"},
            "picture": {"type": "string", "description": "the first frame as a cinematographer would describe it: who is where, facing which way, WHERE EACH PERSON IS LOOKING (at whom or what; never into the lens), what is in the foreground and background, light"},
            "first_frame": blocking.schema(cids),
            "action": {"type": "string", "description": "what happens during the take, within the renderer's limits"},
            "dialogue": {"type": "array", "items": {"type": "object", "properties": {
                "speaker": _ids(cids), "line": {"type": "string"},
                "pause": {"type": "number", "description": "seconds before the line"},
                "emotion": {"type": "string"}}, "required": ["speaker", "line"]}},
            "seconds": {"type": "number", "description": "for shots without dialogue: how long to hold, 2-6"},
            "screen_text": {"type": "string", "description": "exact words on a phone screen or document the story depends on; empty otherwise"},
            "sound": {"type": "string"},
        }, "required": ["purpose", "framing", "characters", "face", "picture", "first_frame", "action", "dialogue"]}}},
        "required": ["shots"]}


# ── Layers ───────────────────────────────────────────────────────────────

def _chat(s, system, user, schema):
    return llm.chat(s, "planner", system, user, schema, temperature=0.6)


def story_spine(s, b, story, series=""):
    return _chat(s, "You are the showrunner of a premium short drama. " + CRAFT +
                 " Read the episode and write its story spine: the logline, the theme, each character's want, need, "
                 "secret and arc in this episode, every setup with where it is planted and where it pays off (including "
                 "props and visual motifs), and a continuity ledger of wardrobe, condition and what each character is "
                 "holding at each scene. Use only the given character ids.",
                 f"{planner._bible_brief(b)}\n\nSERIES CONTEXT:\n{series or '(none)'}\n\nEPISODE:\n{story}", _spine_schema(b))


def scene_cards(s, b, story, spine):
    data = _chat(s, "You are the story editor. " + CRAFT + " " + BLOCKING + " " + planner.TRANSITION_RULES +
                 " Break the episode into scene cards in story order. A new scene starts when the place or time changes. "
                 "Each card states its purpose, the value at stake and how it turns, whose scene it is, how every person "
                 "enters and leaves (no one appears somewhere without the audience seeing or understanding how they got "
                 "there), what the audience learns, and which setups it plants or pays off (use the spine's setup ids). "
                 "Beats are short visual actions in order. Copy dialogue word for word from the episode; never invent or "
                 "cut lines. Use only the given ids.",
                 f"{planner._bible_brief(b)}\n\nSTORY SPINE:\n{json.dumps(spine, ensure_ascii=False)}\n\nEPISODE:\n{story}",
                 _cards_schema(b))
    cards = []
    for i, sc in enumerate(data.get("scenes", []), 1):
        if not isinstance(sc.get("geography"), str) or not sc["geography"].strip():
            raise ValueError(f"Scene {i}: the scene card must define its geography before shot design")
        sc["id"] = f"S{i:02d}"
        store.normalize_cast(b["characters"], sc)
        cards.append(sc)
    if not cards:
        raise ValueError("The writer returned no scenes.")
    return cards


def _context(spine, cards, i):
    prev = cards[i - 1] if i else None
    nxt = cards[i + 1] if i + 1 < len(cards) else None
    brief = lambda c: {k: c.get(k) for k in ("id", "location_id", "time_of_day", "summary", "turn", "exits", "entrances", "beats")} if c else None
    return (f"STORY SPINE:\n{json.dumps(spine, ensure_ascii=False)}\n\nPREVIOUS SCENE:\n{json.dumps(brief(prev), ensure_ascii=False)}"
            f"\n\nNEXT SCENE:\n{json.dumps(brief(nxt), ensure_ascii=False)}")


TARGET_SECONDS = 100


def scene_budget(cards, i, total=TARGET_SECONDS):
    """Seconds this scene gets of the episode's runtime, in proportion to its dialogue and beats."""
    weight = lambda c: 2.5 * len(c.get("dialogue") or []) + len(c.get("beats") or []) + 2
    return round(total * weight(cards[i]) / sum(weight(c) for c in cards))


def design_shots(s, b, spine, cards, i):
    card = cards[i]
    return _chat(s, "You are the director writing the shot list for one scene of a vertical 9:16 premium drama. " +
                 CRAFT + " " + GRAMMAR + " " + LIMITS + " " + BLOCKING +
                 " Every dialogue line of the scene appears exactly once, word for word, in the shot that shows the "
                 "moment it is spoken (off-screen lines may sit in a shot that holds on the listener). Every beat is "
                 "covered. Give every line an acting direction. 'face' is the one character whose mouth may move in the "
                 "shot; lines by anyone else in that shot are heard off-screen.",
                 f"{planner._bible_brief(b)}\n\n{_context(spine, cards, i)}\n\nTHIS SCENE CARD:\n{json.dumps(card, ensure_ascii=False)}"
                 f"\n\nTIME BUDGET: the episode runs about {TARGET_SECONDS} seconds; this scene gets about "
                 f"{scene_budget(cards, i)} seconds of screen time in total.",
                 _shots_schema(b, card))


CHECKLIST = (
    "Script-supervisor checklist - check each item and fix the shot list where it fails: (1) every shot has a purpose "
    "and the scene's value visibly turns; (2) no cut without a reason - merge shots that repeat the same setup with no "
    "turn between them; (3) geography: the first shot sets where people are, entrances and exits match the card and the "
    "neighbouring scenes; (4) continuity matches the ledger (wardrobe, wet or dry, props held); (5) screen direction and "
    "eyelines are consistent; (6) every setup this scene plants or pays off is visible on screen; (7) every dialogue "
    "line appears exactly once, word for word, and 'face' is the speaker or a listener we hold on; (8) nothing breaks "
    "the renderer's limits (walking across the set, entering, handing objects over, two speaking faces, wide shot "
    "carrying dialogue, text on screen other than story-critical screen_text); (9) the scene fits its time budget "
    "and does not cut more than the story needs - merge reverses on short lines into a held reaction. Return the "
    "corrected full shot list and list what you changed in 'issues'."
)


def review_shots(s, b, spine, cards, i, shots):
    return _chat(s, "You are the script supervisor. " + CHECKLIST + " " + GRAMMAR + " " + LIMITS + " " + BLOCKING,
                 f"{planner._bible_brief(b)}\n\n{_context(spine, cards, i)}\n\nTHIS SCENE CARD:\n"
                 f"{json.dumps(cards[i], ensure_ascii=False)}\n\nTIME BUDGET: about {scene_budget(cards, i)} seconds for this "
                 f"scene.\n\nSHOT LIST TO CHECK:\n{json.dumps(shots, ensure_ascii=False)}",
                 _shots_schema(b, cards[i]))


# ── Blocking for shots planned before first_frame existed ──────────────

def _block_schema(shots):
    """One property per shot, each with a schema built from THAT shot's cast: a subject, holder or gaze target outside
    the shot cannot be expressed, so it cannot be generated."""
    return {"type": "object", "properties": {"shots": {"type": "object", "properties": {
        sh["id"]: blocking.schema([c["id"] for c in sh.get("characters", [])]) for sh in shots},
        "required": [sh["id"] for sh in shots]}}, "required": ["shots"]}


def _shot_brief(sh):
    keys = ("id", "framing", "camera", "characters", "action", "dialogue", "screen_text", "image_prompt_override",
            "first_frame")
    return {k: sh[k] for k in keys if sh.get(k)}


def block_scene(s, b, spine, cards, i, scene_shots, errors=None):
    """first_frame for the scene's shots that have none, keeping every other decision of the approved shot list.
    Shots that already have blocking are sent as the geography to copy."""
    todo = [sh for sh in scene_shots if not sh.get("first_frame")]
    fix = ("\n\nYOUR PREVIOUS ANSWER FAILED VALIDATION - fix exactly these errors:\n" + "\n".join(errors) +
           "\nA prop holder must be one of THAT shot's characters or none; an object held by someone who is not in the "
           "shot's cast gets holder none, with the hand described in its placement (e.g. 'in a hand entering from frame "
           "left'). Gaze targets must be that shot's characters, its props, offscreen or camera.") if errors else ""
    return _chat(s, "You are the director blocking the opening frame of shots that are already written and approved. "
                 + BLOCKING + " Do not change a shot's cast, framing, camera intent, action or dialogue: block the "
                 "instant BEFORE its action starts, consistent with the previous shot's end and the shots that already "
                 "have first_frame. A shot's old image_prompt_override describes the intended picture: keep its intent "
                 "but fix anything that contradicts the action or the geography. Subjects must be exactly the shot's "
                 "characters (none for a shot without characters); a character whose voice is heard but who is not in "
                 "the picture is still listed, with visibility offscreen. The speaking face of a dialogue shot must be "
                 "visible and turned at least three-quarters to camera.",
                 f"{planner._bible_brief(b)}\n\n{_context(spine, cards, i)}\n\nTHIS SCENE CARD:\n"
                 f"{json.dumps(cards[i], ensure_ascii=False)}\n\nTHE SCENE'S SHOTS IN ORDER:\n"
                 f"{json.dumps([_shot_brief(x) for x in scene_shots], ensure_ascii=False)}\n\nBLOCK THESE SHOTS: "
                 f"{', '.join(sh['id'] for sh in todo)}{fix}", _block_schema(todo))


def block_shots(s, sid, spine, cards, shots, log=print, save=None):
    """{shot id: first_frame} for every shot without one, validated against the bible (one retry with the errors).
    `save(blocks)` is called after each scene so finished scenes survive a later failure; a scene that stays invalid
    is reported and skipped. Returns (blocks, failures)."""
    b = store.bible(sid)
    out, failures = {}, []
    for i, card in enumerate(cards):
        scene_shots = sorted([sh for sh in shots if sh.get("scene_id") == card["id"]], key=lambda x: x.get("order", 0))
        todo = {sh["id"]: sh for sh in scene_shots if not sh.get("first_frame")}
        if not todo:
            continue
        log(f"Blocking {len(todo)} shots of {card['id']}...")
        errors = None
        for attempt in range(2):
            got = block_scene(s, b, spine, cards, i, scene_shots, errors).get("shots") or {}
            errors = []
            for shot_id, sh in todo.items():
                trial = {**sh, "first_frame": got.get(shot_id), "image_prompt_override": ""}
                problems = ["missing"] if got.get(shot_id) is None else blocking.validate(trial, b)
                errors += [f"{shot_id}: {p}" for p in problems]
            if not errors:
                break
            log(f"  {len(errors)} validation errors" + (", asking for a fix..." if attempt == 0 else ""))
        good = {shot_id: got[shot_id] for shot_id in todo if not any(e.startswith(f"{shot_id}:") for e in errors)}
        for e in errors:
            failures.append(e)
            log(f"  still invalid, left unblocked: {e}")
        out.update(good)
        if save and good:
            save(good)
        log(f"  {card['id']}: {len(good)} of {len(todo)} shots blocked")
    return out, failures


# ── Layer 0: the episode script from the series outline ─────────────────

PRODUCTION = (
    "Production limits (write for them, the way a show writes for its budget): about 90-120 seconds on screen; 3-6 "
    "scenes; reuse the series' existing locations where the story allows; mostly one or two people per scene, no "
    "crowds; nobody walks across a room or hands an object over in a single continuous moment unless it can be cut "
    "around; story told through faces, small physical business and a few important props; dialogue short, every line "
    "doing work."
)


def write_episode(s, b, outline, number, title, previous=""):
    """A shootable episode script in the house format (scene headings, action, NAME / dialogue), continuing the
    previous episode and landing the outline's ending."""
    data = _chat(s, "You are the head writer of a premium vertical short drama. " + CRAFT + " " + PRODUCTION +
                 " Write the complete script of the requested episode from the series outline, in exactly the format of "
                 "the previous episode's script (a title line, a continuity note, '## Scene N - INT./EXT. Place - Time' "
                 "headings, present-tense action paragraphs, character names in capitals above their lines, parentheticals "
                 "only where needed). Continue from where the previous episode ended - the audience has just seen it. Open "
                 "late, land the outline's ending exactly, and end on the outline's cliffhanger image. Keep every canonical "
                 "character detail. Return the script as markdown in 'script'.",
                 f"{planner._bible_brief(b)}\n\nSERIES OUTLINE:\n{outline}\n\nPREVIOUS EPISODE SCRIPT:\n{previous or '(none)'}"
                 f"\n\nWRITE: Episode {number} - {title}",
                 {"type": "object", "properties": {"script": {"type": "string"}, "notes": {"type": "string"}},
                  "required": ["script"]})
    if not (data.get("script") or "").strip():
        raise ValueError("The writer returned an empty script.")
    return data


# ── Into the studio's shot format ───────────────────────────────────────

def to_studio_shots(b, card, designed):
    """Persist the reviewed blocking, validate it, and compile against the current bible."""
    outfits = {c["id"]: c.get("outfit") for c in card.get("characters", [])}
    out = []
    for n, sh in enumerate(copy.deepcopy(designed), 1):
        store.normalize_cast(b["characters"], sh)
        chars = []
        for ch in sh.get("characters", []):
            ch["outfit"] = planner._match_outfit(b, ch["id"], outfits.get(ch["id"]))
            chars.append(ch)
        lines = [d for d in sh.get("dialogue") or [] if (d.get("line") or "").strip()]
        face = sh.get("face") if sh.get("face") in [c["id"] for c in chars] else ""
        speaks = any(d.get("speaker") == face for d in lines) if face else False
        engine = "infinitetalk" if face and speaks else "ltx_lipsync"
        if not isinstance(sh.get("first_frame"), dict):
            raise ValueError(f"{card['id']} shot {n}: the director must supply first_frame blocking; gaze cannot be guessed")
        seconds = float(sh.get("seconds") or 0) if not lines else 0.0
        out.append(planner.new_shot(
            id=f"{card['id']}_SH{n:02d}", scene_id=card["id"], location_id=card.get("location_id"),
            time_of_day=planner._match_tod(b, card), framing=sh.get("framing", "medium"),
            camera=sh.get("camera") or "static, eye level", characters=chars, dialogue=lines,
            action=sh.get("action", ""), sound=sh.get("sound", ""), face=face, engine=engine,
            frame_source="qwen_refs" if chars else "zimage", first_frame=sh["first_frame"],
            screen_text=(sh.get("screen_text") or "").strip(),
            duration_s=seconds, duration_locked=bool(seconds), action_seconds=seconds or 0.8,
            notes=sh.get("purpose", "")))
        prompts.image_prompt(out[-1], b)  # Validate before accepting the generated shot list.
    return out


def plan_cards(s, sid, story, series="", log=print):
    b = store.bible(sid)
    log("Story spine (theme, arcs, setups and payoffs, continuity ledger)...")
    spine = story_spine(s, b, story, series)
    log(f"  {len(spine.get('setups') or [])} setups, {len(spine.get('ledger') or [])} continuity entries")
    log("Scene cards (purpose, value turn, entrances and exits)...")
    cards = scene_cards(s, b, story, spine)
    log(f"  {len(cards)} scenes")
    return spine, cards


def plan_shots(s, sid, spine, cards, log=print, review=True):
    b = store.bible(sid)
    shots, notes = [], []
    for i, card in enumerate(cards):
        log(f"Shot design for {card['id']}: {card.get('summary', '')[:60]}...")
        designed = design_shots(s, b, spine, cards, i).get("shots", [])
        if review and designed:
            log(f"  script-supervisor review of {len(designed)} shots...")
            checked = review_shots(s, b, spine, cards, i, designed)
            if checked.get("shots"):
                designed = checked["shots"]
            for issue in checked.get("issues") or []:
                notes.append(f"{card['id']}: {issue}")
                log(f"    fixed: {issue[:120]}")
        sc = to_studio_shots(b, card, designed)
        log(f"  {len(sc)} shots")
        shots.extend(sc)
    for sh in shots:
        sh["duration_s"] = sh["duration_s"] or planner.compute_duration(sh, b, s["infinitetalk"]["max_seconds"])
    return shots, notes
