#!/usr/bin/env python3
"""
Take Reviewer — Watch takes for each scene, pick your favorite, assemble the final film.

Usage:
    python reviewer.py <project_name>
    python reviewer.py  (lists available projects)
"""

import json
import os
import subprocess
import sys
import tkinter as tk
from tkinter import ttk, messagebox
from PIL import Image, ImageTk
import cv2

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config
from config import FFMPEG_PATH, get_output_root, project_state_path
from assembler import concat_scenes


def open_with_system_default(path: str):
    """Open a file in the system default app across platforms."""
    if os.name == "nt":
        os.startfile(path)
        return

    import subprocess
    opener = "open" if sys.platform == "darwin" else "xdg-open"
    subprocess.Popen([opener, path])


def load_state(project_name: str) -> dict:
    path = project_state_path(project_name)
    with open(path) as f:
        return json.load(f)


def save_state(state: dict):
    path = project_state_path(state["project_name"])
    with open(path, "w") as f:
        json.dump(state, f, indent=2)


def get_thumbnail(video_path: str, time_sec: float = 1.0, size: tuple = (384, 216)) -> Image.Image:
    """Extract a single frame from a video as a PIL Image."""
    cap = cv2.VideoCapture(video_path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 24
    cap.set(cv2.CAP_PROP_POS_FRAMES, int(time_sec * fps))
    ret, frame = cap.read()
    cap.release()
    if not ret:
        # Fallback: try first frame
        cap = cv2.VideoCapture(video_path)
        ret, frame = cap.read()
        cap.release()
    if not ret:
        img = Image.new("RGB", size, (30, 30, 30))
        return img
    frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    img = Image.fromarray(frame_rgb)
    img.thumbnail(size, Image.LANCZOS)
    return img


class ReviewerGUI:
    def __init__(self, project_name: str):
        self.state = load_state(project_name)
        self.scenes = self.state["scenes"]
        self.current_scene_idx = 0
        self.selections = {}  # scene_number -> take path

        # Skip to first unreviewed scene
        for i, s in enumerate(self.scenes):
            if not s.get("selected_take"):
                self.current_scene_idx = i
                break
        else:
            # All reviewed — start at 0 for re-review
            self.current_scene_idx = 0

        # Load existing selections
        for s in self.scenes:
            if s.get("selected_take"):
                self.selections[s["scene_number"]] = s["selected_take"]

        self.root = tk.Tk()
        self.root.title(f"Take Reviewer — {project_name}")
        self.root.geometry("1100x750")
        self.root.configure(bg="#1c1c1c")
        self._build_ui()
        self._show_scene()

    def _build_ui(self):
        try:
            import sv_ttk
            sv_ttk.set_theme("dark")
        except ImportError:
            ttk.Style().theme_use("clam")

        style = ttk.Style()
        style.configure("TLabel", font=("Segoe UI", 11))
        style.configure("TButton", font=("Segoe UI", 10), padding=6)
        style.configure("Header.TLabel", font=("Segoe UI", 14, "bold"), foreground="#89b4fa")
        style.configure("Scene.TLabel", font=("Segoe UI", 11), foreground="#f9e2af", wraplength=1050)
        style.configure("Selected.TButton", font=("Segoe UI", 10, "bold"))

        main = ttk.Frame(self.root, padding=15)
        main.pack(fill=tk.BOTH, expand=True)

        # Header with scene counter
        header_frame = ttk.Frame(main)
        header_frame.pack(fill=tk.X)
        self.header_label = ttk.Label(header_frame, text="", style="Header.TLabel")
        self.header_label.pack(side=tk.LEFT)
        self.progress_label = ttk.Label(header_frame, text="", foreground="#a6e3a1")
        self.progress_label.pack(side=tk.RIGHT)

        # Scene description
        self.desc_label = ttk.Label(main, text="", style="Scene.TLabel")
        self.desc_label.pack(anchor="w", pady=(8, 4))

        # Dialogue
        self.dialogue_label = ttk.Label(main, text="", foreground="#cba6f7", wraplength=1050)
        self.dialogue_label.pack(anchor="w", pady=(0, 10))

        # Takes container
        self.takes_frame = ttk.Frame(main)
        self.takes_frame.pack(fill=tk.BOTH, expand=True)

        # Redo controls
        redo_frame = ttk.Frame(main)
        redo_frame.pack(fill=tk.X, pady=(8, 0))

        self.redo_btn = ttk.Button(redo_frame, text="Regenerate Takes",
                                    command=self._redo_takes)
        self.redo_btn.pack(side=tk.LEFT)

        ttk.Label(redo_frame, text="  New takes:").pack(side=tk.LEFT, padx=(8, 4))
        self.redo_count_var = tk.IntVar(value=3)
        ttk.Spinbox(redo_frame, from_=1, to=10, textvariable=self.redo_count_var,
                     width=3, font=("Segoe UI", 11)).pack(side=tk.LEFT)

        self.clear_old_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(redo_frame, text="Replace old takes",
                        variable=self.clear_old_var).pack(side=tk.LEFT, padx=(12, 0))

        self.redo_status = ttk.Label(redo_frame, text="", foreground="#f9e2af")
        self.redo_status.pack(side=tk.LEFT, padx=12)

        # Navigation
        nav_frame = ttk.Frame(main)
        nav_frame.pack(fill=tk.X, pady=(10, 0))

        self.prev_btn = ttk.Button(nav_frame, text="< Prev Scene", command=self._prev_scene)
        self.prev_btn.pack(side=tk.LEFT)

        self.assemble_btn = ttk.Button(nav_frame, text="Assemble Final Film", command=self._assemble)
        self.assemble_btn.pack(side=tk.LEFT, padx=20)

        self.status_label = ttk.Label(nav_frame, text="", foreground="#a6e3a1")
        self.status_label.pack(side=tk.LEFT, padx=10)

        self.next_btn = ttk.Button(nav_frame, text="Next Scene >", command=self._next_scene)
        self.next_btn.pack(side=tk.RIGHT)

    def _show_scene(self):
        scene = self.scenes[self.current_scene_idx]
        scene_num = scene["scene_number"]
        total = len(self.scenes)
        reviewed = len(self.selections)

        self.header_label.configure(
            text=f"Scene {scene_num}/{total} — {scene.get('shot_type', '')} ({scene['duration_seconds']}s)"
        )
        self.progress_label.configure(text=f"{reviewed}/{total} selected")
        self.desc_label.configure(text=scene["description"])

        dialogue = scene.get("dialogue", "")
        if dialogue:
            self.dialogue_label.configure(text=f'Dialogue: {dialogue[:300]}')
        else:
            self.dialogue_label.configure(text="(no dialogue)")

        # Clear old takes
        for widget in self.takes_frame.winfo_children():
            widget.destroy()

        # Get takes for this scene
        takes = [t for t in scene.get("takes", []) if t.get("status") == "generated"]
        if not takes:
            ttk.Label(self.takes_frame, text="No takes generated for this scene",
                      foreground="#f38ba8").pack()
            return

        selected_path = self.selections.get(scene_num)

        for t in takes:
            take_num = t["take"]
            take_path = t["path"]
            is_selected = (take_path == selected_path)

            # Frame for each take
            take_frame = ttk.Frame(self.takes_frame)
            take_frame.pack(side=tk.LEFT, padx=8, pady=4, fill=tk.Y)

            # Thumbnail
            try:
                pil_img = get_thumbnail(take_path, time_sec=2.0, size=(320, 180))
                tk_img = ImageTk.PhotoImage(pil_img)
                thumb_label = ttk.Label(take_frame, image=tk_img)
                thumb_label.image = tk_img  # Keep reference
                thumb_label.pack()
            except Exception:
                ttk.Label(take_frame, text="[no preview]", foreground="#6c7086").pack()

            # Take label
            label_text = f"Take {take_num}"
            if is_selected:
                label_text += " (SELECTED)"
            ttk.Label(take_frame, text=label_text,
                      foreground="#a6e3a1" if is_selected else "#cdd6f4").pack(pady=(4, 2))

            # Buttons
            btn_frame = ttk.Frame(take_frame)
            btn_frame.pack()

            play_btn = ttk.Button(btn_frame, text="Play",
                                  command=lambda p=take_path: self._play(p))
            play_btn.pack(side=tk.LEFT, padx=2)

            select_btn = ttk.Button(btn_frame, text="Select" if not is_selected else "Selected",
                                    command=lambda sn=scene_num, p=take_path: self._select(sn, p))
            select_btn.pack(side=tk.LEFT, padx=2)

        # Update nav buttons
        self.prev_btn.configure(state=tk.NORMAL if self.current_scene_idx > 0 else tk.DISABLED)
        self.next_btn.configure(state=tk.NORMAL if self.current_scene_idx < total - 1 else tk.DISABLED)
        self.assemble_btn.configure(state=tk.NORMAL if reviewed == total else tk.DISABLED)

    def _play(self, path: str):
        """Open video in system default player."""
        open_with_system_default(path)

    def _select(self, scene_num: int, path: str):
        """Select a take for a scene."""
        self.selections[scene_num] = path

        # Save to state
        for s in self.scenes:
            if s["scene_number"] == scene_num:
                s["selected_take"] = path
                s["status"] = "approved"
                break
        save_state(self.state)

        # Refresh display
        self._show_scene()
        self.status_label.configure(text=f"Take selected for scene {scene_num}")

        # Auto-advance to next unreviewed scene
        for i, s in enumerate(self.scenes):
            if not s.get("selected_take"):
                self.current_scene_idx = i
                self.root.after(500, self._show_scene)
                return

    def _redo_takes(self):
        """Reject all takes for current scene and generate new ones."""
        scene = self.scenes[self.current_scene_idx]
        scene_num = scene["scene_number"]
        redo_count = self.redo_count_var.get()
        clear_old = self.clear_old_var.get()

        action = "Replace all takes with" if clear_old else "Add"
        if messagebox.askyesno("Redo Takes",
                                f"{action} {redo_count} new take(s) for scene {scene_num}?\n\n"
                                f"This will run video generation in the background."):
            # Clear old selection
            scene.pop("selected_take", None)
            scene.pop("status", None)
            self.selections.pop(scene_num, None)

            # Optionally clear old takes
            if clear_old:
                scene["takes"] = []

            # Mark scene for redo
            scene["takes_done"] = False
            scene["redo_takes"] = redo_count
            save_state(self.state)

            # Disable redo button during generation
            self.redo_btn.configure(state=tk.DISABLED)
            self.redo_status.configure(text=f"Generating {redo_count} new take(s) for scene {scene_num}...")

            import threading
            t = threading.Thread(
                target=self._generate_redo_takes,
                args=(scene, redo_count),
                daemon=True,
            )
            t.start()

    def _generate_redo_takes(self, scene, count):
        """Generate new takes for a scene in a background thread."""
        import random
        import shutil
        import logging
        import time
        from config import LTX_FPS, COMFYUI_LAUNCHER, COMFYUI_STARTUP_TIMEOUT
        from comfyui_client import (
            ComfyUIClient, load_workflow_template, build_workflow, calc_frames,
            load_i2v_template, build_i2v_workflow,
        )

        log = logging.getLogger("reviewer")
        scene_num = scene["scene_number"]

        try:
            client = ComfyUIClient()

            # Check if ComfyUI is running, auto-launch if not
            if not client.check_alive():
                self.root.after(0, self.redo_status.configure,
                                {"text": "ComfyUI not running — launching..."})
                import subprocess as _sp
                if not COMFYUI_LAUNCHER or not os.path.isfile(COMFYUI_LAUNCHER):
                    raise RuntimeError(
                        "ComfyUI is not running and the configured launcher does not exist. "
                        "Start Comfy Desktop manually, then retry."
                    )
                kwargs = {"cwd": os.path.dirname(COMFYUI_LAUNCHER)}
                if os.name == "nt":
                    kwargs["creationflags"] = _sp.CREATE_NEW_PROCESS_GROUP
                _sp.Popen(COMFYUI_LAUNCHER, **kwargs)
                deadline = time.time() + COMFYUI_STARTUP_TIMEOUT
                while time.time() < deadline:
                    if client.check_alive():
                        break
                    time.sleep(3)
                else:
                    raise RuntimeError("ComfyUI did not start in time")

            client.connect()
            template = load_workflow_template()
            i2v_template = load_i2v_template()

            current_project_dir = config.project_dir(self.state["project_name"])
            scenes_dir = os.path.join(current_project_dir, "scenes")
            os.makedirs(scenes_dir, exist_ok=True)

            frames = calc_frames(scene["duration_seconds"], LTX_FPS)

            # Check if this scene has an approved keyframe for i2v
            keyframe_path = scene.get("selected_keyframe")
            use_i2v = i2v_template and keyframe_path and os.path.exists(keyframe_path)
            uploaded_kf_name = None
            if use_i2v:
                try:
                    uploaded_kf_name = client.upload_image(keyframe_path)
                    log.info("Using I2V mode with keyframe: %s", os.path.basename(keyframe_path))
                except Exception as e:
                    log.warning("Failed to upload keyframe: %s — falling back to T2V", e)
                    use_i2v = False

            # Start numbering after existing takes
            existing_count = len(scene.get("takes", []))
            new_takes = []

            for i in range(1, count + 1):
                take_num = existing_count + i
                seed = random.randint(0, 2**32 - 1)

                self.root.after(0, self.redo_status.configure,
                                {"text": f"Scene {scene_num}: take {i}/{count} ({'i2v' if use_i2v else 't2v'})..."})

                if use_i2v:
                    workflow = build_i2v_workflow(
                        i2v_template, scene["ltx_prompt"], frames, seed, uploaded_kf_name
                    )
                else:
                    workflow = build_workflow(template, scene["ltx_prompt"], frames, seed)

                try:
                    prompt_id = client.queue_prompt(workflow)
                    history = client.wait_for_completion(prompt_id, timeout=900)
                    raw_output = client.get_output_path(history)
                except Exception as e:
                    log.error("Redo take %d failed: %s", take_num, e)
                    new_takes.append({"take": take_num, "status": "failed", "error": str(e)})
                    continue

                take_filename = f"scene_{scene_num:03d}_take_{take_num}.mp4"
                take_path = os.path.join(scenes_dir, take_filename)
                shutil.copy2(raw_output, take_path)

                new_takes.append({
                    "take": take_num,
                    "status": "generated",
                    "path": take_path,
                    "seed": seed,
                })

            # Append new takes to scene
            scene.setdefault("takes", []).extend(new_takes)
            scene["takes_done"] = True
            scene.pop("redo_takes", None)
            save_state(self.state)

            client.disconnect()

            generated = sum(1 for t in new_takes if t["status"] == "generated")
            self.root.after(0, self.redo_status.configure,
                            {"text": f"Done! {generated} new take(s) for scene {scene_num}"})
            self.root.after(0, self._show_scene)

        except Exception as e:
            self.root.after(0, self.redo_status.configure,
                            {"text": f"Error: {e}"})
            log.error("Redo failed: %s", e, exc_info=True)
        finally:
            self.root.after(0, self.redo_btn.configure, {"state": tk.NORMAL})

    def _prev_scene(self):
        if self.current_scene_idx > 0:
            self.current_scene_idx -= 1
            self._show_scene()

    def _next_scene(self):
        if self.current_scene_idx < len(self.scenes) - 1:
            self.current_scene_idx += 1
            self._show_scene()

    def _assemble(self):
        """Assemble selected takes into final film."""
        # Collect selected paths in scene order
        paths = []
        for s in self.scenes:
            path = s.get("selected_take")
            if not path:
                messagebox.showerror("Missing Selection",
                                     f"Scene {s['scene_number']} has no selected take!")
                return
            if not os.path.exists(path):
                messagebox.showerror("Missing File",
                                     f"File not found: {path}")
                return
            paths.append(path)

        final_dir = config.project_dir(self.state["project_name"])
        final_path = os.path.join(final_dir, "final.mp4")

        self.status_label.configure(text="Assembling final film...")
        self.root.update()

        try:
            concat_scenes(paths, final_path)
            self.state["final_path"] = final_path
            from datetime import datetime
            self.state["completed_at"] = datetime.now().isoformat()
            save_state(self.state)

            self.status_label.configure(text=f"Done! {final_path}")
            if messagebox.askyesno("Assembly Complete",
                                   f"Final film saved to:\n{final_path}\n\nPlay it now?"):
                open_with_system_default(final_path)

        except Exception as e:
            messagebox.showerror("Assembly Failed", str(e))
            self.status_label.configure(text=f"Assembly failed: {e}")

    def run(self):
        self.root.mainloop()


def list_projects():
    output_dir = get_output_root()
    if not os.path.exists(output_dir):
        print("No projects found.")
        return
    projects = []
    for name in os.listdir(output_dir):
        state_path = os.path.join(output_dir, name, "state.json")
        if os.path.exists(state_path):
            with open(state_path) as f:
                state = json.load(f)
            total = len(state.get("scenes", []))
            has_takes = any(s.get("takes_done") for s in state.get("scenes", []))
            reviewed = sum(1 for s in state.get("scenes", []) if s.get("selected_take"))
            status = "ready for review" if has_takes else "generating"
            if reviewed == total and total > 0:
                status = "fully reviewed"
            projects.append((name, total, reviewed, status))

    if not projects:
        print("No projects found.")
        return

    print("Available projects:")
    for name, total, reviewed, status in projects:
        print(f"  {name}: {total} scenes, {reviewed}/{total} reviewed [{status}]")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        list_projects()
        print("\nUsage: python reviewer.py <project_name>")
        sys.exit(0)

    project_name = sys.argv[1]
    try:
        app = ReviewerGUI(project_name)
        app.run()
    except FileNotFoundError:
        print(f"Project '{project_name}' not found.")
        list_projects()
        sys.exit(1)
