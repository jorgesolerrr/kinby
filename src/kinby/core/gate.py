"""Decide whether one tool call may run."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from kinby.contracts import PermissionMode, ToolCall
from kinby.instance.permissions import GateAction, GatePolicy
from kinby.plugins.tools import Tool


@dataclass(frozen=True)
class GateDecision:
    action: GateAction
    rule: str


_PRESETS = {
    PermissionMode.READ_ONLY: {
        False: GateAction.ALLOW,
        True: GateAction.DENY,
    },
    PermissionMode.ASK: {
        False: GateAction.ALLOW,
        True: GateAction.ASK,
    },
    PermissionMode.AUTO: {
        False: GateAction.ALLOW,
        True: GateAction.ASK,
    },
    PermissionMode.FULL_ACCESS: {
        False: GateAction.ALLOW,
        True: GateAction.ALLOW,
    },
}


def evaluate(
    policy: GatePolicy,
    mode: PermissionMode,
    call: ToolCall,
    tool: Tool | None,
    workspace: Path,
) -> GateDecision:
    """Evaluate one call without running the tool or changing state."""
    override = policy.tools.get(call.name)
    if override is not None:
        return GateDecision(override, f"tools.{call.name}")
    writes = tool is not None and tool.write
    if (
        mode is PermissionMode.AUTO
        and writes
        and _paths_are_inside_workspace(tool, call, workspace)
    ):
        return GateDecision(GateAction.ALLOW, "mode.auto.workspace")
    return GateDecision(
        _PRESETS[mode][writes],
        f"mode.{mode.value}.{'write' if writes else 'read'}",
    )


def _paths_are_inside_workspace(tool: Tool, call: ToolCall, workspace: Path) -> bool:
    if not tool.paths:
        return False
    root = workspace.resolve()
    for parameter in tool.paths:
        path = call.arguments.get(parameter)
        if not isinstance(path, str) or not (root / path).resolve().is_relative_to(root):
            return False
    return True
