"""Unattended Studio test run: builds the bible, plans "The Rain Boat", renders frames and takes, cuts the film.

Start Comfy Desktop, Ollama and start-studio.bat first, then:
    .venv\\Scripts\\python tools\\demo_run.py
Everything it does is visible in the Studio UI (http://localhost:8020) and can be redone from there.
"""

import os
import sys
import time

import requests

BASE = os.environ.get("STUDIO_URL", "http://127.0.0.1:8020")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STORY = os.path.join(ROOT, "examples", "the_rain_boat.txt")
SAM_LORA = "Sam/ohwx_sam_flux1dev_2500.safetensors"


LOGFILE = os.path.join(ROOT, "studio_data", "demo_log.txt")


def log(msg):
    line = f"{time.strftime('%H:%M:%S')} {msg}"
    print(line, flush=True)
    os.makedirs(os.path.dirname(LOGFILE), exist_ok=True)
    with open(LOGFILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def call(method, path, **kw):
    r = requests.request(method, BASE + path, timeout=120, **kw)
    if r.status_code >= 400:
        raise SystemExit(f"{method} {path} -> {r.status_code}: {r.text[:800]}")
    return r.json() if r.text else None


def wait_job(job):
    last = 0
    while True:
        j = next((x for x in call("GET", "/api/jobs") if x["id"] == job["id"]), None)
        if j is None:
            raise SystemExit("job vanished")
        new = j["log"][-(len(j["log"]) - last):] if len(j["log"]) > last else []
        for line in new:
            log("    " + line)
        last = len(j["log"])
        if j["status"] in ("done", "error", "cancelled"):
            if j["status"] != "done":
                raise SystemExit(f"Job '{j['title']}' {j['status']}: {j['error']}")
            return j
        time.sleep(3)


def main():
    try:
        _run()
    except SystemExit as exc:
        if exc.code not in (None, 0):
            log(f"STOPPED: {exc.code}")
        raise
    except Exception as exc:
        log(f"CRASHED: {exc!r}")
        raise


def _run():
    log(f"Waiting for Studio at {BASE} ...")
    for _ in range(60):
        try:
            st = call("GET", "/api/status")
            break
        except Exception:
            time.sleep(2)
    else:
        raise SystemExit("Studio is not running. Start start-studio.bat first.")
    log(f"ComfyUI up: {st['comfy']} | Ollama models: {st['ollama_models']} | missing: {st['missing']}")
    if not st["comfy"]:
        raise SystemExit("ComfyUI is not running - start Comfy Desktop.")
    global SAM_LORA
    match = [l for l in st["loras"] if l.replace("\\", "/").endswith("ohwx_sam_flux1dev_2500.safetensors")]
    if not match:
        log(f"LoRAs ComfyUI reports ({len(st['loras'])}): {st['loras'][:60]}")
        raise SystemExit(f"LoRA {SAM_LORA} not found in ComfyUI's list.")
    SAM_LORA = match[0]
    log(f"Sam LoRA: {SAM_LORA}")

    series = next((s for s in call("GET", "/api/series") if s["name"] == "Sam"), None) or \
        call("POST", "/api/series", json={"name": "Sam"})
    sid = series["id"]
    log(f"Series: {sid}")
    call("PUT", f"/api/s/{sid}/style", json={
        "look": "stylised 3D animated family film, soft rounded shapes, gentle subsurface skin, "
                "warm global illumination, shallow depth of field",
        "grade": "fresh morning colours, soft golden highlights, gentle contrast",
        "default_frame_source": "flux_lora", "default_engine": "ltx"})

    bible = call("GET", f"/api/s/{sid}")["bible"]
    if "sam" not in bible["characters"]:
        call("POST", f"/api/s/{sid}/characters", json={"name": "Sam"})
    call("PUT", f"/api/s/{sid}/characters/sam", json={
        "name": "Sam", "trigger": "ohwx_sam",
        "appearance": "a 3-year-old boy with short dark brown hair, big brown eyes, round soft cheeks, "
                      "light olive skin, small button nose, curious happy expression",
        "outfits": {"default": "teal raincoat printed with little yellow trees, striped white and navy "
                               "shirt underneath, red rubber boots"},
        "default_outfit": "default",
        "image_lora": {"name": SAM_LORA, "strength": 0.9},
        "voice": {"wpm": 90, "description": "small, bright toddler voice"},
        "acting_notes": "curious, playful, laughs easily"})

    if "backyard" not in bible["locations"]:
        call("POST", f"/api/s/{sid}/locations", json={"name": "Backyard"})
    call("PUT", f"/api/s/{sid}/locations/backyard", json={
        "name": "Backyard",
        "description": "small family backyard with wet green grass, shiny puddles, a white wooden fence, "
                       "an apple tree dripping with raindrops, a back door with steps",
        "variants": {"morning": "just after rain, clouds breaking, soft morning light"},
        "palette": "fresh greens, teal, warm yellow", "ambience": "dripping water, birds chirping"})

    sam = call("GET", f"/api/s/{sid}")["bible"]["characters"]["sam"]
    if not sam.get("sheet"):
        log("Character sheet (Flux + LoRA) ...")
        wait_job(call("POST", f"/api/s/{sid}/characters/sam/sheet", json={"source": "flux_lora"}))
        sam = call("GET", f"/api/s/{sid}")["bible"]["characters"]["sam"]
    for item in sam.get("sheet", []):
        if item["view"] in ("front", "full_body") and item["file"] not in sam.get("refs", []):
            call("POST", f"/api/s/{sid}/characters/sam/refs", json={"add": item["file"]})

    loc = call("GET", f"/api/s/{sid}")["bible"]["locations"]["backyard"]
    if not (loc.get("plates") or {}).get("morning"):
        log("Location plates for Backyard / morning (Z-Image) ...")
        wait_job(call("POST", f"/api/s/{sid}/locations/backyard/plates", json={"variant": "morning", "source": "zimage"}))
        loc = call("GET", f"/api/s/{sid}")["bible"]["locations"]["backyard"]
        first = next(cand for cand in loc["candidates"] if cand["variant"] == "morning")
        call("POST", f"/api/s/{sid}/locations/backyard/approve", json={"file": first["file"], "variant": "morning"})
        log(f"  approved plate {first['file']}")

    ep = next((e for e in call("GET", f"/api/s/{sid}")["episodes"] if e["title"] == "The Rain Boat"), None) or \
        call("POST", f"/api/s/{sid}/episodes", json={"title": "The Rain Boat"})
    eid = ep["id"]
    with open(STORY, encoding="utf-8") as f:
        call("PUT", f"/api/s/{sid}/e/{eid}/story", json={"story": f.read()})

    log("Planning scenes and shots (Ollama) ...")
    wait_job(call("POST", f"/api/s/{sid}/e/{eid}/plan"))
    e = call("GET", f"/api/s/{sid}/e/{eid}")
    for sh in e["shots"]:
        dl = " / ".join(d["line"] for d in sh["dialogue"])
        log(f"  {sh['id']} {sh['duration_s']}s {sh['framing']:<16} {sh['action'][:70]} {('| ' + dl) if dl else ''}")
    log(f"Runtime {e['_runtime']} s")
    call("POST", f"/api/s/{sid}/e/{eid}/gate", json={"gate": "shots", "value": True})

    log("First frames ...")
    wait_job(call("POST", f"/api/s/{sid}/e/{eid}/frames", json={}))
    e = call("GET", f"/api/s/{sid}/e/{eid}")
    for sh in e["shots"]:
        cands = sh["frame"]["candidates"]
        if not cands or sh["frame"]["selected"] is not None:
            continue
        best = max(range(len(cands)), key=lambda k: ((cands[k].get("check") or {}).get("score") or 0))
        call("POST", f"/api/s/{sid}/e/{eid}/shots/{sh['id']}/frame", json={"select": best})
        log(f"  {sh['id']}: picked candidate {best} (check {(cands[best].get('check') or {}).get('score')})")
    call("POST", f"/api/s/{sid}/e/{eid}/gate", json={"gate": "frames", "value": True})

    log("Animatic ...")
    wait_job(call("POST", f"/api/s/{sid}/e/{eid}/animatic"))

    log("Takes (LTX-2.3, 1 per shot) ...")
    wait_job(call("POST", f"/api/s/{sid}/e/{eid}/takes", json={"takes": 1}))

    log("Final cut ...")
    wait_job(call("POST", f"/api/s/{sid}/e/{eid}/cut"))
    e = call("GET", f"/api/s/{sid}/e/{eid}")
    log(f"DONE: {e['outputs'].get('final')} ({e['outputs'].get('runtime')} s)")
    log(f"Review it in the browser: {BASE}/#/s/{sid}/e/{eid}/cut")


if __name__ == "__main__":
    sys.exit(main())
