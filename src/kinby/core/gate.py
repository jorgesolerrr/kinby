"""Decide whether one tool call may run."""

from __future__ import annotations

import re
from collections.abc import Collection
from dataclasses import dataclass
from pathlib import Path

from kinby.contracts import GateRule, PermissionMode, ToolCall
from kinby.instance.permissions import GateAction, GatePolicy
from kinby.plugins.tools import Tool


@dataclass(frozen=True)
class GateDecision:
    action: GateAction
    rule: GateRule


#: The rule of a call the gate treats as a write because an authored tool makes it.
AUTHORED_RULE = GateRule("authored.write")
#: The tools that open a URL. Each runs unasked for a URL already seen in the thread, and as a
#: write for any other (ADR 0110).
URL_READS = frozenset({"web_fetch", "browser_open"})

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
    *,
    granted: bool = False,
    marked_read_only: bool = False,
    seen_urls: Collection[str] = (),
) -> GateDecision:
    """Evaluate one call without running the tool or changing state.

    A hub tool's write asks in every mode but read-only, ahead of any rule (ADR 0096).
    Consolidation's own write runs unasked in every mode but read-only, where it is denied,
    also ahead of any rule (ADR 0100). So do notify and a view write, unless a rule names the
    tool (ADR 0103, ADR 0108). A routine grant, when *granted*, allows the call ahead of the mode
    in every mode but read-only, so the ceiling still bounds it (ADR 0112). An authored tool is a
    write whatever it declares, unless the user *marked_read_only* it (ADR 0109). A tool that
    opens a URL is allowed for one of the thread's *seen_urls*, and a write for any other
    (ADR 0110).
    """
    if tool is not None and tool.hub and tool.write:
        action = GateAction.DENY if mode is PermissionMode.READ_ONLY else GateAction.ASK
        return GateDecision(action, GateRule("hub.write"))
    if tool is not None and tool.consolidation:
        action = GateAction.DENY if mode is PermissionMode.READ_ONLY else GateAction.ALLOW
        return GateDecision(action, GateRule("consolidation.write"))
    if tool is not None and tool.notify and call.name not in policy.tools:
        action = GateAction.DENY if mode is PermissionMode.READ_ONLY else GateAction.ALLOW
        return GateDecision(action, GateRule("notify"))
    if tool is not None and tool.view and call.name not in policy.tools:
        action = GateAction.DENY if mode is PermissionMode.READ_ONLY else GateAction.ALLOW
        return GateDecision(action, GateRule("view"))
    if (
        mode is not PermissionMode.READ_ONLY
        and call.name == "bash"
        and isinstance(command := call.arguments.get("command"), str)
    ):
        for index, pattern in enumerate(policy.bash.denylist):
            if re.search(pattern, command):
                return GateDecision(GateAction.DENY, GateRule(f"bash.deny[{index}]"))
        for index, pattern in enumerate(policy.bash.ask):
            if re.search(pattern, command):
                return GateDecision(GateAction.ASK, GateRule(f"bash.ask[{index}]"))
    override = policy.tools.get(call.name)
    if override is not None:
        return GateDecision(override, GateRule(f"tools.{call.name}"))
    if granted and mode is not PermissionMode.READ_ONLY:
        return GateDecision(GateAction.ALLOW, GateRule("routine.grant"))
    if call.name in URL_READS:
        if call.arguments.get("url") in seen_urls:
            return GateDecision(GateAction.ALLOW, GateRule("web.seen_url"))
        return GateDecision(_PRESETS[mode][True], GateRule("web.unseen_url"))
    if tool is not None and tool.authored and not marked_read_only:
        return GateDecision(_PRESETS[mode][True], AUTHORED_RULE)
    writes = tool is not None and tool.write and not tool.authored
    if (
        mode is PermissionMode.AUTO
        and writes
        and _paths_are_inside_workspace(tool, call, workspace)
    ):
        return GateDecision(GateAction.ALLOW, GateRule("mode.auto.workspace"))
    return GateDecision(
        _PRESETS[mode][writes],
        GateRule(f"mode.{mode.value}.{'write' if writes else 'read'}"),
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
