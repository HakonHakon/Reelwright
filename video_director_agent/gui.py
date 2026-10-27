#!/usr/bin/env python3
"""Simple GUI launcher for the Video Director Agent."""

import os
import sys
import re
import threading
import tkinter as tk
from tkinter import ttk, scrolledtext

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def slugify(text: str) -> str:
    slug = re.sub(r"[^\w\s-]", "", text.lower())
    slug = re.sub(r"[\s-]+", "_", slug)
    return slug[:50].strip("_")


def _restart_ollama(log):
    """Kill and restart Ollama to ensure clean state."""
    import subprocess
    import time
    from llm_client import is_ollama_provider, provider_label

    if not is_ollama_provider():
        log.info("Skipping Ollama restart because LLM provider is %s.", provider_label())
        return

    log.info("Restarting Ollama...")
    # Kill any running Ollama processes
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/f", "/im", "ollama.exe"],
                           capture_output=True, timeout=10)
            subprocess.run(["taskkill", "/f", "/im", "ollama_llama_server.exe"],
                           capture_output=True, timeout=10)
        else:
            subprocess.run(["pkill", "-f", "ollama"], capture_output=True, timeout=10)
        time.sleep(2)
    except Exception as e:
        log.warning("Could not kill Ollama: %s (may not have been running)", e)

    # Start Ollama serve in background
    try:
        kwargs = {
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
        }
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        subprocess.Popen(["ollama", "serve"], **kwargs)
    except FileNotFoundError:
        log.warning("ollama not found on PATH, skipping restart")
        return

    # Wait for Ollama to be ready
    import urllib.request
    deadline = time.time() + 30
    while time.time() < deadline:
        try:
            with urllib.request.urlopen("http://localhost:11434/api/tags", timeout=2) as r:
                if r.status == 200:
                    log.info("Ollama restarted and ready.")
                    return
        except Exception:
            time.sleep(1)
    log.warning("Ollama did not respond within 30s after restart")


class DirectorGUI:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("KupkaProd Cinema Pipeline — Powered by LTX 2.3")
        self.root.geometry("800x780")
        self.root.configure(bg="#1c1c1c")
        self.running = False
        self.thread = None
        self._check_first_run()
        self._build_ui()

    def _check_first_run(self):
        settings_path = os.path.join(os.path.dirname(__file__), "user_settings.json")
        if not os.path.exists(settings_path):
            self._show_welcome_wizard()

    def _show_setup(self):
        """Settings dialog (accessible anytime via Settings button)."""
        self._show_welcome_wizard(skip_to_settings=True)

    def _show_welcome_wizard(self, skip_to_settings=False):
        """Multi-page first-run wizard with setup instructions."""
        from tkinter import filedialog
        from config import save_user_settings, _DEFAULTS, _get

        wiz = tk.Toplevel(self.root)
        wiz.title("KupkaProd Cinema Pipeline — Setup Wizard")
        wiz.geometry("700x550")
        wiz.configure(bg="#1c1c1c")
        wiz.grab_set()

        style = ttk.Style()
        style.configure("Wiz.TLabel", background="#1c1c1c", foreground="#cdd6f4",
                         font=("Segoe UI", 11), wraplength=640)
        style.configure("WizTitle.TLabel", background="#1c1c1c", foreground="#89b4fa",
                         font=("Segoe UI", 16, "bold"))
        style.configure("WizStep.TLabel", background="#1c1c1c", foreground="#f9e2af",
                         font=("Segoe UI", 12, "bold"))

        # Page content
        pages = []

        # --- PAGE 1: Welcome ---
        def page_welcome(frame):
            ttk.Label(frame, text="Welcome to KupkaProd Cinema Pipeline", style="WizTitle.TLabel").pack(anchor="w", pady=(0, 15))
            ttk.Label(frame, text=(
                "This tool turns text prompts and screenplays into fully produced videos "
                "using AI — entirely on your local machine.\n\n"
                "Before you start, you'll need a few things set up. "
                "This wizard will walk you through each step."
            ), style="Wiz.TLabel").pack(anchor="w")

        # --- PAGE 2: Prerequisites ---
        def page_prereqs(frame):
            ttk.Label(frame, text="Step 1: Prerequisites", style="WizStep.TLabel").pack(anchor="w", pady=(0, 10))
            ttk.Label(frame, text=(
                "Make sure you have the following installed:\n\n"
                "1. ComfyUI (v0.18 or newer)\n"
                "   Download: github.com/comfyanonymous/ComfyUI\n\n"
                "2. Ollama (for the AI planning/evaluation)\n"
                "   Download: ollama.ai\n"
                "   Then run:  ollama pull gemma4:e4b\n\n"
                "3. Python packages:\n"
                "   pip install websocket-client ollama opencv-python Pillow\n\n"
                "4. FFmpeg (usually bundled with ComfyUI on Windows)"
            ), style="Wiz.TLabel", justify=tk.LEFT).pack(anchor="w")

        # --- PAGE 3: ComfyUI Models ---
        def page_models(frame):
            ttk.Label(frame, text="Step 2: ComfyUI Models & Nodes", style="WizStep.TLabel").pack(anchor="w", pady=(0, 10))
            ttk.Label(frame, text=(
                "Install these in ComfyUI:\n\n"
                "Custom Nodes (install via ComfyUI Manager or git clone):\n"
                "  - ComfyUI-LTXVideo (Lightricks LTX-AV video generation)\n"
                "  - ComfyUI-VideoHelperSuite (video output nodes)\n"
                "  - ComfyUI-GGUF (optional, for quantized models)\n\n"
                "Models (place in ComfyUI/models/ subfolders):\n"
                "  - LTX 2.3 video model (diffusion_models/)\n"
                "  - LTX video VAE: LTX23_video_vae_bf16.safetensors\n"
                "  - LTX audio VAE: LTX23_audio_vae_bf16.safetensors\n"
                "  - Text encoder: qwen_3_4b.safetensors (clip/)\n"
                "  - Z-Image Turbo: z_image_turbo_bf16.safetensors (diffusion_models/)\n"
                "  - Image VAE: ae.safetensors (vae/)"
            ), style="Wiz.TLabel", justify=tk.LEFT).pack(anchor="w")

        # --- PAGE 4: Test Workflows ---
        def page_workflows(frame):
            ttk.Label(frame, text="Step 3: Test Your Workflows", style="WizStep.TLabel").pack(anchor="w", pady=(0, 10))
            ttk.Label(frame, text=(
                "IMPORTANT: Before using this tool, you must verify your workflows\n"
                "work manually in ComfyUI first.\n\n"
                "1. VIDEO WORKFLOW (LTX-AV text-to-video):\n"
                "   - Open your t2v workflow in ComfyUI\n"
                "   - Type a test prompt and queue it\n"
                "   - Verify it generates a video with audio\n"
                "   - The agent will grab the API format from history\n\n"
                "2. KEYFRAME WORKFLOW (Z-Image Turbo):\n"
                "   - Open your image gen workflow in ComfyUI\n"
                "   - Use: UNETLoader + ModelSamplingAuraFlow + CLIPLoader (lumina2)\n"
                "   - CLIP: qwen_3_4b.safetensors | VAE: ae.safetensors\n"
                "   - Queue a test image at your desired resolution\n"
                "   - The agent will grab this from history too\n\n"
                "Both workflows must complete successfully at least once\n"
                "before the agent can use them."
            ), style="Wiz.TLabel", justify=tk.LEFT).pack(anchor="w")

        # --- PAGE 5: Export Workflows ---
        def page_export(frame):
            ttk.Label(frame, text="Step 4: Export Workflow Templates", style="WizStep.TLabel").pack(anchor="w", pady=(0, 10))
            ttk.Label(frame, text=(
                "After testing both workflows manually:\n\n"
                "Option A (Recommended):\n"
                "  Queue each workflow once in ComfyUI. The agent auto-grabs\n"
                "  the API format from ComfyUI's /history endpoint.\n\n"
                "Option B (Manual):\n"
                "  1. In ComfyUI, go to Settings and enable Dev Mode\n"
                "  2. Click 'Save (API Format)' in the menu\n"
                "  3. Save video workflow as:\n"
                "     video_director_agent/workflow_template.json\n"
                "  4. Save keyframe workflow as:\n"
                "     video_director_agent/keyframe_template.json\n\n"
                "Then update the node IDs in config.py to match your workflows.\n"
                "See README.md for details on finding node IDs."
            ), style="Wiz.TLabel", justify=tk.LEFT).pack(anchor="w")

        # --- PAGE 6: Settings ---
        def page_settings(frame):
            ttk.Label(frame, text="Step 5: Configure Paths", style="WizStep.TLabel").pack(anchor="w", pady=(0, 10))

            ttk.Label(frame, text="ComfyUI Root Folder:", style="Wiz.TLabel").pack(anchor="w")
            comfy_var = tk.StringVar(value=_get("comfyui_root"))
            cf = ttk.Frame(frame)
            cf.pack(fill=tk.X, pady=(2, 8))
            ttk.Entry(cf, textvariable=comfy_var, width=55).pack(side=tk.LEFT, fill=tk.X, expand=True)
            ttk.Button(cf, text="Browse...",
                       command=lambda: comfy_var.set(filedialog.askdirectory(title="Select ComfyUI Root") or comfy_var.get())
                       ).pack(side=tk.RIGHT, padx=(8, 0))

            ttk.Label(frame, text="Project Output Folder:", style="Wiz.TLabel").pack(anchor="w")
            output_var = tk.StringVar(value=_get("project_output_root"))
            of = ttk.Frame(frame)
            of.pack(fill=tk.X, pady=(2, 8))
            ttk.Entry(of, textvariable=output_var, width=55).pack(side=tk.LEFT, fill=tk.X, expand=True)
            ttk.Button(of, text="Browse...",
                       command=lambda: output_var.set(filedialog.askdirectory(title="Select Project Output Folder") or output_var.get())
                       ).pack(side=tk.RIGHT, padx=(8, 0))

            ttk.Label(frame, text="ComfyUI Launch Script (filename in root):", style="Wiz.TLabel").pack(anchor="w")
            launcher_var = tk.StringVar(value=_get("comfyui_launcher"))
            ttk.Entry(frame, textvariable=launcher_var, width=55).pack(anchor="w", pady=(2, 8))

            ttk.Label(frame, text="LLM Provider:", style="Wiz.TLabel").pack(anchor="w")
            provider_var = tk.StringVar(value=_get("llm_provider"))
            ttk.Combobox(
                frame,
                textvariable=provider_var,
                values=("ollama", "openai_compatible"),
                state="readonly",
                width=24,
            ).pack(anchor="w", pady=(2, 8))

            ttk.Label(frame, text="LLM API Base URL:", style="Wiz.TLabel").pack(anchor="w")
            llm_url_var = tk.StringVar(value=_get("llm_base_url"))
            ttk.Entry(frame, textvariable=llm_url_var, width=55).pack(anchor="w", pady=(2, 8))

            llm_thinking_var = tk.BooleanVar(value=str(_get("llm_enable_thinking")).strip().lower() in ("1", "true", "yes", "on"))
            ttk.Checkbutton(
                frame,
                text="Enable llama.cpp thinking mode",
                variable=llm_thinking_var,
            ).pack(anchor="w", pady=(2, 8))

            llm_breakdown_only_var = tk.BooleanVar(value=str(_get("llm_reasoning_breakdown_only")).strip().lower() in ("1", "true", "yes", "on"))
            ttk.Checkbutton(
                frame,
                text="Reasoning only for breakdown",
                variable=llm_breakdown_only_var,
            ).pack(anchor="w", pady=(0, 8))

            llm_creative_drafting_var = tk.BooleanVar(value=str(_get("llm_creative_drafting_mode")).strip().lower() in ("1", "true", "yes", "on"))
            ttk.Checkbutton(
                frame,
                text="Creative drafting mode",
                variable=llm_creative_drafting_var,
            ).pack(anchor="w", pady=(0, 8))

            ttk.Label(frame, text="LLM reasoning format:", style="Wiz.TLabel").pack(anchor="w")
            llm_reasoning_var = tk.StringVar(value=_get("llm_reasoning_format"))
            ttk.Combobox(
                frame,
                textvariable=llm_reasoning_var,
                values=("none", "deepseek"),
                state="readonly",
                width=24,
            ).pack(anchor="w", pady=(2, 8))

            ttk.Label(frame, text="LLM Creative Model (prompt writing):", style="Wiz.TLabel").pack(anchor="w")
            creative_model_var = tk.StringVar(value=_get("ollama_model_creative"))
            ttk.Entry(frame, textvariable=creative_model_var, width=40).pack(anchor="w", pady=(2, 8))

            ttk.Label(frame, text="LLM Fast Model (evaluation/helpers):", style="Wiz.TLabel").pack(anchor="w")
            fast_model_var = tk.StringVar(value=_get("ollama_model_fast"))
            ttk.Entry(frame, textvariable=fast_model_var, width=40).pack(anchor="w", pady=(2, 8))

            # Store vars for save
            frame._settings_vars = (
                comfy_var,
                output_var,
                launcher_var,
                provider_var,
                llm_url_var,
                llm_thinking_var,
                llm_breakdown_only_var,
                llm_creative_drafting_var,
                llm_reasoning_var,
                creative_model_var,
                fast_model_var,
            )

        # --- PAGE 7: Ready ---
        def page_ready(frame):
            ttk.Label(frame, text="You're All Set!", style="WizTitle.TLabel").pack(anchor="w", pady=(0, 15))
            ttk.Label(frame, text=(
                "How to use KupkaProd Cinema Pipeline:\n\n"
                "1. Type a movie brief or click 'Load Script File' to load a screenplay\n\n"
                "2. Click 'Start Production' — the AI will:\n"
                "   - Break your script into scenes with full descriptions\n"
                "   - Generate storyboard keyframe images for each scene\n"
                "   - Stop and wait for your review\n\n"
                "3. Click 'Storyboard' to review and approve keyframe images\n\n"
                "4. Click 'Start Production' again — it will:\n"
                "   - Generate 3 video takes per scene\n"
                "   - Stop and wait for your review\n\n"
                "5. Click 'Review Takes' to pick your favorite take per scene\n"
                "   and assemble the final film\n\n"
                "Tip: Long productions can run overnight. The state is saved\n"
                "after every step, so you can close and resume anytime."
            ), style="Wiz.TLabel", justify=tk.LEFT).pack(anchor="w")

            self._dont_show_var = tk.BooleanVar(value=False)
            ttk.Checkbutton(frame, text="Don't show this wizard on startup",
                            variable=self._dont_show_var).pack(anchor="w", pady=(15, 0))

        if skip_to_settings:
            pages = [("Settings", page_settings)]
        else:
            pages = [
                ("Welcome", page_welcome),
                ("Prerequisites", page_prereqs),
                ("Models & Nodes", page_models),
                ("Test Workflows", page_workflows),
                ("Export Templates", page_export),
                ("Settings", page_settings),
                ("Ready!", page_ready),
            ]

        current_page = [0]
        content_frame = [None]

        # Navigation bar
        nav = ttk.Frame(wiz)
        nav.pack(side=tk.BOTTOM, fill=tk.X, padx=20, pady=10)

        page_label = ttk.Label(nav, text="", foreground="#6c7086", background="#1c1c1c")
        page_label.pack(side=tk.LEFT)

        next_btn = ttk.Button(nav, text="Next >")
        next_btn.pack(side=tk.RIGHT)
        prev_btn = ttk.Button(nav, text="< Back")
        prev_btn.pack(side=tk.RIGHT, padx=(0, 8))

        def show_page(idx):
            current_page[0] = idx
            if content_frame[0]:
                content_frame[0].destroy()
            content_frame[0] = ttk.Frame(wiz, padding=20)
            content_frame[0].pack(fill=tk.BOTH, expand=True)

            title, builder = pages[idx]
            builder(content_frame[0])

            page_label.configure(text=f"Page {idx + 1} of {len(pages)}")
            prev_btn.configure(state=tk.NORMAL if idx > 0 else tk.DISABLED)

            is_last = idx == len(pages) - 1
            is_settings = pages[idx][0] == "Settings"
            if is_last:
                next_btn.configure(text="Finish", command=finish)
            elif is_settings and skip_to_settings:
                next_btn.configure(text="Save", command=finish)
            else:
                next_btn.configure(text="Next >", command=lambda: show_page(idx + 1))

        def finish():
            # Save settings from the settings page
            for idx, (title, _) in enumerate(pages):
                if title == "Settings":
                    frame = content_frame[0] if current_page[0] == idx else None
                    if frame is None:
                        # Need to find the settings frame - check if vars exist
                        break
                    if hasattr(frame, '_settings_vars'):
                        (
                            comfy_var,
                            output_var,
                            launcher_var,
                            provider_var,
                            llm_url_var,
                            llm_thinking_var,
                            llm_breakdown_only_var,
                            llm_creative_drafting_var,
                            llm_reasoning_var,
                            creative_model_var,
                            fast_model_var,
                        ) = frame._settings_vars
                        save_user_settings({
                            "comfyui_root": comfy_var.get(),
                            "project_output_root": output_var.get(),
                            "comfyui_launcher": launcher_var.get(),
                            "llm_provider": provider_var.get(),
                            "llm_base_url": llm_url_var.get(),
                            "llm_enable_thinking": bool(llm_thinking_var.get()),
                            "llm_reasoning_breakdown_only": bool(llm_breakdown_only_var.get()),
                            "llm_creative_drafting_mode": bool(llm_creative_drafting_var.get()),
                            "llm_reasoning_format": llm_reasoning_var.get(),
                            "ollama_host": llm_url_var.get(),
                            "ollama_model_creative": creative_model_var.get(),
                            "ollama_model_fast": fast_model_var.get(),
                        })
                    break

            # Mark wizard as shown if checkbox checked
            if hasattr(self, '_dont_show_var') and self._dont_show_var.get():
                settings_path = os.path.join(os.path.dirname(__file__), "user_settings.json")
                if not os.path.exists(settings_path):
                    save_user_settings({"wizard_shown": True})

            wiz.destroy()

        prev_btn.configure(command=lambda: show_page(current_page[0] - 1))
        show_page(len(pages) - 1 if skip_to_settings else 0)
        wiz.wait_window()

    def _build_ui(self):
        # Modern dark theme via sv_ttk (falls back to clam if not installed)
        try:
            import sv_ttk
            sv_ttk.set_theme("dark")
        except ImportError:
            ttk.Style().theme_use("clam")

        style = ttk.Style()
        style.configure("TLabel", font=("Segoe UI", 11))
        style.configure("TButton", font=("Segoe UI", 11, "bold"), padding=8)
        style.configure("Header.TLabel", font=("Segoe UI", 16, "bold"), foreground="#89b4fa")
        style.configure("TEntry", font=("Segoe UI", 11))

        main = ttk.Frame(self.root, padding=20)
        main.pack(fill=tk.BOTH, expand=True)

        # Header
        ttk.Label(main, text="KupkaProd Cinema Pipeline", style="Header.TLabel").pack(anchor="w")
        ttk.Label(main, text="Autonomous video creation — Powered by LTX 2.3").pack(anchor="w", pady=(0, 15))

        # Prompt / Script input
        prompt_header = ttk.Frame(main)
        prompt_header.pack(fill=tk.X)
        ttk.Label(prompt_header, text="Movie Brief or Script:").pack(side=tk.LEFT)
        self.load_script_btn = ttk.Button(prompt_header, text="Load Script File...", command=self._load_script)
        self.load_script_btn.pack(side=tk.RIGHT)
        self.mode_var = tk.StringVar(value="brief")
        self.mode_label = ttk.Label(prompt_header, text="", foreground="#a6e3a1")
        self.mode_label.pack(side=tk.RIGHT, padx=8)

        self.prompt_text = scrolledtext.ScrolledText(
            main, height=8, font=("Segoe UI", 11),
            bg="#2b2b2b", fg="#e0e0e0", insertbackground="#e0e0e0",
            wrap=tk.WORD, relief=tk.FLAT, padx=8, pady=8,
        )
        self.prompt_text.pack(fill=tk.X, pady=(4, 12))

        # Project name
        name_frame = ttk.Frame(main)
        name_frame.pack(fill=tk.X, pady=(0, 12))
        ttk.Label(name_frame, text="Project Name:").pack(side=tk.LEFT)
        self.project_var = tk.StringVar()
        self.project_entry = ttk.Entry(name_frame, textvariable=self.project_var, width=40)
        self.project_entry.pack(side=tk.LEFT, padx=(8, 0))
        ttk.Label(name_frame, text="(auto from brief if empty)", foreground="#6c7086").pack(side=tk.LEFT, padx=8)

        # Mode checkboxes
        opts_frame = ttk.Frame(main)
        opts_frame.pack(fill=tk.X, pady=(0, 4))
        self.lazy_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(opts_frame, text="Lazy Mode (AI auto-selects best keyframes and takes — no manual review needed)",
                        variable=self.lazy_var).pack(side=tk.LEFT)

        opts_frame2 = ttk.Frame(main)
        opts_frame2.pack(fill=tk.X, pady=(0, 4))
        self.t2v_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(opts_frame2, text="T2V Only (skip keyframe generation — go straight to video)",
                        variable=self.t2v_var,
                        command=self._toggle_t2v).pack(side=tk.LEFT)

        opts_frame3 = ttk.Frame(main)
        opts_frame3.pack(fill=tk.X, pady=(0, 8))
        self.skip_kf_eval_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(opts_frame3, text="Skip Keyframe Evaluation (experimental — disable AI quality check on keyframes)",
                        variable=self.skip_kf_eval_var).pack(side=tk.LEFT)

        opts_frame4 = ttk.Frame(main)
        opts_frame4.pack(fill=tk.X, pady=(0, 8))
        self.subtitle_safe_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            opts_frame4,
            text="Subtitle Safe Mode (less literal dialogue prompting to reduce burned-in captions)",
            variable=self.subtitle_safe_var,
        ).pack(side=tk.LEFT)

        # Takes per scene + scene duration limits
        from config import TAKES_PER_SCENE, SCENE_MIN_SEC, SCENE_MAX_SEC
        params_frame = ttk.Frame(main)
        params_frame.pack(fill=tk.X, pady=(0, 8))

        ttk.Label(params_frame, text="Takes/scene:").pack(side=tk.LEFT)
        self.takes_var = tk.IntVar(value=TAKES_PER_SCENE)
        ttk.Spinbox(params_frame, from_=1, to=10, textvariable=self.takes_var,
                     width=3, font=("Segoe UI", 11)).pack(side=tk.LEFT, padx=(4, 16))

        ttk.Label(params_frame, text="Scene duration:").pack(side=tk.LEFT)
        self.scene_min_var = tk.IntVar(value=SCENE_MIN_SEC)
        ttk.Spinbox(params_frame, from_=1, to=30, textvariable=self.scene_min_var,
                     width=3, font=("Segoe UI", 11)).pack(side=tk.LEFT, padx=(4, 0))
        ttk.Label(params_frame, text="–").pack(side=tk.LEFT, padx=2)
        self.scene_max_var = tk.IntVar(value=SCENE_MAX_SEC)
        ttk.Spinbox(params_frame, from_=2, to=120, textvariable=self.scene_max_var,
                     width=3, font=("Segoe UI", 11)).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Label(params_frame, text="sec").pack(side=tk.LEFT)

        # Resolution sliders
        from config import KF_WIDTH, KF_HEIGHT, VIDEO_WIDTH, VIDEO_HEIGHT
        res_frame = ttk.LabelFrame(main, text="Resolution", padding=8)
        res_frame.pack(fill=tk.X, pady=(0, 8))
        style.configure("TLabelframe.Label", foreground="#89b4fa",
                         font=("Segoe UI", 10, "bold"))

        # Image (keyframe) resolution
        self.img_row = img_row = ttk.Frame(res_frame)
        img_row.pack(fill=tk.X, pady=(0, 4))
        ttk.Label(img_row, text="Image:").pack(side=tk.LEFT)
        self.kf_w_var = tk.IntVar(value=KF_WIDTH)
        self.kf_h_var = tk.IntVar(value=KF_HEIGHT)
        self.kf_w_label = ttk.Label(img_row, text=f"{KF_WIDTH}x{KF_HEIGHT}", width=12)
        self.kf_w_label.pack(side=tk.RIGHT)
        ttk.Label(img_row, text="H:").pack(side=tk.RIGHT, padx=(8, 0))
        kf_h_scale = ttk.Scale(img_row, from_=512, to=2048, variable=self.kf_h_var,
                                orient=tk.HORIZONTAL, length=120,
                                command=lambda _: self._update_res_labels())
        kf_h_scale.pack(side=tk.RIGHT)
        ttk.Label(img_row, text="W:").pack(side=tk.RIGHT, padx=(8, 0))
        kf_w_scale = ttk.Scale(img_row, from_=512, to=2048, variable=self.kf_w_var,
                                orient=tk.HORIZONTAL, length=120,
                                command=lambda _: self._update_res_labels())
        kf_w_scale.pack(side=tk.RIGHT)

        # Video resolution
        vid_row = ttk.Frame(res_frame)
        vid_row.pack(fill=tk.X)
        ttk.Label(vid_row, text="Video:").pack(side=tk.LEFT)
        self.vid_w_var = tk.IntVar(value=VIDEO_WIDTH)
        self.vid_h_var = tk.IntVar(value=VIDEO_HEIGHT)
        self.vid_w_label = ttk.Label(vid_row, text=f"{VIDEO_WIDTH}x{VIDEO_HEIGHT}", width=12)
        self.vid_w_label.pack(side=tk.RIGHT)
        ttk.Label(vid_row, text="H:").pack(side=tk.RIGHT, padx=(8, 0))
        vid_h_scale = ttk.Scale(vid_row, from_=256, to=1024, variable=self.vid_h_var,
                                 orient=tk.HORIZONTAL, length=120,
                                 command=lambda _: self._update_res_labels())
        vid_h_scale.pack(side=tk.RIGHT)
        ttk.Label(vid_row, text="W:").pack(side=tk.RIGHT, padx=(8, 0))
        vid_w_scale = ttk.Scale(vid_row, from_=256, to=1024, variable=self.vid_w_var,
                                 orient=tk.HORIZONTAL, length=120,
                                 command=lambda _: self._update_res_labels())
        vid_w_scale.pack(side=tk.RIGHT)

        # Buttons
        btn_frame = ttk.Frame(main)
        btn_frame.pack(fill=tk.X, pady=(0, 12))
        self.start_btn = ttk.Button(btn_frame, text="Start Production", command=self._start)
        self.start_btn.pack(side=tk.LEFT)
        self.stop_btn = ttk.Button(btn_frame, text="Stop", command=self._stop, state=tk.DISABLED)
        self.stop_btn.pack(side=tk.LEFT, padx=8)
        self.storyboard_btn = ttk.Button(btn_frame, text="Storyboard", command=self._open_storyboard)
        self.storyboard_btn.pack(side=tk.LEFT, padx=8)
        self.review_btn = ttk.Button(btn_frame, text="Review Takes", command=self._open_reviewer)
        self.review_btn.pack(side=tk.LEFT, padx=8)
        self.settings_btn = ttk.Button(btn_frame, text="Settings", command=self._show_setup)
        self.settings_btn.pack(side=tk.RIGHT)

        # Status
        self.status_var = tk.StringVar(value="Ready")
        status_label = ttk.Label(main, textvariable=self.status_var, foreground="#a6e3a1")
        status_label.pack(anchor="w", pady=(0, 8))

        # Log output (read-only but selectable/copyable)
        ttk.Label(main, text="Log:").pack(anchor="w")
        self.log_text = scrolledtext.ScrolledText(
            main, height=18, font=("Consolas", 10),
            bg="#1a1a1a", fg="#b0b0b0", insertbackground="#b0b0b0",
            selectbackground="#3a3a5a", selectforeground="#e0e0e0",
            wrap=tk.WORD, relief=tk.FLAT, padx=8, pady=8,
        )
        self.log_text.pack(fill=tk.BOTH, expand=True, pady=(4, 0))

        # License footer
        footer = ttk.Label(
            main,
            text="KupkaProd Cinema Pipeline  |  Free for non-commercial use  |  Commercial licensing: matt.kupka@gmail.com",
            foreground="#6c7086", font=("Segoe UI", 9),
        )
        footer.pack(anchor="center", pady=(6, 0))
        # Make read-only but allow select/copy (block all key input except Ctrl+C/A)
        self.log_text.bind("<Key>", lambda e: "break" if e.keysym not in ("c", "a") or not (e.state & 4) else None)

    @staticmethod
    def _snap(val, step):
        """Snap a value to the nearest multiple of step."""
        return round(val / step) * step

    def _toggle_t2v(self):
        """Enable/disable keyframe-related UI based on T2V checkbox."""
        is_t2v = self.t2v_var.get()
        state = tk.DISABLED if is_t2v else tk.NORMAL
        for child in self.img_row.winfo_children():
            try:
                child.configure(state=state)
            except tk.TclError:
                pass

    def _update_res_labels(self):
        kw = self._snap(self.kf_w_var.get(), 64)
        kh = self._snap(self.kf_h_var.get(), 64)
        self.kf_w_var.set(kw)
        self.kf_h_var.set(kh)
        self.kf_w_label.configure(text=f"{kw}x{kh}")

        vw = self._snap(self.vid_w_var.get(), 32)
        vh = self._snap(self.vid_h_var.get(), 32)
        self.vid_w_var.set(vw)
        self.vid_h_var.set(vh)
        self.vid_w_label.configure(text=f"{vw}x{vh}")

    def _log(self, msg: str):
        self.log_text.insert(tk.END, msg + "\n")
        self.log_text.see(tk.END)

    def _set_status(self, msg: str):
        self.status_var.set(msg)

    def _load_script(self):
        from tkinter import filedialog
        path = filedialog.askopenfilename(
            title="Load Script File",
            filetypes=[
                ("Script files", "*.txt *.md *.fountain *.fdx"),
                ("All files", "*.*"),
            ],
        )
        if path:
            with open(path, encoding="utf-8") as f:
                content = f.read()
            self.prompt_text.delete("1.0", tk.END)
            self.prompt_text.insert("1.0", content)
            self.mode_var.set("script")
            self.mode_label.configure(text="Script loaded")
            # Auto-fill project name from filename
            basename = os.path.splitext(os.path.basename(path))[0]
            self.project_var.set(slugify(basename))

    def _start(self):
        brief = self.prompt_text.get("1.0", tk.END).strip()
        project = self.project_var.get().strip()

        # If no prompt but project name exists, try to resume from saved state
        if not brief and project:
            from agent import load_state
            existing = load_state(project)
            if existing:
                brief = existing["brief"]
                self._log(f"Resuming project '{project}' from saved state.")
            else:
                self._set_status("No saved state found for that project. Enter a prompt!")
                return
        elif not brief:
            self._set_status("Enter a movie brief or load a script first!")
            return

        project = project or slugify(brief)
        self.project_var.set(project)
        is_script = self.mode_var.get() == "script"

        self.running = True
        self.start_btn.configure(state=tk.DISABLED)
        self.stop_btn.configure(state=tk.NORMAL)
        lazy = self.lazy_var.get()
        t2v_only = self.t2v_var.get()
        skip_kf_eval = self.skip_kf_eval_var.get()
        subtitle_safe = self.subtitle_safe_var.get()
        mode_str = "script" if is_script else "brief"
        flags = []
        if lazy:
            flags.append("LAZY")
        if t2v_only:
            flags.append("T2V")
        if skip_kf_eval:
            flags.append("SKIP_KF_EVAL")
        if subtitle_safe:
            flags.append("SAFE_SUBS")
        flags_str = f" [{', '.join(flags)}]" if flags else ""
        self._set_status(f"Starting production ({mode_str}){flags_str}: {project}")
        self._log(f"{'=' * 60}")
        self._log(f"Starting: {project} (mode: {mode_str}){flags_str}")
        self._log(f"Input: {brief[:200]}...")
        self._log(f"{'=' * 60}")

        # Gather settings
        takes = self.takes_var.get()
        scene_min = self.scene_min_var.get()
        scene_max = self.scene_max_var.get()
        res = {
            "kf_width": self._snap(self.kf_w_var.get(), 64),
            "kf_height": self._snap(self.kf_h_var.get(), 64),
            "video_width": self._snap(self.vid_w_var.get(), 32),
            "video_height": self._snap(self.vid_h_var.get(), 32),
        }
        self.thread = threading.Thread(
            target=self._run_agent,
            args=(brief, project, is_script, lazy, res, t2v_only, takes, scene_min, scene_max, skip_kf_eval, subtitle_safe),
            daemon=True,
        )
        self.thread.start()

    def _stop(self):
        self.running = False
        self._set_status("Stopping after current scene...")
        self.stop_btn.configure(state=tk.DISABLED)

    def _open_storyboard(self):
        from config import project_state_path
        project = self.project_var.get().strip()
        if not project:
            self._set_status("Enter a project name first!")
            return
        state_path = project_state_path(project)
        if not os.path.exists(state_path):
            self._set_status(f"Project '{project}' doesn't exist yet. Click 'Start Production' first to generate scenes and keyframes.")
            return
        try:
            import json
            with open(state_path) as f:
                st = json.load(f)
            has_keyframes = any(s.get("keyframe_candidates") for s in st.get("scenes", []))
            if not has_keyframes:
                self._set_status(f"Project '{project}' has no keyframes yet. Click 'Start Production' first -- it will generate keyframes then stop for your review.")
                return
            from storyboard import StoryboardGUI
            sb = StoryboardGUI(project)
            sb.run()
        except Exception as e:
            self._set_status(f"Error opening storyboard: {e}")

    def _open_reviewer(self):
        from config import project_state_path
        project = self.project_var.get().strip()
        if not project:
            self._set_status("Enter a project name first!")
            return
        state_path = project_state_path(project)
        if not os.path.exists(state_path):
            self._set_status(f"Project '{project}' doesn't exist yet. Run production first.")
            return
        try:
            import json
            with open(state_path) as f:
                st = json.load(f)
            has_takes = any(s.get("takes") for s in st.get("scenes", []))
            if not has_takes:
                self._set_status(f"Project '{project}' has no video takes yet. Approve storyboard first, then re-run production.")
                return
            from reviewer import ReviewerGUI
            reviewer = ReviewerGUI(project)
            reviewer.run()
        except Exception as e:
            self._set_status(f"Error opening reviewer: {e}")

    def _run_agent(self, brief: str, project_name: str, is_script: bool = False,
                   lazy: bool = False, res: dict = None, t2v_only: bool = False,
                   takes: int = 3, scene_min: int = 2, scene_max: int = 30,
                   skip_kf_eval: bool = True, subtitle_safe: bool = False):
        """Run the agent pipeline in a background thread."""
        import logging
        import config
        import comfyui_client
        import agent as agent_mod

        # Apply resolution settings to config before anything imports them
        import director as director_mod
        import keyframe_gen as kf_mod
        if res:
            config.KF_WIDTH = res["kf_width"]
            config.KF_HEIGHT = res["kf_height"]
            config.VIDEO_WIDTH = res["video_width"]
            config.VIDEO_HEIGHT = res["video_height"]
            kf_mod.KF_WIDTH = res["kf_width"]
            kf_mod.KF_HEIGHT = res["kf_height"]
            comfyui_client.VIDEO_WIDTH = res["video_width"]
            comfyui_client.VIDEO_HEIGHT = res["video_height"]

        # Takes per scene + scene duration limits
        config.TAKES_PER_SCENE = takes
        agent_mod.TAKES_PER_SCENE = takes
        config.SCENE_MIN_SEC = scene_min
        config.SCENE_MAX_SEC = scene_max
        director_mod.SCENE_MIN_SEC = scene_min
        director_mod.SCENE_MAX_SEC = scene_max

        # T2V mode: skip keyframe generation entirely
        if t2v_only:
            config.USE_KEYFRAMES = False
            agent_mod.USE_KEYFRAMES = False

        # Skip keyframe evaluation (experimental feature)
        if skip_kf_eval:
            config.SKIP_KF_EVAL = True
            kf_mod.SKIP_KF_EVAL = True

        config.SUBTITLE_SAFE_MODE = subtitle_safe
        director_mod.SUBTITLE_SAFE_MODE = subtitle_safe

        # Redirect logging to GUI
        class GUIHandler(logging.Handler):
            def __init__(self, gui):
                super().__init__()
                self.gui = gui

            def emit(self, record):
                msg = self.format(record)
                self.gui.root.after(0, self.gui._log, msg)

        # Set up logging for this run
        logger = logging.getLogger()
        logger.setLevel(logging.INFO)
        handler = GUIHandler(self)
        handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
        logger.addHandler(handler)

        try:
            from llm_client import is_ollama_provider, provider_label
            from config import COMFYUI_HOST
            from comfyui_client import ComfyUIClient, load_workflow_template
            from agent import preflight, run

            # Restart Ollama only when Ollama is the active provider.
            prep_status = "Restarting Ollama..." if is_ollama_provider() else f"Preparing {provider_label()}..."
            self.root.after(0, self._set_status, prep_status)
            _restart_ollama(logging.getLogger("agent"))

            client = ComfyUIClient()
            self.root.after(0, self._set_status, "Running preflight checks...")
            preflight(client, logging.getLogger("agent"))

            self.root.after(0, self._set_status, f"Producing: {project_name}")
            run(brief, project_name, logging.getLogger("agent"), is_script=is_script, lazy=lazy)

            # Check what state we ended in
            from agent import load_state as _load_state
            final_state = _load_state(project_name)
            if final_state and final_state.get("completed_at"):
                self.root.after(0, self._set_status, "Production complete!")
                self.root.after(0, self._log, f"Done! Final film: {final_state.get('final_path', 'check output folder')}")
            elif final_state and not final_state.get("storyboard_approved"):
                self.root.after(0, self._set_status, "Storyboard ready — review keyframes and approve, then Start again")
                self.root.after(0, self._log, "Click 'Storyboard' to review and approve keyframes, then click 'Start Production' to resume.")
            elif final_state and final_state.get("generation_completed_at"):
                self.root.after(0, self._set_status, "All takes generated — review and assemble")
                self.root.after(0, self._log, "Click 'Review Takes' to pick your favorites and assemble the final film.")
            else:
                self.root.after(0, self._set_status, "Pipeline paused — click Start to resume")
                self.root.after(0, self._log, "Run stopped. Click Start Production to resume where you left off.")

        except SystemExit:
            self.root.after(0, self._set_status, "Preflight failed — check log")
        except Exception as e:
            self.root.after(0, self._set_status, f"Error: {e}")
            self.root.after(0, self._log, f"ERROR: {e}")
        finally:
            logger.removeHandler(handler)
            self.running = False
            self.root.after(0, lambda: self.start_btn.configure(state=tk.NORMAL))
            self.root.after(0, lambda: self.stop_btn.configure(state=tk.DISABLED))

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    app = DirectorGUI()
    app.run()
