"""Sandboxed file operations and subprocess execution with a minimal explicit environment.

Scope: file operations are path-checked against ``root`` (``..``, absolute paths and
symlinks that resolve outside are rejected). Commands run with cwd=root, an explicit
three-variable environment, their own process group and a hard timeout. This is NOT a
filesystem jail: a shell command can still read absolute paths the OS user can read.
"""

import contextlib
import os
import shutil
import signal
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType

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
        proc = subprocess.Popen(
            ["/bin/sh", "-c", cmd],
            cwd=self.root,
            env=minimal_env(self._home),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            out, _ = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            self._kill_group(proc)
            out, _ = proc.communicate()
            text = out.decode("utf-8", errors="replace")
            msg = f"Command timed out after {timeout:g}s and was killed."
            return CommandResult(f"{text}\n{msg}".lstrip("\n"), None, True)
        finally:
            self._kill_group(proc)  # reap stragglers left in the group
        return CommandResult(out.decode("utf-8", errors="replace"), proc.returncode, False)

    @staticmethod
    def _kill_group(proc: subprocess.Popen[bytes]) -> None:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(proc.pid, signal.SIGKILL)
