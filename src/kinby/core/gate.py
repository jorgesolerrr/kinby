"""Decide whether one tool call may run."""

from __future__ import annotations

import re
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
    if (
        mode is not PermissionMode.READ_ONLY
        and call.name == "bash"
        and isinstance(command := call.arguments.get("command"), str)
    ):
        for index, pattern in enumerate(policy.bash.deny):
            if re.search(pattern, command):
                return GateDecision(GateAction.DENY, f"bash.deny[{index}]")
        for index, pattern in enumerate(policy.bash.ask):
            if re.search(pattern, command):
                return GateDecision(GateAction.ASK, f"bash.ask[{index}]")
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
    arguments = tool.resolve_paths(call.arguments, root)
    for parameter in tool.paths:
        path = arguments.get(parameter)
        if not isinstance(path, str) or not Path(path).is_relative_to(root):
            return False
    return True
