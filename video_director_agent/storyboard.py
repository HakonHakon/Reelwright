#!/usr/bin/env python3
"""
Storyboard Reviewer -- Review keyframe images for each scene.
Approve, reject with notes, or request regeneration before going to video production.

Usage:
    python storyboard.py <project_name>
"""

import json
import os
import sys
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
from PIL import Image, ImageTk

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import project_state_path


def load_state(project_name: str) -> dict:
    path = project_state_path(project_name)
    with open(path) as f:
        return json.load(f)


def save_state(state: dict):
    path = project_state_path(state["project_name"])
    with open(path, "w") as f:
        json.dump(state, f, indent=2)


def open_with_system_default(path: str):
    """Open a file in the system default app across platforms."""
    if os.name == "nt":
        os.startfile(path)
        return

    import subprocess
    opener = "open" if sys.platform == "darwin" else "xdg-open"
    subprocess.Popen([opener, path])


class StoryboardGUI:
    def __init__(self, project_name: str):
        self.state = load_state(project_name)
        self.scenes = self.state["scenes"]
        self.characters = self.state.get("characters", {})
        self.current_idx = 0

        # Find first unreviewed scene
        for i, s in enumerate(self.scenes):
            if not s.get("keyframe_approved"):
                self.current_idx = i
                break

        self.root = tk.Tk()
        self.root.title(f"Storyboard Review -- {project_name}")
        self.root.geometry("1200x800")
        self.root.configure(bg="#1e1e2e")
        self._build_ui()
        self._show_scene()

    def _build_ui(self):
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TLabel", background="#1e1e2e", foreground="#cdd6f4", font=("Segoe UI", 10))
        style.configure("TButton", font=("Segoe UI", 10), padding=5)
        style.configure("Header.TLabel", font=("Segoe UI", 14, "bold"), foreground="#89b4fa")
        style.configure("Pass.TLabel", foreground="#a6e3a1")
        style.configure("Fail.TLabel", foreground="#f38ba8")
        style.configure("TFrame", background="#1e1e2e")

        main = ttk.Frame(self.root, padding=10)
        main.pack(fill=tk.BOTH, expand=True)

        # Header
        header = ttk.Frame(main)
        header.pack(fill=tk.X)
        self.header_label = ttk.Label(header, style="Header.TLabel")
        self.header_label.pack(side=tk.LEFT)
        self.progress_label = ttk.Label(header, foreground="#a6e3a1")
        self.progress_label.pack(side=tk.RIGHT)

        # Scene info
        self.desc_label = ttk.Label(main, wraplength=1150, foreground="#f9e2af")
        self.desc_label.pack(anchor="w", pady=(8, 2))
        self.dialogue_label = ttk.Label(main, wraplength=1150, foreground="#cba6f7")
        self.dialogue_label.pack(anchor="w", pady=(0, 8))

        # Keyframe candidates grid
        self.grid_frame = ttk.Frame(main)
        self.grid_frame.pack(fill=tk.BOTH, expand=True)

        # Rejection notes
        notes_frame = ttk.Frame(main)
        notes_frame.pack(fill=tk.X, pady=(8, 4))
        ttk.Label(notes_frame, text="Rejection notes (why it's wrong):").pack(anchor="w")
        self.notes_text = scrolledtext.ScrolledText(
            notes_frame, height=3, font=("Segoe UI", 10),
            bg="#313244", fg="#cdd6f4", insertbackground="#cdd6f4",
            wrap=tk.WORD, relief=tk.FLAT,
        )
        self.notes_text.pack(fill=tk.X)

        # Navigation
        nav = ttk.Frame(main)
        nav.pack(fill=tk.X, pady=(8, 0))
        self.prev_btn = ttk.Button(nav, text="< Prev", command=self._prev)
        self.prev_btn.pack(side=tk.LEFT)
        self.regen_btn = ttk.Button(nav, text="Regenerate Rejected", command=self._regenerate)
        self.regen_btn.pack(side=tk.LEFT, padx=10)
        self.proceed_btn = ttk.Button(nav, text="Proceed to Video Production",
                                       command=self._proceed)
        self.proceed_btn.pack(side=tk.LEFT, padx=10)
        self.status_label = ttk.Label(nav, text="", foreground="#a6e3a1")
        self.status_label.pack(side=tk.LEFT, padx=10)
        self.next_btn = ttk.Button(nav, text="Next >", command=self._next)
        self.next_btn.pack(side=tk.RIGHT)

    def _show_scene(self):
        scene = self.scenes[self.current_idx]
        scene_num = scene["scene_number"]
        total = len(self.scenes)
        approved = sum(1 for s in self.scenes if s.get("keyframe_approved"))

        self.header_label.configure(
            text=f"Scene {scene_num}/{total} -- {scene.get('shot_type', '')} ({scene.get('duration_seconds', '?')}s)"
        )
        self.progress_label.configure(text=f"{approved}/{total} approved")
        self.desc_label.configure(text=scene.get("description", ""))

        dlg = scene.get("dialogue", "")
        self.dialogue_label.configure(text=f"Dialogue: {dlg[:300]}" if dlg else "(no dialogue)")

        # Clear grid
        for w in self.grid_frame.winfo_children():
            w.destroy()

        # Show keyframe candidates
        candidates = scene.get("keyframe_candidates", [])
        if not candidates:
            ttk.Label(self.grid_frame, text="No keyframes generated yet",
                      foreground="#f38ba8").pack()
            return

        for cand in candidates:
            if cand.get("status") != "generated":
                continue

            frame = ttk.Frame(self.grid_frame)
            frame.pack(side=tk.LEFT, padx=6, pady=4)

            cand_num = cand["candidate"]
            cand_path = cand["path"]
            ev = cand.get("eval", {})
            verdict = ev.get("verdict", "?")
            is_selected = scene.get("selected_keyframe") == cand_path

            # Thumbnail
            try:
                img = Image.open(cand_path)
                img.thumbnail((280, 160), Image.LANCZOS)
                tk_img = ImageTk.PhotoImage(img)
                lbl = ttk.Label(frame, image=tk_img)
                lbl.image = tk_img
                lbl.pack()
            except Exception:
                ttk.Label(frame, text="[no preview]").pack()

            # Eval info
            char_acc = ev.get("character_accuracy", "?")
            verdict_style = "Pass.TLabel" if verdict == "PASS" else "Fail.TLabel"
            info_text = f"#{cand_num} | AI: {verdict} | Char: {char_acc}"
            if is_selected:
                info_text += " | SELECTED"
            ttk.Label(frame, text=info_text, style=verdict_style).pack()

            # Fail reason
            fail = ev.get("fail_reason")
            if fail:
                ttk.Label(frame, text=fail[:80], foreground="#6c7086",
                          wraplength=270, font=("Segoe UI", 8)).pack()

            # Buttons
            btn_f = ttk.Frame(frame)
            btn_f.pack()
            ttk.Button(btn_f, text="Select" if not is_selected else "Selected",
                       command=lambda p=cand_path, sn=scene_num: self._select(sn, p)
                       ).pack(side=tk.LEFT, padx=2)
            ttk.Button(btn_f, text="Full Size",
                       command=lambda p=cand_path: open_with_system_default(p)
                       ).pack(side=tk.LEFT, padx=2)

        # Nav state
        self.prev_btn.configure(state=tk.NORMAL if self.current_idx > 0 else tk.DISABLED)
        self.next_btn.configure(state=tk.NORMAL if self.current_idx < total - 1 else tk.DISABLED)
        self.proceed_btn.configure(state=tk.NORMAL if approved == total else tk.DISABLED)

    def _select(self, scene_num: int, path: str):
        for s in self.scenes:
            if s["scene_number"] == scene_num:
                s["selected_keyframe"] = path
                s["keyframe_approved"] = True
                break
        save_state(self.state)
        self._show_scene()

        # Auto-advance
        for i, s in enumerate(self.scenes):
            if not s.get("keyframe_approved"):
                self.current_idx = i
                self.root.after(300, self._show_scene)
                return

    def _prev(self):
        if self.current_idx > 0:
            self.current_idx -= 1
            self._show_scene()

    def _next(self):
        if self.current_idx < len(self.scenes) - 1:
            self.current_idx += 1
            self._show_scene()

    def _regenerate(self):
        """Regenerate keyframes for scenes where user rejected all options."""
        notes = self.notes_text.get("1.0", tk.END).strip()
        scene = self.scenes[self.current_idx]

        if not notes:
            self.status_label.configure(text="Enter rejection notes first!")
            return

        scene["rejection_notes"] = notes
        scene["keyframe_approved"] = False
        scene.pop("selected_keyframe", None)
        scene.pop("keyframe_candidates", None)
        save_state(self.state)

        self.status_label.configure(
            text=f"Scene {scene['scene_number']} marked for regen. Re-run the agent to regenerate."
        )
        self.notes_text.delete("1.0", tk.END)

    def _proceed(self):
        """Mark storyboard as complete, ready for video production."""
        self.state["storyboard_approved"] = True
        save_state(self.state)
        messagebox.showinfo("Storyboard Approved",
                            "All keyframes approved! Run the agent again to start video production.")
        self.root.destroy()

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python storyboard.py <project_name>")
        sys.exit(1)
    app = StoryboardGUI(sys.argv[1])
    app.run()
