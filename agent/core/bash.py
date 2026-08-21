"""Read-only shell gate: allow inspect commands, reject anything that can write."""

from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess

SHELL_META = re.compile(r"[;&|`$<>\n\r]")

ALLOWED = {
    "ls",
    "dir",
    "cat",
    "type",
    "head",
    "tail",
    "wc",
    "pwd",
    "whoami",
    "date",
    "uname",
    "find",
    "grep",
    "egrep",
    "fgrep",
    "rg",
    "findstr",
    "less",
    "more",
    "file",
    "stat",
    "du",
    "df",
    "tree",
    "echo",
    "printenv",
    "which",
    "where",
    "realpath",
    "readlink",
    "basename",
    "dirname",
    "git",
    "python",
    "python3",
    "py",
}

GIT_READ = {
    "status",
    "log",
    "diff",
    "show",
    "ls-files",
    "ls-tree",
    "branch",
    "remote",
    "rev-parse",
    "describe",
    "blame",
    "cat-file",
    "grep",
    "shortlog",
}

VERSION_ONLY = {"python", "python3", "py"}
FIND_WRITE_FLAGS = {"-delete", "-exec", "-execdir", "-ok", "-okdir"}


def _prog(token: str) -> str:
    name = os.path.basename(token).lower()
    return name[:-4] if name.endswith(".exe") else name


def deny_reason(command: str) -> str | None:
    if not command or not command.strip():
        return "empty command"
    if SHELL_META.search(command):
        return "shell operators are not allowed in read-only mode"
    try:
        argv = shlex.split(command)
    except ValueError as e:
        return f"could not parse command: {e}"
    if not argv:
        return "empty command"

    prog = _prog(argv[0])
    if prog not in ALLOWED:
        return f"'{prog}' is not a read-only command"

    if prog in VERSION_ONLY:
        args = argv[1:]
        if args not in (["--version"], ["-V"], ["version"]):
            return "python may only be used as 'python --version' (or -V) in read-only mode"

    if prog == "git":
        if "-c" in argv[1:]:
            return "git -c is not allowed in read-only mode"
        rest = [a for a in argv[1:] if not a.startswith("-")]
        if not rest:
            return "git needs a read-only subcommand (status, log, diff, ...)"
        sub = rest[0]
        if sub not in GIT_READ:
            return f"git {sub} is not allowed in read-only mode"

    if prog == "find" and FIND_WRITE_FLAGS.intersection(argv[1:]):
        return "find -delete/-exec is not allowed in read-only mode"

    return None


def _bash_exe() -> str | None:
    git_bash = os.path.join("C:\\Program Files", "Git", "bin", "bash.exe")
    if os.path.isfile(git_bash):
        return git_bash
    bash = shutil.which("bash")
    if not bash:
        return None
    if os.name == "nt" and os.path.normcase(bash).endswith(os.path.normcase("\\system32\\bash.exe")):
        return None
    return bash


def run_read_only(command: str) -> str:
    blocked = deny_reason(command)
    if blocked:
        return f"BLOCKED: {blocked}"

    bash = _bash_exe()
    try:
        if bash:
            completed = subprocess.run(
                [bash, "-lc", command],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
            )
        else:
            completed = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
            )
    except Exception as e:
        return f"ERROR: {e}"

    out = ((completed.stdout or "") + (completed.stderr or "")).strip()
    if out:
        return out
    return f"(exit {completed.returncode})"
