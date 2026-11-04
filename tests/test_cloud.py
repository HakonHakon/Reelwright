"""ChatGPT (OpenAI) and Gemini connectors: request shapes, fallbacks, key handling and the frame job.
All HTTP is mocked; no key or network is needed."""
import base64
import io
import json
import os
import stat

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from studio import cloud, jobs, llm, planner, settings, store
from studio.app import app

KEY = "sk-test-SECRET1234567890"


def png(w=40, h=30, color=(200, 30, 30)):
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, "PNG")
    return buf.getvalue()


class Resp:
    def __init__(self, data, status=200):
        self._data, self.status_code, self.ok = data, status, status < 400
        self.text = json.dumps(data)

    def json(self):
        return self._data


@pytest.fixture
def s(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    cfg = settings._deep_merge(settings.DEFAULTS, {"openai": {"api_key": KEY}, "gemini": {"api_key": "AIzaTESTKEY123456789"}})
    return cfg


@pytest.fixture
def calls(monkeypatch):
    log, replies = [], []

    def post(url, headers=None, timeout=None, **kw):
        log.append({"url": url, "headers": headers, **json.loads(json.dumps({k: v for k, v in kw.items() if k == "json"})),
                    **{k: v for k, v in kw.items() if k != "json"}})
        return replies.pop(0)
    monkeypatch.setattr(cloud.requests, "post", post)
    return log, replies


def test_openai_chat_uses_json_schema_and_retries_without_temperature(s, calls):
    log, replies = calls
    replies += [Resp({"error": {"message": "Unsupported value: 'temperature' does not support 0.5"}}, 400),
                Resp({"choices": [{"message": {"content": '{"shots": []}'}}]})]
    out = cloud.chat_json(s, "openai", "sys", "user", {"type": "object"}, temperature=0.5)
    assert out == {"shots": []}
    assert log[0]["url"].endswith("/chat/completions") and log[0]["json"]["temperature"] == 0.5
    assert log[0]["json"]["response_format"]["type"] == "json_schema"
    assert "temperature" not in log[1]["json"]
    assert log[0]["headers"]["Authorization"] == f"Bearer {KEY}"


def test_gemini_chat_falls_back_to_schema_in_prompt(s, calls):
    log, replies = calls
    replies += [Resp({"error": {"message": "Invalid JSON payload: Unknown name \"responseJsonSchema\""}}, 400),
                Resp({"candidates": [{"content": {"parts": [{"text": "```json\n{\"ok\": true}\n```"}]}}]})]
    out = cloud.chat_json(s, "gemini", "sys", "write", {"type": "object", "properties": {"ok": {"type": "boolean"}}})
    assert out == {"ok": True}
    assert ":generateContent" in log[0]["url"] and log[0]["json"]["generationConfig"]["responseJsonSchema"]
    assert "responseJsonSchema" not in log[1]["json"]["generationConfig"]
    assert "JSON Schema" in log[1]["json"]["contents"][0]["parts"][0]["text"]
    assert log[0]["headers"]["x-goog-api-key"].startswith("AIza")


def test_openai_image_edit_sends_references_and_fits_project_size(s, calls, tmp_path):
    log, replies = calls
    ref = tmp_path / "sam.png"
    ref.write_bytes(png())
    replies.append(Resp({"data": [{"b64_json": base64.b64encode(png(1536, 1024)).decode()}] * 2}))
    out = cloud.generate_images(s, "img:openai", "Sam on a rooftop", 2, 1344, 768, [str(ref)])
    assert len(out) == 2 and Image.open(io.BytesIO(out[0])).size == (1344, 768)
    call = log[0]
    assert call["url"].endswith("/images/edits")
    assert [f[0] for f in call["files"]] == ["image[]"]
    assert call["data"]["size"] == "1536x1024" and call["data"]["model"] == "gpt-image-2"


def test_openai_image_without_references_uses_generations(s, calls):
    log, replies = calls
    replies.append(Resp({"data": [{"b64_json": base64.b64encode(png(1024, 1536)).decode()}]}))
    out = cloud.generate_images(s, "img:openai", "a street", 1, 768, 1344)
    assert log[0]["url"].endswith("/images/generations") and log[0]["json"]["size"] == "1024x1536"
    assert Image.open(io.BytesIO(out[0])).size == (768, 1344)


def test_gemini_image_reads_inline_data_and_sets_aspect_ratio(s, calls, tmp_path):
    log, replies = calls
    ref = tmp_path / "sam.jpg"
    Image.new("RGB", (50, 50)).save(ref)
    img = base64.b64encode(png(1024, 576)).decode()
    replies += [Resp({"candidates": [{"content": {"parts": [{"text": "here"}, {"inlineData": {"mimeType": "image/png", "data": img}}]}}]})] * 2
    out = cloud.generate_images(s, "gemini_image", "Sam", 2, 1344, 768, [str(ref)])
    assert len(out) == 2 and len(log) == 2
    body = log[0]["json"]
    assert body["generationConfig"]["imageConfig"]["aspectRatio"] == "16:9"
    assert "IMAGE" in body["generationConfig"]["responseModalities"]
    assert "inline_data" in body["contents"][0]["parts"][0] and body["contents"][0]["parts"][-1] == {"text": "Sam"}


def test_gemini_image_reports_blocked_reply(s, calls):
    _, replies = calls
    replies.append(Resp({"candidates": [{"finishReason": "IMAGE_SAFETY", "content": {"parts": []}}]}))
    with pytest.raises(cloud.CloudError, match="IMAGE_SAFETY"):
        cloud.generate_images(s, "img:gemini:gemini-3-pro-image", "x", 1, 64, 64)


def test_errors_never_contain_the_key(s, calls):
    _, replies = calls
    replies.append(Resp({"error": {"message": f"Incorrect API key provided: {KEY}"}}, 401))
    with pytest.raises(cloud.CloudError) as err:
        cloud.chat_json(s, "openai", "a", "b", {})
    assert KEY not in str(err.value) and "401" in str(err.value)


def test_errors_never_contain_a_google_key(s, calls):
    _, replies = calls
    replies.append(Resp({"error": {"message": "API key not valid: AQ.Ab8RN6Jabcdefghijklmnop"}}, 400))
    with pytest.raises(cloud.CloudError) as err:
        cloud.chat_json(s, "gemini", "a", "b", {})
    assert "AQ.Ab8" not in str(err.value) and "[key]" in str(err.value)


def test_missing_key_is_a_clear_error(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(cloud.CloudError, match="GEMINI_API_KEY"):
        cloud.chat_json(settings._deep_merge(settings.DEFAULTS, {}), "gemini", "a", "b", {})


def test_environment_key_wins(s, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-from-env")
    assert cloud.api_key(s, "openai") == "sk-from-env" and cloud.key_source(s, "openai") == "env"


def test_llm_routes_planner_to_the_chosen_provider(s, monkeypatch):
    seen = {}
    monkeypatch.setattr(cloud, "chat_json", lambda s_, p, *a, **k: seen.update(p=p) or {"x": 1})
    monkeypatch.setattr(llm, "chat_json", lambda *a, **k: pytest.fail("Ollama must not be called"))
    assert llm.chat({**s, "planner_provider": "gemini"}, "planner", "sys", "u", {}) == {"x": 1}
    assert seen["p"] == "gemini"


# ── API and jobs ────────────────────────────────────────────────────────

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DATA_ROOT", str(tmp_path))
    monkeypatch.setattr(settings, "SETTINGS_PATH", str(tmp_path / "settings.json"))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    return TestClient(app)


def test_settings_api_masks_keys_and_keeps_them_on_save(client):
    r = client.put("/api/settings", json={"openai": {"api_key": KEY}, "planner_provider": "openai"})
    assert r.status_code == 200 and KEY not in r.text
    got = client.get("/api/settings").json()
    assert KEY not in json.dumps(got)
    assert got["user"]["openai"]["api_key"] == cloud.MASK
    # saving the masked settings back (as the Settings page does) keeps the real key
    user = got["user"]
    user["openai"]["text_model"] = "my-model"
    assert client.put("/api/settings", json=user).status_code == 200
    raw = settings.raw_user()
    assert raw["openai"]["api_key"] == KEY and raw["openai"]["text_model"] == "my-model"
    assert stat.S_IMODE(os.stat(settings.SETTINGS_PATH).st_mode) == 0o600
    status = client.get("/api/status").json()
    assert status["planner_provider"] == "openai" and "openai:api_key" not in status["missing"]


def test_cloud_frames_render_without_comfyui_and_send_character_refs(client, monkeypatch):
    sid = client.post("/api/series", json={"name": "Film"}).json()["id"]
    eid = client.post(f"/api/s/{sid}/episodes", json={"title": "Pilot"}).json()["id"]
    sam = client.post(f"/api/s/{sid}/characters", json={"name": "Sam"}).json()
    ref = os.path.join(store.char_dir(sid, sam["id"]), "refs", "sam.png")
    os.makedirs(os.path.dirname(ref))
    with open(ref, "wb") as f:
        f.write(png())
    store.save_character(sid, {**store.get_character(sid, sam["id"]), "refs": [store.rel(ref)]}, bump=False)
    sh = planner.new_shot(id="S01_SH01", order=0, characters=[{"id": "sam", "position": "center"}],
                          action="Sam waves", frame_source="img:gemini")
    store.update_episode(sid, eid, lambda e: e["shots"].append(sh))
    client.put("/api/settings", json={"gemini": {"api_key": "AIzaTESTKEY123456789"}, "vision_check": False,
                                      "candidates_per_shot": 2})

    class DeadComfy:
        def __init__(self, *a): pass
        def alive(self): return False
        def free(self): pass
        def upload(self, *a): pytest.fail("cloud frames must not upload to ComfyUI")
    monkeypatch.setattr(jobs.comfy_mod, "Comfy", DeadComfy)
    seen = {}

    def fake(s, source, prompt, n, w, h, refs=None, log=None):
        seen.update(provider=source, prompt=prompt, refs=refs, n=n)
        return [png(w, h)] * n
    monkeypatch.setattr(cloud, "generate_images", fake)
    job = jobs.Job("frames", "t", None, {})
    jobs.job_frames(job, sid, eid)
    shot = store.get_episode(sid, eid)["shots"][0]
    assert [c["source"] for c in shot["frame"]["candidates"]] == ["img:gemini"] * 2
    assert all(os.path.exists(store.abs_media(c["file"])) for c in shot["frame"]["candidates"])
    assert seen["provider"] == "img:gemini" and seen["refs"] == [ref] and "Image 1 is Sam" in seen["prompt"]



# ── AI models page ──────────────────────────────────────────────────────

def test_ai_page_adds_a_connection_and_stores_its_key_privately(client):
    view = client.get("/api/ai").json()
    assert [c["id"] for c in view["connections"]] == ["openai", "gemini"] and view["presets"]
    conns = view["connections"] + [{"id": "openrouter", "name": "OpenRouter", "type": "openai_compatible",
                                    "base_url": "https://openrouter.ai/api/v1", "text_model": "some/model",
                                    "extra_models": "other/model, some/image-model"}]
    r = client.put("/api/ai", json={"connections": conns, "defaults": {"planner": "openrouter:some/model"}})
    assert r.status_code == 200, r.text
    r = client.put("/api/ai/openrouter/key", json={"key": "sk-or-SECRETKEY123"})
    assert r.status_code == 200 and "SECRETKEY" not in r.text
    view = client.get("/api/ai").json()
    assert "SECRETKEY" not in json.dumps(view)
    conn = next(c for c in view["connections"] if c["id"] == "openrouter")
    assert conn["key"] == "saved" and view["defaults"]["planner"] == "openrouter:some/model"
    refs = [c["ref"] for c in view["choices"]["text"]]
    assert "openrouter:some/model" in refs and "openrouter:other/model" in refs
    assert "img:openrouter:some/image-model" in [c["source"] for c in view["choices"]["image"]]
    path = os.path.join(settings.DATA_ROOT, "secrets.json")
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600 and "SECRETKEY" not in open(settings.SETTINGS_PATH).read()
    assert cloud.api_key(settings.load(), "openrouter") == "sk-or-SECRETKEY123"
    # removing the connection removes its key
    client.put("/api/ai", json={"connections": [c for c in conns if c["id"] != "openrouter"],
                                "defaults": {"planner": "gemini"}})
    assert "openrouter" not in json.load(open(path))


@pytest.mark.parametrize("bad, msg", [
    ({"id": "x", "type": "openai_compatible", "base_url": "http://example.com/v1"}, "https"),
    ({"id": "Bad Id", "type": "openai", "base_url": "https://api.openai.com/v1"}, "lowercase"),
    ({"id": "ollama", "type": "openai", "base_url": "https://api.openai.com/v1"}, "ollama"),
    ({"id": "x", "type": "nope", "base_url": "https://a.b"}, "type"),
])
def test_ai_page_rejects_bad_connections(client, bad, msg):
    r = client.put("/api/ai", json={"connections": [bad]})
    assert r.status_code == 400 and msg in r.text


def test_local_openai_compatible_server_needs_no_key_and_falls_back_to_json_mode(calls, monkeypatch):
    log, replies = calls
    s = settings._deep_merge(settings.DEFAULTS, {"ai_connections": cloud.validate_connections([
        {"id": "lmstudio", "name": "LM Studio", "type": "openai_compatible", "base_url": "http://localhost:1234/v1",
         "text_model": "qwen"}])})
    replies += [Resp({"error": {"message": "response_format json_schema is not supported"}}, 400),
                Resp({"choices": [{"message": {"content": '{"a": 1}'}}]})]
    assert cloud.chat_json(s, "lmstudio", "sys", "u", {"type": "object"}) == {"a": 1}
    assert "Authorization" not in log[0]["headers"]
    assert log[1]["json"]["response_format"] == {"type": "json_object"}
    assert "JSON Schema" in log[1]["json"]["messages"][0]["content"]


def test_job_choice_overrides_the_default(s, monkeypatch):
    seen = {}
    monkeypatch.setattr(cloud, "chat_json", lambda s_, conn, *a, model="", **k: seen.update(conn=conn, model=model) or {})
    llm.chat(llm.with_choice({**s, "planner": "openai"}, "planner", "gemini:gemini-3.8-flash"), "planner", "a", "b", {})
    assert seen == {"conn": "gemini", "model": "gemini-3.8-flash"}
    llm.chat({**s, "planner": "openai"}, "planner", "a", "b", {})
    assert seen == {"conn": "openai", "model": "gpt-6.1-terra"}


def test_generate_endpoints_reject_unknown_models(client):
    sid = client.post("/api/series", json={"name": "Film"}).json()["id"]
    eid = client.post(f"/api/s/{sid}/episodes", json={"title": "Pilot"}).json()["id"]
    r = client.post(f"/api/s/{sid}/e/{eid}/plan", json={"model": "nosuch:model"})
    assert r.status_code == 400 and "nosuch" in r.text
    r = client.post(f"/api/s/{sid}/e/{eid}/frames", json={"source": "img:nosuch"})
    assert r.status_code == 400
