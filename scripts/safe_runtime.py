"""Small Unix runtime primitives shared by capture, ASR and optional decoders."""
from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import math
import os
import shlex
import signal
import subprocess
import tempfile
from pathlib import Path
from typing import Any


def fsync_directory(path: Path) -> None:
    fd = os.open(str(path), os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.' + path.name + '.', dir=str(path.parent))
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, str(path))
        fsync_directory(path.parent)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    atomic_text(path, json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n')


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value, dict):
        raise ValueError(f'{path}: expected a JSON object')
    return value


@contextlib.contextmanager
def exclusive_lock(path: Path, blocking: bool = False):
    """The OS releases this lock on exit/crash; stale PID files are not locks."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a') as stream:
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        except BlockingIOError as exc:
            raise RuntimeError(f'another worker owns {path}') from exc
        try:
            yield
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def archive_file(source: Path, target: Path) -> None:
    """Same-filesystem, no-overwrite publication, retry-safe after a crash."""
    if not source.exists():
        if target.exists():
            return
        raise FileNotFoundError(str(source))
    try:
        os.link(str(source), str(target))
    except FileExistsError:
        if sha256(source) != sha256(target):
            raise FileExistsError(f'refusing to overwrite different archive: {target}')
    fsync_directory(target.parent)
    source.unlink()
    fsync_directory(source.parent)


def command_argv(command: str, wav: Path) -> list[str]:
    # Tokenize the template FIRST. Substitution must not split paths with spaces.
    argv = shlex.split(command)
    if not argv:
        raise ValueError('decoder command is empty')
    has_placeholder = any('{wav}' in part for part in argv)
    argv = [part.replace('{wav}', str(wav)) for part in argv]
    return argv if has_placeholder else argv + [str(wav)]


def positive_timeout(value: float) -> float:
    value = float(value)
    if not math.isfinite(value) or value <= 0:
        raise ValueError('timeout must be a finite positive number')
    return value


def run_command(argv: list[str], timeout: float) -> dict[str, Any]:
    """Bound wall time/output and reap a one-shot command's process group.

    Nested repository adapters inherit the outer process group. An outer
    timeout therefore also kills their decoder children. Commands must be
    trusted and must not daemonize/start their own sessions; this is not a
    security sandbox. stdout/stderr are each capped at 256 KiB while reading.
    """
    import selectors
    import sys
    import time
    timeout = positive_timeout(timeout)
    nested = os.environ.get('SDR_DECODER_PGID') == str(os.getpgrp())
    if not nested:
        wrapper = ('import os,sys; os.environ["SDR_DECODER_PGID"]=str(os.getpgrp()); '
                   'os.execvpe(sys.argv[1],sys.argv[1:],os.environ)')
        launch = [sys.executable, '-c', wrapper] + list(argv)
    else:
        launch = list(argv)
    result: dict[str, Any] = {'argv': argv, 'returncode': None, 'stdout': '', 'stderr': '', 'error': None}
    buffers = {'stdout': bytearray(), 'stderr': bytearray()}
    try:
        proc = subprocess.Popen(launch, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, start_new_session=not nested)
    except OSError as exc:
        result['error'] = str(exc)
        return result
    deadline = time.monotonic() + timeout
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(proc.stdout, selectors.EVENT_READ, 'stdout')
            selector.register(proc.stderr, selectors.EVENT_READ, 'stderr')
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    result['error'] = f'decoder timed out after {timeout:g}s'
                    break
                for key, _ in selector.select(min(remaining, 0.1)):
                    block = os.read(key.fileobj.fileno(), 8192)
                    if not block:
                        selector.unregister(key.fileobj)
                        continue
                    target = buffers[key.data]
                    if len(target) + len(block) > 262144:
                        result['error'] = 'decoder output exceeds 256 KiB; refusing partial evidence'
                        break
                    target.extend(block)
                if result['error']:
                    break
            if not result['error']:
                try:
                    proc.wait(timeout=max(0.001, deadline - time.monotonic()))
                except subprocess.TimeoutExpired:
                    result['error'] = f'decoder timed out after {timeout:g}s'
    finally:
        # Clean the owned group even if its leader exited first. Nested calls
        # kill their direct child; the outer owner cleans any descendants.
        try:
            if not nested:
                os.killpg(proc.pid, signal.SIGKILL)
            elif proc.poll() is None:
                proc.kill()
        except ProcessLookupError:
            pass
        proc.wait()
        proc.stdout.close()
        proc.stderr.close()
    result['returncode'] = proc.returncode
    for key, value in buffers.items():
        result[key] = value.decode('utf-8', errors='replace').strip()
    if proc.returncode and not result['error']:
        result['error'] = f'decoder exited {proc.returncode}'
    return result


def decoded_output(stdout: str, error: str | None = None) -> dict[str, Any]:
    """Validate structured output; never interpret diagnostic JSON as text."""
    output: dict[str, Any] = {'decoded': False, 'text': '', 'confidence': None, 'wpm': None, 'error': error}
    if error:
        return output
    text = stdout.strip()
    if text.startswith(('{', '[')):
        try:
            value = json.loads(text)
            if not isinstance(value, dict):
                raise ValueError('decoder JSON must be an object')
            if value.get('error') or value.get('decoded') is False:
                output['error'] = str(value['error']) if value.get('error') else None
                return output
            if 'decoded' in value and not isinstance(value['decoded'], bool):
                raise ValueError('decoded must be a boolean')
            text = value.get('text', value.get('decoded_text', ''))
            if not isinstance(text, str):
                raise ValueError('decoded text must be a string')
            for key in ('confidence', 'wpm'):
                v = value.get(key)
                if isinstance(v, (float, int)) and not isinstance(v, bool) and math.isfinite(v):
                    if (key == 'confidence' and 0 <= v <= 1) or (key == 'wpm' and v > 0):
                        output[key] = v
        except (ValueError, TypeError) as exc:
            output['error'] = str(exc)
            return output
    output['text'] = text.strip()
    output['decoded'] = bool(output['text'])
    return output
