"""Blocking, prompt provenance, and shot edits must agree before an image is generated."""
import copy

import pytest
from fastapi.testclient import TestClient

from studio import blocking, jobs, planner, prompts, settings, store
from studio.app import app


@pytest.fixture
def planned(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DATA_ROOT", str(tmp_path))
    monkeypatch.setattr(settings, "SETTINGS_PATH", str(tmp_path / "settings.json"))
    sid = store.create_series("Blocking")["id"]
    eid = store.create_episode(sid, "Pilot")["id"]
    character = store.new_character("Maryam", appearance="dark hair, warm wide smile, brown eyes",
                                    outfits={"default": "cream sweater"})
    store.save_character(sid, character)
    sh = planner.new_shot(id="S01_SH01", scene_id="S01", characters=[{"id": "maryam", "position": "background"}],
                          action="She steps away from the window later", first_frame={
        "camera": "vertical wide, street side", "layout": "A closed window separates camera and bakery",
        "moment": "Maryam stands behind the glass", "lighting": "warm tungsten inside, night outside",
        "subjects": [{"id": "maryam", "position": "right", "depth": "background", "zone": "inside behind glass",
                      "space": "inside", "pose": "standing still", "facing": "outward", "gaze_target": "window",
                      "gaze_direction": "down toward the sill", "expression": "guarded, lips closed",
                      "visibility": "upper_body", "wardrobe_state": "dry, sleeves rolled"}],
        "props": [{"id": "window", "placement": "between camera and woman", "state": "closed", "holder": "none"}],
        "constraints": ["No woman outside"]})
    store.update_episode(sid, eid, lambda e: e["shots"].append(sh))
    return sid, eid, sh, store.bible(sid)


def test_plan_compiles_current_bible_without_inheriting_transient_actions_or_style_conflicts(planned):
    _, _, sh, bible = planned
    bible["style"]["look"] = "photorealistic, 2.39:1 widescreen, motivated camera movement, subtle grain"
    p = prompts.image_prompt(sh, bible)
    assert "Exactly 1 people" in p
    assert "inside behind glass" in p and "Looking at window, down toward the sill" in p
    assert "steps away" not in p and "wide smile" not in p
    assert "2.39:1" not in p and "camera movement" not in p and "subtle grain" in p
    bible["characters"]["maryam"]["outfits"]["default"] = "blue sweater"
    assert "blue sweater" in prompts.image_prompt(sh, bible)
    assert "cream sweater" not in prompts.image_prompt(sh, bible)


@pytest.mark.parametrize("mistake", ["duplicate", "missing_gaze", "unknown_target", "unknown_holder", "missing_subject", "override", "malformed"])
def test_incomplete_or_conflicting_blocking_is_rejected(planned, mistake):
    _, _, sh, bible = planned
    plan = sh["first_frame"]
    if mistake == "duplicate":
        plan["subjects"].append(copy.deepcopy(plan["subjects"][0]))
    elif mistake == "missing_gaze":
        del plan["subjects"][0]["gaze_target"]
    elif mistake == "unknown_target":
        plan["subjects"][0]["gaze_target"] = "someone_the_renderer_must_invent"
    elif mistake == "unknown_holder":
        plan["props"][0]["holder"] = "missing_character"
    elif mistake == "missing_subject":
        plan["subjects"] = []
    elif mistake == "override":
        sh["image_prompt_override"] = "Move her outside instead"
    else:
        plan["subjects"][0]["id"] = ["not", "an", "id"]
    assert blocking.validate(sh, bible)
    with pytest.raises(ValueError):
        prompts.image_prompt(sh, bible)


def test_shot_api_preserves_plan_and_rejects_invalid_edits_atomically(planned):
    sid, eid, sh, _ = planned
    c = TestClient(app)
    url = f"/api/s/{sid}/e/{eid}/shots"
    result = c.put(url, json=[sh])
    assert result.status_code == 200, result.text
    assert result.json()["shots"][0]["first_frame"] == sh["first_frame"]
    before = store.get_episode(sid, eid)
    sh["first_frame"]["subjects"][0]["gaze_target"] = "invented"
    assert c.put(url, json=[sh]).status_code == 400
    assert store.get_episode(sid, eid) == before


def test_blocking_edits_mark_existing_frames_stale(planned):
    _, _, sh, bible = planned
    before = store.bible_signature(bible, sh)
    sh["first_frame"]["subjects"][0]["zone"] = "outside"
    assert store.bible_signature(bible, sh) != before


def test_vision_checker_observes_facts_without_being_shown_desired_staging(planned, monkeypatch):
    _, _, sh, bible = planned
    seen = {}
    def chat(*args, **kwargs):
        seen["question"] = args[3]
        return {"people": [{"id": "maryam", "space": "inside", "depth": "background", "gaze_target": "window",
                            "looks_at_camera": False, "evidence": "behind the pane"}],
                "props": [{"id": "window", "holder": "none", "state": "closed", "evidence": "reflections"}]}
    monkeypatch.setattr(jobs.llm, "chat", chat)
    result = jobs._vision_check(settings.load(), "unused.png", sh, bible)
    assert result["matches"] is True
    assert "Maryam stands behind the glass" not in seen["question"]
    assert "She steps away" not in seen["question"]


def test_observed_wrong_space_overrides_a_models_claim_that_everything_matches(planned):
    _, _, sh, _ = planned
    observed = {"matches": True, "score": 10, "people": [
        {"id": "maryam", "space": "outside", "depth": "foreground", "gaze_target": "window",
         "looks_at_camera": False}], "props": [{"id": "window", "holder": "none"}]}
    check = blocking.compare_observations(sh, observed)
    assert not check["matches"]
    assert any("expected space=inside, observed outside" in issue for issue in check["issues"])


def test_automatic_frame_selection_respects_the_users_first_candidate(planned):
    sid, eid, _, _ = planned
    def prepare(ep):
        frame = ep["shots"][0]["frame"]
        frame["candidates"] = [{"file": "original.png"}, {"file": "new.png", "check": {
            "matches": True, "looks_at_camera": False, "score": 9}}]
        frame["selected"] = 0
    store.update_episode(sid, eid, prepare)
    assert jobs.auto_select_frames(sid, eid, ["S01_SH01"]) == 0
    assert store.get_episode(sid, eid)["shots"][0]["frame"]["selected"] == 0


def test_gaze_of_a_faceless_subject_and_fixed_prop_holders_are_not_failures():
    from studio import blocking
    shot = {"first_frame": {"subjects": [
        {"id": "hakon", "space": "outside", "depth": "foreground", "gaze_target": "maryam", "visibility": "back_of_head"},
        {"id": "maryam", "space": "inside", "depth": "background", "gaze_target": "hakon", "visibility": "upper_body"}],
        "props": [{"id": "car", "holder": "none"}, {"id": "umbrella", "holder": "chauffeur"}]}}
    seen = {"people": [{"id": "hakon", "space": "outside", "depth": "foreground", "gaze_target": "car", "looks_at_camera": None},
                       {"id": "maryam", "space": "inside", "depth": "background", "gaze_target": "unknown", "looks_at_camera": False}],
            "props": [{"id": "car", "holder": "chauffeur"}, {"id": "umbrella", "holder": "chauffeur"}]}
    assert blocking.compare_observations(shot, seen)["matches"] is True
    seen["people"][1]["gaze_target"] = "camera"; seen["people"][1]["looks_at_camera"] = True
    out = blocking.compare_observations(shot, seen)
    assert out["matches"] is False and out["looks_at_camera"] is True        # a real wrong gaze still fails
    seen["people"][1].update(gaze_target="hakon", looks_at_camera=False); seen["props"][1]["holder"] = "none"
    assert "umbrella: expected holder=chauffeur" in " ".join(blocking.compare_observations(shot, seen)["issues"])


def test_people_behind_glass_are_layered_and_composed_out():
    from studio import blocking
    shot = {"first_frame": {"constraints": [], "subjects": [
        {"id": "hakon", "space": "outside", "depth": "foreground", "visibility": "back_of_head", "zone": "street"},
        {"id": "erik", "space": "outside", "depth": "foreground", "visibility": "upper_body", "zone": "street"},
        {"id": "maryam", "space": "inside", "depth": "background", "visibility": "upper_body", "zone": "behind the bakery window"}]}}
    assert [x["id"] for x in blocking.layered_subjects(shot)] == ["maryam"]
    shot["first_frame"]["layout"] = "A wet street outside a bakery. Maryam stands inside behind the glass. The car waits."
    comp = blocking.composition_shot(shot, blocking.layered_subjects(shot), {"maryam": "Maryam Haddad"})
    assert comp["first_frame"]["layout"] == "A wet street outside a bakery. The car waits."
    assert comp["first_frame"]["subjects"][2]["visibility"] == "offscreen"
    assert "behind the bakery window: lit but empty" in comp["first_frame"]["constraints"][0]
    assert shot["first_frame"]["subjects"][2]["visibility"] == "upper_body"          # the original is untouched
    for x in shot["first_frame"]["subjects"]: x["space"] = "inside"
    assert blocking.layered_subjects(shot) == []                                     # one space: business as usual


def test_composition_scrubs_every_mention_of_layered_people():
    from studio import blocking
    sub = lambda cid, space, depth, vis, zone, target, gaze: {
        "id": cid, "position": "left", "depth": depth, "space": space, "zone": zone, "pose": "standing, hands down",
        "facing": "toward the window", "gaze_target": target, "gaze_direction": gaze, "expression": "calm",
        "visibility": vis, "wardrobe_state": "wet coat"}
    shot = {"characters": [{"id": "hakon"}, {"id": "erik"}, {"id": "maryam"}], "first_frame": {
        "camera": "Wide.", "layout": "Street.", "moment": "Night.", "lighting": "Rain.",
        "subjects": [sub("hakon", "outside", "foreground", "back_of_head", "street", "maryam",
                         "straight ahead toward Maryam inside, no visible face"),
                     sub("erik", "outside", "foreground", "upper_body", "street", "hakon", "down-left at Hakon"),
                     sub("maryam", "inside", "background", "upper_body", "behind the glass", "hakon", "down")],
        "props": [{"id": "window", "placement": "background", "state": "closed glass; Maryam visible above the sill",
                   "holder": "none"}],
        "constraints": ["Exactly THREE people: two men outside, one small woman INSIDE.",
                        "Maryam is chest-up behind the glass; no legs on the pavement. Nobody smiles."]}}
    comp = blocking.composition_shot(shot, blocking.layered_subjects(shot), {"maryam": "Maryam Haddad"})
    plan = comp["first_frame"]
    assert plan["constraints"] == ["Nobody smiles.", "behind the glass: lit but empty, nobody there yet"]
    assert plan["props"][0]["state"] == "closed glass"
    hakon = plan["subjects"][0]
    assert hakon["gaze_target"] == "offscreen" and hakon["gaze_direction"] == "toward behind the glass, no visible face"
    bible = {"characters": {c: {"name": c.title()} for c in ("hakon", "erik", "maryam")},
             "style": {"grade": "Hakon's world cold, Maryam's bakery warm"}}
    text = blocking.image_prompt(comp, bible, composition=True)
    assert "maryam" not in text.lower() and "THREE" not in text and "Exactly 2 people" in text
    # The composition check cannot verify a gaze at someone who is added later
    seen = {"people": [{"id": "hakon", "space": "outside", "depth": "foreground", "gaze_target": "window",
                        "looks_at_camera": False},
                       {"id": "erik", "space": "outside", "depth": "foreground", "gaze_target": "hakon",
                        "looks_at_camera": False}], "props": [{"id": "window", "holder": "none"}]}
    hakon.update(visibility="upper_body")
    assert blocking.compare_observations(comp, seen)["matches"] is True


def test_gaze_at_scenery_beside_the_target_is_unverifiable():
    from studio import blocking
    shot = {"first_frame": {"subjects": [
        {"id": "hakon", "space": "outside", "depth": "foreground", "visibility": "back_of_head", "gaze_target": "offscreen"},
        {"id": "chauffeur", "space": "outside", "depth": "foreground", "visibility": "upper_body", "gaze_target": "hakon"}],
        "props": [{"id": "car", "placement": "at the curb, beside Hakon", "holder": "none"},
                  {"id": "lamp", "placement": "far across the street", "holder": "none"}]}}
    person = lambda cid, target: {"id": cid, "space": "outside", "depth": "foreground", "gaze_target": target,
                                  "looks_at_camera": False}
    seen = {"people": [person("hakon", "unknown"), person("chauffeur", "car")],
            "props": [{"id": "car", "holder": "none"}, {"id": "lamp", "holder": "none"}]}
    assert blocking.compare_observations(shot, seen)["matches"] is True
    seen["people"][1]["gaze_target"] = "lamp"
    assert "chauffeur: expected gaze_target=hakon" in " ".join(blocking.compare_observations(shot, seen)["issues"])


def test_depth_is_checked_as_an_order_not_a_label():
    from studio import blocking
    person = lambda cid, depth: {"id": cid, "space": "outside", "depth": depth, "gaze_target": "unknown",
                                 "looks_at_camera": False}
    sub = lambda cid, depth: {"id": cid, "space": "outside", "depth": depth, "visibility": "upper_body",
                              "gaze_target": "offscreen"}
    solo = {"first_frame": {"subjects": [sub("hakon", "midground")], "props": []}}
    assert blocking.compare_observations(solo, {"people": [person("hakon", "foreground")], "props": []})["matches"]
    pair = {"first_frame": {"subjects": [sub("hakon", "midground"), sub("erik", "foreground")], "props": []}}
    same_order = {"people": [person("hakon", "background"), person("erik", "midground")], "props": []}
    assert blocking.compare_observations(pair, same_order)["matches"]
    reversed_ = {"people": [person("hakon", "foreground"), person("erik", "background")], "props": []}
    assert "erik should be nearer the camera than hakon" in blocking.compare_observations(pair, reversed_)["issues"][0]


def test_scenery_for_scenery_gaze_is_not_an_error_but_person_for_scenery_is():
    from studio import blocking
    shot = {"first_frame": {"subjects": [{"id": "hakon", "space": "inside", "depth": "midground",
                                          "visibility": "full_body", "gaze_target": "windows"},
                                         {"id": "erik", "space": "inside", "depth": "midground",
                                          "visibility": "full_body", "gaze_target": "hakon"}],
                            "props": [{"id": "windows", "placement": "back wall", "holder": "none"},
                                      {"id": "lamp", "placement": "on the table", "holder": "none"}]}}
    person = lambda cid, target: {"id": cid, "space": "inside", "depth": "midground", "gaze_target": target,
                                  "looks_at_camera": False}
    props = [{"id": "windows", "holder": "none"}, {"id": "lamp", "holder": "none"}]
    assert blocking.compare_observations(shot, {"people": [person("hakon", "lamp"), person("erik", "hakon")],
                                                "props": props})["matches"]
    out = blocking.compare_observations(shot, {"people": [person("hakon", "erik"), person("erik", "lamp")], "props": props})
    assert len(out["issues"]) == 2


def test_only_critical_issues_reject_a_frame():
    from studio import blocking
    sub = lambda cid, target: {"id": cid, "space": "inside", "depth": "midground", "visibility": "upper_body",
                               "gaze_target": target}
    shot = {"first_frame": {"subjects": [sub("hakon", "dough"), sub("maryam", "hakon")], "props": [
        {"id": "dough", "placement": "on the counter", "state": "covered", "holder": "none"},
        {"id": "watch", "placement": "on his wrist", "state": "concealed under the cuff", "holder": "hakon"},
        {"id": "pen", "placement": "on the ledger", "state": "capped", "holder": "maryam"}]}}
    person = lambda cid, target, space="inside": {"id": cid, "space": space, "depth": "midground",
                                                  "gaze_target": target, "looks_at_camera": False}
    soft_only = blocking.compare_observations(shot, {"people": [person("hakon", "maryam"), person("maryam", "hakon")],
                                                     "props": [{"id": "dough", "holder": "none"}]})
    assert soft_only["matches"] is True and soft_only["score"] == 8          # gaze at object + pen not seen
    assert not any("watch" in x for x in soft_only["issues"])                  # concealed: nothing to observe
    wrong = blocking.compare_observations(shot, {"people": [person("hakon", "dough", "outside"),
                                                            person("maryam", "dough")], "props": []})
    assert wrong["matches"] is False and len(wrong["critical"]) == 2          # wrong side + wrong person looked at


def test_inside_outside_is_soft_only_in_a_doorway():
    from studio import blocking
    def run(zone):
        shot = {"first_frame": {"subjects": [{"id": "hakon", "space": "inside", "depth": "midground", "zone": zone,
                                              "position": "left", "visibility": "upper_body", "gaze_target": "offscreen"}],
                                "props": []}}
        return blocking.compare_observations(shot, {"people": [{"id": "hakon", "space": "outside", "depth": "midground",
                                                                "gaze_target": "unknown", "looks_at_camera": False}],
                                                    "props": []})
    assert run("threshold, just inside the door")["matches"] is True
    assert run("behind the bakery window")["matches"] is False


def test_an_empty_shot_asks_for_nobody_instead_of_zero_people():
    from studio import blocking
    shot = {"characters": [], "framing": "wide", "first_frame": {
        "camera": "Wide.", "layout": "Empty street.", "moment": "Rain.", "lighting": "Night.", "subjects": [],
        "props": [], "constraints": []}}
    text = blocking.image_prompt(shot, {"characters": {}, "style": {}})
    assert "Nobody is in the frame" in text and "Exactly 0" not in text


def test_a_partial_person_gets_no_face_description():
    from studio import blocking
    sub = {"id": "hakon", "position": "right edge", "depth": "foreground", "space": "inside", "zone": "table",
           "pose": "only his right hand in frame", "facing": "down", "gaze_target": "phone", "gaze_direction": "down",
           "expression": "not visible", "visibility": "partial", "wardrobe_state": "bare forearm"}
    shot = {"characters": [{"id": "hakon", "outfit": "street"}], "framing": "insert", "first_frame": {
        "camera": "Overhead.", "layout": "Table.", "moment": "Still.", "lighting": "Night.", "subjects": [sub],
        "props": [{"id": "phone", "placement": "centre", "state": "lit", "holder": "none"}], "constraints": []}}
    bible = {"characters": {"hakon": {"name": "Hakon", "appearance": "balding man, round glasses, stubble",
                                      "outfits": {"street": "black jacket"}}}, "style": {}}
    text = blocking.image_prompt(shot, bible, composition=True)
    assert "round glasses" not in text and "only his right hand in frame" in text
