# Desktop app (Windows, Ubuntu, other Linux): plan and status

Goal: install Reelwright like a normal program (installer, Start menu / app launcher icon, one window),
without cloning Git, creating virtual environments or editing systemd units by hand. Renders keep running
locally on the user's NVIDIA GPU, exactly as today.

## Status

| Phase | What | State |
| --- | --- | --- |
| 0 | Portability groundwork (paths, ports, process control, Windows test run) | done, except the first Windows CI run (needs a push) and a lock file |
| 1 | Supervisor + desktop window, running against existing installs (dev mode) | not started |
| 2 | First-run setup: find or install ComfyUI, Ollama, FFmpeg, runtimes, models | not started |
| 3 | Installers: Windows (NSIS .exe), Ubuntu (.deb), other Linux (AppImage); auto-update | not started |
| 4 | Desktop comforts: tray, notifications, keep-awake while rendering, phone access toggle | not started |
| 5 | Docs, release checklist, CI on Windows + Ubuntu | not started |

## What we are packaging (today)

| Part | Today | Size | Desktop app |
| --- | --- | --- | --- |
| Studio (FastAPI + static JS) | `.venv`, `uvicorn studio.app:app` on 8020 (0.0.0.0) | small | bundled, localhost only by default |
| ComfyUI + custom nodes | `~/ComfyUI`, systemd unit, 127.0.0.1:8188 | 5-10 GB + models | use existing, or managed install |
| Models (Z-Image, Wan 2.2, H3, ...) | ComfyUI `models/` | 30-100 GB | never bundled; downloaded on demand to a folder the user picks |
| Ollama + Qwen 2.5 VL 7B | `.runtime/ollama`, systemd unit, 11434 | 2.4 GB + 6 GB | use existing, or managed install |
| FFmpeg / ffprobe | `.runtime/ffmpeg` | 100 MB | bundled static build per OS |
| LatentSync env + weights | `.runtime/lipsync-env`, `.runtime/LatentSync` | 6 GB | optional component, installed on first use |
| Face tools (KEEP, CodeFormer, identity) | `.runtime/face-env`, ... | 10 GB | optional component, installed on first use |
| HTTPS for phones (Caddy) | systemd unit, 8443 | 50 MB | optional, off by default |

The heavy parts are Python/CUDA environments and model weights. They cannot sensibly go inside an installer,
so the installer is small and a first-run setup builds the rest, with progress, resume and checksums.

## Recommended architecture

```
Electron shell (window, menus, tray, updates)
  └─ starts the Supervisor (Python, studio/desktop/supervisor.py)
        ├─ Studio  (uvicorn, 127.0.0.1:<free port>)
        ├─ ComfyUI (managed, or attach to the user's existing one)
        └─ Ollama  (managed, or attach to the user's existing one)
  window loads http://127.0.0.1:<studio port>/
```

- **Shell: Electron**, not Tauri or pywebview. The editor relies on H.264 playback in `<video>`, pointer
  capture and a pile of Chromium-tested behaviour (the browser tests run on Chromium). Tauri/pywebview use
  the system WebView: WebKitGTK on Linux (H.264 depends on GStreamer plugins being installed; our WebKit tests
  already differ) and WebView2 on Windows. Electron costs ~150 MB more but gives one engine everywhere.
- **Backend stays a local web server.** Nothing in the UI needs rewriting; phones and other PCs can still
  connect when the user switches on network access.
- **Python: embedded, not frozen.** Ship `uv` plus a pinned python-build-standalone CPython; create the
  Studio environment from a lock file at install/first run. PyInstaller is not used: the ML environments
  (torch/CUDA) are huge, version-sensitive and already run as separate interpreters.
- **GPU:** NVIDIA + CUDA only (as today). Setup checks the driver version and VRAM and explains clearly
  what will and will not run (e.g. 12 GB cards: no H3, smaller Wan settings).

## Phase 0: portability groundwork (1-2 sessions)

1. **Data and runtime locations** via `platformdirs`:
   projects `Documents/Reelwright` (or chosen), settings/logs in the user config dir, runtimes in the user
   data dir, models in a user-chosen folder (Windows: often another drive). `STUDIO_DATA` already exists;
   add `STUDIO_RUNTIME` and `STUDIO_MODELS`, and stop storing absolute paths (e.g. `ffmpeg` in
   `settings.json`): store tool names and resolve them at start-up.
2. **Ports**: Studio, ComfyUI and Ollama ports chosen at start-up (prefer 8020/8188/11434, fall back to free
   ones) and passed through settings, so a second instance or an existing service never collides.
3. **Process control**: one helper for start/stop/kill-tree on both OSes (`start_new_session` / `killpg` on
   POSIX, `CREATE_NEW_PROCESS_GROUP` + `taskkill /T` on Windows). It replaces the ad-hoc code in
   `comfy_nodes/reelwright_lipsync/__init__.py`, `timeline.run`, `media`.
4. **Windows code audit**: path separators in media URLs (`store.rel` already normalises), `fontconfig`-free
   titles (already Pillow), file locking on replace (`Path.replace` of open videos fails on Windows), long
   paths, `.bat` helpers. Run the whole test suite on Windows and fix what fails.
5. **Single source of versions**: `pyproject.toml` + `uv.lock` for Studio; pinned commits for ComfyUI and its
   custom nodes; a `components.json` manifest (name, version, URL per OS, SHA-256, size, licence).

## Phase 1: supervisor and window (2 sessions)

1. `studio/desktop/supervisor.py`: starts the three services in order, health-checks them (`/api/status`,
   ComfyUI `/system_stats`, Ollama `/api/tags`), restarts a crashed service, writes rotating logs, and stops
   everything cleanly on quit (finishing or cancelling running jobs first, with a prompt).
2. **Attach mode**: if ComfyUI/Ollama already answer on their ports (the current Linux systemd setup,
   Comfy Desktop on Windows), use them and do not start or stop them.
3. Electron `main.js`: single-instance lock, splash with service status, then the Studio window; menus
   (File: open projects folder, settings; Help: logs, about); `powerSaveBlocker` while a job runs.
4. Dev mode: `npm run dev` uses the repository's `.venv`; works on this PC against the existing services.
5. Tests: Playwright's Electron driver opens the app, waits for the project list, and quits cleanly.

## Phase 2: first-run setup (3 sessions)

A setup page (served by Studio itself, so it is ordinary HTML) shown until the essentials are present:

1. Check: GPU, driver/CUDA, free disk on the chosen drives, existing ComfyUI/Ollama installs.
2. Choose: projects folder, models folder, "use my ComfyUI" (path + port) or "install for me".
3. Components, each with Install / Repair / Remove and a size: Core (FFmpeg, Studio env) - required;
   ComfyUI + nodes; Ollama + planner model; model packs per family (Z-Image, Wan 2.2, H3, Flux...);
   Lip-sync; Face correction. Downloads are resumable, verified by SHA-256, and can run in the background.
4. Reuse the existing setup scripts' knowledge (`setup.ps1`, `scripts/install_lipsync.py`, the model lists in
   Settings) but move it into one Python installer module with tests, so Windows and Linux share it.
5. "Missing model" messages in the app link to the component that provides it.

## Phase 3: installers and updates (2 sessions)

- **electron-builder** targets: Windows NSIS `.exe` (per-user install, no admin), Ubuntu `.deb`
  (22.04 and 24.04), AppImage for other distributions. Flatpak/Snap later at most: their sandboxes make CUDA,
  large model folders and spawning ComfyUI awkward.
- **Auto-update** of the app (shell + Studio code) via GitHub Releases; components update separately from the
  setup page, never silently (model packs are tens of GB).
- **Code signing**: Windows needs a code-signing certificate, otherwise SmartScreen warns on every download
  (decision below). Linux packages signed with a GPG key.
- **Migrations**: settings and project schema versions; a backup of `episode.json` before each migration
  (the backup folder pattern already exists).

## Phase 4: desktop comforts (1-2 sessions)

- Tray icon: keep rendering with the window closed; job finished / failed notifications.
- "Allow phones and tablets on my network" switch: binds Studio to the LAN, starts Caddy HTTPS, shows the
  QR code and certificate steps (the existing mobile install flow). Off by default (today's server listens
  on 0.0.0.0 without login; the desktop app should not do that silently).
- Optional: a simple access code for network mode.
- Native file dialogs where the browser ones are clumsy (choose projects/models folders, export).

## Phase 5: docs, CI and release (1 session)

- GitHub Actions: unit + browser tests on `ubuntu-24.04` and `windows-latest` (no GPU; GPU paths are mocked
  as now), package builds on tags, a smoke test of each installer in a clean VM image.
- Manual release checklist on real GPUs (Windows + Ubuntu): install from scratch, attach to existing
  ComfyUI, render a frame, a take, a lip-sync clip, a timeline export.
- README: "Download for Windows / Ubuntu / Linux" replaces the clone instructions; LOCAL-SETUP.md becomes the
  developer guide.

## Risks

- **Model and runtime size**: first run can mean 50+ GB of downloads; must be resumable and selectable.
- **CUDA/driver mismatches** on Windows; pin torch builds per CUDA major and test on a clean machine.
- **Licences**: FFmpeg (GPL build vs LGPL), model licences (some non-commercial), KeySync etc. The
  `components.json` manifest records each licence and the setup page shows it before download.
- **Private repository**: auto-update from a private GitHub repo needs a token; public releases or a small
  release bucket are simpler (decision below).
- **Two ways to run on this PC**: the systemd services stay supported; the desktop app attaches to them.

## Decisions (made 2026-11-02)

1. **Electron** for the shell (one Chromium everywhere; the editor is tested on Chromium).
2. **Private first**: for your own PCs, released from the private repository; nothing that would block
   going public later (component manifest records licences from the start).
3. **No Windows code-signing certificate yet**: SmartScreen will warn on first run; revisit before sharing.
4. **Phone access stays, as an opt-in switch** that is off by default; the desktop app listens on
   127.0.0.1 only until it is switched on.

## Progress log

- Phase 0 (2026-11-02):
  - `studio/paths.py`: data / runtime / models / logs folders. A checkout keeps `studio_data` and
    `.runtime` (this PC is unchanged); the desktop app (`REELWRIGHT_DESKTOP=1`, or any installed copy that is
    not a checkout) uses Documents/Reelwright and %LOCALAPPDATA% / ~/.local/share/Reelwright/runtime;
    STUDIO_DATA, STUDIO_RUNTIME, STUDIO_MODELS override each one. `settings.DATA_ROOT` now comes from here.
  - Tools: `paths.find_tool` ignores a stale absolute ffmpeg path from settings.json and falls back to the
    bundled copy (Windows `ffmpeg/bin`, Linux `ffmpeg/usr/bin`) or PATH; `tool_environment` adds the bundled
    Linux ffmpeg's libraries, so `start-studio.sh`'s LD_LIBRARY_PATH line is no longer required.
  - Ports: `REELWRIGHT_COMFY_HOST` / `REELWRIGHT_OLLAMA_HOST` override the saved hosts at start-up (never
    written back); `python -m studio --port 0` (or `--any-port`) picks a free port and prints
    `REELWRIGHT_PORT <n>` first, default host 127.0.0.1. The systemd service still runs uvicorn on 0.0.0.0:8020.
  - Processes: `studio/procs.py` (own process group, kill the whole tree with killpg / taskkill /T, cancellable
    run); `timeline.run` uses it, so a cancelled render no longer leaves grandchildren running. The ComfyUI
    lip-sync node keeps its own copy (it runs in ComfyUI's Python and cannot import Studio).
  - Windows: `paths.replace` retries while Windows briefly locks a file being streamed; no Linux-only
    commands found in `studio/*.py`.
  - Packaging: `pyproject.toml` (installable wheel with the static files, `reelwright-studio` command,
    `[test]` extra); `numpy` added to requirements.txt (it was only installed via opencv).
  - CI: `.github/workflows/tests.yml` runs the tests on ubuntu-24.04 and windows-latest. Not run yet: the
    branch has to be pushed first, and the first Windows run will likely show more to fix.
  - Left over: `uv.lock` (uv is not installed here), the first Windows CI run and its fixes.
  - +11 tests (`tests/test_portability.py`).

## Starting prompt for the next session
"Read docs/DESKTOP-APP-PLAN.md, run the test suite for a baseline, check the latest Windows CI run and fix
what fails, then do Phase 1 (supervisor + Electron window in dev mode, attaching to the running ComfyUI and
Ollama). Update the Status table and the Progress log when done."
