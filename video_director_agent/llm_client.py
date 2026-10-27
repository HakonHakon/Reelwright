"""Small adapter that supports Ollama and OpenAI-compatible chat endpoints."""

import logging
import re
from typing import Any

import requests

import config

log = logging.getLogger(__name__)

_cached_models: list[str] | None = None
_fallback_warnings: set[tuple[str, str]] = set()


def is_ollama_provider() -> bool:
    return str(config.LLM_PROVIDER).lower() == "ollama"


def provider_label() -> str:
    return "Ollama" if is_ollama_provider() else "OpenAI-compatible API"


def _ollama_client():
    import ollama

    return ollama.Client(host=config.OLLAMA_HOST)


def _openai_base_url() -> str:
    return str(config.LLM_BASE_URL).rstrip("/")


def _headers() -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if config.LLM_API_KEY:
        headers["Authorization"] = f"Bearer {config.LLM_API_KEY}"
    return headers


def clear_model_cache():
    global _cached_models, _fallback_warnings
    _cached_models = None
    _fallback_warnings = set()


def _mime_from_base64(image_b64: str) -> str:
    if image_b64.startswith("/9j/"):
        return "image/jpeg"
    if image_b64.startswith("iVBORw0KGgo"):
        return "image/png"
    if image_b64.startswith("UklGR"):
        return "image/webp"
    return "image/jpeg"


def _build_openai_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    converted = []
    for message in messages:
        role = message.get("role", "user")
        content = message.get("content", "")
        images = message.get("images") or []

        if images:
            parts = []
            if content:
                parts.append({"type": "text", "text": content})
            for image_b64 in images:
                mime = _mime_from_base64(image_b64)
                parts.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime};base64,{image_b64}"},
                })
            converted.append({"role": role, "content": parts or [{"type": "text", "text": ""}]})
        else:
            converted.append({"role": role, "content": content})
    return converted


def _extract_content_from_openai_message(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                texts.append(item.get("text", ""))
            else:
                texts.append(str(item))
        return "\n".join(t for t in texts if t)
    if content is None:
        return ""
    return str(content)


def _normalize_reasoning_output(content: str) -> tuple[str, str | None]:
    """Extract user-facing content from llama.cpp/Gemma reasoning channel wrappers."""
    text = (content or "").strip()
    if not text:
        return "", None

    # Common llama.cpp/Gemma wrapper:
    # <|channel>thought
    # <channel|>final answer
    channel_match = re.match(
        r"^\s*<\|channel\>([a-zA-Z0-9_\-]+)\s*[\r\n]*<channel\|>\s*(.*)\s*$",
        text,
        flags=re.DOTALL,
    )
    if channel_match:
        channel = channel_match.group(1).strip().lower()
        body = channel_match.group(2).strip()
        if channel in {"thought", "thinking", "reasoning", "analysis"}:
            return body, None
        return body, channel

    # Fallback: strip stray channel markers if the model leaked them verbatim.
    cleaned = re.sub(r"<\|channel\>[a-zA-Z0-9_\-]+", "", text)
    cleaned = cleaned.replace("<channel|>", "").strip()
    return cleaned, None


def list_models(force_refresh: bool = False) -> list[str]:
    global _cached_models

    if _cached_models is not None and not force_refresh:
        return list(_cached_models)

    if is_ollama_provider():
        models = [m.model for m in _ollama_client().list().models]
    else:
        response = requests.get(f"{_openai_base_url()}/models", headers=_headers(), timeout=30)
        response.raise_for_status()
        payload = response.json()
        models = [item.get("id", "") for item in payload.get("data", []) if item.get("id")]

    _cached_models = models
    return list(models)


def resolve_model(requested_model: str | None) -> str:
    requested_model = (requested_model or "").strip()
    models = list_models()
    if not models:
        return requested_model

    if requested_model:
        for model in models:
            if model == requested_model:
                return model
        lowered = requested_model.lower()
        for model in models:
            if model.lower() == lowered:
                return model
        for model in models:
            if lowered in model.lower() or model.lower() in lowered:
                return model

    fallback = models[0]
    key = (requested_model, fallback)
    if key not in _fallback_warnings:
        if requested_model:
            log.warning("Configured LLM model '%s' not found on %s; using '%s' instead.",
                        requested_model, provider_label(), fallback)
        else:
            log.info("No LLM model configured; using '%s' from %s.", fallback, provider_label())
        _fallback_warnings.add(key)
    return fallback


def is_model_installed(requested_model: str) -> bool:
    """True if the model (with or without an explicit :tag) is installed on the backend."""
    wanted = (requested_model or "").strip().lower()
    if not wanted:
        return False
    names = [m.lower() for m in list_models(force_refresh=True)]
    candidates = {wanted} if ":" in wanted else {wanted, f"{wanted}:latest"}
    return any(n in candidates for n in names)


def ensure_model_available(requested_model: str | None) -> str:
    models = list_models(force_refresh=True)
    if not models:
        if requested_model:
            return requested_model
        raise RuntimeError(f"No models reported by {provider_label()}")
    return resolve_model(requested_model)


def chat(model: str, messages: list[dict[str, Any]], options: dict[str, Any] | None = None) -> dict[str, Any]:
    options = options or {}

    if is_ollama_provider():
        client = _ollama_client()
        think = bool(options.get("enable_thinking", getattr(config, "LLM_ENABLE_THINKING", False)))
        ollama_options = {k: v for k, v in options.items()
                          if k not in {"enable_thinking", "reasoning_format", "response_format"}}
        try:
            # Explicit think flag: thinking-capable models (e.g. bonsai-2, qwen3) otherwise
            # may spend the token budget reasoning, which truncates the JSON we need.
            response = client.chat(model=model, messages=messages, options=ollama_options, think=think)
        except Exception as exc:  # old client without `think`, or model rejecting the flag
            if "think" not in str(exc).lower():
                raise
            response = client.chat(model=model, messages=messages, options=ollama_options)
        content = response["message"]["content"] or ""
        # Some templates leak reasoning inline instead of in message.thinking.
        content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL)
        content, _ = _normalize_reasoning_output(content)
        return {
            "message": {"content": content},
            "raw": response,
        }

    request_model = resolve_model(model)
    payload: dict[str, Any] = {
        "model": request_model,
        "messages": _build_openai_messages(messages),
    }
    reasoning_format = options.get("reasoning_format", getattr(config, "LLM_REASONING_FORMAT", ""))
    enable_thinking = options.get("enable_thinking", getattr(config, "LLM_ENABLE_THINKING", False))
    if reasoning_format:
        payload["reasoning_format"] = reasoning_format
    if enable_thinking:
        payload["chat_template_kwargs"] = {"enable_thinking": True}
    if "temperature" in options:
        payload["temperature"] = options["temperature"]
    if "num_predict" in options:
        payload["max_tokens"] = options["num_predict"]
    if "stop" in options:
        payload["stop"] = options["stop"]
    if "response_format" in options:
        payload["response_format"] = options["response_format"]

    response = requests.post(
        f"{_openai_base_url()}/chat/completions",
        json=payload,
        headers=_headers(),
        timeout=1800,
    )
    response.raise_for_status()
    data = response.json()

    try:
        raw_message = data["choices"][0]["message"]
        content = _extract_content_from_openai_message(raw_message.get("content"))
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(f"Unexpected chat response from {provider_label()}: {data}") from exc

    normalized_content, inferred_channel = _normalize_reasoning_output(content)
    reasoning_content = raw_message.get("reasoning_content")
    if isinstance(reasoning_content, list):
        reasoning_content = _extract_content_from_openai_message(reasoning_content)
    elif reasoning_content is not None:
        reasoning_content = str(reasoning_content)

    return {
        "message": {
            "content": normalized_content,
            "reasoning_content": reasoning_content,
            "channel": inferred_channel,
        },
        "raw": data,
        "model": request_model,
    }


def unload_model(model: str, log_obj=None):
    if not is_ollama_provider():
        if log_obj:
            log_obj.info("Skipping model unload because LLM provider is %s.", provider_label())
        return

    client = _ollama_client()
    client.generate(model=model, prompt="", keep_alive=0)
