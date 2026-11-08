"""Validate a first-frame plan and compile it without inventing spatial decisions."""
import re

SUBJECT_FIELDS = ("id", "position", "depth", "space", "zone", "pose", "facing", "gaze_target",
                  "gaze_direction", "expression", "visibility", "wardrobe_state")
PLAN_FIELDS = ("camera", "layout", "moment", "lighting", "subjects", "props", "constraints")
VISIBILITY = ("full_body", "upper_body", "partial", "back_of_head", "silhouette", "offscreen")
FACELESS = ("back_of_head", "silhouette")


def schema(character_ids):
    subject = {key: {"type": "string"} for key in SUBJECT_FIELDS}
    if character_ids:
        subject["id"]["enum"] = list(character_ids)
    subject["depth"]["enum"] = ["foreground", "midground", "background"]
    subject["space"]["enum"] = ["inside", "outside", "other"]
    subject["visibility"]["enum"] = list(VISIBILITY)
    subject["gaze_target"]["description"] = "Declared character id, prop id, offscreen, or camera; never inferred"
    return {"type": "object", "properties": {
        "camera": {"type": "string", "description": "First-frame camera location, height and view; no movement"},
        "layout": {"type": "string", "description": "Set geography, barriers, inside/outside and the scene axis"},
        "moment": {"type": "string", "description": "One frozen opening moment, before the take's actions happen"},
        "lighting": {"type": "string"},
        "subjects": {"type": "array", "items": {"type": "object", "properties": subject,
                                                   "required": list(SUBJECT_FIELDS)},
                     **({} if character_ids else {"maxItems": 0, "description": "No characters in this shot: empty"})},
        "props": {"type": "array", "items": {"type": "object", "properties": {
            "id": {"type": "string"}, "placement": {"type": "string"}, "state": {"type": "string"},
            "holder": {"type": "string", "enum": list(character_ids) + ["none"],
                       "description": "Character id of THIS shot who holds it by hand, or none"}},
            "required": ["id", "placement", "state", "holder"]}},
        "constraints": {"type": "array", "items": {"type": "string"}},
    }, "required": list(PLAN_FIELDS)}


def validate(shot, bible):
    """Return specific planning errors before spending GPU time. Legacy shots may omit a plan."""
    plan = shot.get("first_frame")
    if plan is None:
        return []
    if not isinstance(plan, dict):
        return ["First-frame blocking must be an object"]
    errors = []
    for key in PLAN_FIELDS[:4]:
        if not isinstance(plan.get(key), str) or not plan[key].strip():
            errors.append(f"First-frame blocking needs {key}")
    if shot.get("image_prompt_override"):
        errors.append("Clear the full image prompt override when using first-frame blocking")
    subjects, props = plan.get("subjects"), plan.get("props")
    if not isinstance(subjects, list) or not isinstance(props, list):
        return errors + ["First-frame subjects and props must be lists"]
    if not isinstance(plan.get("constraints"), list) or any(
            not isinstance(c, str) or not c.strip() for c in plan.get("constraints", [])):
        errors.append("First-frame constraints must be a list of nonempty strings")
    ids = []
    for subject in subjects:
        if not isinstance(subject, dict):
            errors.append("Each first-frame subject must be an object")
            continue
        cid = subject.get("id")
        ids.append(cid)
        if not isinstance(cid, str) or cid not in bible["characters"]:
            errors.append(f"Unknown first-frame character: {cid}")
        for field in SUBJECT_FIELDS:
            if not isinstance(subject.get(field), str) or not subject[field].strip():
                errors.append(f"{cid}: first-frame blocking needs {field}")
        if subject.get("depth") not in ("foreground", "midground", "background"):
            errors.append(f"{cid}: invalid depth")
        if subject.get("space") not in ("inside", "outside", "other"):
            errors.append(f"{cid}: invalid space (inside, outside or other)")
        if subject.get("visibility") not in VISIBILITY:
            errors.append(f"{cid}: invalid visibility")
    if len(ids) != len(set(str(cid) for cid in ids)):
        errors.append("A character must have exactly one first-frame subject entry")
    cast = [ch.get("id") for ch in shot.get("characters", [])]
    if set(str(cid) for cid in ids) != set(str(cid) for cid in cast):
        errors.append("First-frame subjects must match the shot cast, including explicitly offscreen characters")
    prop_ids = []
    for prop in props:
        if not isinstance(prop, dict):
            errors.append("Each first-frame prop must be an object")
            continue
        prop_ids.append(prop.get("id"))
        for field in ("id", "placement", "state", "holder"):
            if not isinstance(prop.get(field), str) or not prop[field].strip():
                errors.append(f"Prop {prop.get('id')}: blocking needs {field}")
        if prop.get("holder") != "none" and prop.get("holder") not in ids:
            errors.append(f"Prop {prop.get('id')}: unknown holder {prop.get('holder')}")
    if len(prop_ids) != len(set(str(pid) for pid in prop_ids)) or set(map(str, prop_ids)) & set(map(str, ids)):
        errors.append("Prop ids must be unique and different from character ids")
    targets = set(map(str, ids + prop_ids)) | {"offscreen", "camera"}
    for subject in subjects:
        if isinstance(subject, dict) and str(subject.get("gaze_target")) not in targets:
            errors.append(f"{subject.get('id')}: unknown gaze target {subject.get('gaze_target')}")
    return errors


def subject_for(shot, cid):
    return next((s for s in (shot.get("first_frame") or {}).get("subjects", []) if s["id"] == cid), None)


def identity_description(character):
    """A smiling portrait is not an instruction to smile in every scene."""
    transient = re.compile(r"\b(smile|smiling|grin|grinning|expression|laughing|crying|frowning)\b", re.I)
    return ", ".join(x.strip() for x in character.get("appearance", "").split(",") if not transient.search(x))


def still_style(text):
    """Shot camera and output dimensions win over old series aspect/movement boilerplate."""
    conflicting = re.compile(r"\d+(?:\.\d+)?\s*:\s*\d+(?:\.\d+)?|\b(widescreen|landscape|portrait|camera movement)\b", re.I)
    return ", ".join(part.strip() for part in text.split(",") if not conflicting.search(part))


def composition_description(character):
    """The FULL appearance: the text is what keeps a face consistent from shot to shot. (Qwen identity editing was
    measured on Ep2 and does not reliably impose a reference face, so facial details cannot be left to it.)"""
    return re.sub(r"\battractive\s+", "", identity_description(character), flags=re.I)


def image_prompt(shot, bible, composition=False):
    errors = validate(shot, bible)
    if errors:
        raise ValueError(f"{shot.get('id') or 'Shot'}: " + "; ".join(errors))
    plan = shot["first_frame"]
    visible = [s for s in plan["subjects"] if s["visibility"] != "offscreen"]
    names = {cid: c.get("name") or cid for cid, c in bible["characters"].items()}
    roles = {ch["id"]: ch for ch in shot.get("characters", [])}
    parts = [f"{shot.get('framing', 'medium').capitalize()} cinematic film still. {plan['camera']}",
             f"Geography: {plan['layout']}", f"Opening instant: {plan['moment']}",
             (f"Exactly {len(visible)} people total, each appears once; no extra people or reflections of people"
              if visible else "Nobody is in the frame: an empty scene with no people, figures, silhouettes, passers-by "
                              "or hands anywhere, not even in the distance or behind windows")]
    for subject in plan["subjects"]:
        cid = subject["id"]
        if subject.get("layered"):
            continue                                       # its empty zone is a constraint; naming it draws it
        if subject["visibility"] == "offscreen":
            parts.append(f"{names[cid]} stays offscreen, not visible anywhere")
            continue
        character = bible["characters"][cid]
        outfit = roles[cid].get("outfit") or character.get("default_outfit") or "default"
        clothes = (character.get("outfits") or {}).get(outfit, "")
        target = names.get(subject["gaze_target"], subject["gaze_target"].replace("_", " "))
        look = (f"Looking {subject['gaze_direction']}" if subject.get("gaze_layered")
                else f"Looking at {target}, {subject['gaze_direction']}")
        description = composition_description(character) if composition else identity_description(character)
        if subject["visibility"] == "partial":
            description = ""          # a hand or shoulder: describing the face makes the model draw the whole person
        appearance = f"Appearance: {description}. " if description else ""
        parts.append(
            f"{names[cid]}: {subject['position']}; {subject['depth']}; {subject['zone']}. "
            f"{subject['pose']}. Facing {subject['facing']}. {look}. "
            f"Expression: {subject['expression']}. Visible: {subject['visibility'].replace('_', ' ')}. "
            f"{appearance}Wearing {clothes}. {subject['wardrobe_state']}")
    for prop in plan["props"]:
        holder = names.get(prop["holder"], prop["holder"])
        parts.append(f"{prop['id'].replace('_', ' ')}: {prop['placement']}; {prop['state']}; holder: {holder}")
    parts.extend(plan["constraints"])
    parts.append(f"Lighting: {plan['lighting']}")
    text = shot.get("screen_text") or ""
    parts.append(f'The planned screen or paper reads exactly "{text}"; no other lettering' if text
                 else "No text, lettering, logos or watermarks")
    style = bible.get("style") or {}
    hide = shot.get("hide_names") or []
    parts.extend(_drop(still_style(style.get(key, "")), hide, r"\s*[;,]\s*") if hide else still_style(style.get(key, ""))
                 for key in ("look", "grade"))
    return ". ".join(part.strip().rstrip(".") for part in parts if part and part.strip()) + "."


def observation_schema(shot):
    """Describe visible facts without supplying the desired locations to the vision model."""
    plan = shot["first_frame"]
    ids = [s["id"] for s in plan["subjects"]]
    visible = [s["id"] for s in plan["subjects"] if s["visibility"] != "offscreen"]   # anyone else is unidentified
    targets = ids + [p["id"] for p in plan["props"]] + ["offscreen", "camera", "unknown"]
    return {"type": "object", "properties": {
        "people": {"type": "array", "items": {"type": "object", "properties": {
            "id": {"type": "string", "enum": visible + ["unidentified"]},
            "space": {"type": "string", "enum": ["inside", "outside", "other", "unknown"]},
            "depth": {"type": "string", "enum": ["foreground", "midground", "background", "unknown"]},
            "gaze_target": {"type": "string", "enum": targets},
            "looks_at_camera": {"type": "boolean"}, "evidence": {"type": "string"}},
            "required": ["id", "space", "depth", "gaze_target", "looks_at_camera", "evidence"]}},
        "props": {"type": "array", "items": {"type": "object", "properties": {
            "id": {"type": "string"}, "holder": {"type": "string", "enum": ids + ["none", "unknown"]}, "state": {"type": "string"},
            "evidence": {"type": "string"}}, "required": ["id", "holder", "state", "evidence"]}},
    }, "required": ["people", "props"]}


def _scenery(shot, prop_id):
    """A fixed prop nobody holds. Several of them usually lie along one sight line (lamp, model, window behind), and a
    vision model cannot tell which one the eyes rest on, so scenery-for-scenery is not a gaze error."""
    prop = next((p for p in shot["first_frame"]["props"] if p["id"] == prop_id), None)
    return bool(prop) and prop.get("holder") in ("", "none")


def _beside(shot, prop_id, target):
    """A gaze at scenery planned right next to the intended target (the car parked beside the man being looked at)
    cannot be told apart from a gaze at the target itself."""
    prop = next((p for p in shot["first_frame"]["props"] if p["id"] == prop_id), None)
    if not prop or prop.get("holder") not in ("", "none") or target in ("offscreen", "camera"):
        return False
    words = {target, target.replace("_", " ")}
    return any(re.search(rf"\b(beside|next to|by|near|behind|in front of)\s+(\w+\s+)?{re.escape(w)}\b",
                         prop.get("placement", ""), re.I) for w in words if len(w) > 2)


DEPTH_RANK = {"foreground": 0, "midground": 1, "background": 2}


def _depth_order_issues(expected, people):
    """Foreground/midground/background only mean something relative to someone else: with one person there is nothing
    to check, and two planned depths only need to keep their order (a midground speaker reported as foreground is
    fine, the planned foreground shoulder reported behind him is not)."""
    seen = {p.get("id"): DEPTH_RANK.get(p.get("depth")) for p in people if isinstance(p, dict)}
    issues = []
    for i, a in enumerate(expected):
        for b in expected[i + 1:]:
            pa, pb = DEPTH_RANK.get(a["depth"]), DEPTH_RANK.get(b["depth"])
            oa, ob = seen.get(a["id"]), seen.get(b["id"])
            if None in (pa, pb, oa, ob) or pa == pb:
                continue
            near, far = (a, b) if pa < pb else (b, a)
            if seen[near["id"]] > seen[far["id"]]:
                issues.append(f"{near['id']} should be nearer the camera than {far['id']}, observed the reverse")
    return issues


HIDDEN = re.compile(r"\b(concealed|hidden|covered|under (his|her|the) (sleeve|cuff|coat)|in (his|her|a) pocket|worn|"
                    r"on (his|her) wrist|not visible)\b", re.I)


# Someone standing in an open doorway is on neither side; a glass barrier, by contrast, has a clear side.
DOORWAY = re.compile(r"\b(threshold|doorway|door ?step|in the door|at the door|open door|partly open door)\b", re.I)


def compare_observations(shot, observed):
    """A model's flattering score cannot override observed people in the wrong physical space.
    Critical issues (who is in the frame, which side of a barrier, a look into the lens or at the wrong PERSON) reject
    the frame. Soft issues (small props, holders, a look at one object instead of another, depth order) are things
    the local vision model often gets wrong: they lower the score and rank candidates but do not reject."""
    expected = [s for s in shot["first_frame"]["subjects"] if s["visibility"] != "offscreen"]
    people, props = observed.get("people"), observed.get("props")
    if not isinstance(people, list) or not isinstance(props, list):
        return {"matches": None, "score": None, "looks_at_camera": False,
                "issues": ["Vision model did not return structured observations"], "observations": observed}
    critical, soft = [], []
    person_ids = {s["id"] for s in shot["first_frame"]["subjects"]}
    ids = [person.get("id") for person in people if isinstance(person, dict)]
    if sorted(map(str, ids)) != sorted(s["id"] for s in expected):
        critical.append(f"Expected people {[s['id'] for s in expected]}, observed {ids}; missing, duplicate or extra people")
    camera_gaze = any(isinstance(p, dict) and p.get("looks_at_camera") is True for p in people)
    for subject in expected:
        person = next((p for p in people if isinstance(p, dict) and p.get("id") == subject["id"]), {})
        faceless = subject["visibility"] in FACELESS          # eyes cannot be seen, so gaze cannot be checked
        if person and person.get("space") != subject["space"]:
            issue = f"{subject['id']}: expected space={subject['space']}, observed {person.get('space') or 'unknown'}"
            (soft if DOORWAY.search(f"{subject.get('zone', '')} {subject.get('position', '')}") else critical).append(issue)
        seen = person.get("gaze_target")
        if person and not (faceless or subject.get("gaze_layered") or seen in (None, "", "unknown")
                           or _beside(shot, seen, subject["gaze_target"])
                           or (_scenery(shot, seen) and _scenery(shot, subject["gaze_target"]))) \
                and seen != subject["gaze_target"]:
            issue = f"{subject['id']}: expected gaze_target={subject['gaze_target']}, observed {seen}"
            (critical if subject["gaze_target"] in person_ids | {"camera"} else soft).append(issue)
        if person.get("looks_at_camera") is True and subject["gaze_target"] != "camera":
            critical.append(f"{subject['id']}: looks into the camera")
        elif person and person.get("looks_at_camera") is not False and not faceless and subject["gaze_target"] != "camera":
            soft.append(f"{subject['id']}: camera gaze not verified")
    soft += _depth_order_issues(expected, people)
    for prop in shot["first_frame"]["props"]:
        if HIDDEN.search(f"{prop.get('state', '')} {prop.get('placement', '')}"):
            continue                                          # planned out of sight: nothing to observe
        actual = next((p for p in props if isinstance(p, dict) and p.get("id") == prop["id"]), None)
        if not actual:
            soft.append(f"{prop['id']}: planned prop not observed")
        elif prop["holder"] not in ("", "none") and actual.get("holder") != prop["holder"]:   # fixed props are not "held"
            soft.append(f"{prop['id']}: expected holder={prop['holder']}, observed {actual.get('holder')}")
    allowed = any(s["gaze_target"] == "camera" for s in expected)
    return {"matches": not critical, "score": max(0, 10 - 3 * len(critical) - len(soft)), "looks_at_camera": camera_gaze,
            "camera_gaze_allowed": allowed, "issues": critical + [f"(minor) {x}" for x in soft],
            "critical": critical, "soft": soft, "observations": observed}


def layered_subjects(shot):
    """People to add after the composition: everyone visible in a different space (inside vs outside, behind glass)
    from the camera's own space - the space of the people nearest the camera. One space -> nothing to layer."""
    plan = shot.get("first_frame") or {}
    visible = [x for x in plan.get("subjects") or [] if x.get("visibility") != "offscreen"]
    if len({x.get("space") for x in visible}) < 2:
        return []
    order = {"foreground": 0, "midground": 1, "background": 2}
    camera_space = min(visible, key=lambda x: order.get(x.get("depth"), 3))["space"]
    return [x for x in visible if x.get("space") != camera_space]


def _names_any(text, words):
    return any(re.search(rf"\b{re.escape(w)}\b", text, re.I) for w in words)


def _drop(text, words, split=r"(?<=[.;!?])\s+"):
    """Remove every sentence (or clause) of the text that names one of the words, keeping the separators."""
    parts = re.split(f"({split})", text or "")
    pieces, seps = parts[0::2], parts[1::2] + [""]
    kept = [(x, sep) for x, sep in zip(pieces, seps) if x.strip() and not _names_any(x, words)]
    return "".join(x + sep for x, sep in kept).strip().rstrip(",;").strip()


HEADCOUNT = re.compile(r"\b(exactly|only)\b[^.;]*\b(people|persons|men|women|figures)\b|"
                       r"\b(one|two|three|four|five|six|\d+)\s+(people|persons|figures)\b", re.I)


def composition_shot(shot, layered, names=None):
    """A copy of the shot whose composition leaves the layered people out and keeps their place empty. A text
    model draws whoever the prompt names, so every mention goes: they are marked offscreen; sentences of the camera,
    layout, moment and constraints and clauses of the props and other subjects that name them are dropped; planned
    headcounts are dropped (image_prompt states the composition's own); gazes at them point at their empty zone."""
    import copy
    out = copy.deepcopy(shot)
    plan = out["first_frame"]
    ids = {x["id"] for x in layered}
    words = {w.lower() for cid in ids for w in [cid, *((names or {}).get(cid, "") or "").split()] if len(w) > 2}
    for key in ("camera", "layout", "moment"):
        plan[key] = _drop(plan.get(key), words)
    constraints = []
    for line in plan.get("constraints") or []:
        line = " ".join(x for x in re.split(r"(?<=[.!?])\s+", line)
                        if x.strip() and not _names_any(x, words) and not HEADCOUNT.search(x))
        if line.strip():
            constraints.append(line)
    for prop in plan.get("props") or []:
        for key in ("placement", "state"):
            prop[key] = _drop(prop.get(key), words, r"\s*[;,]\s*") or prop[key]
    zones = {x["id"]: x["zone"] for x in plan["subjects"] if x["id"] in ids}
    for x in plan["subjects"]:
        if x["id"] in ids:
            x["visibility"], x["layered"] = "offscreen", True
            constraints.append(f"{x['zone']}: lit but empty, nobody there yet")
            continue
        fallback = {"pose": "standing still", "facing": f"toward {zones.get(x.get('gaze_target'), 'the scene')}",
                    "expression": "neutral", "gaze_direction": ""}
        for key in ("pose", "facing", "gaze_direction", "expression", "wardrobe_state", "position", "zone"):
            if key in x:
                x[key] = _drop(x[key], words, r"\s*[;,]\s*") or fallback.get(key, x[key])
        if x.get("gaze_target") in ids:
            x["gaze_direction"] = ", ".join(v for v in (f"toward {zones[x['gaze_target']]}", x.get("gaze_direction", "")) if v)
            x["gaze_target"] = "offscreen"
            x["gaze_layered"] = True        # the target is added later: the composition's gaze cannot be checked
    plan["constraints"] = constraints
    out["hide_names"] = sorted(words)       # image_prompt also drops style clauses naming them
    return out
