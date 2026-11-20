"""Keep the reference-conditioned Playground output instead of rendering it again."""
from types import SimpleNamespace
from studio import jobs


def test_qwen_playground_keeps_reference_render(monkeypatch):
    ch = {"id": "maryam", "name": "Maryam", "refs": ["maryam.png"]}
    bible = {"characters": {"maryam": ch}, "locations": {"room": {"name": "Room", "plates": {}}}}
    outputs, prompts, saved = [], [], []
    comfy = SimpleNamespace(upload=lambda path, name: name,
                            run=lambda graph, cancel: outputs.append(graph) or {"images": ["conditioned.png"]})
    monkeypatch.setattr(jobs, "_ctx", lambda: ({"qwen_2511": {"max_refs": 3}, "image": {"width": 64, "height": 64}}, comfy))
    monkeypatch.setattr(jobs, "_gpu_prepare", lambda *a: None)
    monkeypatch.setattr(jobs.store, "bible", lambda sid: bible)
    monkeypatch.setattr(jobs.store, "abs_media", lambda rel: rel)
    monkeypatch.setattr(jobs.os.path, "isfile", lambda path: True)
    monkeypatch.setattr(jobs.prompts, "image_prompt", lambda sh, b: "Maryam in a room.")
    monkeypatch.setattr(jobs.prompts, "characters_in", lambda sh, b: [(b['characters'][c['id']], c) for c in sh['characters']])
    monkeypatch.setattr(jobs, "_qwen2511_refs", lambda sh, b, n: [(ch, "maryam.png")])
    monkeypatch.setattr(jobs.workflows, "qwen2511_image", lambda s, prompt, *args: prompts.append(prompt) or {"reference_render": True})
    def forbid_fallback(*args, **kwargs):
        raise AssertionError("Qwen output must not be overwritten by a generic render")
    monkeypatch.setattr(jobs, "_render_images", forbid_fallback)
    monkeypatch.setattr(jobs, "_save_outputs", lambda c, items, *a: saved.extend(items) or ["result.png"])
    monkeypatch.setattr(jobs.store, "playground_dir", lambda sid: "/tmp")
    monkeypatch.setattr(jobs.store, "rel", lambda p: p)
    monkeypatch.setattr(jobs.store, "add_playground_render", lambda sid, row: None)
    jobs.job_playground(SimpleNamespace(say=lambda msg: None, cancel_flag=False), "test", {
        "source": "qwen_2511", "location_id": "room", "character_ids": ["maryam", "maryam"],
        "camera": "220° orbit", "framing": "wide"})
    assert len(outputs) == 1
    assert saved == ["conditioned.png"]
    assert "exactly 1 person (Maryam)" in prompts[0]
    assert "Image 2" not in prompts[0]
    assert "orbit" not in prompts[0]
    assert "fixed viewpoint" in prompts[0]
