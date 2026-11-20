"""Bridge pass: the planner shows how a character gets from one place to the next."""
import pytest

from studio import planner, settings, store


@pytest.fixture
def sid(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DATA_ROOT", str(tmp_path))
    monkeypatch.setattr(settings, "SETTINGS_PATH", str(tmp_path / "settings.json"))
    sid = store.create_series("Lie")["id"]
    store.save_character(sid, store.new_character("Hakon", appearance="man"), bump=False)
    for name in ["Penthouse", "Oslo Street", "Maryam's Bakery", "Bakery Exterior"]:
        store.save_location(sid, store.new_location(name, variants={"night": ""}), bump=False)
    return sid


def scene(sid_, loc, summary="", beats=("beat",), chars=("hakon",)):
    return {"id": sid_, "location_id": loc, "time_of_day": "night", "summary": summary, "beats": list(beats),
            "characters": [{"id": c, "outfit": "default"} for c in chars], "dialogue": []}


def test_gaps_skip_same_building_time_jumps_and_new_characters(sid):
    b = store.bible(sid)
    scenes = [scene("S01", "penthouse"), scene("S02", "oslo_street"), scene("S03", "maryam_s_bakery"),
              scene("S04", "bakery_exterior"), scene("S05", "penthouse", "The next morning, he wakes."),
              scene("S06", "oslo_street", chars=())]
    gaps = planner.find_gaps(scenes, b)
    # penthouse -> street and street -> bakery are candidates; bakery -> its own exterior is one door;
    # "the next morning" is a time jump; nobody carries over into S06.
    assert [i for i, _ in gaps] == [0, 1]
    assert gaps[0][1] == ["hakon"]


def test_bridge_inserted_with_new_draft_location_and_renumbered(sid, monkeypatch):
    b = store.bible(sid)
    calls = []

    def fake_chat(s, role, system, user, schema, **kw):
        calls.append(user)
        if "FROM scene S01" in user:
            return {"move": "a real journey", "location_id": "new", "new_location_name": "Tower Lobby",
                    "new_location_description": "marble lobby", "summary": "He leaves the tower.",
                    "beats": ["Lift doors open on the empty lobby.", "He walks past the waiting car into the rain."]}
        return {"move": "through one door or a few steps", "location_id": "oslo_street", "summary": "", "beats": ["x"]}

    monkeypatch.setattr(planner.llm, "chat", fake_chat)
    scenes = [scene("S01", "penthouse"), scene("S02", "oslo_street"), scene("S03", "maryam_s_bakery")]
    out = planner.bridge_scenes({}, sid, b, scenes, log=lambda *_: None)
    assert [sc["id"] for sc in out] == ["S01", "S02", "S03", "S04"]
    bridge = out[1]
    assert bridge["transition"] is True and bridge["dialogue"] == []
    assert bridge["location_id"] == "tower_lobby" and bridge["characters"][0]["id"] == "hakon"
    assert store.get_location(sid, "tower_lobby")["draft"] is True
    assert out[2]["location_id"] == "oslo_street" and len(calls) == 2


def test_shot_pass_sees_neighbouring_scenes(sid, monkeypatch):
    b = store.bible(sid)
    seen = {}

    def fake_chat(s, role, system, user, schema, **kw):
        seen["system"], seen["user"] = system, user
        return {"shots": []}

    monkeypatch.setattr(planner.llm, "chat", fake_chat)
    prev = scene("S01", "penthouse", "He puts on the old jacket.", beats=["He pulls on the jacket."])
    nxt = scene("S03", "maryam_s_bakery", "He meets Maryam.", beats=["The bell rings."])
    planner.plan_shots({}, b, scene("S02", "oslo_street"), prev, nxt)
    assert "PREVIOUS SCENE S01 at penthouse" in seen["user"] and "He pulls on the jacket." in seen["user"]
    assert "NEXT SCENE S03 at maryam_s_bakery" in seen["user"] and "The bell rings." in seen["user"]
    assert "clean exit" in seen["system"]
