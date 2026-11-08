"""Writing pipeline (spine -> cards -> shots -> review) and the Claude connection, with no real API calls."""
import json
import sys
import types

import pytest

from studio import cloud, prompts, settings, store, writer


@pytest.fixture
def sid(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DATA_ROOT", str(tmp_path))
    monkeypatch.setattr(settings, "SETTINGS_PATH", str(tmp_path / "settings.json"))
    sid = store.create_series("Lie")["id"]
    for n in ("Hakon", "Maryam"):
        store.save_character(sid, store.new_character(n, appearance="a person", outfits={"default": "coat"}), bump=False)
    store.save_location(sid, store.new_location("Bakery", description="old bakery, chalkboard menu, warm lights",
                                                variants={"night": "night, rain"}), bump=False)
    return sid


SPINE = {"logline": "A rich man is fed for free.", "theme": "kindness cannot be bought", "characters": [],
         "setups": [{"id": "logo", "what": "tower logo", "planted": "S01", "payoff": "S01 envelope"}], "ledger": []}
CARDS = {"scenes": [{"location_id": "bakery", "time_of_day": "night", "summary": "He asks her name.", "purpose": "trust",
                     "geography": "Hakon right, Maryam left, both inside across the counter; camera stays on the door side.",
                     "value": "trust", "turn": "guarded -> open", "pov": "hakon",
                     "characters": [{"id": "hakon", "outfit": "default"}, {"id": "maryam", "outfit": "default"}],
                     "beats": ["he asks"], "dialogue": [{"speaker": "hakon", "line": "Name?"}, {"speaker": "maryam", "line": "Maryam."}]}]}
DESIGNED = [{"purpose": "geography", "framing": "two-shot", "characters": [{"id": "hakon", "position": "right"}, {"id": "maryam", "position": "left"}],
             "face": "", "picture": "both at the counter", "action": "they face each other", "dialogue": [], "seconds": 3},
            {"purpose": "the ask", "framing": "over-the-shoulder", "characters": [{"id": "hakon", "position": "right"}, {"id": "maryam", "position": "left"}],
             "face": "hakon", "picture": "over her shoulder onto him", "action": "he asks, listens",
             "dialogue": [{"speaker": "hakon", "line": "Name?"}, {"speaker": "maryam", "line": "Maryam."}]}]


def planned_frame():
    return {"camera": "Eye level from the door side of the counter", "layout": "One counter between two people inside",
            "moment": "Both hold still before the question", "lighting": "Warm tungsten at night",
            "subjects": [{"id": cid, "position": side, "depth": "midground", "zone": "inside the bakery",
                          "space": "inside", "pose": "standing still", "facing": direction, "gaze_target": other,
                          "gaze_direction": f"toward frame {direction}, not into the lens", "expression": "guarded",
                          "visibility": "upper_body", "wardrobe_state": "dry, sleeves unchanged"}
                         for cid, side, direction, other in [("hakon", "right", "left", "maryam"),
                                                             ("maryam", "left", "right", "hakon")]],
            "props": [{"id": "counter", "placement": "between the two people", "state": "stationary", "holder": "none"}],
            "constraints": ["Nobody looks into the camera lens"]}


for designed in DESIGNED:
    designed["first_frame"] = planned_frame()


def test_pipeline_layers_see_the_layer_above_and_review_wins(sid, monkeypatch):
    seen = []

    def fake_chat(s, role, system, user, schema, **kw):
        seen.append((system, user))
        if "showrunner" in system:
            return SPINE
        if "story editor" in system:
            return CARDS
        if "script supervisor" in system:
            return {"issues": ["merged a repeated setup"], "shots": DESIGNED}
        return {"shots": DESIGNED + [DESIGNED[1]]}             # the director's draft repeats a shot

    monkeypatch.setattr(writer.llm, "chat", fake_chat)
    s = settings.load()
    spine, cards = writer.plan_cards(s, sid, "EPISODE TEXT", "SERIES TEXT", log=lambda *_: None)
    assert cards[0]["id"] == "S01" and "SERIES TEXT" in seen[0][1] and "kindness" in seen[1][1]   # cards see the spine
    shots, notes = writer.plan_shots(s, sid, spine, cards, log=lambda *_: None)
    assert "THIS SCENE CARD" in seen[2][1] and "STORY SPINE" in seen[2][1]                    # shots see card + spine
    assert "SHOT LIST TO CHECK" in seen[3][1] and notes == ["S01: merged a repeated setup"]
    assert len(shots) == 2                                                                    # the review's list is kept
    assert shots[0]["engine"] == "ltx_lipsync" and shots[0]["duration_locked"]
    assert shots[1]["engine"] == "infinitetalk" and shots[1]["face"] == "hakon"
    prompt = prompts.image_prompt(shots[1], store.bible(sid))
    assert "chalkboard" not in prompt and "Bakery" not in prompt
    assert shots[1]["image_prompt_override"] == "" and shots[1]["first_frame"]
    assert shots[1]["notes"] == "the ask"


def test_listener_shot_without_own_lines_is_not_lip_synced(sid):
    b = store.bible(sid)
    card = dict(CARDS["scenes"][0], id="S01")
    listen = dict(DESIGNED[1], face="maryam", dialogue=[{"speaker": "hakon", "line": "Name?"}])
    assert writer.to_studio_shots(b, card, [listen])[0]["engine"] == "ltx_lipsync"


def test_claude_connection_sends_closed_schema_and_parses_json(monkeypatch):
    sent = {}

    class Stream:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def get_final_message(self):
            return types.SimpleNamespace(stop_reason="end_turn",
                                         content=[types.SimpleNamespace(type="text", text=json.dumps({"ok": 1}))])

    class Client:
        def __init__(self, **kw): sent["client"] = kw
        class beta:
            class messages:
                @staticmethod
                def stream(**kw):
                    sent["req"] = kw
                    return Stream()

    fake = types.SimpleNamespace(Anthropic=Client, AuthenticationError=Exception, NotFoundError=Exception,
                                 RateLimitError=Exception, APIStatusError=Exception, APIConnectionError=Exception)
    monkeypatch.setitem(sys.modules, "anthropic", fake)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    s = settings.load()
    out = cloud.chat_json(s, "claude", "SYS", "USER", {"type": "object", "properties": {"a": {"type": "object", "properties": {}}}})
    assert out == {"ok": 1}
    req = sent["req"]
    assert req["model"] == "claude-opus-5-5" and req["system"] == "SYS" and "temperature" not in req
    fmt = req["output_config"]["format"]
    assert fmt["type"] == "json_schema" and fmt["schema"]["additionalProperties"] is False
    assert fmt["schema"]["properties"]["a"]["additionalProperties"] is False
    assert req["fallbacks"] == "default" and req["betas"] == ["server-side-fallback-2026-07-01"]


def test_claude_refusal_is_a_readable_error(monkeypatch):
    class Stream:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def get_final_message(self):
            return types.SimpleNamespace(stop_reason="refusal", content=[], stop_details=types.SimpleNamespace(explanation="policy"))

    class Client:
        def __init__(self, **kw): pass
        class beta:
            class messages:
                stream = staticmethod(lambda **kw: Stream())

    monkeypatch.setitem(sys.modules, "anthropic", types.SimpleNamespace(
        Anthropic=Client, AuthenticationError=KeyError, NotFoundError=KeyError, RateLimitError=KeyError,
        APIStatusError=KeyError, APIConnectionError=KeyError))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    with pytest.raises(cloud.CloudError, match="declined"):
        cloud.chat_json(settings.load(), "claude", "S", "U", {"type": "object", "properties": {}})


def test_setting_text_never_borrows_another_times_light(sid):
    from studio import planner
    loc = store.bible(sid)["locations"]["bakery"]
    assert "night, rain" in planner.setting_text(store.bible(sid), loc, "night")
    dawn = planner.setting_text(store.bible(sid), loc, "dawn, before opening")
    assert "dawn, before opening" in dawn and "night, rain" not in dawn


def test_write_episode_continues_the_previous_script(sid, monkeypatch):
    seen = {}
    monkeypatch.setattr(writer.llm, "chat", lambda s, r, system, user, schema, **k: seen.update(u=user) or {"script": "# Ep 2"})
    out = writer.write_episode(settings.load(), store.bible(sid), "OUTLINE", 2, "The Poor Man", "EP1 SCRIPT")
    assert out["script"] == "# Ep 2" and "EP1 SCRIPT" in seen["u"] and "Episode 2 - The Poor Man" in seen["u"]


def test_scene_budget_and_story_critical_screen_text(sid):
    cards = [{"dialogue": [1, 2, 3, 4], "beats": [1]}, {"dialogue": [], "beats": [1, 2]}]
    a, b_ = writer.scene_budget(cards, 0), writer.scene_budget(cards, 1)
    assert a > b_ and abs(a + b_ - writer.TARGET_SECONDS) <= 1
    b = store.bible(sid)
    card = dict(CARDS["scenes"][0], id="S01")
    frame = dict(planned_frame(), subjects=[])
    shot = dict(DESIGNED[0], first_frame=frame, framing="insert", characters=[], screen_text="Dividend paid: 1,800,000 kr")
    p = prompts.image_prompt(writer.to_studio_shots(b, card, [shot])[0], b)
    assert '"Dividend paid: 1,800,000 kr"' in p and "No text, no letters" not in p


def test_gaze_is_compiled_from_explicit_targets_instead_of_character_order(sid):
    b = store.bible(sid)
    card = dict(CARDS["scenes"][0], id="S01")
    frame = planned_frame()
    frame["subjects"][0].update(gaze_target="counter", gaze_direction="down at the countertop")
    shot = dict(DESIGNED[1], first_frame=frame)
    p = prompts.image_prompt(writer.to_studio_shots(b, card, [shot])[0], b)
    assert "Looking at counter, down at the countertop" in p
    assert "Looking at Maryam" not in p
    assert "Looking at Hakon, toward frame right" in p


def test_writer_rejects_missing_blocking_instead_of_guessing(sid):
    shot = {key: value for key, value in DESIGNED[0].items() if key != "first_frame"}
    with pytest.raises(ValueError, match="gaze cannot be guessed"):
        writer.to_studio_shots(store.bible(sid), dict(CARDS["scenes"][0], id="S01"), [shot])


def test_best_candidate_prefers_no_camera_gaze():
    from studio import jobs
    cands = [{"check": {"looks_at_camera": True, "matches": True, "score": 10}},
             {"check": {"looks_at_camera": False, "matches": True, "score": 7}},
             {"check": None}]
    assert jobs.best_candidate(cands) == 1 and jobs.best_candidate([{}]) is None
    assert jobs.best_candidate([{"check": {"matches": False, "looks_at_camera": False, "score": 9}}]) is None
    assert jobs.best_candidate([{"check": {"matches": True, "looks_at_camera": True, "score": 10}}]) is None


def test_block_shots_adds_blocking_without_redesigning_and_retries_on_errors(sid, monkeypatch):
    import copy
    calls = []
    cards = [dict(CARDS["scenes"][0], id="S01")]
    shots = [{"id": "S01_SH01", "scene_id": "S01", "order": 0, "framing": "two-shot", "action": "they face each other",
              "characters": [{"id": "hakon", "position": "right"}, {"id": "maryam", "position": "left"}],
              "first_frame": planned_frame()},
             {"id": "S01_SH02", "scene_id": "S01", "order": 1, "framing": "over-the-shoulder", "action": "he asks",
              "image_prompt_override": "Over her shoulder onto him", "dialogue": [{"speaker": "hakon", "line": "Name?"}],
              "characters": [{"id": "hakon", "position": "right"}, {"id": "maryam", "position": "left"}]}]

    def fake_chat(s, role, system, user, schema, **kw):
        calls.append(user)
        frame = planned_frame()
        if len(calls) == 1:
            frame["subjects"] = frame["subjects"][:1]            # forgets Maryam: must be sent back with the error
        assert list(schema["properties"]["shots"]["properties"]) == ["S01_SH02"]       # one schema per shot
        shot = schema["properties"]["shots"]["properties"]["S01_SH02"]
        assert shot["properties"]["props"]["items"]["properties"]["holder"]["enum"] == ["hakon", "maryam", "none"]
        return {"shots": {"S01_SH02": frame}}

    monkeypatch.setattr(writer.llm, "chat", fake_chat)
    before = copy.deepcopy(shots)
    saved = []
    out, failures = writer.block_shots(settings.load(), sid, SPINE, cards, shots, log=lambda *_: None, save=saved.append)
    assert failures == [] and saved == [out]
    assert list(out) == ["S01_SH02"] and len(out["S01_SH02"]["subjects"]) == 2
    assert "BLOCK THESE SHOTS: S01_SH02" in calls[0] and "Over her shoulder onto him" in calls[0]
    assert "FAILED VALIDATION" in calls[1] and "must match the shot cast" in calls[1]
    assert shots == before                                       # the shot list itself is not touched here
