"""Permission policy supplied to the gate for one turn."""

from __future__ import annotations

import re
import tomllib
from collections.abc import Mapping, MutableMapping
from dataclasses import dataclass, field

import tomlkit
from pydantic import TypeAdapter, ValidationError

from kinby.contracts import GateAction, PermissionMode
from kinby.instance.dataclasses import Instance
from kinby.instance.layout import PERMISSIONS_NAME


class PermissionsError(ValueError):
    """Raised when an instance permission policy cannot be loaded."""


SHIPPED_BASH_DENY = (
    r"(?:^|[;&|\n]\s*)rm\s+-rf\s+(?:/instance|\$\{?KINBY_INSTANCE\}?)(?:/|\s|$)",
    r"\bgit\s+(?:reset\s+--hard|rebase|filter-branch)\b",
    r"\bgit\s+push\b[^\n]*(?:--force(?:-with-lease)?|-f(?:\s|$))",
)


@dataclass(frozen=True)
class BashPolicy:
    #: The instance's own deny patterns, which add to the shipped ones (ADR 0069).
    deny: tuple[str, ...] = ()
    ask: tuple[str, ...] = ()

    @property
    def denylist(self) -> tuple[str, ...]:
        """The deny patterns the gate applies: the shipped ones, then the instance's own."""
        return tuple(dict.fromkeys((*SHIPPED_BASH_DENY, *self.deny)))


@dataclass(frozen=True)
class GatePolicy:
    mode: PermissionMode = PermissionMode.ASK
    ceiling: PermissionMode = PermissionMode.FULL_ACCESS
    tools: Mapping[str, GateAction] = field(default_factory=dict)
    bash: BashPolicy = field(default_factory=BashPolicy)


SHIPPED_POLICY = GatePolicy()
_GATE_POLICY = TypeAdapter(GatePolicy)
_POLICY_KEYS = frozenset({"mode", "ceiling", "tools", "bash"})
_BASH_KEYS = frozenset({"deny", "ask"})


def bash_regex_errors(bash: BashPolicy) -> dict[str, str]:
    """What is wrong with each Bash pattern that is not a valid regex, by its key in the file."""
    errors: dict[str, str] = {}
    for tier, patterns in (("deny", bash.deny), ("ask", bash.ask)):
        for index, pattern in enumerate(patterns):
            try:
                re.compile(pattern)
            except re.error as exc:
                errors[f"bash.{tier}.{index}"] = f"invalid regex: {exc}"
    return errors


def validate_bash_regexes(
    policy: GatePolicy,
    *,
    source: str = PERMISSIONS_NAME,
) -> None:
    """Reject invalid Bash patterns before the gate evaluates them."""
    errors = bash_regex_errors(policy.bash)
    if errors:
        key, message = next(iter(errors.items()))
        raise PermissionsError(f"{source}: {key}: {message}")


def load_permissions(instance: Instance) -> GatePolicy:
    """Read the instance permission policy or return kinby's shipped policy."""
    try:
        content = (instance.path / PERMISSIONS_NAME).read_bytes()
    except FileNotFoundError:
        return SHIPPED_POLICY
    return parse_permissions(content)


def parse_permissions(content: bytes) -> GatePolicy:
    """Parse the bytes of a ``permissions.toml``."""
    try:
        values = tomllib.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise PermissionsError(f"{PERMISSIONS_NAME}: {exc}") from exc
    unknown = values.keys() - _POLICY_KEYS
    if unknown:
        key = min(unknown)
        raise PermissionsError(f"{PERMISSIONS_NAME}: {key}: Extra inputs are not permitted")
    bash_values = values.get("bash")
    if isinstance(bash_values, Mapping) and (unknown := bash_values.keys() - _BASH_KEYS):
        key = min(unknown)
        raise PermissionsError(f"{PERMISSIONS_NAME}: bash.{key}: Extra inputs are not permitted")
    try:
        policy = _GATE_POLICY.validate_python(values)
    except ValidationError as exc:
        first = exc.errors()[0]
        key = ".".join(str(part) for part in first["loc"])
        message = first["msg"].removeprefix("Value error, ")
        raise PermissionsError(f"{PERMISSIONS_NAME}: {key}: {message}") from exc
    validate_bash_regexes(policy)
    return policy


def edited_permissions(text: str, policy: GatePolicy) -> str:
    """Write *policy* into the text of ``permissions.toml``, keeping its comments.

    Only the values that change are touched. The file lists only the instance's own
    deny patterns; an empty *text* gives a whole new file.
    """
    document = tomlkit.parse(text)
    _set_changed(document, "mode", policy.mode.value)
    _set_changed(document, "ceiling", policy.ceiling.value)
    tools = document.setdefault("tools", tomlkit.table())
    for name in tools.keys() - policy.tools.keys():
        del tools[name]
    for name, action in policy.tools.items():
        _set_changed(tools, name, action.value)
    bash = document.setdefault("bash", tomlkit.table())
    _set_changed(bash, "deny", list(policy.bash.deny))
    _set_changed(bash, "ask", list(policy.bash.ask))
    edited = tomlkit.dumps(document)
    parse_permissions(edited.encode("utf-8"))
    return edited


def _set_changed(table: MutableMapping[str, object], key: str, value: object) -> None:
    if table.get(key) != value:
        table[key] = value


_MODE_ORDER = (
    PermissionMode.READ_ONLY,
    PermissionMode.ASK,
    PermissionMode.AUTO,
    PermissionMode.FULL_ACCESS,
)


def constrain_mode(mode: PermissionMode, ceiling: PermissionMode) -> PermissionMode:
    if exceeds_ceiling(mode, ceiling):
        return ceiling
    return mode


def exceeds_ceiling(mode: PermissionMode, ceiling: PermissionMode) -> bool:
    return _MODE_ORDER.index(mode) > _MODE_ORDER.index(ceiling)
