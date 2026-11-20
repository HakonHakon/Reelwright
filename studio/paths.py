"""Where Studio keeps things, on Windows and Linux, from a Git checkout or as an installed desktop app.

Checkout (the default, unchanged): projects in `<repo>/studio_data`, tools in `<repo>/.runtime`.
Desktop app (`REELWRIGHT_DESKTOP=1`, set by the app's supervisor): the usual per-user folders, e.g.
  Windows  %USERPROFILE%\\Documents\\Reelwright         %LOCALAPPDATA%\\Reelwright\\runtime
  Linux    ~/Documents/Reelwright (XDG documents dir)   ~/.local/share/Reelwright/runtime
Each can be overridden: STUDIO_DATA (projects + settings), STUDIO_RUNTIME (tools and environments),
STUDIO_MODELS (model weights, often on another drive).
"""
import os
import shutil
import socket
import sys
from pathlib import Path

APP = 'Reelwright'
REPO_ROOT = Path(__file__).resolve().parent.parent
WINDOWS = os.name == 'nt'


def checkout() -> bool:
    """Running from a Git checkout (or source folder), not from an installed package."""
    return (REPO_ROOT / 'pyproject.toml').is_file() or (REPO_ROOT / '.git').exists()


def desktop() -> bool:
    flag = os.environ.get('REELWRIGHT_DESKTOP', '').lower()
    if flag:
        return flag not in ('0', 'false', 'no')
    return not checkout()  # an installed copy must not write projects into site-packages


def _documents() -> Path:
    if WINDOWS:
        return Path(os.environ.get('USERPROFILE') or Path.home()) / 'Documents'
    # xdg-user-dirs writes DOCUMENTS into this file; fall back to ~/Documents
    config = Path(os.environ.get('XDG_CONFIG_HOME') or Path.home() / '.config') / 'user-dirs.dirs'
    try:
        for line in config.read_text(encoding='utf-8').splitlines():
            if line.startswith('XDG_DOCUMENTS_DIR='):
                value = line.split('=', 1)[1].strip().strip('"').replace('$HOME', str(Path.home()))
                if value and Path(value) != Path.home():
                    return Path(value)
    except OSError:
        pass
    return Path.home() / 'Documents'


def _app_data() -> Path:
    if WINDOWS:
        return Path(os.environ.get('LOCALAPPDATA') or Path.home() / 'AppData' / 'Local') / APP
    return Path(os.environ.get('XDG_DATA_HOME') or Path.home() / '.local' / 'share') / APP


def data_root() -> Path:
    if os.environ.get('STUDIO_DATA'):
        return Path(os.environ['STUDIO_DATA'])
    return _documents() / APP if desktop() else REPO_ROOT / 'studio_data'


def runtime_root() -> Path:
    if os.environ.get('STUDIO_RUNTIME'):
        return Path(os.environ['STUDIO_RUNTIME'])
    return _app_data() / 'runtime' if desktop() else REPO_ROOT / '.runtime'


def models_root() -> Path:
    if os.environ.get('STUDIO_MODELS'):
        return Path(os.environ['STUDIO_MODELS'])
    return runtime_root() / 'models'


def logs_root() -> Path:
    return (_app_data() / 'logs') if desktop() else (data_root())


def _executable(path) -> bool:
    return bool(path) and Path(path).is_file() and os.access(path, os.X_OK)


def find_tool(name: str, configured: str = '') -> str:
    """A tool such as ffmpeg: the configured path if it still exists, else the bundled copy, else PATH.

    Settings written on another PC (or before the runtime folder moved) keep working: a stale absolute
    path is ignored instead of failing every render."""
    if configured and (_executable(configured) or (not os.path.isabs(configured) and shutil.which(configured))):
        return configured
    exe = name + ('.exe' if WINDOWS else '')
    root = runtime_root()
    for candidate in (root / 'ffmpeg' / 'bin' / exe, root / 'ffmpeg' / 'usr' / 'bin' / exe, root / 'bin' / exe):
        if _executable(candidate):
            return str(candidate)
    return shutil.which(name) or name


def tool_environment(tool: str, environ=None) -> dict:
    """Environment for running a bundled Linux ffmpeg, whose shared libraries sit next to it."""
    environ = dict(os.environ if environ is None else environ)
    if WINDOWS:
        return environ
    libraries = Path(tool).resolve().parent.parent / 'lib' / 'x86_64-linux-gnu'
    if libraries.is_dir() and str(libraries) not in environ.get('LD_LIBRARY_PATH', '').split(os.pathsep):
        environ['LD_LIBRARY_PATH'] = os.pathsep.join(filter(None, [str(libraries), environ.get('LD_LIBRARY_PATH', '')]))
    return environ


def port_free(port: int, host: str = '127.0.0.1') -> bool:
    with socket.socket() as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
            return True
        except OSError:
            return False


def free_port(preferred: int = 0, host: str = '127.0.0.1') -> int:
    """The preferred port when it is free, otherwise any free one (so a second copy never collides)."""
    if preferred and port_free(preferred, host):
        return preferred
    with socket.socket() as sock:
        sock.bind((host, 0))
        return sock.getsockname()[1]


def python() -> str:
    return sys.executable


def replace(source, target, attempts: int = 20) -> None:
    """Path.replace that survives Windows briefly locking the target (a browser streaming it, an indexer).

    On Linux this is a single atomic rename."""
    import time
    for attempt in range(attempts):
        try:
            Path(source).replace(target)
            return
        except PermissionError:
            if not WINDOWS or attempt == attempts - 1:
                raise
            time.sleep(.1)
