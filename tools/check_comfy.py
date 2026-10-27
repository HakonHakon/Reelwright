"""Preflight for KupkaProd: validate the API templates against the running ComfyUI.

Checks every node class exists and every model/combo value is available, then
optionally queues a short test render.

    python tools/check_comfy.py                 # validate only
    python tools/check_comfy.py --run keyframe  # + render one keyframe
    python tools/check_comfy.py --run video     # + render a ~2s test clip
"""

import argparse
import copy
import json
import os
import random
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AGENT_DIR = os.path.join(ROOT, "video_director_agent")
TEMPLATES = {
    "video": "workflow_template.json",
    "keyframe": "keyframe_template.json",
    "i2v": "i2v_template.json",
}


def _host_from_settings() -> str:
    path = os.path.join(AGENT_DIR, "user_settings.json")
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f).get("comfyui_host", "127.0.0.1:8188")
    except (OSError, ValueError):
        return "127.0.0.1:8188"


def _get(host: str, path: str, timeout: int = 30):
    with urllib.request.urlopen(f"http://{host}{path}", timeout=timeout) as r:
        return json.loads(r.read())


def _combo_options(spec):
    """Return the option list for a combo input spec, or None if not a combo."""
    if not isinstance(spec, (list, tuple)) or not spec:
        return None
    first = spec[0]
    if isinstance(first, list):
        return first
    if first == "COMBO" and len(spec) > 1 and isinstance(spec[1], dict):
        return spec[1].get("options")
    return None


def validate(template: dict, object_info: dict) -> list[str]:
    problems = []
    for node_id, node in template.items():
        cls = node.get("class_type")
        info = object_info.get(cls)
        if info is None:
            problems.append(f"[{node_id}] node type '{cls}' is not installed")
            continue
        spec_all = {}
        for section in ("required", "optional"):
            spec_all.update(info.get("input", {}).get(section, {}) or {})
        for name, value in node.get("inputs", {}).items():
            if isinstance(value, list):  # link to another node
                if value and value[0] not in template:
                    problems.append(f"[{node_id}] {cls}.{name} links to missing node {value[0]}")
                continue
            options = _combo_options(spec_all.get(name))
            if options and isinstance(value, str) and value not in options:
                if cls == "LoadImage":
                    continue  # filled in at runtime
                problems.append(f"[{node_id}] {cls}.{name} = '{value}' not available in ComfyUI")
    return problems


def run_test(host: str, kind: str, template: dict) -> None:
    wf = copy.deepcopy(template)
    seed = random.randint(1, 2**31)
    if kind == "keyframe":
        for node in wf.values():
            if node["class_type"] == "KSampler":
                node["inputs"]["seed"] = seed
            if node["class_type"] == "EmptySD3LatentImage":
                node["inputs"].update(width=1024, height=576)
    else:
        wf["153:125"]["inputs"]["value"] = 49  # ~2 s at 24 fps
        wf["153:124"]["inputs"].update(width=768, height=448)
        wf["153:132"]["inputs"]["text"] = (
            "A red fox trots across a snowy forest clearing at sunrise, soft golden light, "
            "camera slowly tracking left, crunching snow and birdsong."
        )
        for node in wf.values():
            if node["class_type"] == "RandomNoise":
                node["inputs"]["noise_seed"] = seed

    body = json.dumps({"prompt": wf, "client_id": "kupkaprod-check"}).encode()
    req = urllib.request.Request(f"http://{host}/prompt", data=body,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            prompt_id = json.loads(r.read())["prompt_id"]
    except urllib.error.HTTPError as e:
        print(f"  ComfyUI rejected the prompt:\n{e.read().decode(errors='replace')}")
        sys.exit(1)

    print(f"  queued {kind} test ({prompt_id}), waiting...")
    t0 = time.time()
    while True:
        time.sleep(3)
        hist = _get(host, f"/history/{prompt_id}").get(prompt_id)
        if not hist:
            continue
        status = hist.get("status", {})
        if status.get("status_str") == "error":
            for msg in status.get("messages", []):
                if msg[0] == "execution_error":
                    print(f"  FAILED in {msg[1].get('node_type')}: {msg[1].get('exception_message')}")
            sys.exit(1)
        if hist.get("outputs"):
            break
    for out in hist["outputs"].values():
        for key in ("images", "videos", "gifs"):
            for item in out.get(key, []):
                if isinstance(item, dict) and item.get("filename"):
                    q = urllib.parse.urlencode({"filename": item["filename"],
                                                "subfolder": item.get("subfolder", ""),
                                                "type": item.get("type", "output")})
                    print(f"  OK in {time.time() - t0:.0f}s -> http://{host}/view?{q}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default=_host_from_settings())
    ap.add_argument("--run", choices=["keyframe", "video"])
    args = ap.parse_args()

    try:
        object_info = _get(args.host, "/object_info", timeout=120)
    except Exception as e:
        print(f"ComfyUI not reachable at {args.host}: {e}\nStart Comfy Desktop first.")
        sys.exit(2)
    print(f"ComfyUI at {args.host}: {len(object_info)} node types")

    failed = False
    loaded = {}
    for kind, fname in TEMPLATES.items():
        path = os.path.join(AGENT_DIR, fname)
        if not os.path.exists(path):
            print(f"- {fname}: not present (optional)" if kind == "i2v" else f"- {fname}: MISSING")
            failed |= kind != "i2v"
            continue
        with open(path, encoding="utf-8") as f:
            loaded[kind] = json.load(f)
        problems = validate(loaded[kind], object_info)
        print(f"- {fname}: {'OK' if not problems else f'{len(problems)} problem(s)'}")
        for p in problems:
            print(f"    {p}")
        failed |= bool(problems) and kind != "i2v"

    if failed:
        sys.exit(1)
    if args.run:
        run_test(args.host, args.run, loaded[args.run])


if __name__ == "__main__":
    main()
