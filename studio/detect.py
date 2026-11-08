"""Deterministic frame checks with small OpenCV models on the CPU: who is physically in a frame and whose face it is.

The local vision model is good at semantics (a door is open, an umbrella is held) and bad at counting and recognising
people: it reported Maryam in empty windows and missed a pasted portrait. These checks answer only what detectors
measure reliably on our renders (calibrated on Price of a Lie Ep2, 2026-11-08):
  - fewer people than the plan needs (YOLOX person boxes plus faces outside any box);
  - a reference portrait pasted into the frame (a face nearly identical to the reference photo);
  - two faces with the same identity;
  - how much each visible face resembles its character (soft: ranks candidates).
Head pose is deliberately NOT used for camera gaze: an over-the-shoulder listener looking at the foreground shoulder
is as frontal as a look into the lens.
Models: OpenCV zoo YuNet, SFace and YOLOX (Apache-2.0), downloaded once into the runtime models folder.
"""
import os
import threading

import numpy as np

from . import paths

MODELS = {
    "yunet": "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx",
    "sface": "https://github.com/opencv/opencv_zoo/raw/main/models/face_recognition_sface/face_recognition_sface_2021dec.onnx",
    "yolox": "https://github.com/opencv/opencv_zoo/raw/main/models/object_detection_yolox/object_detection_yolox_2022nov.onnx",
}
PASTED = 0.8           # renders described like their cast portrait reach ~0.6; a pasted portrait is near-identical
SAME = 0.4             # two faces this close to one reference are the same person twice
RESEMBLES = 0.3        # below this a face does not read as its character
MIN_FACE = 32          # px: smaller faces (photos on a wall, crowds far away) are not judged
BODY = ("full_body", "upper_body", "back_of_head", "silhouette")    # visibilities that put a body in the frame

_lock = threading.Lock()
_nets = {}
_refs = {}


def model_dir():
    return os.path.join(str(paths.models_root()), "detect")


def ensure_models(log=print):
    import requests
    os.makedirs(model_dir(), exist_ok=True)
    for name, url in MODELS.items():
        path = os.path.join(model_dir(), os.path.basename(url))
        if os.path.isfile(path) and os.path.getsize(path) > 100_000:
            continue
        log(f"Downloading the {name} detector model (one time)...")
        r = requests.get(url, timeout=300)
        r.raise_for_status()
        with open(path + ".part", "wb") as f:
            f.write(r.content)
        os.replace(path + ".part", path)


def _net(name):
    import cv2
    with _lock:
        if name not in _nets:
            path = os.path.join(model_dir(), os.path.basename(MODELS[name]))
            if name == "yunet":
                _nets[name] = cv2.FaceDetectorYN.create(path, "", (320, 320), 0.7, 0.3, 5000)
            elif name == "sface":
                _nets[name] = cv2.FaceRecognizerSF.create(path, "")
            else:
                _nets[name] = cv2.dnn.readNet(path)
        return _nets[name]


def faces(img, side=640):
    """YuNet faces as rows [x, y, w, h, 5 landmark pairs, score], in full-resolution pixels."""
    import cv2
    h, w = img.shape[:2]
    s = min(1.0, side / max(h, w))
    small = cv2.resize(img, (round(w * s), round(h * s))) if s < 1 else img
    net = _net("yunet")
    with _lock:
        net.setInputSize((small.shape[1], small.shape[0]))
        _, found = net.detect(small)
    if found is None:
        return []
    found = found.copy()
    found[:, :14] /= s
    return list(found)


def embedding(img, face):
    net = _net("sface")
    with _lock:
        return net.feature(net.alignCrop(img, face))


def similarity(a, b):
    import cv2
    return float(_net("sface").match(a, b, cv2.FaceRecognizerSF_FR_COSINE))


def persons(img, threshold=0.5):
    """YOLOX person boxes [x, y, w, h] with scores."""
    import cv2
    h, w = img.shape[:2]
    s = 640 / max(h, w)
    pad = np.full((640, 640, 3), 114, np.uint8)
    pad[:int(h * s), :int(w * s)] = cv2.resize(img, (int(w * s), int(h * s)))
    net = _net("yolox")
    with _lock:
        net.setInput(cv2.dnn.blobFromImage(pad.astype(np.float32)))
        out = net.forward()[0].copy()
    grids, strides = [], []
    for stride in (8, 16, 32):
        n = 640 // stride
        xv, yv = np.meshgrid(np.arange(n), np.arange(n))
        grids.append(np.stack((xv, yv), 2).reshape(-1, 2))
        strides.append(np.full((n * n, 1), stride))
    grid, stride = np.concatenate(grids), np.concatenate(strides)
    out[:, :2] = (out[:, :2] + grid) * stride
    out[:, 2:4] = np.exp(out[:, 2:4]) * stride
    score = out[:, 4] * out[:, 5]                                   # objectness x "person" (COCO class 0)
    keep = score > threshold
    b = out[keep, :4]
    boxes = np.stack([b[:, 0] - b[:, 2] / 2, b[:, 1] - b[:, 3] / 2, b[:, 2], b[:, 3]], 1) / s
    picked = cv2.dnn.NMSBoxes(boxes.tolist(), score[keep].tolist(), threshold, 0.45)
    return [(boxes[i].tolist(), float(score[keep][i])) for i in np.array(picked).flatten()]


def reference_embedding(path):
    """The largest face of a reference photo, cached by file and modification time."""
    import cv2
    key = (path, os.path.getmtime(path))
    if key not in _refs:
        img = cv2.imread(path)
        found = faces(img) if img is not None else []
        _refs[key] = embedding(img, max(found, key=lambda f: f[2] * f[3])) if found else None
    return _refs[key]


def _inside(face, box):
    x, y = face[0] + face[2] / 2, face[1] + face[3] / 2
    return box[0] <= x <= box[0] + box[2] and box[1] <= y <= box[1] + box[3]


def analyze(image_path, references):
    """{people: count, faces: [{box, sims: {cid: similarity}}]} for one frame; references = {cid: [ref paths]}."""
    import cv2
    img = cv2.imread(image_path)
    if img is None:
        raise ValueError(f"Cannot read {image_path}")
    bodies = persons(img)
    refs = {cid: [e for e in (reference_embedding(p) for p in ps if os.path.isfile(p)) if e is not None]
            for cid, ps in references.items()}
    found = []
    for face in faces(img):
        if min(face[2], face[3]) < MIN_FACE:
            continue
        emb = embedding(img, face)
        found.append({"box": [round(float(v)) for v in face[:4]],
                      "in_body": any(_inside(face, box) for box, _ in bodies),
                      "sims": {cid: round(max(similarity(emb, r) for r in rs), 3) for cid, rs in refs.items() if rs}})
    return {"people": len(bodies) + sum(1 for f in found if not f["in_body"]), "bodies": len(bodies), "faces": found}


def resemblance(shot, measured):
    """Mean best similarity of the planned visible faces to their character references (None if nothing to compare).
    Used to prefer, among passing candidates, the one most consistent with the cast."""
    vals = []
    for s in shot["first_frame"]["subjects"]:
        if s["visibility"] in ("full_body", "upper_body"):
            sims = [f["sims"][s["id"]] for f in measured.get("faces", []) if s["id"] in f["sims"]]
            if sims:
                vals.append(max(sims))
    return round(sum(vals) / len(vals), 3) if vals else None


def compare(shot, names, measured):
    """Critical and soft issues from detector measurements against the shot's first-frame plan."""
    subjects = [s for s in shot["first_frame"]["subjects"] if s["visibility"] != "offscreen"]
    critical, soft = [], []
    need = [s["id"] for s in subjects if s["visibility"] in BODY]
    if measured["people"] < len(need):
        critical.append(f"detector: {measured['people']} people found, the plan needs at least {len(need)} "
                        f"({', '.join(names.get(c, c) for c in need)})")
    for cid in {cid for f in measured["faces"] for cid in f["sims"]}:
        sims = sorted((f["sims"].get(cid, -1) for f in measured["faces"]), reverse=True)
        if sims[0] >= PASTED:
            critical.append(f"detector: {names.get(cid, cid)}'s reference portrait appears pasted into the frame "
                            f"(similarity {sims[0]:.2f})")
        if len(sims) > 1 and sims[1] >= SAME:
            critical.append(f"detector: {names.get(cid, cid)} appears twice")
    for s in subjects:
        if s["visibility"] not in ("full_body", "upper_body") or not any(s["id"] in f["sims"] for f in measured["faces"]):
            continue                                       # face not planned visible, or no reference photo
        best = max((f["sims"].get(s["id"], -1) for f in measured["faces"]), default=-1)
        if best < RESEMBLES:
            soft.append(f"detector: no face resembles {names.get(s['id'], s['id'])} (best {best:.2f})")
    return critical, soft
