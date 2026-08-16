"""Execution boundary: disposable worktrees, isolation, argv-only execution.



Two separate guarantees live here and must not be confused:



* **Reset semantics** — worktrees make counterfactuals cheap. Without a cheap

  reset, "it passes now" is unfalsifiable.

* **Confinement** — worktrees do NOT confine executed code. `pytest` runs

  arbitrary repository code that can read credentials, write outside the tree

  and reach the network. Only the isolation tier below constrains that, and

  every receipt states the tier it actually ran under.



Nothing here ever builds a command from a string. `argv` arrives as a list and

is executed as a list; `shell=True` appears nowhere in the runtime.

"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from riftagent.records import IsolationLevel

# Variables a repository process legitimately needs. Everything else — and in

# particular every credential — is dropped rather than filtered, so a new

# secret-bearing variable is excluded by default instead of by maintenance.

ENV_ALLOWLIST = (
    "PATH",
    "LANG",
    "LC_ALL",
    "TZ",
    "TERM",
    "HOME",
    "TMPDIR",
    "SYSTEMROOT",
    "SystemRoot",
    "COMSPEC",
    "ComSpec",
    "PATHEXT",
    "WINDIR",
    "NUMBER_OF_PROCESSORS",
    "PROCESSOR_ARCHITECTURE",
)


IS_WINDOWS = sys.platform == "win32"

IS_LINUX = sys.platform.startswith("linux")


EXCLUDED_FROM_TREE_HASH = (".rift", ".git")


class SandboxError(RuntimeError):
    """Infrastructure failure. Never a statement about the repository."""


@dataclass(frozen=True)
class CommandResult:
    argv: tuple[str, ...]
    exit_code: int
    stdout: str
    stderr: str
    duration_s: float
    timed_out: bool = False

    @property
    def combined(self) -> str:
        return self.stdout + ("\n" + self.stderr if self.stderr else "")


@dataclass(frozen=True)
class IsolationProbe:
    level: IsolationLevel
    detail: str
    bwrap: str | None = None
    rlimits: bool = False
    tree_kill: bool = True


# --------------------------------------------------------------------------

# isolation probing

# --------------------------------------------------------------------------


def _bwrap_works() -> str | None:

    exe = shutil.which("bwrap")
    if not exe:
        return None
    try:
        proc = subprocess.run(
            [exe, "--ro-bind", "/", "/", "--dev", "/dev", "--unshare-net", "--", "/bin/true"],
            capture_output=True,
            timeout=20,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return exe if proc.returncode == 0 else None


def probe_isolation() -> IsolationProbe:
    """Report the isolation actually achievable here — never the one we want.



    A disclosed partial sandbox is a trust floor; an undisclosed one is the

    verification theatre this product exists to eliminate.

    """
    if IS_LINUX:
        bwrap = _bwrap_works()
        if bwrap:
            return IsolationProbe(
                IsolationLevel.FULL,
                "linux + bubblewrap: host read-only, worktree and tmp writable, network unshared",
                bwrap=bwrap,
                rlimits=True,
            )
        return IsolationProbe(
            IsolationLevel.PARTIAL,
            "linux without usable bubblewrap: env allowlist, rlimits, timeout, process-group kill",
            rlimits=True,
        )
    if IS_WINDOWS:
        return IsolationProbe(
            IsolationLevel.PARTIAL,
            "native windows: env allowlist, timeout, Job Object process-tree kill; "
            "no filesystem or network confinement",
            tree_kill=_windows_job_supported(),
        )
    return IsolationProbe(
        IsolationLevel.PARTIAL,
        f"{sys.platform}: env allowlist, timeout, process-group kill; no filesystem or network confinement",
        rlimits=not IS_WINDOWS,
    )


# --------------------------------------------------------------------------

# process-tree termination

# --------------------------------------------------------------------------


def _windows_job_supported() -> bool:

    if not IS_WINDOWS:
        return False
    try:
        import ctypes

        job = ctypes.windll.kernel32.CreateJobObjectW(None, None)  # type: ignore[attr-defined]
        if not job:
            return False
        ctypes.windll.kernel32.CloseHandle(job)  # type: ignore[attr-defined]
        return True
    except Exception:
        return False


class _WindowsJob:
    """A Job Object that kills the whole tree when terminated or closed.



    `taskkill /T` is not used: it walks a parent/child table that a

    re-parented grandchild has already left. A Job Object holds every

    descendant by construction, so termination cannot miss one.

    """

    def __init__(self) -> None:
        import ctypes
        import ctypes.wintypes as w

        self._ctypes = ctypes
        self._k32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        self.handle = self._k32.CreateJobObjectW(None, None)
        if not self.handle:
            raise SandboxError("CreateJobObject failed")

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_ulonglong),
                ("WriteOperationCount", ctypes.c_ulonglong),
                ("OtherOperationCount", ctypes.c_ulonglong),
                ("ReadTransferCount", ctypes.c_ulonglong),
                ("WriteTransferCount", ctypes.c_ulonglong),
                ("OtherTransferCount", ctypes.c_ulonglong),
            ]

        class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_longlong),
                ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", w.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", w.DWORD),
                ("Affinity", ctypes.POINTER(ctypes.c_ulong)),
                ("PriorityClass", w.DWORD),
                ("SchedulingClass", w.DWORD),
            ]

        class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
                ("IoInfo", IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self._k32.SetInformationJobObject(self.handle, 9, ctypes.byref(info), ctypes.sizeof(info)):
            self.close()
            raise SandboxError("SetInformationJobObject failed")

    def assign(self, proc: subprocess.Popen) -> None:
        if not self._k32.AssignProcessToJobObject(self.handle, int(proc._handle)):  # type: ignore[attr-defined]
            raise SandboxError("AssignProcessToJobObject failed")

    def terminate(self) -> None:
        self._k32.TerminateJobObject(self.handle, 1)

    def close(self) -> None:
        if self.handle:
            self._k32.CloseHandle(self.handle)
            self.handle = None


def _posix_limits(memory_bytes: int, processes: int):

    def _apply() -> None:  # pragma: no cover - runs in the forked child
        import resource

        os.setsid()
        try:
            resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))
        except (ValueError, OSError):
            pass
        try:
            resource.setrlimit(resource.RLIMIT_NPROC, (processes, processes))
        except (ValueError, OSError):
            pass

    return _apply


# --------------------------------------------------------------------------

# environment

# --------------------------------------------------------------------------


def build_env(worktree: Path, tmpdir: Path, extra: dict[str, str] | None = None) -> dict[str, str]:
    """Construct the child environment by allowlist.



    Credentials are never inherited: the model key, cloud keys and git tokens

    are absent because nothing copies them, not because a denylist removed

    them.

    """
    env: dict[str, str] = {}
    for name in ENV_ALLOWLIST:
        if name in os.environ:
            env[name] = os.environ[name]
    env["HOME"] = str(tmpdir)
    env["TMPDIR"] = str(tmpdir)
    env["TEMP"] = str(tmpdir)
    env["TMP"] = str(tmpdir)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONHASHSEED"] = "0"
    env["GIT_TERMINAL_PROMPT"] = "0"
    # Pin the reported width: pytest truncates its short summary to the
    # terminal, and a signature must not depend on the window it was captured
    # in.
    env["COLUMNS"] = "200"
    path_entries: list[str] = []
    src = worktree / "src"
    if src.is_dir():
        path_entries.append(str(src))
    runner_site = _runner_site_dir()
    if runner_site:
        # The runner is invoked as `sys.executable -m pytest`, so the child must
        # be able to import what this interpreter can. On Windows the user site
        # directory is located through APPDATA, which the allowlist withholds
        # because credentials live under it. Passing the resolved directory
        # keeps the runner working without granting the child the user profile:
        # a path is not a secret. It is appended, so the worktree still wins.
        path_entries.append(runner_site)
    if path_entries:
        env["PYTHONPATH"] = os.pathsep.join(path_entries)
    env.update(extra or {})
    return env


def _runner_site_dir() -> str | None:
    """Where this interpreter found pytest, if anywhere.



    Reporting `missing_runner` when the runner is in fact installed would be a

    false statement about the environment, which is the class of error this

    product exists to remove.

    """
    try:
        import importlib.util

        spec = importlib.util.find_spec("pytest")
    except (ImportError, ValueError):
        return None
    if spec is None or not spec.origin:
        return None
    return str(Path(spec.origin).parent.parent)


# --------------------------------------------------------------------------

# execution

# --------------------------------------------------------------------------


def run_argv(
    argv: list[str],
    cwd: Path,
    env: dict[str, str],
    timeout_s: float,
    probe: IsolationProbe,
    allow_network: bool = False,
) -> CommandResult:
    """Execute one argv list. No shell, no string interpolation, ever."""
    if probe.level is IsolationLevel.FULL and probe.bwrap:
        argv = _wrap_bwrap(argv, cwd, Path(env["TMPDIR"]), probe.bwrap, allow_network)
    t0 = time.time()
    job = None
    popen_kwargs: dict = {
        "cwd": str(cwd),
        "env": env,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
    }
    if IS_WINDOWS:
        popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    elif probe.rlimits:
        popen_kwargs["preexec_fn"] = _posix_limits(4 * 1024 * 1024 * 1024, 512)
    else:
        popen_kwargs["start_new_session"] = True
    try:
        proc = subprocess.Popen(argv, **popen_kwargs)
    except FileNotFoundError as exc:
        raise SandboxError(f"executable not found: {argv[0]}") from exc
    except OSError as exc:
        raise SandboxError(f"spawn failed: {exc}") from exc
    if IS_WINDOWS and probe.tree_kill:
        try:
            job = _WindowsJob()
            job.assign(proc)
        except SandboxError:
            job = None
    timed_out = False
    try:
        stdout, stderr = proc.communicate(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        timed_out = True
        _kill_tree(proc, job)
        stdout, stderr = proc.communicate()
    finally:
        if job is not None:
            job.close()
    return CommandResult(
        argv=tuple(argv),
        exit_code=proc.returncode if proc.returncode is not None else -1,
        stdout=stdout or "",
        stderr=stderr or "",
        duration_s=time.time() - t0,
        timed_out=timed_out,
    )


def _kill_tree(proc: subprocess.Popen, job: _WindowsJob | None) -> None:

    if job is not None:
        job.terminate()
        return
    if IS_WINDOWS:
        proc.kill()
        return
    try:
        os.killpg(os.getpgid(proc.pid), 9)
    except (ProcessLookupError, PermissionError, OSError):
        proc.kill()


def _wrap_bwrap(argv: list[str], cwd: Path, tmpdir: Path, bwrap: str, allow_network: bool) -> list[str]:

    wrapped = [
        bwrap,
        "--ro-bind",
        "/",
        "/",
        "--dev",
        "/dev",
        "--proc",
        "/proc",
        "--bind",
        str(cwd),
        str(cwd),
        "--bind",
        str(tmpdir),
        str(tmpdir),
        "--chdir",
        str(cwd),
        "--die-with-parent",
        "--unshare-pid",
    ]
    if not allow_network:
        wrapped.append("--unshare-net")
    return [*wrapped, "--", *argv]


# --------------------------------------------------------------------------

# repository trees

# --------------------------------------------------------------------------


def is_git_repo(root: Path) -> bool:

    return (Path(root) / ".git").exists()


def _git(root: Path, *args: str, check: bool = True, timeout: float = 120.0) -> CommandResult:

    argv = ["git", "-C", str(root), *args]
    env = {k: v for k, v in os.environ.items() if k in ENV_ALLOWLIST}
    env.setdefault("GIT_TERMINAL_PROMPT", "0")
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    t0 = time.time()
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, env=env, errors="replace")
    except FileNotFoundError as exc:
        raise SandboxError("git is not installed") from exc
    except subprocess.TimeoutExpired as exc:
        raise SandboxError(f"git timed out: {' '.join(args)}") from exc
    res = CommandResult(tuple(argv), proc.returncode, proc.stdout, proc.stderr, time.time() - t0)
    if check and res.exit_code != 0:
        raise SandboxError(f"git {' '.join(args)} failed: {res.stderr.strip() or res.stdout.strip()}")
    return res


def tracked_files(root: Path) -> list[str]:

    root = Path(root)
    if is_git_repo(root):
        out = _git(root, "ls-files", "-z").stdout
        names = [n for n in out.split("\0") if n]
    else:
        names = []
        for path in sorted(root.rglob("*")):
            if path.is_file():
                rel = path.relative_to(root).as_posix()
                names.append(rel)
    return sorted(n for n in names if not any(n == e or n.startswith(e + "/") for e in EXCLUDED_FROM_TREE_HASH))


def tree_hash(root: Path) -> str:
    """Content hash of the tracked working tree, excluding `.rift/`.



    Used only to detect drift between an interruption and a resume. Any

    tracked change invalidates recorded evidence; the runtime never reasons

    about which changed file "cannot matter".

    """
    root = Path(root)
    digest = hashlib.sha256()
    for name in tracked_files(root):
        path = root / name
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        try:
            digest.update(hashlib.sha256(path.read_bytes()).digest())
        except OSError:
            digest.update(b"<unreadable>")
        digest.update(b"\0")
    return digest.hexdigest()


class Worktree:
    """A disposable copy of the repository, rooted outside it."""

    def __init__(self, repo_root: Path, label: str):
        self.repo_root = Path(repo_root).resolve()
        self.label = label
        self._base = Path(tempfile.mkdtemp(prefix=f"rift_{label}_"))
        self.path = self._base / "repo"
        self.tmpdir = self._base / "tmp"
        self.tmpdir.mkdir(parents=True, exist_ok=True)
        self._git_worktree = False
        self._materialise()
        self._manifest = {p.relative_to(self.path).as_posix() for p in self.path.rglob("*") if ".git" not in p.parts}

    def _materialise(self) -> None:
        if is_git_repo(self.repo_root):
            try:
                _git(self.repo_root, "worktree", "add", "--detach", "--force", str(self.path), "HEAD")
                self._git_worktree = True
                self._copy_untracked()
                return
            except SandboxError:
                self._git_worktree = False
        shutil.copytree(
            self.repo_root,
            self.path,
            ignore=shutil.ignore_patterns(".git", ".rift", "*.egg-info", "__pycache__", ".pytest_cache"),
        )

    def _copy_untracked(self) -> None:
        """A worktree carries committed content only. Uncommitted tracked

        modifications are part of the state under test, so they are carried

        over; `.rift/` never is."""
        res = _git(self.repo_root, "status", "--porcelain=1", "-z", "--untracked-files=all", check=False)
        for entry in res.stdout.split("\0"):
            if len(entry) < 4:
                continue
            rel = entry[3:]
            if not rel or rel.startswith(".rift"):
                continue
            src = self.repo_root / rel
            dst = self.path / rel
            if src.is_file():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)

    def apply_patch(self, diff: str, reverse: bool = False) -> None:
        """Apply a unified diff, or raise without touching the tree.



        Two strip levels are attempted. `git apply` defaults to `-p1`, which

        assumes the `a/`/`b/` prefixes `git diff` writes; plain `diff -u` and

        many other generators emit bare repository-relative paths, which need

        `-p0`. Live calibration lost a semantically correct patch to exactly

        this — the fix was right and the prefix convention was not.



        This is a format accommodation and nothing more. `--check` must still

        pass at whichever level is used, so a patch that does not apply exactly

        is still refused; and the counterfactual gate, not this method, decides

        whether an applied patch means anything.

        """
        patch_file = self._base / ("patch.diff" if not reverse else "patch_rev.diff")
        patch_file.write_text(diff, encoding="utf-8", newline="\n")
        base = ["apply", "--whitespace=nowarn"]
        if reverse:
            base.append("--reverse")
        failures: list[str] = []
        for level in ("-p1", "-p0"):
            args = [*base, level]
            check = _git(self.path, *args, "--check", str(patch_file), check=False)
            if check.exit_code == 0:
                _git(self.path, *args, str(patch_file))
                return
            failures.append(f"{level}: {check.stderr.strip() or check.stdout.strip()}")
        raise SandboxError(
            f"git apply --check failed ({'reverse' if reverse else 'forward'}) at every strip "
            f"level: {'; '.join(failures)}"
        )

    def tracked_manifest(self) -> set[str]:
        """Every path present when this worktree was materialised.



        Recorded once at construction, so anything appearing later is state a

        *phase* created rather than state the repository contains. Comparing

        against this set is what lets the reset remove arbitrary generated

        files — a sqlite database, a lock file, a fixture — instead of only the

        cache names someone thought to enumerate.

        """
        return self._manifest

    def hash(self) -> str:
        return tree_hash(self.path)

    def phase_state_hash(self, patch_paths: frozenset[str] = frozenset()) -> str:
        """Hash only the files whose state a gate phase is answerable for.

        `hash()` covers everything in the worktree, which is right for
        recording a tree but wrong for validating a phase: in a non-Git
        worktree an ordinary runtime file written by a test changes it, and the
        post-execution check then reports tracked-tree corruption for debris
        that `reset_episode` is about to remove anyway.

        The considered set is the construction-time baseline plus the paths the
        frozen patch owns. Absence is hashed explicitly, so a deleted baseline
        file, a deleted patch-owned file, and an unexpectedly *restored*
        patch-deleted file all change the digest. Everything else is runtime
        state, governed by `reset_episode` and deliberately invisible here.

        Identical semantics for Git and non-Git repositories: it reads the
        worktree, never the index.
        """
        digest = hashlib.sha256()
        for rel in sorted(set(self._manifest) | set(patch_paths)):
            path = self.path / rel
            digest.update(rel.encode("utf-8"))
            digest.update(b"\x00")
            if path.is_file():
                digest.update(hashlib.sha256(path.read_bytes()).digest())
            elif path.is_dir():
                digest.update(b"<dir>")
            else:
                digest.update(b"<absent>")
            digest.update(b"\x00")
        return digest.hexdigest()

    def dispose(self) -> None:
        if self._git_worktree:
            _git(self.repo_root, "worktree", "remove", "--force", str(self.path), check=False)
            _git(self.repo_root, "worktree", "prune", check=False)
        shutil.rmtree(self._base, ignore_errors=True)

    def __enter__(self) -> Worktree:
        return self

    def __exit__(self, *exc: object) -> None:
        self.dispose()


__all__ = [
    "ENV_ALLOWLIST",
    "CommandResult",
    "IsolationProbe",
    "SandboxError",
    "Worktree",
    "build_env",
    "is_git_repo",
    "probe_isolation",
    "run_argv",
    "tracked_files",
    "tree_hash",
]
