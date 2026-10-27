"""File-backed store for series, bible entries and episodes.

Layout (all JSON, editable by hand):
    studio_data/series/<sid>/series.json
    studio_data/series/<sid>/bible/style.json
    studio_data/series/<sid>/bible/props.json
    studio_data/series/<sid>/bible/characters/<cid>/character.json  (+ refs/, sheet/, voice file)
    studio_data/series/<sid>/bible/locations/<lid>/location.json    (+ plates/)
    studio_data/series/<sid>/episodes/<eid>/episode.json            (+ frames/, takes/, uploads/, out/)
"""

import copy
import datetime as _dt
import hashlib
import json
import os
import re
import threading

from . import settings as _settings

_lock = threading.RLock()


def now() -> str:
    return _dt.datetime.now().isoformat(timespec="seconds")


def slug(text: str, fallback: str = "item") -> str:
    s = re.sub(r"[^a-z0-9]+", "_", (text or "").lower()).strip("_")
    return s[:40] or fallback


def root() -> str:
    return os.path.join(_settings.DATA_ROOT, "series")


def series_dir(sid: str) -> str:
    return os.path.join(root(), sid)


def bible_dir(sid: str) -> str:
    return os.path.join(series_dir(sid), "bible")


def char_dir(sid: str, cid: str) -> str:
    return os.path.join(bible_dir(sid), "characters", cid)


def loc_dir(sid: str, lid: str) -> str:
    return os.path.join(bible_dir(sid), "locations", lid)


def ep_dir(sid: str, eid: str) -> str:
    return os.path.join(series_dir(sid), "episodes", eid)


def _read(path: str, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return copy.deepcopy(default)


def _write(path: str, data) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def _unique(base: str, existing) -> str:
    cand, n = base, 2
    while cand in existing:
        cand = f"{base}_{n}"
        n += 1
    return cand


# ── Series ──────────────────────────────────────────────────────────────

DEFAULT_STYLE = {
    "look": "stylised 3D animated film, soft global illumination, expressive characters",
    "grade": "warm cinematic colour grade, gentle contrast",
    "aspect": "16:9",
    "negative": "photorealistic, text, watermark, logo, subtitles, deformed hands, extra fingers",
    "music_policy": "no music in shots; score added at the cut",
    "default_frame_source": "flux_lora",
    "default_engine": "ltx",
    "version": 1,
}


def list_series() -> list[dict]:
    out = []
    if os.path.isdir(root()):
        for sid in sorted(os.listdir(root())):
            s = _read(os.path.join(series_dir(sid), "series.json"))
            if s:
                out.append(s)
    return out


def create_series(name: str) -> dict:
    with _lock:
        existing = {s["id"] for s in list_series()}
        sid = _unique(slug(name, "series"), existing)
        s = {"id": sid, "name": name or sid, "created": now()}
        _write(os.path.join(series_dir(sid), "series.json"), s)
        _write(os.path.join(bible_dir(sid), "style.json"), DEFAULT_STYLE)
        _write(os.path.join(bible_dir(sid), "props.json"), [])
        return s


def get_series(sid: str) -> dict | None:
    return _read(os.path.join(series_dir(sid), "series.json"))


# ── Bible ───────────────────────────────────────────────────────────────

def get_style(sid: str) -> dict:
    return {**DEFAULT_STYLE, **(_read(os.path.join(bible_dir(sid), "style.json"), {}) or {})}


def save_style(sid: str, style: dict) -> dict:
    with _lock:
        cur = get_style(sid)
        style = {**cur, **style, "version": int(cur.get("version", 1)) + 1}
        _write(os.path.join(bible_dir(sid), "style.json"), style)
        return style


def get_props(sid: str) -> list:
    return _read(os.path.join(bible_dir(sid), "props.json"), []) or []


def save_props(sid: str, props: list) -> None:
    _write(os.path.join(bible_dir(sid), "props.json"), props)


def new_character(name: str, **fields) -> dict:
    c = {
        "id": slug(name, "character"), "name": name, "trigger": "",
        "appearance": "", "outfits": {"default": ""}, "default_outfit": "default",
        "acting_notes": "", "never_change": [],
        "image_lora": {"name": "", "strength": 0.9},
        "video_lora": {"name": "", "strength": 0.8},
        "voice": {"file": "", "wpm": 140, "description": ""},
        "frame_source": "",          # "" = inherit series default
        "refs": [], "sheet": [], "version": 1, "draft": False,
    }
    c.update({k: v for k, v in fields.items() if v is not None})
    return c


def list_characters(sid: str) -> list[dict]:
    base = os.path.join(bible_dir(sid), "characters")
    out = []
    if os.path.isdir(base):
        for cid in sorted(os.listdir(base)):
            c = _read(os.path.join(base, cid, "character.json"))
            if c:
                out.append(c)
    return out


def get_character(sid: str, cid: str) -> dict | None:
    return _read(os.path.join(char_dir(sid, cid), "character.json"))


def save_character(sid: str, c: dict, bump: bool = True) -> dict:
    with _lock:
        if not c.get("id"):
            c["id"] = _unique(slug(c.get("name"), "character"), {x["id"] for x in list_characters(sid)})
        cur = get_character(sid, c["id"])
        if cur and bump:
            c["version"] = int(cur.get("version", 1)) + 1
        _write(os.path.join(char_dir(sid, c["id"]), "character.json"), c)
        return c


def new_location(name: str, **fields) -> dict:
    loc = {
        "id": slug(name, "location"), "name": name, "description": "",
        "variants": {"day": ""}, "palette": "", "ambience": "",
        "plates": {}, "candidates": [], "version": 1, "draft": False,
    }
    loc.update({k: v for k, v in fields.items() if v is not None})
    return loc


def list_locations(sid: str) -> list[dict]:
    base = os.path.join(bible_dir(sid), "locations")
    out = []
    if os.path.isdir(base):
        for lid in sorted(os.listdir(base)):
            loc = _read(os.path.join(base, lid, "location.json"))
            if loc:
                out.append(loc)
    return out


def get_location(sid: str, lid: str) -> dict | None:
    return _read(os.path.join(loc_dir(sid, lid), "location.json"))


def save_location(sid: str, loc: dict, bump: bool = True) -> dict:
    with _lock:
        if not loc.get("id"):
            loc["id"] = _unique(slug(loc.get("name"), "location"), {x["id"] for x in list_locations(sid)})
        cur = get_location(sid, loc["id"])
        if cur and bump:
            loc["version"] = int(cur.get("version", 1)) + 1
        _write(os.path.join(loc_dir(sid, loc["id"]), "location.json"), loc)
        return loc


def bible(sid: str) -> dict:
    return {
        "style": get_style(sid),
        "characters": {c["id"]: c for c in list_characters(sid)},
        "locations": {l["id"]: l for l in list_locations(sid)},
        "props": get_props(sid),
    }


def bible_signature(b: dict, shot: dict) -> str:
    """Hash of the bible versions a shot depends on (for stale detection)."""
    parts = [f"style:{b['style'].get('version', 1)}"]
    loc = b["locations"].get(shot.get("location_id") or "")
    if loc:
        parts.append(f"loc:{loc['id']}:{loc.get('version', 1)}")
    for ch in shot.get("characters", []):
        c = b["characters"].get(ch.get("id"))
        if c:
            parts.append(f"char:{c['id']}:{c.get('version', 1)}")
    return hashlib.sha1("|".join(parts).encode()).hexdigest()[:12]


# ── Episodes ────────────────────────────────────────────────────────────

def list_episodes(sid: str) -> list[dict]:
    base = os.path.join(series_dir(sid), "episodes")
    out = []
    if os.path.isdir(base):
        for eid in sorted(os.listdir(base)):
            e = _read(os.path.join(base, eid, "episode.json"))
            if e:
                out.append({k: e.get(k) for k in ("id", "title", "created")} | {"shots": len(e.get("shots", []))})
    return out


def create_episode(sid: str, title: str) -> dict:
    with _lock:
        existing = {e["id"] for e in list_episodes(sid)}
        eid = _unique(slug(title, "episode"), existing)
        e = {
            "id": eid, "title": title or eid, "created": now(), "story": "",
            "scenes": [], "shots": [],
            "gates": {"shots": False, "frames": False, "animatic": False, "takes": False},
            "stats": {"spf": {}}, "music": {"file": "", "volume": 0.25, "duck": True},
            "outputs": {},
        }
        _write(os.path.join(ep_dir(sid, eid), "episode.json"), e)
        return e


def get_episode(sid: str, eid: str) -> dict | None:
    return _read(os.path.join(ep_dir(sid, eid), "episode.json"))


def save_episode(sid: str, e: dict) -> dict:
    with _lock:
        _write(os.path.join(ep_dir(sid, e["id"]), "episode.json"), e)
        return e


def update_episode(sid: str, eid: str, fn) -> dict:
    """Atomic read-modify-write; fn mutates the episode dict in place."""
    with _lock:
        e = get_episode(sid, eid)
        if e is None:
            raise KeyError(f"episode {sid}/{eid} not found")
        fn(e)
        _write(os.path.join(ep_dir(sid, eid), "episode.json"), e)
        return e


def find_shot(e: dict, shot_id: str) -> dict:
    for s in e.get("shots", []):
        if s["id"] == shot_id:
            return s
    raise KeyError(f"shot {shot_id} not found")


def renumber_shots(e: dict) -> None:
    """Keep ids stable once assets exist; only fill missing ids and order."""
    used = {s["id"] for s in e["shots"] if s.get("id")}
    for i, s in enumerate(e["shots"]):
        s["order"] = i
        if not s.get("id"):
            sc = s.get("scene_id") or "S00"
            n = 1
            while f"{sc}_SH{n:02d}" in used:
                n += 1
            s["id"] = f"{sc}_SH{n:02d}"
            used.add(s["id"])


def rel(path: str) -> str:
    """Path relative to the data root, with forward slashes (for /media URLs)."""
    return os.path.relpath(path, _settings.DATA_ROOT).replace("\\", "/")


def abs_media(relpath: str) -> str:
    p = os.path.abspath(os.path.join(_settings.DATA_ROOT, relpath))
    if not p.startswith(os.path.abspath(_settings.DATA_ROOT)):
        raise ValueError("path escapes data root")
    return p
