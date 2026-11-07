"""A shell read: a bash command the gate treats as a read under ask and auto (ADR 0119)."""

from dataclasses import replace
from pathlib import Path

import pytest

from kinby.contracts import PermissionMode, ToolCall
from kinby.core.gate import evaluate
from kinby.core.shell_reads import is_shell_read
from kinby.instance.permissions import SHIPPED_POLICY, BashPolicy, GateAction, GatePolicy
from kinby.plugins import Tool
from kinby.plugins.defaults.shell import bash

WORKSPACE = Path("/instance/workspace")
OVERNIGHT = "cd /instance/workspace; ls -R . | head -50; ls learning"


@pytest.mark.parametrize(
    "command",
    [
        OVERNIGHT,
        "ls",
        "cat notes.md",
        "grep -rn foo .",
        "find . -name '*.md'",
        'echo "1200*0.3" | bc',
        "git log --oneline -5",
        "ls 2>/dev/null",
        "ls >/dev/null 2>&1",
        "cat /instance/workspace/notes.md && wc -l notes.md || true",
        "ls\npwd\n",
        "cd ./learning && ls",
        "git show HEAD~1",
    ],
)
def test_a_command_that_only_reads_inside_the_workspace_is_a_shell_read(command: str) -> None:
    assert is_shell_read(command, WORKSPACE)


@pytest.mark.parametrize(
    "command",
    [
        "echo x > f",
        "ls >> f",
        "ls $(pwd)",
        "ls `pwd`",
        "cat /instance/.env",
        "cat ../.env",
        "cat ~/.bashrc",
        "find . -delete",
        "find . -exec rm {} \\;",
        "sort -o out in",
        "sort -no out in",
        "sort --output=out in",
        "sort --compress-program=sh in",
        "tree -o out",
        "tree -R -H .",
        "date -s 2026-01-01",
        "date --set=2026-01-01",
        "file -C -m magic",
        "git diff --output=out",
        "git push",
        "git -C /tmp log",
        "python x.py",
        "curl https://example.com",
        "env",
        "printenv",
        "sed -i s/a/b/ f",
        "ls &",
        "ls | sh",
        "cat 'unclosed",
        "cat < notes.md",
        "cat <<EOF",
        "(ls)",
        "ls {a,b}",
        "ls; rm -rf x",
        "sort ';' cat -o out",
        "ls # '\nrm -rf x\n'",
        "cat .?/.env",
        "grep -f/instance/.env x",
        "cd",
        "cd -",
        "FOO=bar ls",
        "jq -n env",
        "git show HEAD:../.env",
        "echo x >&2victim",
        "cd learning && ls",
        "jq -n 'include \"m\"; .'",
    ],
)
def test_any_other_command_is_not_a_shell_read(command: str) -> None:
    assert not is_shell_read(command, WORKSPACE)


def _gate(
    command: str, mode: PermissionMode, policy: GatePolicy = SHIPPED_POLICY, tool: Tool = bash
) -> tuple[GateAction, str]:
    call = ToolCall(call_id="call", name="bash", arguments={"command": command}, write=True)
    decision = evaluate(policy, mode, call, tool, WORKSPACE)
    return decision.action, decision.rule


@pytest.mark.parametrize("mode", [PermissionMode.ASK, PermissionMode.AUTO])
def test_ask_and_auto_allow_a_shell_read(mode: PermissionMode) -> None:
    assert _gate(OVERNIGHT, mode) == (GateAction.ALLOW, "bash.read")


@pytest.mark.parametrize("command", ["echo x > f", "cat /instance/.env", "curl https://x.dev"])
def test_auto_asks_before_other_bash(command: str) -> None:
    assert _gate(command, PermissionMode.AUTO) == (GateAction.ASK, "mode.auto.write")


def test_read_only_denies_a_shell_read() -> None:
    assert _gate("ls", PermissionMode.READ_ONLY) == (GateAction.DENY, "mode.read-only.write")


def test_full_access_allows_bash_as_before() -> None:
    assert _gate("ls", PermissionMode.FULL_ACCESS) == (GateAction.ALLOW, "mode.full-access.write")


def test_bash_deny_and_ask_patterns_win_over_a_shell_read() -> None:
    policy = replace(SHIPPED_POLICY, bash=BashPolicy(deny=("secret",), ask=("notes",)))

    assert _gate("cat secret.md", PermissionMode.AUTO, policy)[0] is GateAction.DENY
    assert _gate("cat notes.md", PermissionMode.AUTO, policy) == (GateAction.ASK, "bash.ask[0]")


@pytest.mark.parametrize("action", [GateAction.DENY, GateAction.ASK])
def test_a_bash_tool_rule_wins_over_a_shell_read(action: GateAction) -> None:
    policy = replace(SHIPPED_POLICY, tools={"bash": action})

    assert _gate("ls", PermissionMode.AUTO, policy) == (action, "tools.bash")


def test_an_authored_tool_named_bash_is_never_a_shell_read() -> None:
    authored = replace(bash, authored=True)

    assert _gate("ls", PermissionMode.AUTO, tool=authored)[0] is GateAction.ASK


def test_code_run_asks_under_auto_unless_the_routine_granted_it() -> None:
    code_run = replace(bash, name="code_run")
    call = ToolCall(call_id="call", name="code_run", arguments={"script": "ls"}, write=True)

    asked = evaluate(SHIPPED_POLICY, PermissionMode.AUTO, call, code_run, WORKSPACE)
    granted = evaluate(SHIPPED_POLICY, PermissionMode.AUTO, call, code_run, WORKSPACE, granted=True)

    assert (asked.action, asked.rule) == (GateAction.ASK, "mode.auto.write")
    assert (granted.action, granted.rule) == (GateAction.ALLOW, "routine.grant")
