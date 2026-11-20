"""Ollama client with JSON-schema structured output (keeps small models on-format)."""

import base64
import json
import re


class LLMError(RuntimeError):
    pass


def _client(host: str):
    import ollama
    return ollama.Client(host=host)


def _strip(text: str) -> str:
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.DOTALL).strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", text, flags=re.DOTALL)
    return (m.group(1) if m else text).strip()


def chat_json(host: str, model: str, system: str, user: str, schema: dict,
              images: list[str] | None = None, temperature: float = 0.6,
              num_ctx: int = 16384, num_predict: int = 8192, retries: int = 2) -> dict:
    client = _client(host)
    msg = {"role": "user", "content": user}
    if images:
        msg["images"] = [_b64(p) for p in images]
    messages = [{"role": "system", "content": system}, msg]
    opts = {"temperature": temperature, "num_ctx": num_ctx, "num_predict": num_predict}
    last_err = None
    for attempt in range(retries + 1):
        try:
            try:
                resp = client.chat(model=model, messages=messages, format=schema, options=opts, think=False)
            except Exception as exc:  # model without thinking support / older client
                if "think" not in str(exc).lower():
                    raise
                resp = client.chat(model=model, messages=messages, format=schema, options=opts)
            content = _strip(resp["message"]["content"])
            return json.loads(content)
        except json.JSONDecodeError as exc:
            last_err = exc
            opts["temperature"] = max(0.2, opts["temperature"] - 0.2)
            opts["num_predict"] = min(opts["num_predict"] * 2, 32768)
        except Exception as exc:
            raise LLMError(f"Ollama call failed ({model}): {exc}") from exc
    raise LLMError(f"{model} returned invalid JSON after {retries + 1} attempts: {last_err}")


def unload(host: str, models: list[str]) -> None:
    try:
        client = _client(host)
    except Exception:
        return
    for m in {m for m in models if m}:
        try:
            client.generate(model=m, prompt="", keep_alive=0)
        except Exception:
            pass


def installed(host: str) -> list[str]:
    try:
        return [m.model for m in _client(host).list().models]
    except Exception:
        return []


def _b64(path: str) -> str:
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode()


def ensure_model(host: str, model: str, log=print) -> None:
    """Pull an Ollama model if it isn't installed yet (logs progress every ~10%)."""
    names = installed(host)
    if model in names or (":" not in model and f"{model}:latest" in names):
        return
    log(f"Ollama model {model} not installed - downloading (one time)...")
    client = _client(host)
    last = -10
    for part in client.pull(model, stream=True):
        total, done = getattr(part, "total", None) or 0, getattr(part, "completed", None) or 0
        if total:
            pct = int(100 * done / total)
            if pct >= last + 10:
                log(f"  {model}: {pct}% of {total / 1e9:.1f} GB")
                last = pct
    log(f"  {model} ready.")


# ── model routing (Ollama on this PC, or an AI connection) ─────────────

def ref(s: dict, role: str) -> str:
    """The model reference used for 'planner' (writing) or 'vision' (frame checks)."""
    from . import cloud
    return cloud.role_ref(s, role)


def provider(s: dict, role: str) -> str:
    from . import cloud
    return cloud.parse_ref(ref(s, role))[0]


def describe(s: dict, role: str) -> str:
    from . import cloud
    return cloud.label(s, ref(s, role))


def chat(s: dict, role: str, system: str, user: str, schema: dict, images: list[str] | None = None,
         temperature: float = 0.6, **ollama_opts) -> dict:
    """Structured JSON from the model chosen for this role (settings, or a per-job choice in s[role])."""
    from . import cloud
    try:
        conn, model = cloud.resolve_text(s, ref(s, role))
        if conn == "ollama":
            return chat_json(s["ollama_host"], ollama_model(s, role), system, user, schema, images=images,
                             temperature=temperature, **ollama_opts)
        return cloud.chat_json(s, conn, system, user, schema, images=images, temperature=temperature, model=model)
    except cloud.CloudError as exc:
        raise LLMError(str(exc)) from exc


def ollama_model(s: dict, role: str) -> str:
    from . import cloud
    conn, model = cloud.parse_ref(ref(s, role))
    return (model or s[f"{role}_model"]) if conn == "ollama" else ""


def prepare(s: dict, role: str, log=print) -> None:
    """Make sure the role's model can run: pull a missing Ollama model; cloud models need nothing."""
    m = ollama_model(s, role)
    if m:
        ensure_model(s["ollama_host"], m, log)
    else:
        log(f"Using {describe(s, role)}.")


def with_choice(s: dict, role: str, choice: str) -> dict:
    """Settings copy that uses `choice` (a model reference) for this job only."""
    return {**s, role: choice} if choice else s
