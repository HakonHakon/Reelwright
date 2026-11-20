"""Desktop-app groundwork: folders, tools, ports and process trees on Windows and Linux."""
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

from studio import paths, procs


@pytest.fixture
def clean_env(monkeypatch, tmp_path):
    for name in ('STUDIO_DATA', 'STUDIO_RUNTIME', 'STUDIO_MODELS', 'REELWRIGHT_DESKTOP', 'XDG_DATA_HOME', 'XDG_CONFIG_HOME'):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv('HOME', str(tmp_path / 'home'))
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: tmp_path / 'home'))
    return tmp_path


def test_a_checkout_keeps_its_folders_inside_the_repository(clean_env):
    assert paths.data_root() == paths.REPO_ROOT / 'studio_data'
    assert paths.runtime_root() == paths.REPO_ROOT / '.runtime'
    assert paths.models_root() == paths.REPO_ROOT / '.runtime' / 'models'


def test_the_desktop_app_uses_the_linux_user_folders(clean_env, monkeypatch):
    monkeypatch.setattr(paths, 'WINDOWS', False)
    monkeypatch.setenv('REELWRIGHT_DESKTOP', '1')
    home = clean_env / 'home'
    assert paths.data_root() == home / 'Documents' / 'Reelwright'
    assert paths.runtime_root() == home / '.local' / 'share' / 'Reelwright' / 'runtime'
    # a translated documents folder from xdg-user-dirs is respected
    (home / '.config').mkdir(parents=True)
    (home / '.config' / 'user-dirs.dirs').write_text('XDG_DOCUMENTS_DIR="$HOME/Dokumenter"\n')
    assert paths.data_root() == home / 'Dokumenter' / 'Reelwright'
    monkeypatch.setenv('XDG_DATA_HOME', str(clean_env / 'data'))
    assert paths.runtime_root() == clean_env / 'data' / 'Reelwright' / 'runtime'


def test_the_desktop_app_uses_the_windows_user_folders(clean_env, monkeypatch):
    monkeypatch.setattr(paths, 'WINDOWS', True)
    monkeypatch.setenv('REELWRIGHT_DESKTOP', '1')
    monkeypatch.setenv('USERPROFILE', str(clean_env / 'Users' / 'hakon'))
    monkeypatch.setenv('LOCALAPPDATA', str(clean_env / 'Users' / 'hakon' / 'AppData' / 'Local'))
    assert paths.data_root() == clean_env / 'Users' / 'hakon' / 'Documents' / 'Reelwright'
    assert paths.runtime_root() == clean_env / 'Users' / 'hakon' / 'AppData' / 'Local' / 'Reelwright' / 'runtime'


def test_every_folder_can_be_moved_by_an_environment_variable(clean_env, monkeypatch):
    monkeypatch.setenv('REELWRIGHT_DESKTOP', '1')
    for name, function in (('STUDIO_DATA', paths.data_root), ('STUDIO_RUNTIME', paths.runtime_root), ('STUDIO_MODELS', paths.models_root)):
        monkeypatch.setenv(name, str(clean_env / name))
        assert function() == clean_env / name


def _fake_tool(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('#!/bin/sh\nexit 0\n'); path.chmod(0o755)
    return path


@pytest.mark.skipif(os.name == 'nt', reason='POSIX executable bits')
def test_a_stale_tool_path_falls_back_to_the_bundled_copy_and_then_path(clean_env, monkeypatch):
    runtime = clean_env / 'runtime'
    monkeypatch.setenv('STUDIO_RUNTIME', str(runtime))
    monkeypatch.setenv('PATH', str(clean_env / 'empty'))
    assert paths.find_tool('ffmpeg', '/old/pc/ffmpeg') == 'ffmpeg', 'nothing found: the bare name, so errors say what is missing'
    bundled = _fake_tool(runtime / 'ffmpeg' / 'usr' / 'bin' / 'ffmpeg')
    assert paths.find_tool('ffmpeg', '/old/pc/ffmpeg') == str(bundled)
    mine = _fake_tool(clean_env / 'mine' / 'ffmpeg')
    assert paths.find_tool('ffmpeg', str(mine)) == str(mine), 'a configured tool that exists always wins'
    libraries = runtime / 'ffmpeg' / 'usr' / 'lib' / 'x86_64-linux-gnu'
    libraries.mkdir(parents=True)
    env = paths.tool_environment(str(bundled), {'LD_LIBRARY_PATH': '/usr/lib'})
    assert env['LD_LIBRARY_PATH'].split(os.pathsep) == [str(libraries.resolve()), '/usr/lib'], 'the bundled ffmpeg finds its libraries'
    assert paths.tool_environment(str(bundled), env)['LD_LIBRARY_PATH'] == env['LD_LIBRARY_PATH'], 'added only once'


def test_ports_prefer_the_usual_one_and_never_collide():
    import socket
    with socket.socket() as busy:
        busy.bind(('127.0.0.1', 0)); busy.listen(1)
        taken = busy.getsockname()[1]
        assert not paths.port_free(taken)
        other = paths.free_port(taken)
        assert other != taken and paths.port_free(other)
    free = paths.free_port(0)
    assert paths.free_port(free) == free


def test_replace_waits_for_a_briefly_locked_file_on_windows(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, 'WINDOWS', True)
    source, target = tmp_path / 'new.mp4', tmp_path / 'final.mp4'
    source.write_text('new'); target.write_text('old')
    real, calls = Path.replace, []
    def flaky(self, other):
        calls.append(1)
        if len(calls) < 3:
            raise PermissionError('in use')
        return real(self, other)
    monkeypatch.setattr(Path, 'replace', flaky)
    paths.replace(source, target)
    assert target.read_text() == 'new' and len(calls) == 3
    monkeypatch.setattr(paths, 'WINDOWS', False)
    source.write_text('again'); calls.clear()
    with pytest.raises(PermissionError):
        paths.replace(source, target)


PARENT = '''
import subprocess, sys, time
child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
print(child.pid, flush=True)
time.sleep(60)
'''


def _alive(pid):
    if os.name == 'nt':
        out = subprocess.run(['tasklist', '/FI', f'PID eq {pid}'], capture_output=True, text=True).stdout
        return str(pid) in out
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    try:  # a zombie still answers kill(0); it has exited
        return Path(f'/proc/{pid}/stat').read_text().split(')')[-1].split()[0] != 'Z'
    except OSError:
        return True


def test_kill_tree_also_stops_grandchildren():
    process = procs.spawn([sys.executable, '-c', PARENT], stdout=subprocess.PIPE, text=True)
    grandchild = int(process.stdout.readline())
    assert _alive(grandchild)
    procs.kill_tree(process, timeout=3)
    for _ in range(50):
        if not _alive(grandchild):
            break
        time.sleep(.1)
    assert process.poll() is not None and not _alive(grandchild), 'no orphaned helper keeps the GPU busy'


def test_run_cancellable_reports_output_and_cancels_the_whole_tree():
    code, output = procs.run_cancellable([sys.executable, '-c', 'import sys; print("hello"); sys.exit(3)'], lambda: None)
    assert code == 3 and 'hello' in output
    started = time.monotonic()

    class Cancelled(Exception):
        pass

    def check():
        if time.monotonic() - started > .5:
            raise Cancelled()
    with pytest.raises(Cancelled):
        procs.run_cancellable([sys.executable, '-c', 'import time; time.sleep(60)'], check)
    assert time.monotonic() - started < 10


def test_python_m_studio_picks_a_free_port_and_serves(tmp_path):
    env = {**os.environ, 'STUDIO_DATA': str(tmp_path / 'data')}
    process = procs.spawn([sys.executable, '-m', 'studio', '--port', '0'], cwd=str(paths.REPO_ROOT), env=env,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        first = process.stdout.readline().split()
        assert first[0] == 'REELWRIGHT_PORT', first
        port = int(first[1])
        for _ in range(100):
            try:
                with urllib.request.urlopen(f'http://127.0.0.1:{port}/', timeout=1) as response:
                    assert response.status == 200
                    break
            except OSError:
                time.sleep(.1)
        else:
            pytest.fail('Studio did not answer')
        urllib.request.urlopen(f'http://127.0.0.1:{port}/api/settings', timeout=5).read()
        assert (tmp_path / 'data' / 'settings.json').is_file(), 'projects and settings go where STUDIO_DATA says'
    finally:
        procs.kill_tree(process)


def test_an_installed_copy_uses_the_user_folders_without_being_told(clean_env, monkeypatch):
    monkeypatch.setattr(paths, 'WINDOWS', False)
    monkeypatch.setattr(paths, 'REPO_ROOT', clean_env / 'site-packages')
    assert paths.desktop() and paths.data_root() == clean_env / 'home' / 'Documents' / 'Reelwright'
    monkeypatch.setenv('REELWRIGHT_DESKTOP', '0')
    assert not paths.desktop()
