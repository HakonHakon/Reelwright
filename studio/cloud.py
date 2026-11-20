"""AI connections: OpenAI (ChatGPT), Google Gemini and any OpenAI-compatible service
(OpenRouter, Groq, DeepSeek, Mistral, xAI, Together, LM Studio ...).

A connection can write the bible draft, scenes and shots, check frames, and render first frames,
character sheets and location plates from the characters' reference photos.

Model references used everywhere:
  text:  "<connection>" (its default text model) or "<connection>:<model>"; "ollama:<model>" is local
  image: "img:<connection>" or "img:<connection>:<model>"  (legacy: "openai_image", "gemini_image")

API keys: the connection's environment variable wins, else <data root>/secrets.json (owner-only).
Keys never appear in errors, logs or API responses.
"""
import base64
import copy
import io
import json
import mimetypes
import os
import re
import threading
from urllib.parse import urlparse

import requests
from PIL import Image, ImageOps

TYPES = {"openai": "OpenAI", "gemini": "Google Gemini", "anthropic": "Anthropic Claude", "codex": "ChatGPT app (Codex on this PC)",
         "claude_code": "Claude Code on this PC",
         "openai_compatible": "OpenAI-compatible"}
DEFAULT_CONNECTIONS = [
    {"id": "openai", "name": "ChatGPT (OpenAI)", "type": "openai", "base_url": "https://api.openai.com/v1",
     "api_key_env": "OPENAI_API_KEY", "text_model": "gpt-6.1-terra", "image_model": "gpt-image-2",
     "image_quality": "", "max_refs": 8, "extra_models": "", "enabled": True},
    {"id": "gemini", "name": "Gemini (Google)", "type": "gemini",
     "base_url": "https://generativelanguage.googleapis.com/v1beta", "api_key_env": "GEMINI_API_KEY",
     "text_model": "gemini-3.6-flash", "image_model": "gemini-3.1-flash-image", "max_refs": 6,
     "extra_models": "", "enabled": True},
    {"id": "claude", "name": "Claude (Anthropic)", "type": "anthropic", "base_url": "https://api.anthropic.com",
     "api_key_env": "ANTHROPIC_API_KEY", "text_model": "claude-opus-5-5", "image_model": "", "max_refs": 8,
     "extra_models": "", "enabled": True},
    # GPT through the ChatGPT desktop app's Codex CLI, on the user's ChatGPT subscription (no API key).
    {"id": "codex", "name": "ChatGPT (Codex on this PC)", "type": "codex", "base_url": "http://localhost/codex",
     "api_key_env": "", "text_model": "gpt-6.1-sol", "image_model": "", "max_refs": 0, "extra_models": "",
     "enabled": True},
    # Claude through the Claude Code CLI, on the user's Claude subscription (no API key).
    {"id": "claude_code", "name": "Claude (Claude Code on this PC)", "type": "claude_code",
     "base_url": "http://localhost/claude-code", "api_key_env": "", "text_model": "opus", "image_model": "",
     "max_refs": 0, "extra_models": "sonnet", "enabled": True},
    # Claude (and other models) billed to a fal.ai account, through fal's OpenRouter OpenAI-compatible endpoint.
    {"id": "fal", "name": "fal.ai (OpenRouter)", "type": "openai_compatible",
     "base_url": "https://fal.run/openrouter/router/openai/v1", "api_key_env": "FAL_KEY",
     "text_model": "anthropic/claude-opus-5.5", "image_model": "", "max_refs": 8,
     "extra_models": "anthropic/claude-sonnet-5", "enabled": True},
]
# Starting points for "Add connection" (all editable).
PRESETS = [
    {"label": "OpenRouter (many models, one key)", "type": "openai_compatible", "id": "openrouter", "name": "OpenRouter",
     "base_url": "https://openrouter.ai/api/v1", "api_key_env": "OPENROUTER_API_KEY"},
    {"label": "fal.ai (OpenRouter: Claude, GPT, Gemini... on your fal balance)", "type": "openai_compatible", "id": "fal",
     "name": "fal.ai (OpenRouter)", "base_url": "https://fal.run/openrouter/router/openai/v1", "api_key_env": "FAL_KEY"},
    {"label": "Groq", "type": "openai_compatible", "id": "groq", "name": "Groq",
     "base_url": "https://api.groq.com/openai/v1", "api_key_env": "GROQ_API_KEY"},
    {"label": "DeepSeek", "type": "openai_compatible", "id": "deepseek", "name": "DeepSeek",
     "base_url": "https://api.deepseek.com/v1", "api_key_env": "DEEPSEEK_API_KEY"},
    {"label": "Mistral", "type": "openai_compatible", "id": "mistral", "name": "Mistral",
     "base_url": "https://api.mistral.ai/v1", "api_key_env": "MISTRAL_API_KEY"},
    {"label": "xAI (Grok)", "type": "openai_compatible", "id": "xai", "name": "xAI",
     "base_url": "https://api.x.ai/v1", "api_key_env": "XAI_API_KEY"},
    {"label": "Together AI", "type": "openai_compatible", "id": "together", "name": "Together AI",
     "base_url": "https://api.together.xyz/v1", "api_key_env": "TOGETHER_API_KEY"},
    {"label": "LM Studio on this PC (no key)", "type": "openai_compatible", "id": "lmstudio", "name": "LM Studio",
     "base_url": "http://localhost:1234/v1", "api_key_env": ""},
    {"label": "Another OpenAI-compatible service", "type": "openai_compatible", "id": "custom", "name": "Custom",
     "base_url": "https://", "api_key_env": ""},
    {"label": "Another OpenAI account", "type": "openai", "id": "openai2", "name": "OpenAI (second key)",
     "base_url": "https://api.openai.com/v1", "api_key_env": ""},
    {"label": "Another Gemini key", "type": "gemini", "id": "gemini2", "name": "Gemini (second key)",
     "base_url": "https://generativelanguage.googleapis.com/v1beta", "api_key_env": ""},
]
LEGACY_IMAGE_SOURCES = {"openai_image": "openai", "gemini_image": "gemini"}
MASK = "********"
TIMEOUT = 300
TEXT_TIMEOUT = 900     # writing a whole script with reasoning can take several minutes
_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
_lock = threading.Lock()


class CloudError(RuntimeError):
    pass


# ── connections ─────────────────────────────────────────────────────────

def connections(s: dict) -> list[dict]:
    """The configured connections (defaults + user list), with legacy openai/gemini sections applied."""
    if s.get("ai_connections") is not None:
        return copy.deepcopy(s["ai_connections"])
    conns = copy.deepcopy(DEFAULT_CONNECTIONS)
    for c in conns:                        # settings saved before connections existed
        legacy = s.get(c["id"])
        if isinstance(legacy, dict):
            c.update({k: legacy[k] for k in ("text_model", "image_model", "image_quality", "base_url", "max_refs")
                      if legacy.get(k) not in (None, "")})
    return conns


def get_conn(s: dict, conn_id: str) -> dict:
    for c in connections(s):
        if c["id"] == conn_id:
            return c
    raise CloudError(f"There is no AI connection called '{conn_id}'. Add it on the AI models page.")


def name(s, conn_id):
    try:
        return get_conn(s, conn_id)["name"]
    except CloudError:
        return conn_id


def validate_connections(conns) -> list[dict]:
    """Clean a list sent by the AI models page; raises ValueError with a readable message."""
    if not isinstance(conns, list):
        raise ValueError("Connections must be a list")
    out, seen = [], set()
    for raw in conns:
        if not isinstance(raw, dict):
            raise ValueError("Each connection must be an object")
        c = {k: raw.get(k) for k in ("id", "name", "type", "base_url", "api_key_env", "text_model", "image_model",
                                     "image_quality", "max_refs", "extra_models", "enabled")}
        cid = str(c["id"] or "").strip().lower()
        if not _ID.match(cid) or cid == "ollama":
            raise ValueError(f"Connection id '{cid}': use 1-32 lowercase letters, digits, - or _ (not 'ollama')")
        if cid in seen:
            raise ValueError(f"Two connections are called '{cid}'")
        seen.add(cid)
        if c["type"] not in TYPES:
            raise ValueError(f"{cid}: unknown type '{c['type']}'")
        url = str(c["base_url"] or "").strip().rstrip("/")
        p = urlparse(url)
        local = p.hostname in ("localhost", "127.0.0.1", "::1") or (p.hostname or "").endswith(".local")
        if p.scheme != "https" and not (p.scheme == "http" and local):
            raise ValueError(f"{cid}: the address must start with https:// (http:// only for this PC)")
        env = str(c["api_key_env"] or "").strip()
        if env and not re.match(r"^[A-Z_][A-Z0-9_]{0,63}$", env):
            raise ValueError(f"{cid}: environment variable names use A-Z, 0-9 and _")
        try:
            max_refs = max(0, min(16, int(c["max_refs"] if c["max_refs"] not in (None, "") else 6)))
        except (TypeError, ValueError):
            raise ValueError(f"{cid}: max reference images must be a number") from None
        out.append({"id": cid, "name": str(c["name"] or cid).strip()[:60], "type": c["type"], "base_url": url,
                    "api_key_env": env, "text_model": str(c["text_model"] or "").strip(),
                    "image_model": str(c["image_model"] or "").strip(),
                    "image_quality": str(c["image_quality"] or "").strip(), "max_refs": max_refs,
                    "extra_models": str(c["extra_models"] or "").strip(), "enabled": c["enabled"] is not False})
    return out


# ── keys ────────────────────────────────────────────────────────────────

def _secrets_path():
    from . import settings
    return os.path.join(settings.DATA_ROOT, "secrets.json")


def _read_secrets() -> dict:
    try:
        with open(_secrets_path(), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def set_key(conn_id: str, key: str | None) -> None:
    """Store (or with an empty key, remove) a connection's key in the owner-only secrets file."""
    with _lock:
        data = _read_secrets()
        if key:
            data[conn_id] = key.strip()
        else:
            data.pop(conn_id, None)
        path = _secrets_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass


def api_key(s: dict, conn_id: str) -> str:
    try:
        c = get_conn(s, conn_id)
    except CloudError:
        return ""
    if c.get("api_key_env") and os.environ.get(c["api_key_env"]):
        return os.environ[c["api_key_env"]].strip()
    stored = _read_secrets().get(conn_id)
    if stored:
        return stored
    legacy = s.get(conn_id) if isinstance(s.get(conn_id), dict) else {}
    return (legacy.get("api_key") or "").strip()


def key_source(s: dict, conn_id: str) -> str:
    """'env', 'saved' or '' (for the UI; never the key itself)."""
    try:
        c = get_conn(s, conn_id)
    except CloudError:
        return ""
    if c.get("api_key_env") and os.environ.get(c["api_key_env"]):
        return "env"
    return "saved" if api_key(s, conn_id) else ""


def _is_local(c):
    return urlparse(c["base_url"]).hostname in ("localhost", "127.0.0.1", "::1")


def _need_key(s, c):
    key = api_key(s, c["id"])
    if not key and not _is_local(c):
        env = c.get("api_key_env")
        raise CloudError(f"{c['name']} has no API key. Add it on the AI models page"
                         + (f" or set {env} on the Studio server." if env else "."))
    return key


def _headers(c, key):
    if c["type"] == "gemini":
        return {"x-goog-api-key": key}
    if "fal.run" in (c.get("base_url") or ""):     # fal keys go in "Authorization: Key <key>", not Bearer
        return {"Authorization": f"Key {key}"} if key else {}
    return {"Authorization": f"Bearer {key}"} if key else {}


def _error(c, response) -> CloudError:
    """A readable error with the provider's own message, without request headers or payloads."""
    detail = ""
    try:
        data = response.json()
        err = data.get("error") if isinstance(data, dict) else None
        detail = (err.get("message") if isinstance(err, dict) else err) or ""
    except ValueError:
        detail = response.text[:200]
    detail = re.sub(r"(sk-|AIza|AQ\.|gsk_|xai-)[A-Za-z0-9_.\-]{8,}", "[key]", str(detail))[:400]
    return CloudError(f"{c['name']} HTTP {response.status_code}" + (f": {detail}" if detail else ""))


def _post(c, url, headers, timeout=TIMEOUT, **kwargs):
    """POST with retries on a busy service (503 overloaded, 429 rate limited, 500/502): 10 s, 30 s, 60 s."""
    import time
    for wait in (10, 30, 60, None):
        try:
            r = requests.post(url, headers=headers, timeout=timeout, **kwargs)
        except requests.RequestException as exc:
            raise CloudError(f"{c['name']} is unreachable: {type(exc).__name__}") from None
        if r.ok:
            return r.json()
        if r.status_code not in (429, 500, 502, 503) or wait is None or "limit: 0" in r.text:
            raise _error(c, r)
        time.sleep(wait)


def _strip_json(text: str) -> dict:
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.DOTALL).strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", text, flags=re.DOTALL)
    try:
        return json.loads((m.group(1) if m else text).strip())
    except json.JSONDecodeError as exc:
        raise CloudError(f"The model did not return valid JSON: {exc}") from None


def _image_bytes(path, max_side=1536) -> tuple[bytes, str]:
    """Reference/check images, upright and at most max_side px (keeps uploads small)."""
    with Image.open(path) as im:
        im = ImageOps.exif_transpose(im).convert("RGB")
        im.thumbnail((max_side, max_side))
        buf = io.BytesIO()
        im.save(buf, "PNG")
    return buf.getvalue(), "image/png"


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


# ── model references ────────────────────────────────────────────────────

def parse_ref(ref: str) -> tuple[str, str]:
    conn, _, model = str(ref or "").partition(":")
    return conn.strip(), model.strip()


def role_ref(s: dict, role: str) -> str:
    """Default text model for 'planner' (writing) or 'vision' (frame checks)."""
    if s.get(role):
        return s[role]
    provider = s.get(f"{role}_provider") or "ollama"          # older settings
    return f"ollama:{s[f'{role}_model']}" if provider == "ollama" else provider


def resolve_text(s: dict, ref: str) -> tuple[str, str]:
    conn, model = parse_ref(ref)
    if conn == "ollama":
        return conn, model or s["planner_model"]
    c = get_conn(s, conn)
    model = model or c.get("text_model")
    if not model:
        raise CloudError(f"{c['name']} has no text model set. Choose one on the AI models page.")
    return conn, model


def is_image_source(source: str) -> bool:
    return bool(source) and (source.startswith("img:") or source in LEGACY_IMAGE_SOURCES)


def resolve_image(s: dict, source: str) -> tuple[dict, str]:
    if source in LEGACY_IMAGE_SOURCES:
        conn, model = LEGACY_IMAGE_SOURCES[source], ""
    else:
        conn, model = parse_ref(source[4:])
    c = get_conn(s, conn)
    model = model or c.get("image_model")
    if not model:
        raise CloudError(f"{c['name']} has no image model set. Choose one on the AI models page.")
    return c, model


def max_refs(s, source) -> int:
    try:
        return int(resolve_image(s, source)[0].get("max_refs", 6))
    except CloudError:
        return 6


def label(s: dict, ref: str) -> str:
    if is_image_source(ref):
        try:
            c, model = resolve_image(s, ref)
            return f"{c['name']} · {model}"
        except CloudError:
            return ref
    conn, model = parse_ref(ref)
    if conn == "ollama":
        return f"Ollama · {model or s['planner_model']} (this PC)"
    try:
        conn, model = resolve_text(s, ref)
        return f"{name(s, conn)} · {model}"
    except CloudError:
        return ref


def _extras(c):
    return [m.strip() for m in re.split(r"[,\n]", c.get("extra_models") or "") if m.strip()]


def choices(s: dict, ollama_models: list[str]) -> dict:
    """What the pickers offer: text models (writing, checking) and cloud image models."""
    text, image = [], []
    for m in ollama_models:
        text.append({"ref": f"ollama:{m}", "label": f"Ollama · {m} (this PC)", "conn": "ollama"})
    for c in connections(s):
        if not c.get("enabled", True):
            continue
        ready = bool(api_key(s, c["id"])) or _is_local(c)
        for m in dict.fromkeys([c.get("text_model")] + [x for x in _extras(c) if "image" not in x.lower()]):
            if m:
                text.append({"ref": f"{c['id']}:{m}", "label": f"{c['name']} · {m}", "conn": c["id"], "ready": ready})
        for m in dict.fromkeys([c.get("image_model")] + [x for x in _extras(c) if "image" in x.lower()]):
            if m:
                image.append({"source": f"img:{c['id']}:{m}", "label": f"{c['name']} · {m}", "conn": c["id"],
                              "ready": ready})
    return {"text": text, "image": image}


# ── text (structured JSON) ──────────────────────────────────────────────

def chat_json(s: dict, conn_id: str, system: str, user: str, schema: dict,
              images: list[str] | None = None, temperature: float = 0.6, model: str = "") -> dict:
    c = get_conn(s, conn_id)
    model = model or c.get("text_model")
    if c["type"] == "gemini":
        return _gemini_chat(s, c, model, system, user, schema, images or [], temperature)
    if c["type"] == "anthropic":
        return _anthropic_chat(s, c, model, system, user, schema, images or [])
    if c["type"] == "codex":
        return _codex_chat(s, c, model, system, user, schema)
    if c["type"] == "claude_code":
        return _claude_code_chat(s, c, model, system, user, schema)
    return _openai_chat(s, c, model, system, user, schema, images or [], temperature)


def _closed_schema(schema):
    """Structured outputs want every object closed: additionalProperties false, recursively."""
    if isinstance(schema, dict):
        out = {k: _closed_schema(v) for k, v in schema.items()}
        if out.get("type") == "object":
            out["additionalProperties"] = False
            out.setdefault("properties", {})
        return out
    if isinstance(schema, list):
        return [_closed_schema(x) for x in schema]
    return schema


CODEX_PATHS = ("/usr/lib/chatgpt/resources/codex", os.path.expanduser("~/.codex/plugins/.plugin-appserver/codex"))


def _strict_schema(schema):
    """OpenAI strict structured output: every object closed and every property required."""
    if isinstance(schema, dict):
        out = {k: _strict_schema(v) for k, v in schema.items()}
        if out.get("type") == "object":
            out["additionalProperties"] = False
            out["properties"] = out.get("properties") or {}
            out["required"] = list(out["properties"].keys())
        return out
    if isinstance(schema, list):
        return [_strict_schema(x) for x in schema]
    return schema


def _codex_chat(s, c, model, system, user, schema):
    """One non-interactive Codex run (the ChatGPT app's CLI, the user's subscription) in an empty read-only sandbox;
    the final message is constrained to the schema and read back from a file."""
    import shutil, subprocess, tempfile
    exe = next((p for p in CODEX_PATHS if os.path.isfile(p)), None) or shutil.which("codex")
    if not exe:
        raise CloudError("The ChatGPT app's Codex CLI was not found on this PC.")
    with tempfile.TemporaryDirectory(prefix="reelwright_codex_") as work:
        sch, out = os.path.join(work, "schema.json"), os.path.join(work, "answer.json")
        with open(sch, "w", encoding="utf-8") as f:
            json.dump(_strict_schema(schema), f)
        prompt = (f"{system}\n\n{user}\n\nAnswer directly from the text above. Do not run commands, read files or "
                  "browse; reply only with the final JSON.")
        cmd = [exe, "exec", "--skip-git-repo-check", "--ephemeral", "--sandbox", "read-only", "--color", "never",
               "--output-schema", sch, "-o", out]
        if model:
            cmd += ["-m", model]
        try:
            r = subprocess.run(cmd + ["-"], input=prompt, capture_output=True, text=True, cwd=work, timeout=TEXT_TIMEOUT)
        except subprocess.TimeoutExpired:
            raise CloudError(f"{c['name']} did not finish within {TEXT_TIMEOUT // 60} minutes.") from None
        if r.returncode != 0 or not os.path.isfile(out):
            tail = (r.stderr or r.stdout or "").strip().splitlines()[-3:]
            raise CloudError(f"{c['name']} failed: {' / '.join(tail)[:300]}")
        try:
            with open(out, encoding="utf-8") as f:
                return json.load(f)
        except ValueError:
            raise CloudError(f"{c['name']} returned no valid JSON.") from None


CLAUDE_PATHS = (os.path.expanduser("~/.local/bin/claude"), os.path.expanduser("~/.claude/local/claude"))


def _claude_code_chat(s, c, model, system, user, schema):
    """One non-interactive Claude Code run (the user's Claude subscription) with no tools, settings, MCP servers or
    saved session; the answer is validated against the schema by the CLI and returned as structured_output."""
    import shutil, subprocess, tempfile
    exe = next((p for p in CLAUDE_PATHS if os.path.isfile(p)), None) or shutil.which("claude")
    if not exe:
        raise CloudError("The Claude Code CLI (claude) was not found on this PC.")
    cmd = [exe, "-p", "--output-format", "json", "--json-schema", json.dumps(_strict_schema(schema)),
           "--tools", "", "--no-session-persistence", "--setting-sources", "", "--strict-mcp-config",
           "--system-prompt", system]
    if model:
        cmd += ["--model", model]
    with tempfile.TemporaryDirectory(prefix="reelwright_claude_") as work:
        try:
            r = subprocess.run(cmd, input=user, capture_output=True, text=True, cwd=work, timeout=TEXT_TIMEOUT)
        except subprocess.TimeoutExpired:
            raise CloudError(f"{c['name']} did not finish within {TEXT_TIMEOUT // 60} minutes.") from None
    try:
        data = json.loads(r.stdout)
    except ValueError:
        tail = (r.stderr or r.stdout or "").strip().splitlines()[-3:]
        raise CloudError(f"{c['name']} failed: {' / '.join(tail)[:300]}") from None
    if data.get("is_error") or not isinstance(data.get("structured_output"), dict):
        raise CloudError(f"{c['name']} failed: {str(data.get('result') or data.get('subtype') or 'no JSON answer')[:300]}")
    return data["structured_output"]


def _anthropic_chat(s, c, model, system, user, schema, images):
    """Claude via the official SDK: schema-constrained JSON (output_config.format), adaptive thinking at the
    connection's effort (default high - writing is intelligence-sensitive), streamed so long scripts do not time
    out, and the server-side refusal fallback. Sampling parameters are not sent (current models reject them)."""
    import anthropic
    key = _need_key(s, c)
    client = anthropic.Anthropic(api_key=key, base_url=c.get("base_url") or None, max_retries=2)
    content = []
    for p in images:
        data, mime = _image_bytes(p)
        content.append({"type": "image", "source": {"type": "base64", "media_type": mime, "data": _b64(data)}})
    content.append({"type": "text", "text": user})
    try:
        with client.beta.messages.stream(
            model=model, max_tokens=64000, system=system,
            messages=[{"role": "user", "content": content}],
            output_config={"effort": c.get("effort") or "high",
                           "format": {"type": "json_schema", "schema": _closed_schema(schema)}},
            betas=["server-side-fallback-2026-07-01"], fallbacks="default",
        ) as stream:
            msg = stream.get_final_message()
    except anthropic.AuthenticationError:
        raise CloudError(f"{c['name']}: the API key was rejected. Check it on the AI models page.") from None
    except anthropic.NotFoundError:
        raise CloudError(f"{c['name']}: model '{model}' was not found.") from None
    except anthropic.RateLimitError:
        raise CloudError(f"{c['name']}: rate limited - try again in a minute.") from None
    except anthropic.APIStatusError as exc:
        raise CloudError(f"{c['name']}: HTTP {exc.status_code} - {str(exc.message)[:300]}") from None
    except anthropic.APIConnectionError:
        raise CloudError(f"{c['name']} is unreachable.") from None
    if msg.stop_reason == "refusal":
        why = getattr(getattr(msg, "stop_details", None), "explanation", "") or ""
        raise CloudError(f"{c['name']} declined this request. {why}".strip())
    if msg.stop_reason == "max_tokens":
        raise CloudError(f"{c['name']}: the answer was cut off at the output limit.")
    text = next((b.text for b in msg.content if b.type == "text"), "")
    try:
        return json.loads(text)
    except ValueError:
        raise CloudError(f"{c['name']} returned no valid JSON.") from None


def _openai_chat(s, c, model, system, user, schema, images, temperature):
    key = _need_key(s, c)
    content = [{"type": "text", "text": user}]
    for p in images:
        data, mime = _image_bytes(p)
        content.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{_b64(data)}"}})
    body = {"model": model,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": content if images else user}],
            "response_format": {"type": "json_schema",
                                "json_schema": {"name": "result", "schema": schema, "strict": False}},
            "temperature": temperature}
    url, headers = c["base_url"] + "/chat/completions", _headers(c, key)
    for _ in range(3):
        try:
            data = _post(c, url, headers, timeout=TEXT_TIMEOUT, json=body)
            break
        except CloudError as exc:
            msg = str(exc).lower()
            if "temperature" in msg and "temperature" in body:
                body.pop("temperature")              # reasoning models only accept their default
            elif ("response_format" in msg or "json_schema" in msg) and \
                    body["response_format"]["type"] == "json_schema":
                # services without JSON Schema support: JSON mode, schema in the prompt
                body["response_format"] = {"type": "json_object"}
                body["messages"][0]["content"] = f"{system}\n\nReply with JSON only, matching this JSON Schema:\n{json.dumps(schema)}"
            else:
                raise
    else:
        raise CloudError(f"{c['name']} rejected the request")
    msg = (data.get("choices") or [{}])[0].get("message") or {}
    if msg.get("refusal"):
        raise CloudError(f"{c['name']} declined: {msg['refusal'][:300]}")
    return _strip_json(msg.get("content") or "")


def _gemini_chat(s, c, model, system, user, schema, images, temperature):
    key = _need_key(s, c)
    parts = [{"text": user}]
    for p in images:
        data, mime = _image_bytes(p)
        parts.append({"inline_data": {"mime_type": mime, "data": _b64(data)}})
    body = {"systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {"temperature": temperature, "responseMimeType": "application/json",
                                 "responseJsonSchema": schema}}
    url, headers = f"{c['base_url']}/models/{model}:generateContent", _headers(c, key)
    try:
        data = _post(c, url, headers, timeout=TEXT_TIMEOUT, json=body)
    except CloudError as exc:
        if "responsejsonschema" not in str(exc).lower().replace("_", ""):
            raise
        body["generationConfig"].pop("responseJsonSchema")
        parts[0]["text"] = f"{user}\n\nReply with JSON only, matching this JSON Schema:\n{json.dumps(schema)}"
        data = _post(c, url, headers, timeout=TEXT_TIMEOUT, json=body)
    cands = data.get("candidates") or []
    if not cands:
        reason = (data.get("promptFeedback") or {}).get("blockReason")
        raise CloudError(f"Gemini returned nothing{f' (blocked: {reason})' if reason else ''}")
    text = "".join(p.get("text", "") for p in (cands[0].get("content") or {}).get("parts") or [] if not p.get("thought"))
    return _strip_json(text)


# ── images ──────────────────────────────────────────────────────────────

def _fit(data: bytes, width: int, height: int) -> bytes:
    """Centre-crop to the project's aspect ratio and size, so cloud frames match local ones."""
    with Image.open(io.BytesIO(data)) as im:
        im = ImageOps.fit(im.convert("RGB"), (int(width), int(height)), Image.Resampling.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, "PNG")
    return buf.getvalue()


def _openai_size(width, height):
    r = width / height
    return "1536x1024" if r > 1.15 else "1024x1536" if r < 0.87 else "1024x1024"


GEMINI_RATIOS = {"1:1": 1, "16:9": 16 / 9, "9:16": 9 / 16, "4:3": 4 / 3, "3:4": 3 / 4,
                 "3:2": 3 / 2, "2:3": 2 / 3, "5:4": 5 / 4, "4:5": 4 / 5, "21:9": 21 / 9}


def _gemini_ratio(width, height):
    r = width / height
    return min(GEMINI_RATIOS, key=lambda k: abs(GEMINI_RATIOS[k] - r))


def generate_images(s: dict, source: str, prompt: str, n: int, width: int, height: int,
                    refs: list[str] | None = None, log=None) -> list[bytes]:
    """n PNG images at width x height from an image source ("img:<conn>[:<model>]").
    refs: local reference image paths (characters first)."""
    c, model = resolve_image(s, source)
    refs = [p for p in (refs or []) if p and os.path.isfile(p)][:int(c.get("max_refs", 6))]
    if c["type"] == "gemini":
        out = [_gemini_image(s, c, model, prompt, width, height, refs) for _ in range(int(n))]
    else:
        out = _openai_images(s, c, model, prompt, n, width, height, refs, log)
    if not out:
        raise CloudError(f"{c['name']} returned no images")
    return [_fit(d, width, height) for d in out]


def _openai_images(s, c, model, prompt, n, width, height, refs, log=None):
    key = _need_key(s, c)
    headers = _headers(c, key)
    fields = {"model": model, "prompt": prompt[:32000], "n": int(n), "size": _openai_size(width, height)}
    if c.get("image_quality"):
        fields["quality"] = c["image_quality"]
    if refs:
        files = []
        for i, p in enumerate(refs):
            data, mime = _image_bytes(p)
            files.append(("image[]", (f"ref{i + 1}.png", data, mime)))
        try:
            data = _post(c, c["base_url"] + "/images/edits", headers,
                         data={k: str(v) for k, v in fields.items()}, files=files)
        except CloudError as exc:
            if c["type"] == "openai" or not re.search(r"HTTP (404|405|501)", str(exc)):
                raise
            if log:
                log(f"{c['name']} cannot use reference photos; rendering from the text prompt only.")
            data = _post(c, c["base_url"] + "/images/generations", headers, json=fields)
    else:
        data = _post(c, c["base_url"] + "/images/generations", headers, json=fields)
    out = []
    for item in data.get("data") or []:
        if item.get("b64_json"):
            out.append(base64.b64decode(item["b64_json"]))
        elif str(item.get("url", "")).startswith("https://"):
            r = requests.get(item["url"], timeout=120)
            r.raise_for_status()
            out.append(r.content)
    return out


def _gemini_image(s, c, model, prompt, width, height, refs):
    key = _need_key(s, c)
    parts = []
    for p in refs:
        data, mime = _image_bytes(p)
        parts.append({"inline_data": {"mime_type": mime, "data": _b64(data)}})
    parts.append({"text": prompt})
    body = {"contents": [{"role": "user", "parts": parts}],
            "generationConfig": {"responseModalities": ["TEXT", "IMAGE"],
                                 "imageConfig": {"aspectRatio": _gemini_ratio(width, height)}}}
    url, headers = f"{c['base_url']}/models/{model}:generateContent", _headers(c, key)
    try:
        data = _post(c, url, headers, json=body)
    except CloudError as exc:
        if "imageconfig" not in str(exc).lower().replace("_", ""):
            raise
        body["generationConfig"].pop("imageConfig")    # models without aspect-ratio control
        data = _post(c, url, headers, json=body)
    for cand in data.get("candidates") or []:
        for part in (cand.get("content") or {}).get("parts") or []:
            inline = part.get("inlineData") or part.get("inline_data")
            if inline and inline.get("data"):
                return base64.b64decode(inline["data"])
    reason = ((data.get("candidates") or [{}])[0].get("finishReason")
              or (data.get("promptFeedback") or {}).get("blockReason") or "no image in the reply")
    raise CloudError(f"{c['name']} made no image ({reason})")


# ── model lists / test ──────────────────────────────────────────────────

def list_models(s: dict, conn_id: str) -> list[str]:
    c = get_conn(s, conn_id)
    if c["type"] in ("codex", "claude_code"):        # CLIs on this PC: no model listing endpoint
        return [m for m in [c.get("text_model"), *(c.get("extra_models") or "").split(",")] if m and m.strip()]
    key = _need_key(s, c)
    if c["type"] == "anthropic":
        import anthropic
        try:
            return sorted(m.id for m in anthropic.Anthropic(api_key=key, base_url=c.get("base_url") or None).models.list())
        except anthropic.APIError as exc:
            raise CloudError(f"{c['name']}: {type(exc).__name__}") from None
    try:
        params = {"pageSize": 1000} if c["type"] == "gemini" else None
        r = requests.get(c["base_url"] + "/models", headers=_headers(c, key), params=params, timeout=30)
    except requests.RequestException as exc:
        raise CloudError(f"{c['name']} is unreachable: {type(exc).__name__}") from None
    if not r.ok:
        raise _error(c, r)
    data = r.json()
    if c["type"] == "gemini":
        return sorted(m["name"].removeprefix("models/") for m in data.get("models") or [] if m.get("name"))
    return sorted(m["id"] for m in data.get("data") or [] if m.get("id"))


# ── settings helpers (older keys saved inside settings.json) ───────────

def mask_keys(d: dict) -> dict:
    out = copy.deepcopy(d)
    for p in ("openai", "gemini"):
        if isinstance(out.get(p), dict) and out[p].get("api_key"):
            out[p]["api_key"] = MASK
    return out


def keep_masked_keys(new_user: dict, old_user: dict) -> dict:
    for p in ("openai", "gemini"):
        sect = new_user.get(p)
        if isinstance(sect, dict) and sect.get("api_key") == MASK:
            old = (old_user.get(p) or {}).get("api_key")
            if old:
                sect["api_key"] = old
            else:
                sect.pop("api_key")
    return new_user
