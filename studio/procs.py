"""Start, watch and stop child processes the same way on Windows and Linux.

Renders run ffmpeg, LatentSync and helper Pythons that may start their own children. Stopping only the direct
child leaves grandchildren running (and holding the GPU), so every child gets its own process group and is
stopped as a whole tree.
"""
import os
import signal
import subprocess
import tempfile

WINDOWS = os.name == 'nt'
CREATE_NO_WINDOW = 0x08000000
CREATE_NEW_PROCESS_GROUP = 0x00000200


def group_flags() -> dict:
    """Popen arguments that give the child its own process group (and no console window on Windows)."""
    if WINDOWS:
        return {'creationflags': CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW}
    return {'start_new_session': True}


def spawn(command, **kwargs) -> subprocess.Popen:
    return subprocess.Popen(command, **group_flags(), **kwargs)


def kill_tree(process: subprocess.Popen, timeout: float = 5) -> None:
    """Ask the whole process tree to stop, then force it after `timeout` seconds."""
    if process.poll() is not None:
        return
    if WINDOWS:
        # taskkill /T reaches grandchildren; without /F first, console programs get a chance to exit
        subprocess.run(['taskkill', '/T', '/PID', str(process.pid)], capture_output=True, creationflags=CREATE_NO_WINDOW)
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
    try:
        process.wait(timeout=timeout)
        return
    except subprocess.TimeoutExpired:
        pass
    if WINDOWS:
        subprocess.run(['taskkill', '/F', '/T', '/PID', str(process.pid)], capture_output=True, creationflags=CREATE_NO_WINDOW)
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    process.wait()


def run_cancellable(command, check, env=None, poll: float = .25):
    """Run `command`, calling `check()` while it runs (it raises to cancel). Returns (exit code, output tail)."""
    with tempfile.TemporaryFile(mode='w+', encoding='utf-8', errors='replace') as log:
        process = spawn(command, stdout=log, stderr=log, env=env)
        try:
            while True:
                check()
                try:
                    code = process.wait(timeout=poll)
                    break
                except subprocess.TimeoutExpired:
                    pass
        finally:
            kill_tree(process)
        log.seek(0)
        return code, log.read()[-2000:]
