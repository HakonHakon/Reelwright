"""Dialogue voices through VoiceStudio (headless engine on this PC, service reelwright-voice).

Each character gets one reference voice (designed from a description, or an uploaded recording); every line
is cloned from it so the character always sounds like the same person, with an acting direction ("thrilled",
"shy but proud") for the delivery. Lines are checked with speech recognition and retried when the words are off.
"""
import difflib
import re

import requests

DESIGN_SEED = 1234
REFERENCE_LINE = "Hi! This is my voice, and I'm always ready for an adventure!"


class VoiceError(RuntimeError):
    pass


def cfg(s: dict) -> dict:
    return s["voices"]


def _url(s, path):
    return cfg(s)["host"].rstrip("/") + path


def _error(r) -> VoiceError:
    try:
        detail = r.json().get("detail")
        if isinstance(detail, dict):
            detail = detail.get("message") or str(detail)
    except ValueError:
        detail = r.text[:200]
    return VoiceError(f"VoiceStudio HTTP {r.status_code}: {str(detail)[:300]}")


def health(s: dict) -> dict | None:
    try:
        r = requests.get(_url(s, "/health"), timeout=3)
        return r.json() if r.ok else None
    except requests.RequestException:
        return None


def require(s: dict) -> None:
    if not health(s):
        raise VoiceError("VoiceStudio is not running. Start it with: systemctl --user start reelwright-voice")


def unload(s: dict) -> None:
    """Free VoiceStudio's GPU memory (before ComfyUI renders); never fails."""
    try:
        loaded = requests.get(_url(s, "/model/loaded"), timeout=5).json().get("models", [])
        for m in loaded:
            mid = m.get("id") or m.get("model_id")
            if mid:
                requests.post(_url(s, f"/model/unload/{mid}"), timeout=30)
    except (requests.RequestException, ValueError):
        pass


def instruct_for(s: dict, description: str) -> str:
    """Voice-design tags (e.g. 'male, child, high pitch') from a plain description."""
    r = requests.post(_url(s, "/design/describe"), json={"description": description}, timeout=30)
    if not r.ok:
        raise _error(r)
    return r.json().get("instruct") or ""


def _generate(s, data, files=None) -> bytes:
    try:
        r = requests.post(_url(s, "/generate"), data=data, files=files, timeout=900)
    except requests.RequestException as exc:
        raise VoiceError(f"VoiceStudio is unreachable: {type(exc).__name__}") from None
    if not r.ok or not r.headers.get("content-type", "").startswith("audio"):
        raise _error(r)
    return r.content


def design_reference(s: dict, description: str, language: str | None = None) -> tuple[bytes, str]:
    """A reference recording for a character, from a description. Returns (wav bytes, its transcript)."""
    tags = instruct_for(s, description)
    wav = _generate(s, {"text": REFERENCE_LINE, "language": language or cfg(s)["language"],
                        "instruct": tags, "seed": str(DESIGN_SEED), "engine": cfg(s)["design_engine"]})
    return wav, REFERENCE_LINE


def speak(s: dict, text: str, ref_path: str, ref_text: str, emotion: str = "", seed: int = 7,
          language: str | None = None) -> bytes:
    """One line in the character's voice, acted with the given direction."""
    c = cfg(s)
    data = {"text": text, "language": language or c["language"], "ref_text": ref_text or "", "seed": str(seed),
            "engine": c["engine"]}
    if emotion and c.get("emotion", True):
        data.update(emo_text=emotion[:500], emo_alpha=str(c["emo_alpha"]))
    with open(ref_path, "rb") as f:
        return _generate(s, data, files={"ref_audio": ("reference.wav", f, "audio/wav")})


def transcribe(s: dict, path: str, language: str | None = None) -> str:
    with open(path, "rb") as f:
        r = requests.post(_url(s, "/v1/audio/transcriptions"), timeout=300,
                          files={"file": ("line.wav", f, "audio/wav")},
                          data={"model": "whisper-1", "language": language or cfg(s)["language"]})
    if not r.ok:
        raise _error(r)
    return (r.json().get("text") or "").strip()


def _words(t: str) -> list[str]:
    return re.findall(r"[^\W_]+", t.lower().replace("'", ""))


def match(wanted: str, heard: str) -> float:
    """0..1 similarity of the spoken letters (punctuation, case, apostrophes and word breaks ignored,
    so 'Anytime' heard as 'Any time' still matches)."""
    a, b = "".join(_words(wanted)), "".join(_words(heard))
    if not a and not b:
        return 1.0
    return difflib.SequenceMatcher(None, a, b).ratio()
