"""How agent commands are isolated from the harness: none, or another Linux user.

``Sandbox`` asks a ``CommandIsolation`` to wrap every command, to make the directories the
agent writes usable, to stop what a command started, and to clean up after it.

``NoIsolation`` is the historical behaviour: commands run as the harness's own user and can
read its environment (``/proc/$PPID/environ``), files and network. ``LinuxUserIsolation`` runs
each command as a dedicated unprivileged user, without network, and kills that user's
processes after every command.
"""

import contextlib
import os
import pwd
import shlex
import signal
import stat
import subprocess
from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path


class IsolationError(Exception):
    """The requested isolation cannot be used (bad user, missing tools)."""


@dataclass(frozen=True)
class WrappedCommand:
    """Exactly what ``Popen`` receives: the argv and the environment of the outer process."""

    argv: list[str]
    env: dict[str, str]


class CommandIsolation(ABC):
    """Wraps agent commands and manages the directories and processes around them."""

    warning: str | None = None  # shown once by `agent-lab run`; None when nothing to warn about

    @abstractmethod
    def wrap(self, shell_command: str, env: Mapping[str, str]) -> WrappedCommand:
        """The process to start for ``shell_command`` with inner environment ``env``."""

    @abstractmethod
    def prepare(self, directory: Path) -> None:
        """Make ``directory`` (and what is in it) usable by the agent. Never follows symlinks."""

    @abstractmethod
    def share_file(self, path: Path) -> None:
        """Make a file the harness wrote editable by the agent. Never follows symlinks."""

    @abstractmethod
    def kill(self, proc: subprocess.Popen[bytes]) -> None:
        """Stop everything the command started."""

    @abstractmethod
    def cleanup(self, directories: Sequence[Path]) -> None:
        """After a command: no leftover processes, and the harness can use ``directories``."""

    def verify(self, hidden: Sequence[Path], read_only: Sequence[Path]) -> list[str]:
        """Problems that make this isolation unusable; empty means it works.

        ``hidden`` paths must not be readable and ``read_only`` paths not writable by the
        agent's commands. The default has nothing to check.
        """
        return []


class NoIsolation(CommandIsolation):
    """Commands run as the harness's own user: not isolated."""

    warning = (
        "agent commands are not isolated (--isolation none): they run as your user and can "
        "read your environment (including ANTHROPIC_API_KEY via /proc), your files and the network"
    )

    def wrap(self, shell_command: str, env: Mapping[str, str]) -> WrappedCommand:
        return WrappedCommand(["/bin/sh", "-c", shell_command], dict(env))

    def prepare(self, directory: Path) -> None:
        pass

    def share_file(self, path: Path) -> None:
        pass

    def kill(self, proc: subprocess.Popen[bytes]) -> None:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(proc.pid, signal.SIGKILL)

    def cleanup(self, directories: Sequence[Path]) -> None:
        pass


Runner = Callable[..., "subprocess.CompletedProcess[str]"]


class LinuxUserIsolation(CommandIsolation):
    """Run commands as another uid, with no network, through ``sudo -n``.

    Each command becomes ``sudo -n unshare --net setpriv --reuid --regid --clear-groups
    --no-new-privs env -i <minimal env> /bin/sh -c 'umask 0; <command>'``. Processes of
    another uid cannot be signalled with ``killpg``, so ``kill`` and ``cleanup`` end every
    process of that user with ``sudo -n pkill -KILL -u <user>``; this is only correct because
    commands run one at a time, and the constructor refuses the harness's own uid.

    ``umask 0`` makes everything the agent creates readable and writable by the harness; the
    harness reclaims anything the agent made unreadable by running ``chmod`` as that user.
    The harness user needs passwordless ``sudo`` for ``unshare``, ``setpriv`` and ``pkill``,
    which makes it root-equivalent: use this on ephemeral CI machines.
    """

    DEFAULT_USER = "agentlab-sandbox"
    _OUTER_PATH = "/usr/sbin:/usr/bin:/sbin:/bin"

    def __init__(
        self,
        user: str = DEFAULT_USER,
        runner: Runner = subprocess.run,
        lookup: Callable[[str], pwd.struct_passwd] = pwd.getpwnam,
        harness_uid: int | None = None,
    ) -> None:
        try:
            entry = lookup(user)
        except KeyError:
            raise IsolationError(
                f"user {user!r} does not exist (run scripts/setup_sandbox_user.sh)"
            ) from None
        own_uid = os.getuid() if harness_uid is None else harness_uid
        if entry.pw_uid in (0, own_uid):
            raise IsolationError(
                f"user {user!r} has uid {entry.pw_uid}, which is the harness's or root: "
                "killing that user's processes would kill the harness"
            )
        self.user = user
        self._uid = entry.pw_uid
        self._gid = entry.pw_gid
        self._harness_uid = own_uid
        self._run = runner

    def wrap(self, shell_command: str, env: Mapping[str, str]) -> WrappedCommand:
        argv = [
            *self._as_user(),
            "env",
            "-i",
            *(f"{key}={value}" for key, value in env.items()),
            "/bin/sh",
            "-c",
            f"umask 0; {shell_command}",
        ]
        return WrappedCommand(argv, {"PATH": self._OUTER_PATH})

    def prepare(self, directory: Path) -> None:
        """Open the directory tree to the agent without ever following a symlink.

        The harness owns these files, so no sudo is needed. Ancestors the harness owns get
        ``o+x`` only (temporary directories are 0700 and would hide the workspace).
        """
        if directory.is_symlink():
            return
        root = directory.resolve()
        for ancestor in root.parents:
            if ancestor.stat().st_uid == self._harness_uid:
                self._add_mode(ancestor, 0o001)
        self._add_mode(root, 0o777)
        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            base = Path(dirpath)
            for name in [*dirnames, *filenames]:
                self._add_mode(base / name, 0o777)  # a symlink is skipped inside

    def share_file(self, path: Path) -> None:
        self._add_mode(path, 0o666)
        for ancestor in path.parents:  # directories the file tool just created
            if ancestor.is_symlink() or ancestor.stat().st_uid != self._harness_uid:
                break
            self._add_mode(ancestor, 0o001)

    def kill(self, proc: subprocess.Popen[bytes]) -> None:
        self._kill_user_processes()

    def cleanup(self, directories: Sequence[Path]) -> None:
        self._kill_user_processes()
        for directory in directories:
            if directory.is_dir() and not directory.is_symlink():
                # chmod -R never follows symlinks; it runs as the owner of the agent's files.
                wrapped = self.wrap(f"chmod -R a+rwX {shlex.quote(str(directory))}", {})
                self._run(wrapped.argv, env=wrapped.env, capture_output=True, timeout=60)

    def verify(self, hidden: Sequence[Path], read_only: Sequence[Path]) -> list[str]:
        """Problems that make this isolation unusable; an empty list means it works."""
        sudo = self._probe(["sudo", "-n", "true"])
        if sudo is None or sudo.returncode != 0:
            return ["passwordless `sudo -n` does not work for the harness user"]
        problems: list[str] = []
        uid = self._probe_inner("id -u")
        if uid is None or uid.returncode != 0:
            return ["cannot run a command as the sandbox user (need sudo for unshare/setpriv)"]
        if uid.stdout.strip() != str(self._uid):
            problems.append(f"commands do not run as uid {self._uid} (got {uid.stdout.strip()!r})")
        # A fresh network namespace has no IPv4 route; the host's always has one. (Its
        # auto-created tunnel devices such as gre0 or sit0 have no address and reach nothing.)
        routes = self._probe_inner("tail -n +2 /proc/net/route | wc -l")
        count = routes.stdout.strip() if routes is not None else "unknown"
        if count != "0":
            problems.append(f"network is not isolated ({count} IPv4 routes are visible)")
        for path in hidden:
            if self._test_inner("-r", path) or self._test_inner("-x", path):
                problems.append(f"{path} is accessible to the sandbox user (run the setup script)")
        for path in read_only:
            if self._test_inner("-w", path):
                problems.append(f"{path} is writable by the sandbox user (run the setup script)")
        return problems

    def _as_user(self) -> list[str]:
        return [
            "sudo",
            "-n",
            "unshare",
            "--net",
            "setpriv",
            f"--reuid={self._uid}",
            f"--regid={self._gid}",
            "--clear-groups",
            "--no-new-privs",
        ]

    def _kill_user_processes(self) -> None:
        with contextlib.suppress(OSError, subprocess.SubprocessError):
            self._run(
                ["sudo", "-n", "pkill", "-KILL", "-u", str(self._uid)],
                env={"PATH": self._OUTER_PATH},
                capture_output=True,
                timeout=30,
            )

    def _probe(self, argv: list[str]) -> subprocess.CompletedProcess[str] | None:
        try:
            return self._run(
                argv, env={"PATH": self._OUTER_PATH}, capture_output=True, text=True, timeout=30
            )
        except OSError, subprocess.SubprocessError:
            return None

    def _probe_inner(self, command: str) -> subprocess.CompletedProcess[str] | None:
        wrapped = self.wrap(command, {"PATH": self._OUTER_PATH})
        return self._probe_wrapped(wrapped)

    def _probe_wrapped(self, wrapped: WrappedCommand) -> subprocess.CompletedProcess[str] | None:
        try:
            return self._run(
                wrapped.argv, env=wrapped.env, capture_output=True, text=True, timeout=30
            )
        except OSError, subprocess.SubprocessError:
            return None

    def _test_inner(self, flag: str, path: Path) -> bool:
        done = self._probe_inner(f"test {flag} {shlex.quote(str(path))}")
        return done is not None and done.returncode == 0

    @staticmethod
    def _add_mode(path: Path, bits: int) -> None:
        """OR ``bits`` into the mode of a real directory or regular file, never a link.

        Opens with O_NOFOLLOW and uses fchmod, so a symlink swapped in at the last moment is
        refused rather than followed. Anything else (links, sockets, files we do not own) is
        skipped.
        """
        try:
            info = os.lstat(path)
            if stat.S_ISDIR(info.st_mode):
                flags = os.O_RDONLY | os.O_DIRECTORY
            elif stat.S_ISREG(info.st_mode):
                flags = os.O_RDONLY | os.O_NONBLOCK
            else:
                return
            fd = os.open(path, flags | os.O_NOFOLLOW)
        except OSError:
            return
        try:
            mode = stat.S_IMODE(os.fstat(fd).st_mode)
            if flags & os.O_DIRECTORY == 0:  # a file: read/write, execute only if it already is
                bits = (bits & 0o666) | (bits & 0o111 if mode & 0o111 else 0)
            os.fchmod(fd, mode | bits)
        except PermissionError:
            pass
        finally:
            os.close(fd)
