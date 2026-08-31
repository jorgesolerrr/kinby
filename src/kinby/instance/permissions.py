"""Permission policy supplied to the gate for one turn."""

from __future__ import annotations

import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum

from pydantic import TypeAdapter, ValidationError

from kinby.contracts import PermissionMode
from kinby.instance.dataclasses import Instance
from kinby.instance.layout import PERMISSIONS_NAME


class PermissionsError(ValueError):
    """Raised when an instance permission policy cannot be loaded."""


class GateAction(StrEnum):
    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"


SHIPPED_BASH_DENY = (
    r"(?:^|[;&|\n]\s*)rm\s+-rf\s+(?:/instance|\$\{?KINBY_INSTANCE\}?)(?:/|\s|$)",
    r"\bgit\s+(?:reset\s+--hard|rebase|filter-branch)\b",
    r"\bgit\s+push\b[^\n]*(?:--force(?:-with-lease)?|-f(?:\s|$))",
)


@dataclass(frozen=True)
class BashPolicy:
    deny: tuple[str, ...] = SHIPPED_BASH_DENY
    ask: tuple[str, ...] = ()


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


def validate_bash_regexes(
    policy: GatePolicy,
    *,
    source: str = PERMISSIONS_NAME,
) -> None:
    """Reject invalid Bash patterns before the gate evaluates them."""
    for tier, patterns in (("deny", policy.bash.deny), ("ask", policy.bash.ask)):
        for index, pattern in enumerate(patterns):
            try:
                re.compile(pattern)
            except re.error as exc:
                raise PermissionsError(
                    f"{source}: bash.{tier}.{index}: invalid regex: {exc}"
                ) from exc


def load_permissions(instance: Instance) -> GatePolicy:
    """Read the instance permission policy or return kinby's shipped policy."""
    path = instance.path / PERMISSIONS_NAME
    try:
        with path.open("rb") as permissions_file:
            values = tomllib.load(permissions_file)
    except FileNotFoundError:
        return SHIPPED_POLICY
    except tomllib.TOMLDecodeError as exc:
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
