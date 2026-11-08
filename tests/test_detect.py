"""Detector findings against the first-frame plan (pure logic; the OpenCV models are not loaded here)."""
from studio import detect, jobs


def shot(*subjects):
    return {"first_frame": {"subjects": [{"id": cid, "visibility": vis} for cid, vis in subjects], "props": []}}


NAMES = {"hakon": "Hakon", "maryam": "Maryam", "erik": "Erik"}


def face(**sims):
    return {"box": [0, 0, 50, 50], "in_body": True, "sims": sims}


def test_missing_person_is_critical_but_partial_and_offscreen_people_are_not_required():
    plan = shot(("hakon", "back_of_head"), ("erik", "upper_body"), ("maryam", "upper_body"), ("nina", "offscreen"))
    critical, _ = detect.compare(plan, NAMES, {"people": 2, "faces": []})
    assert critical and "needs at least 3" in critical[0]
    plan = shot(("hakon", "upper_body"), ("erik", "partial"))
    assert detect.compare(plan, NAMES, {"people": 1, "faces": [face(hakon=0.5)]}) == ([], [])


def test_pasted_portrait_and_duplicates_are_critical_and_weak_resemblance_is_soft():
    plan = shot(("hakon", "upper_body"), ("maryam", "upper_body"))
    critical, soft = detect.compare(plan, NAMES, {"people": 2, "faces": [face(hakon=0.8, maryam=0.1),
                                                                         face(hakon=0.1, maryam=0.12)]})
    assert any("pasted" in c for c in critical) and soft == ["detector: no face resembles Maryam (best 0.12)"]
    critical, _ = detect.compare(plan, NAMES, {"people": 2, "faces": [face(maryam=0.45), face(maryam=0.42)]})
    assert critical == ["detector: Maryam appears twice"]


def test_a_detector_failure_never_blocks_the_check(monkeypatch):
    monkeypatch.setattr(detect, "ensure_models", lambda *a, **k: (_ for _ in ()).throw(OSError("offline")))
    result = jobs._with_detector({"matches": True, "score": 10, "issues": []}, "x.png", shot(), {"characters": {}})
    assert result["matches"] is True and "detector unavailable: offline" in result["issues"][0]
