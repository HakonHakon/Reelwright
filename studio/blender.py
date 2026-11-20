"""Blender MCP client for previz (pre-visualisation) animation.

Connects to a Blender instance running the BlenderMCP server (default
http://127.0.0.1:9876) and asks it to build a simple 3D previz from a shot's
approved blockout state: cubes stand in for characters, the camera follows the
planned orbit/height/distance/lens, and the movement text is turned into
keyframed transforms. The result is rendered to an MP4 previz video that the
user can review before committing to AI take generation.

Requires Blender with the BlenderMCP addon/server running on the same machine.
See https://github.com/.... / BlenderMCP for the server side.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
import uuid
from typing import Any

import requests


class BlenderMCPError(RuntimeError):
    pass


class BlenderMCPClient:
    """Minimal JSON-RPC client over HTTP for a BlenderMCP server."""

    def __init__(self, host: str = "127.0.0.1", port: int = 9876, timeout: float = 30.0):
        self.base = f"http://{host}:{port}"
        self.timeout = timeout
        self._session = requests.Session()

    def _call(self, method: str, params: dict | None = None) -> Any:
        payload = {"jsonrpc": "2.0", "method": method, "params": params or {}, "id": str(uuid.uuid4().hex[:8])}
        try:
            r = self._session.post(self.base + "/rpc", json=payload, timeout=self.timeout)
        except requests.RequestException as exc:
            raise BlenderMCPError(f"Cannot reach Blender MCP at {self.base}: {exc}") from exc
        if r.status_code != 200:
            raise BlenderMCPError(f"Blender MCP HTTP {r.status_code}: {r.text[:300]}")
        data = r.json()
        if "error" in data:
            raise BlenderMCPError(f"Blender MCP {method}: {data['error']}")
        return data.get("result")

    def alive(self) -> bool:
        try:
            self._call("ping", {})
            return True
        except Exception:
            return False

    def list_tools(self) -> list[str]:
        return self._call("tools/list", {}) or []

    def call_tool(self, name: str, arguments: dict) -> Any:
        return self._call("tools/call", {"name": name, "arguments": arguments})


def previz_blender(
    client: BlenderMCPClient,
    shot: dict,
    bible: dict,
    out_path: str,
    fps: int = 24,
    seconds: float = 6.0,
) -> dict:
    """Ask Blender MCP to render a previz animation for one shot.

    Returns {"frames": int, "seconds": float, "file": out_path, "blend": str|None}.
    """
    pv = shot.get("previz") or {}
    camera = pv.get("camera") or {"azimuth": 0, "height": 5.5, "distance": 13, "lens": 50}
    entities = pv.get("entities") or []
    movement = (pv.get("movement") or "").strip()
    framing = (shot.get("framing") or "medium").lower()

    # Build character cubes from the shot cast.
    characters = []
    for ch in shot.get("characters", []) or []:
        cid = ch.get("id")
        c = bible["characters"].get(cid) or {}
        ent = next((e for e in entities if e.get("id") == cid), None)
        characters.append({
            "id": cid,
            "name": c.get("name", cid),
            "position": (ent or {}).get("position") or [0, 0, 1.5],
            "rotation": (ent or {}).get("rotation") or [0, 0, 0],
            "scale": (ent or {}).get("scale") or 1.0,
        })

    # Props from entities marked kind=="prop".
    props = [e for e in entities if e.get("kind") == "prop"]

    payload = {
        "shot_id": shot.get("id"),
        "scene_id": shot.get("scene_id"),
        "framing": framing,
        "camera": {
            "azimuth": float(camera.get("azimuth", 0)),
            "height": float(camera.get("height", 5.5)),
            "distance": float(camera.get("distance", 13)),
            "lens": float(camera.get("lens", 50)),
        },
        "movement": movement,
        "seconds": float(seconds),
        "fps": int(fps),
        "characters": characters,
        "props": props,
        "out_path": out_path,
    }

    # Prefer a dedicated BlenderMCP tool if available; fall back to run_script.
    try:
        tools = client.list_tools()
    except Exception:
        tools = []

    tool_names = {t.get("name") if isinstance(t, dict) else t for t in tools}

    if "blender_previz" in tool_names:
        result = client.call_tool("blender_previz", payload)
    elif "run_script" in tool_names:
        script = _previz_script(payload)
        result = client.call_tool("run_script", {"script": script})
    else:
        # Last resort: POST the whole payload as a custom method.
        result = client._call("blender.previz", payload)

    return {
        "frames": int(seconds * fps),
        "seconds": float(seconds),
        "file": out_path,
        "blend": (result or {}).get("blend") if isinstance(result, dict) else None,
    }


def _previz_script(payload: dict) -> str:
    """Self-contained Blender Python script that creates and renders the previz."""
    import base64
    return (
        "import bpy, json, math, os\n"
        "payload = json.loads('''" + base64.b64encode(json.dumps(payload).encode()).decode() + "''')\n"
        "bpy.ops.object.select_all(action='SELECT')\n"
        "bpy.ops.object.delete()\n"
        "scene = bpy.context.scene\n"
        "scene.render.fps = payload['fps']\n"
        "scene.frame_end = int(payload['seconds'] * payload['fps'])\n"
        "scene.render.resolution_x = 1024\n"
        "scene.render.resolution_y = 576\n"
        "scene.render.image_settings.file_format = 'FFMPEG'\n"
        "scene.render.ffmpeg.format = 'MPEG4'\n"
        "scene.render.filepath = payload['out_path']\n"
        "\n"
        "# Ground plane\n"
        "bpy.ops.mesh.primitive_plane_add(size=60, location=(0, 0, 0))\n"
        "ground = bpy.context.active_object\n"
        "ground.name = 'ground'\n"
        "mat = bpy.data.materials.new('ground_mat')\n"
        "mat.use_nodes = True\n"
        "mat.node_tree.nodes['Principled BSDF'].inputs['Base Color'].default_value = (0.125, 0.15, 0.145, 1)\n"
        "ground.data.materials.append(mat)\n"
        "\n"
        "# Camera\n"
        "cam = bpy.data.cameras.new('previz_cam')\n"
        "cam.lens = payload['camera']['lens']\n"
        "cam_obj = bpy.data.objects.new('previz_cam', cam)\n"
        "bpy.context.collection.objects.link(cam_obj)\n"
        "bpy.context.scene.camera = cam_obj\n"
        "az = math.radians(payload['camera']['azimuth'])\n"
        "h = payload['camera']['height']\n"
        "d = payload['camera']['distance']\n"
        "cam_obj.location = (d * math.sin(az), h, d * math.cos(az))\n"
        "cam_obj.rotation_euler = (math.radians(60), 0, -az)\n"
        "cam_obj.keyframe_insert(data_path='location', frame=1)\n"
        "cam_obj.keyframe_insert(data_path='rotation_euler', frame=1)\n"
        "\n"
        "# Characters as cubes\n"
        "colors = [(0.78,0.53,0.38,1),(0.49,0.62,0.65,1),(0.78,0.72,0.5,1),(0.57,0.52,0.66,1),(0.47,0.61,0.45,1),(0.69,0.42,0.4,1)]\n"
        "for i, ch in enumerate(payload['characters']):\n"
        "    bpy.ops.mesh.primitive_cube_add(size=1, location=tuple(ch['position']))\n"
        "    ob = bpy.context.active_object\n"
        "    ob.name = f\"char_{ch['id']}\"\n"
        "    ob.rotation_euler = tuple(math.radians(v) for v in ch['rotation'])\n"
        "    ob.scale = (ch['scale'], ch['scale'], ch['scale'])\n"
        "    m = bpy.data.materials.new(f\"mat_{ch['id']}\")\n"
        "    m.use_nodes = True\n"
        "    m.node_tree.nodes['Principled BSDF'].inputs['Base Color'].default_value = colors[i % len(colors)]\n"
        "    ob.data.materials.append(m)\n"
        "    # keyframe start position\n"
        "    ob.keyframe_insert(data_path='location', frame=1)\n"
        "    ob.keyframe_insert(data_path='rotation_euler', frame=1)\n"
        "    # movement: translate along +X over the shot duration (placeholder for movement text)\n"
        "    mv = payload.get('movement', '')\n"
        "    end_frame = scene.frame_end\n"
        "    if 'walk' in mv.lower() or 'move' in mv.lower():\n"
        "        ob.location = (ch['position'][0] + 2, ch['position'][1], ch['position'][2])\n"
        "    elif 'turn' in mv.lower():\n"
        "        ob.rotation_euler = (ch['rotation'][0], ch['rotation'][1] + math.radians(90), ch['rotation'][2])\n"
        "    ob.keyframe_insert(data_path='location', frame=end_frame)\n"
        "    ob.keyframe_insert(data_path='rotation_euler', frame=end_frame)\n"
        "\n"
        "# Props\n"
        "for pr in payload.get('props', []):\n"
        "    bpy.ops.mesh.primitive_cube_add(size=1, location=tuple(pr.get('position', [0, 0, 0.5])))\n"
        "    pob = bpy.context.active_object\n"
        "    pob.name = f\"prop_{pr.get('id')}\"\n"
        "    pm = bpy.data.materials.new(f\"mat_prop_{pr.get('id')}\")\n"
        "    pm.use_nodes = True\n"
        "    pm.node_tree.nodes['Principled BSDF'].inputs['Base Color'].default_value = (0.6, 0.56, 0.5, 1)\n"
        "    pob.data.materials.append(pm)\n"
        "\n"
        "# Lighting\n"
        "bpy.ops.object.light_add(type='SUN', location=(5, 10, 8))\n"
        "bpy.context.active_object.data.energy = 3\n"
        "bpy.ops.object.light_add(type='AREA', location=(-5, 5, 4))\n"
        "bpy.context.active_object.data.energy = 800\n"
        "\n"
        "bpy.ops.render.render(animation=True)\n"
        "print('PREVIZ_DONE', payload['out_path'])\n"
    )
