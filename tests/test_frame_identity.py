"""First-frame composition and portrait identity must be separate model calls."""
import os
import shutil
from pathlib import Path

import pytest
from PIL import Image

from studio import jobs, planner, settings, store
from studio.app import _required_model_options


class FrameComfy:
    def __init__(self, directory):
        self.directory = directory
        self.graphs = []
        self.uploads = {}
        self.events = []
        self.on_composition = None
        self.fail_identity = False

    def upload(self, path, name):
        assert os.path.isfile(path)
        self.uploads[name] = path
        return name

    def free(self):
        self.events.append("free")

    def run(self, graph, cancel):
        assert not cancel()
        self.graphs.append(graph)
        identity = graph["4"]["class_type"] == "TextEncodeQwenImageEditPlus"
        self.events.append("identity" if identity else "composition")
        if identity and self.fail_identity:
            return {"images": []}
        n = graph["7"]["inputs"]["batch_size"]
        images = []
        for i in range(n):
            path = self.directory / f"render_{len(self.graphs)}_{i}.png"
            Image.new("RGB", (64, 96), (len(self.graphs) * 20, i * 40, 90)).save(path)
            images.append({"filename": path.name})
        if not identity and self.on_composition:
            self.on_composition()
        return {"images": images}

    def download(self, item, destination):
        shutil.copyfile(self.directory / item["filename"], destination)
        return destination


@pytest.fixture
def frame_project(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DATA_ROOT", str(tmp_path))
    monkeypatch.setattr(settings, "SETTINGS_PATH", str(tmp_path / "settings.json"))
    settings.save({"candidates_per_shot": 2, "vision_check": False, "identity_pass": True,
                   "image": {"width": 64, "height": 96},
                   "location_ref": {"enabled": True, "framings": ["wide"]},
                   "style_defaults": {"default_frame_source": "qwen_refs"}})
    sid = store.create_series("Identity film")["id"]
    eid = store.create_episode(sid, "Pilot")["id"]
    roles = []
    for name, position in (("Amin", "left"), ("Mona", "right"), ("Lee", "background")):
        ch = store.new_character(name)
        portrait = tmp_path / f"{ch['id']}.jpg"
        Image.new("RGB", (32, 48), "red").save(portrait)
        ch.update(refs=[store.rel(str(portrait))], appearance=f"{name}, wearing a blue coat")
        store.save_character(sid, ch)
        roles.append({"id": ch["id"], "position": position})
    plate = tmp_path / "room.png"
    Image.new("RGB", (64, 96), "green").save(plate)
    store.save_location(sid, store.new_location("Room", description="a warmly lit room",
                                                plates={"day": store.rel(str(plate))}))
    shot = planner.new_shot(id="S01_SH01", order=0, start_mode="new", framing="wide",
                            location_id="room", time_of_day="day",
                            characters=roles[:2], action="Amin looks right at Mona in warm light")
    store.update_episode(sid, eid, lambda e: e["shots"].append(shot))
    comfy = FrameComfy(tmp_path)
    monkeypatch.setattr(jobs, "_ctx", lambda: (settings.load(), comfy))
    monkeypatch.setattr(jobs, "_gpu_prepare", lambda *args: None)
    return sid, eid, comfy, roles


@pytest.mark.parametrize("source", ["qwen_refs", "auto", "override"])
def test_reference_frames_compose_then_apply_identity(frame_project, source):
    sid, eid, comfy, _ = frame_project
    def configure(ep):
        ep["shots"][0]["frame_source"] = "zimage" if source == "override" else source
    store.update_episode(sid, eid, configure)
    job = jobs.Job("frames", "test", None, {})
    jobs.job_frames(job, sid, eid, source_override="qwen_refs" if source == "override" else "")
    assert comfy.events == ["composition", "free", "identity", "identity", "free"]
    composition, *edits = comfy.graphs
    assert not any(n["class_type"] == "LoadImage" for n in composition.values())
    assert composition["7"]["inputs"]["batch_size"] == 2
    assert "Amin looks right at Mona in warm light" in composition["4"]["inputs"]["text"]
    candidates = store.get_episode(sid, eid)["shots"][0]["frame"]["candidates"]
    assert len(candidates) == 2
    for index, (edit, candidate) in enumerate(zip(edits, candidates)):
        assert candidate["source"] == "qwen_refs"
        assert candidate["plate"] is None
        assert len(candidate["refs"]) == 2
        assert candidate["file"] != candidate["composition"]
        assert os.path.isfile(store.abs_media(candidate["file"]))
        assert comfy.uploads[edit["50"]["inputs"]["image"]] == store.abs_media(candidate["composition"])
        assert edit["8"]["inputs"]["seed"] == candidate["seed"] + index
        prompt = edit["4"]["inputs"]["prompt"]
        assert "Give Amin, left, in image 1" in prompt
        assert "Give Mona, right, in image 1" in prompt
        assert "gaze direction" in prompt and "Do not copy the portraits' pose" in prompt
        assert "Amin looks right at Mona in warm light" not in prompt
        assert candidate["identity_prompts"] == [prompt]
    assert job.progress == [1, 1]


def test_three_characters_keep_frame_input_and_use_additional_pass(frame_project):
    sid, eid, comfy, roles = frame_project
    store.update_episode(sid, eid, lambda e: e["shots"][0].update(characters=roles))
    jobs.job_frames(jobs.Job("frames", "test", None, {}), sid, eid)
    assert len(comfy.graphs) == 5  # Two identity passes for each of two compositions.
    for first, second in (comfy.graphs[1:3], comfy.graphs[3:5]):
        assert len([n for n in first.values() if n["class_type"] == "LoadImage"]) == 3
        assert len([n for n in second.values() if n["class_type"] == "LoadImage"]) == 2
        assert "Give Lee, background, in image 1" in second["4"]["inputs"]["prompt"]
        source = comfy.uploads[second["50"]["inputs"]["image"]]
        assert os.path.basename(source).startswith("identity_")
    candidates = store.get_episode(sid, eid)["shots"][0]["frame"]["candidates"]
    assert all(len(c["refs"]) == 3 and len(c["identity_prompts"]) == 2 for c in candidates)


@pytest.mark.parametrize("cancel", [False, True])
def test_failed_or_cancelled_identity_does_not_publish_compositions(frame_project, cancel):
    sid, eid, comfy, _ = frame_project
    original = {"file": "existing.png", "source": "upload"}
    def prepare(ep):
        ep["shots"][0]["frame"].update(candidates=[original], selected=0)
    store.update_episode(sid, eid, prepare)
    job = jobs.Job("frames", "test", None, {})
    if cancel:
        comfy.on_composition = lambda: setattr(job, "cancel_flag", True)
    else:
        comfy.fail_identity = True
    with pytest.raises(jobs.Cancelled if cancel else RuntimeError):
        jobs.job_frames(job, sid, eid, force=True)
    frame = store.get_episode(sid, eid)["shots"][0]["frame"]
    assert frame["candidates"] == [original] and frame["selected"] == 0
    assert list(Path(store.ep_dir(sid, eid)).glob("frames/*/layout_*.png"))


def test_reference_source_checks_both_model_families(frame_project):
    s = settings.load()
    required = _required_model_options(s)
    assert {s["zimage"]["unet"], s["qwen_edit"]["unet"]} <= required[("UNETLoader", "unet_name")]
    assert {s["zimage"]["clip"], s["qwen_edit"]["clip"]} <= required[("CLIPLoader", "clip_name")]


def test_missing_portraits_skip_both_stages(frame_project):
    sid, eid, comfy, _ = frame_project
    for character in store.list_characters(sid):
        character["refs"] = []
        store.save_character(sid, character)
    job = jobs.Job("frames", "test", None, {})
    jobs.job_frames(job, sid, eid)
    assert not comfy.graphs
    assert store.get_episode(sid, eid)["shots"][0]["frame"]["candidates"] == []
    assert any("no reference photo" in line for line in job.log)


def explicit_frame(roles):
    return {"camera": "eye level from outside", "layout": "Two people separated by a closed window",
            "moment": "The man watches the woman before he turns", "lighting": "night, warm window",
            "subjects": [{"id": role["id"], "position": role["position"], "depth": depth,
                          "space": "outside" if zone == "outside" else "inside",
                          "zone": zone, "pose": "standing still", "facing": facing,
                          "gaze_target": other, "gaze_direction": "through the glass, never at camera",
                          "expression": "guarded", "visibility": "upper_body", "wardrobe_state": "sleeves down"}
                         for role, depth, zone, facing, other in [
                             (roles[0], "midground", "outside", "toward window", roles[1]["id"]),
                             (roles[1], "background", "inside behind glass", "outward", roles[0]["id"])]],
            "props": [], "constraints": ["Exactly one woman, inside only"]}


@pytest.mark.parametrize("all_rejected", [False, True])
def test_identity_only_runs_on_compositions_that_pass_blocking(frame_project, monkeypatch, all_rejected):
    sid, eid, comfy, roles = frame_project
    plan = explicit_frame(roles)
    store.update_episode(sid, eid, lambda e: e["shots"][0].update(first_frame=plan))
    checks = [{"matches": False, "score": 4, "looks_at_camera": False, "issues": ["woman outside"]},
              {"matches": not all_rejected, "score": 9, "looks_at_camera": False, "issues": []},
              {"matches": True, "score": 10, "looks_at_camera": False, "issues": [], "final": True}]
    seen_plans = []
    monkeypatch.setattr(jobs, "_vision_check", lambda s, path, shot, b: seen_plans.append(shot) or checks.pop(0))
    monkeypatch.setattr(jobs.llm, "unload", lambda *args: None)
    job = jobs.Job("frames", "test", None, {})
    if all_rejected:
        checks[2:] = [dict(checks[0]), dict(checks[0])]           # the retry batch is rejected too
        with pytest.raises(RuntimeError, match="No composition passed for 1 shot"):
            jobs.job_frames(job, sid, eid)
        assert len(comfy.graphs) == 2                               # one composition batch plus one retry
        assert any("retry 2/2" in line for line in job.log) and any("Continuing" in line for line in job.log)
        assert not store.get_episode(sid, eid)["shots"][0]["frame"]["candidates"]
    else:
        jobs.job_frames(job, sid, eid)
        # The woman is inside behind glass, the man outside: compose without her, identity on him, then add her.
        assert len(comfy.graphs) == 4                     # identity, insert, then a text-only performance pass
        candidate, = store.get_episode(sid, eid)["shots"][0]["frame"]["candidates"]
        assert candidate["batch_index"] == 1
        assert candidate["composition_check"]["matches"] is True
        assert candidate["first_frame"] == plan
        assert comfy.graphs[1]["8"]["inputs"]["seed"] == candidate["seed"] + 1
        performance = comfy.graphs[-1]["4"]["inputs"]["prompt"]
        assert "Change ONLY" in performance and "no smile" in performance
        comfy.graphs.pop()
        assert "inside behind glass" in comfy.graphs[-1]["4"]["inputs"]["prompt"]
        assert "Add exactly one person" in comfy.graphs[-1]["4"]["inputs"]["prompt"]
        assert any("composing without them" in line for line in job.log)
        assert "background, inside behind glass" in comfy.graphs[-1]["4"]["inputs"]["prompt"]
        insert = comfy.graphs[-1]["4"]["inputs"]["prompt"]
        assert "not smiling" in insert and "NOT look at the viewer" in insert
        assert f"at {roles[0]['position']} (outside)" in insert                      # gaze target by place, not name
        assert candidate["check"]["final"] is True                                     # finished frame checked...
        assert seen_plans[-1]["first_frame"]["subjects"][1]["visibility"] == "upper_body"  # ...against the full plan
        assert store.get_episode(sid, eid)["shots"][0]["frame"].get("selected") is not None


def test_without_the_identity_pass_a_passing_composition_is_the_candidate(frame_project, monkeypatch):
    sid, eid, comfy, roles = frame_project
    settings.save({**settings.load(), "identity_pass": False})
    plan = explicit_frame(roles)
    for subject in plan["subjects"]:
        subject.update(space="inside", zone="inside the bakery", depth="midground")   # one space: nothing layered
    store.update_episode(sid, eid, lambda e: e["shots"][0].update(first_frame=plan))
    monkeypatch.setattr(jobs, "_vision_check", lambda *args: {"matches": True, "score": 9, "looks_at_camera": False,
                                                               "issues": [], "resemblance": 0.5})
    monkeypatch.setattr(jobs.llm, "unload", lambda *args: None)
    jobs.job_frames(jobs.Job("frames", "test", None, {}), sid, eid)
    assert len(comfy.graphs) == 1                                   # the composition batch only: no Qwen edits
    candidates = store.get_episode(sid, eid)["shots"][0]["frame"]["candidates"]
    assert len(candidates) == 2 and all(c["identity_prompts"] == [] for c in candidates)
    assert all(os.path.basename(c["file"]).startswith("c") for c in candidates)
