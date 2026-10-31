"""Sandboxed file operations and subprocess execution with a minimal explicit environment.

Scope: file operations are path-checked against ``root`` (``..``, absolute paths and
symlinks that resolve outside are rejected). Commands run with cwd=root, an explicit
three-variable environment, their own process group and a hard timeout. This is NOT a
filesystem jail: a shell command can still read absolute paths the OS user can read.
"""

import contextlib
import os
import select
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType

OUTPUT_CAP_BYTES = 1_000_000
DRAIN_TIMEOUT_S = 5.0
_POLL_INTERVAL_S = 0.05
_BASE_PATH = "/usr/local/bin:/usr/bin:/bin"
_SKIP_DIRS = {"__pycache__", ".pytest_cache", ".git", ".mypy_cache", ".ruff_cache"}


class SandboxError(Exception):
    """Raised when an operation would leave the sandbox root."""


@dataclass(frozen=True)
class CommandResult:
    output: str
    exit_code: int | None
    timed_out: bool


def minimal_env(home: Path) -> dict[str, str]:
    """The ONLY environment a sandboxed command sees. Nothing is inherited."""
    interpreter_dir = str(Path(sys.executable).parent)
    return {
        "PATH": f"{interpreter_dir}:{_BASE_PATH}",
        "HOME": str(home),
        "LANG": "C.UTF-8",
    }


class _OutputReader:
    """Incrementally reads a pipe fd, keeping the first OUTPUT_CAP_BYTES."""

    def __init__(self, fd: int) -> None:
        self._fd = fd
        self._buf = bytearray()
        self._discarded = 0
        self.eof = False

    def _read_chunk(self) -> None:
        chunk = os.read(self._fd, 65536)
        if not chunk:
            self.eof = True
            return
        room = OUTPUT_CAP_BYTES - len(self._buf)
        if room > 0:
            self._buf += chunk[:room]
        self._discarded += max(0, len(chunk) - max(room, 0))

    def read_some(self, wait: float) -> None:
        """Wait up to ``wait`` seconds for data and read one chunk if available."""
        if select.select([self._fd], [], [], wait)[0]:
            self._read_chunk()

    def drain_available(self) -> None:
        """Read everything already sitting in the pipe, without waiting for more."""
        while not self.eof and select.select([self._fd], [], [], 0)[0]:
            self._read_chunk()

    def read_until_eof(self, deadline: float) -> None:
        while not self.eof and (remaining := deadline - time.monotonic()) > 0:
            self.read_some(min(remaining, _POLL_INTERVAL_S))

    def text(self) -> str:
        out = self._buf.decode("utf-8", errors="replace")
        if self._discarded:
            out += f"\n[... output truncated, {self._discarded} bytes discarded ...]"
        return out


class Sandbox:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self._home = Path(tempfile.mkdtemp(prefix="agent-lab-home-"))

    def __enter__(self) -> Sandbox:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        shutil.rmtree(self._home, ignore_errors=True)

    def resolve(self, path: str | Path) -> Path:
        """Resolve ``path`` (relative to root) and require it to stay inside root."""
        try:
            candidate = Path(path)
            if not candidate.is_absolute():
                candidate = self.root / candidate
            resolved = candidate.resolve()
        except (ValueError, OSError) as exc:
            raise SandboxError(f"invalid path {str(path)!r}: {exc}") from exc
        if not resolved.is_relative_to(self.root):
            raise SandboxError(f"path {str(path)!r} is outside the sandbox")
        return resolved

    def read_text(self, path: str | Path) -> str:
        return self.resolve(path).read_text(encoding="utf-8")

    def write_text(self, path: str | Path, content: str) -> None:
        target = self.resolve(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    def list_files(self, path: str | Path = ".") -> list[str]:
        """Recursively list files under ``path`` as sorted root-relative paths."""
        base = self.resolve(path)
        found: list[str] = []
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
            for name in filenames:
                found.append(str((Path(dirpath) / name).relative_to(self.root)))
        return sorted(found)

    def run_command(self, cmd: str, timeout: float) -> CommandResult:
        """Run ``cmd`` in its own process group; never blocks past ~timeout + 5s.

        Output is read incrementally: the first OUTPUT_CAP_BYTES are kept and the rest
        discarded (with a marker). On timeout the whole group is killed and remaining
        output is awaited for at most DRAIN_TIMEOUT_S. Once the shell itself has exited,
        only output already in the pipe is collected, so a daemonised child (e.g. via
        setsid) that keeps the pipe open cannot hold us up.
        """
        proc = subprocess.Popen(
            ["/bin/sh", "-c", cmd],
            cwd=self.root,
            env=minimal_env(self._home),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        assert proc.stdout is not None
        reader = _OutputReader(proc.stdout.fileno())
        deadline = time.monotonic() + timeout
        timed_out = False
        try:
            while not reader.eof:
                if proc.poll() is not None:
                    reader.drain_available()
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    timed_out = True
                    break
                reader.read_some(min(remaining, _POLL_INTERVAL_S))
            if not timed_out:
                try:
                    proc.wait(timeout=max(0.0, deadline - time.monotonic()))
                except subprocess.TimeoutExpired:
                    timed_out = True
            if timed_out:
                self._kill_group(proc)
                reader.read_until_eof(time.monotonic() + DRAIN_TIMEOUT_S)
                with contextlib.suppress(subprocess.TimeoutExpired):
                    proc.wait(timeout=DRAIN_TIMEOUT_S)
        finally:
            self._kill_group(proc)  # reap stragglers left in the group
            proc.stdout.close()
            with contextlib.suppress(subprocess.TimeoutExpired):
                proc.wait(timeout=DRAIN_TIMEOUT_S)

        text = reader.text()
        if timed_out:
            msg = f"Command timed out after {timeout:g}s and was killed."
            return CommandResult(f"{text}\n{msg}".lstrip("\n"), None, True)
        return CommandResult(text, proc.returncode, False)

    @staticmethod
    def _kill_group(proc: subprocess.Popen[bytes]) -> None:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(proc.pid, signal.SIGKILL)
