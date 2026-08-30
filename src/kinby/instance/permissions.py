"""Permission policy supplied to the gate for one turn."""

from __future__ import annotations

from dataclasses import dataclass

from kinby.contracts import PermissionMode


@dataclass(frozen=True)
class GatePolicy:
    mode: PermissionMode = PermissionMode.ASK


SHIPPED_POLICY = GatePolicy()
