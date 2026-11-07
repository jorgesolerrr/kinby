"""Tell a shell read: a bash command that only reads inside the workspace (ADR 0119).

A command this module cannot parse or does not recognise is not a shell read, so a wrong guess
asks.
"""

from __future__ import annotations

import re
from pathlib import Path

#: The programs a shell read may run. git is limited further to its reading subcommands.
_PROGRAMS = frozenset(
    {"cd", "pwd", "ls", "cat", "head", "tail", "wc", "grep", "find", "stat", "file", "du", "tree"}
    | {"diff", "cut", "tr", "sort", "echo", "printf", "date", "basename", "dirname", "realpath"}
    | {"jq", "bc", "expr", "true", "git"}
)
_GIT_READS = frozenset({"status", "log", "diff", "show"})
_FIND_WRITES = frozenset(
    {"-exec", "-execdir", "-ok", "-okdir", "-delete", "-fprint", "-fprint0", "-fprintf", "-fls"}
)
#: The short flags and long flag prefixes that make a program write or run another program. Long
#: flags are prefixes because GNU tools accept any unambiguous abbreviation.
_WRITE_FLAGS: dict[str, tuple[str, tuple[str, ...]]] = {
    "sort": ("o", ("--o", "--co")),
    "tree": ("oR", ("--o",)),
    "date": ("s", ("--s",)),
    "file": ("C", ("--co",)),
    "git": ("", ("--ou",)),
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


def is_shell_read(command: str, workspace: Path) -> bool:
    """Whether *command* only runs programs that read, on paths inside *workspace*."""
    commands = _commands(command)
    return bool(commands) and all(_reads(words, workspace) for words in commands)


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


def _reads(words: list[str], workspace: Path) -> bool:
    program, *arguments = words
    if program not in _PROGRAMS or not all(_confined(each, workspace) for each in arguments):
        return False
    if any(_writes(program, each) for each in arguments):
        return False
    match program:
        case "git":
            return arguments[:1] != [] and arguments[0] in _GIT_READS
        case "cd":
            # Without a directory cd goes home, cd - goes wherever OLDPWD says, and CDPATH can
            # send any directory not led by / or . outside the workspace.
            return len(arguments) == 1 and arguments[0].startswith(("/", "."))
        case "jq":
            # env reads the environment, and a module can load from outside the workspace.
            return not any(re.search(r"\b(env|include|import)\b", each) for each in arguments)
    return True


def _writes(program: str, argument: str) -> bool:
    if program == "find":
        return argument in _FIND_WRITES
    letters, prefixes = _WRITE_FLAGS.get(program, ("", ()))
    if argument.startswith("--"):
        return argument.startswith(prefixes)
    return argument.startswith("-") and any(letter in argument[1:] for letter in letters)


def _confined(argument: str, workspace: Path) -> bool:
    """Whether *argument* names no path outside *workspace*."""
    if argument.startswith("~") or "=~" in argument or ":~" in argument:
        return False
    if argument.startswith("-"):
        # A path glued to a flag, as in -f/path or --file=path, is not worth telling apart.
        return "/" not in argument
    parts = re.split(r"[/:=]", argument)
    if ".." in parts:
        return False
    # A glob after a leading dot, as in .?, can match the parent directory.
    if any(part.startswith(".") and re.search(r"[*?\[]", part) for part in parts):
        return False
    return not argument.startswith("/") or Path(argument).is_relative_to(workspace)
