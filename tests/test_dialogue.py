"""Dialogue voices via VoiceStudio: references, acted lines with word checks, caching and timeline placement.
VoiceStudio itself is mocked (no GPU or network); ffmpeg is real."""
import subprocess

import pytest

from studio import dialogue, jobs, settings, store, timeline, voices
from test_lipsync import film  # noqa: F401  (two 1 s shots, ffmpeg check)


def tone(seconds=0.6, hz=440):
    return subprocess.check_output(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', f'sine=frequency={hz}:duration={seconds}',
                                    '-ar', '24000', '-ac', '1', '-f', 'wav', '-'])


def test_word_match_ignores_punctuation_case_and_word_breaks():
    assert voices.match("Anytime!", "Any time?") > 0.95
    assert voices.match("Sam Spider is always ready!", "sam spider is always ready.") == 1.0
    assert voices.match("Got it!", "Hot pot") < 0.75


@pytest.fixture
def cast(film, monkeypatch):
    client, sid, eid, shots, music = film
    for name, desc in (("Hakon", "a cheerful four-year-old boy"), ("Maryam", "")):
        c = store.new_character(name); c["id"] = name.lower()
        c["voice"]["description"] = desc
        store.save_character(sid, c, bump=False)
    def lines(e):
        e["shots"][0]["dialogue"] = [{"speaker": "hakon", "line": "Spider swing!", "emotion": "thrilled"},
                                     {"speaker": "hakon", "line": "Got it!"}]
        e["shots"][1]["dialogue"] = [{"speaker": "Hakon", "line": "Anytime!", "emotion": "shy"},
                                     {"speaker": "narrator", "line": "Not in the bible"}]
    store.update_episode(sid, eid, lines)
    calls = {"speak": [], "design": [], "unload": 0}
    heard = {}
    monkeypatch.setattr(voices, "health", lambda s: {"status": "ok"})
    monkeypatch.setattr(voices, "unload", lambda s: calls.__setitem__("unload", calls["unload"] + 1))
    monkeypatch.setattr(dialogue, "_free_gpu", lambda job, s: None)
    monkeypatch.setattr(voices, "design_reference", lambda s, d, language=None: (calls["design"].append(d), (tone(2.0, 300), "ref text"))[1])
    def speak(s, text, ref, ref_text, emotion="", seed=7, language=None):
        calls["speak"].append((text, emotion, seed)); heard["last"] = text
        return tone(0.4 if text != "Anytime!" else 0.3)
    monkeypatch.setattr(voices, "speak", speak)
    monkeypatch.setattr(voices, "transcribe", lambda s, path, language=None: heard.get("override", {}).pop(heard["last"], heard["last"]))
    return client, sid, eid, calls, heard


def run(sid, eid):
    job = jobs.Job("dialogue", "t", None, {})
    dialogue.job_dialogue(job, sid, eid)
    return job


def test_dialogue_is_voiced_checked_and_placed_inside_each_shot(cast):
    client, sid, eid, calls, heard = cast
    job = run(sid, eid)
    # a character without a voice got one designed from its description; acting directions were passed on
    assert calls["design"] == ["a cheerful four-year-old boy"]
    assert ("Spider swing!", "thrilled", 7) in calls["speak"] and ("Anytime!", "shy", 7) in calls["speak"]
    assert any("narrator" in m and "skipped" in m for m in job.log)
    e = store.get_episode(sid, eid); edit = e["timeline"]; fps = edit["fps"]
    clips = sorted(edit["audio"], key=lambda t: t["start_frame"])
    assert [c["id"].startswith("dlg_") for c in clips] == [True] * 3
    assert all(c["guide"] and not c["muted"] for c in clips) and {c["lane"] for c in clips} == {0}
    # shot 1 = frames 0-29, shot 2 = frames 30-59: lines start after the lead-in, the second line follows the first
    assert clips[0]["start_frame"] == round(dialogue.LEAD_IN * fps) and clips[2]["start_frame"] >= 30
    assert clips[1]["start_frame"] >= clips[0]["start_frame"] + clips[0]["frames"]
    assert all(c["file"] in e["audio_assets"] for c in clips)
    c = store.get_character(sid, "hakon")
    assert c["voice"]["designed"] and c["voice"]["ref_text"] == "ref text"


def test_unchanged_lines_are_reused_and_other_lanes_kept(cast):
    client, sid, eid, calls, heard = cast
    run(sid, eid)
    n = len(calls["speak"])
    # the user adds a music/effects clip on lane 0 of their own
    def add_fx(ep):
        ep["timeline"]["audio"].append({**ep["timeline"]["audio"][0], "id": "fx1", "guide": False, "lane": 0})
        ep["timeline"]["audio"] = [t for t in ep["timeline"]["audio"] if t["id"] == "fx1"] + \
                                  [t for t in ep["timeline"]["audio"] if t["id"] != "fx1"]
    store.update_episode(sid, eid, add_fx)
    job = run(sid, eid)
    assert len(calls["speak"]) == n, "unchanged lines must not be voiced again"
    assert any("unchanged" in m for m in job.log)
    audio = store.get_episode(sid, eid)["timeline"]["audio"]
    assert [t["id"] for t in audio if not t["id"].startswith("dlg_")] == ["fx1"]
    assert {t["lane"] for t in audio if t["id"].startswith("dlg_")} == {1}, "dialogue moves below the kept lane"


def test_misheard_line_is_retried_and_the_best_take_kept(cast):
    client, sid, eid, calls, heard = cast
    heard["override"] = {"Got it!": "Hot pot"}           # first take of 'Got it!' is misheard once
    job = run(sid, eid)
    seeds = [seed for text, _, seed in calls["speak"] if text == "Got it!"]
    assert seeds == [7, 11] and any("trying again" in m for m in job.log)
    take = next(v for v in store.get_episode(sid, eid)["dialogue_takes"].values() if v["text"] == "Got it!")
    assert take["seed"] == 11 and take["match"] == 1.0


def test_endpoint_refuses_when_voicestudio_is_down(film, monkeypatch):
    client, sid, eid, shots, music = film
    monkeypatch.setattr(voices, "health", lambda s: None)
    r = client.post(f"/api/s/{sid}/e/{eid}/dialogue", json={})
    assert r.status_code == 400 and "reelwright-voice" in r.text


def test_comfy_jobs_free_voicestudio_gpu_memory(monkeypatch):
    freed = []
    monkeypatch.setattr(voices, "unload", lambda s: freed.append(True))
    monkeypatch.setattr(jobs.llm, "unload", lambda *a: None)

    class Alive:
        def alive(self): return True
    jobs._gpu_prepare(jobs.Job("x", "t", None, {}), settings.load(), Alive())
    assert freed == [True]


def test_voicestudio_profile_is_cast_onto_a_character(cast, monkeypatch):
    client, sid, eid, calls, heard = cast
    lib = [{"id": "me01", "name": "Hakon (my voice)", "kind": "clone", "language": "English",
            "ref_text": "This is my real voice.", "own_voice": True, "demo": False}]
    monkeypatch.setattr(voices, "profiles", lambda s: lib)
    monkeypatch.setattr(voices, "profile_audio", lambda s, pid: tone(3.0, 220))
    assert client.get("/api/voices/profiles").json()[0]["id"] == "me01"
    c = client.post(f"/api/s/{sid}/characters/hakon/voice/profile", json={"profile_id": "me01"}).json()
    assert c["voice"]["profile"] == {"id": "me01", "name": "Hakon (my voice)"}
    assert c["voice"]["ref_text"] == "This is my real voice." and c["voice"]["designed"] is False
    assert c["voice"]["description"] == "a cheerful four-year-old boy"      # kept for the video prompt
    assert client.post(f"/api/s/{sid}/characters/hakon/voice/profile", json={"profile_id": "nope"}).status_code == 404
    run(sid, eid)
    assert calls["design"] == []            # the cast voice is used, nothing designed for Hakon
