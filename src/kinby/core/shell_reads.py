"""Tell a shell read: a bash command that only reads inside the workspace (ADR 0119), the
turn's saved outputs (ADR 0121), or the hub through kinby's CLI (ADR 0120).

A command this module cannot parse or does not recognise is not a shell read, so a wrong guess
asks.
"""

from __future__ import annotations

import re
from glob import glob
from pathlib import Path

from kinby.contracts import HUB_READS, RELAYED_READS

#: The programs a shell read may run. git is not one: its repository's config, attributes and
#: hooks sit in the workspace, which auto writes unasked, and can make any git command run a
#: program.
_PROGRAMS = frozenset(
    {"cd", "pwd", "ls", "cat", "head", "tail", "wc", "grep", "find", "stat", "file", "du", "tree"}
    | {"diff", "cut", "tr", "sort", "echo", "printf", "date", "basename", "dirname", "realpath"}
    | {"jq", "bc", "expr", "true"}
)
_FIND_UNSAFE = frozenset(
    {"-exec", "-execdir", "-ok", "-okdir", "-delete", "-fprint", "-fprint0", "-fprintf", "-fls"}
    | {"-L", "-follow"}
)
#: The short flags and long flag prefixes that make a program write, set a shell variable such as
#: PATH, run another program, follow links while it walks a directory, or read its input's names or
#: its own program from a file. Long flags are prefixes because GNU tools accept any unambiguous
#: abbreviation.
_UNSAFE_FLAGS: dict[str, tuple[str, tuple[str, ...]]] = {
    "sort": ("o", ("--o", "--co", "--fi")),
    "tree": ("oRl", ("--o",)),
    "date": ("s", ("--s",)),
    "file": ("C", ("--co",)),
    "grep": ("R", ("--de",)),
    "ls": ("L", ("--de",)),
    "du": ("L", ("--de",)),
    "diff": ("r", ("--rec",)),
    "jq": ("f", ("--f",)),
    "printf": ("v", ()),
}
#: Characters that start an expansion, a subshell, a group or an input redirection.
_UNSUPPORTED = frozenset("$`(){}<")
_TOKEN = re.compile(
    r"""(?P<space>[ \t]+)
    |(?P<separator>\|\||&&|[|;\n])
    |(?P<redirect>(?:\d+|&)?>>?(?:&\d+(?![^\s|;&]))?)
    |(?P<word>(?:[^\s|;&<>'"\\]|\\.|'[^']*'|"(?:[^"\\]|\\.)*")+)""",
    re.VERBOSE | re.DOTALL,
)
_WORD_PART = re.compile(r"""'([^']*)'|"((?:[^"\\]|\\.)*)"|\\(.)|([^'"\\]+)""", re.DOTALL)
_DOUBLE_QUOTED_ESCAPE = re.compile(r'\\([\\"\n])')


def is_shell_read(command: str, workspace: Path, *, saved_outputs: Path | None = None) -> bool:
    """Whether *command* only runs programs that read, on paths inside *workspace* or the turn's
    *saved_outputs*, through any link or glob."""
    commands = _commands(command)
    if not commands:
        return False
    root = workspace.resolve()
    roots = (root,) if saved_outputs is None else (root, saved_outputs.resolve())
    # Every directory a command may run in: a cd before it may have failed, or run in a pipe.
    directories = {root}
    for program, *arguments in commands:
        if not _reads(program, arguments, directories, roots):
            return False
        if program == "cd":
            directories |= {(each / arguments[0]).resolve() for each in directories}
    return True


def _commands(command: str) -> list[list[str]] | None:
    """The words of each simple command in *command*, or None when it holds anything but simple
    commands joined by ``|``, ``;``, ``&&``, ``||`` or newlines, with output sent only to
    ``/dev/null`` or another output."""
    if _UNSUPPORTED & set(command):
        return None
    commands: list[list[str]] = [[]]
    awaiting_target = False
    position = 0
    while position < len(command):
        token = _TOKEN.match(command, position)
        if token is None:
            return None
        position = token.end()
        kind, text = token.lastgroup, token.group()
        if kind == "space":
            continue
        if awaiting_target:
            if kind != "word" or _unquoted(text) != "/dev/null":
                return None
            awaiting_target = False
        elif kind == "separator":
            commands.append([])
        elif kind == "redirect":
            awaiting_target = "&" not in text.lstrip("&")
        elif text.startswith("#"):
            return None
        else:
            commands[-1].append(_unquoted(text))
    if awaiting_target:
        return None
    return [words for words in commands if words]


def _unquoted(word: str) -> str:
    """*word* as bash hands it to a program, quotes and escapes removed."""
    parts = []
    for single, double, escaped, plain in _WORD_PART.findall(word):
        if escaped:
            parts.append("" if escaped == "\n" else escaped)
        else:
            parts.append(single or plain or _DOUBLE_QUOTED_ESCAPE.sub(_escaped, double))
    return "".join(parts)


def _escaped(match: re.Match[str]) -> str:
    return "" if match.group(1) == "\n" else match.group(1)


def _reads(
    program: str, arguments: list[str], directories: set[Path], roots: tuple[Path, ...]
) -> bool:
    if program == "kinby":
        return _hub_read(arguments)
    if program not in _PROGRAMS or any(_unsafe(program, each) for each in arguments):
        return False
    if not all(_confined(each, directories, roots) for each in arguments):
        return False
    match program:
        case "cd":
            # Without a directory cd goes home, cd - goes wherever OLDPWD says, and CDPATH can
            # send any directory not led by / or . outside the workspace.
            return len(arguments) == 1 and arguments[0].startswith(("/", "."))
        case "jq":
            # env reads the environment, and a module can load from outside the workspace.
            return not any(re.search(r"\b(env|include|import)\b", each) for each in arguments)
    return True


def _hub_read(arguments: list[str]) -> bool:
    """Whether kinby's *arguments* only read the hub. Their values go to the hub, never to a
    path."""
    match arguments:
        case ["hub", "read", method, *_]:
            return method in HUB_READS | RELAYED_READS
        case ["hub", "help" | "methods" | "wait", *_]:
            return True
    return False


def _unsafe(program: str, argument: str) -> bool:
    if program == "find":
        return argument in _FIND_UNSAFE
    letters, prefixes = _UNSAFE_FLAGS.get(program, ("", ()))
    if argument.startswith("--"):
        return argument.startswith(prefixes)
    return argument.startswith("-") and any(letter in argument[1:] for letter in letters)


def _confined(argument: str, directories: set[Path], roots: tuple[Path, ...]) -> bool:
    """Whether *argument* names no path outside *roots* from any of *directories*."""
    if argument.startswith("~") or "=~" in argument or ":~" in argument:
        return False
    if not argument.startswith("-"):
        return _inside(argument, directories, roots)
    # An absolute path glued to a flag, as in -f/path, is not worth telling apart.
    if "/" in argument:
        return False
    glued = argument.partition("=")[2] if argument.startswith("--") else argument[2:]
    return not glued or _inside(glued, directories, roots)


def _inside(name: str, directories: set[Path], roots: tuple[Path, ...]) -> bool:
    """Whether *name*, read from each of *directories*, resolves inside *roots* through its links,
    and so does everything it matches as a glob."""
    # Before bash 5.2, a glob after a leading dot, as in .?, matches the parent directory.
    if any(part.startswith(".") and re.search(r"[*?\[]", part) for part in name.split("/")):
        return False
    for directory in directories:
        path = directory / name
        for each in glob(str(path)) or [path]:
            resolved = Path(each).resolve()
            if not any(resolved.is_relative_to(root) for root in roots):
                return False
    return True
