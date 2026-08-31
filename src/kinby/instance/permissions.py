"""Permission policy supplied to the gate for one turn."""

from __future__ import annotations

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


@dataclass(frozen=True)
class GatePolicy:
    mode: PermissionMode = PermissionMode.ASK
    ceiling: PermissionMode = PermissionMode.FULL_ACCESS
    tools: Mapping[str, GateAction] = field(default_factory=dict)


SHIPPED_POLICY = GatePolicy()
_GATE_POLICY = TypeAdapter(GatePolicy)
_POLICY_KEYS = frozenset({"mode", "ceiling", "tools"})


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
    try:
        policy = _GATE_POLICY.validate_python(values)
    except ValidationError as exc:
        first = exc.errors()[0]
        key = ".".join(str(part) for part in first["loc"])
        message = first["msg"].removeprefix("Value error, ")
        raise PermissionsError(f"{PERMISSIONS_NAME}: {key}: {message}") from exc
    if policy.mode is PermissionMode.AUTO:
        raise PermissionsError(
            f"{PERMISSIONS_NAME}: mode: auto is not supported until path bounds are available"
        )
    return policy
