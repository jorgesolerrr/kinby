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
        "ls 2>/dev/null",
        "ls >/dev/null 2>&1",
        "cat /instance/workspace/notes.md && wc -l notes.md || true",
        "ls\npwd\n",
        "cd ./learning && ls",
        "head -n5 notes.md",
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
        "jq -n -f query.jq",
        "jq -nf query.jq",
        "git log --oneline -5",
        "git status",
        "git diff --no-index --ext-diff a b",
        "grep -R foo .",
        "find -L . -name x",
        "find . -follow",
        "ls -RL .",
        "du -L .",
        "tree -l",
        "diff -r a b",
        "sort --files0-from=list",
        "printf -v PATH .; cat -c id",
        "printf -vPATH .",
    ],
)
def test_any_other_command_is_not_a_shell_read(command: str) -> None:
    assert not is_shell_read(command, WORKSPACE)


@pytest.fixture
def linked(tmp_path: Path) -> Path:
    """A workspace holding links to a secret outside it, at its top and in a subdirectory."""
    secret = tmp_path / ".env"
    secret.write_text("KEY=secret", encoding="utf-8")
    workspace = tmp_path / "workspace"
    (workspace / "sub").mkdir(parents=True)
    (workspace / "notes.md").write_text("notes", encoding="utf-8")
    (workspace / "link").symlink_to(secret)
    (workspace / "sub" / "link").symlink_to(secret)
    (workspace / "out").symlink_to(tmp_path)
    return workspace


@pytest.mark.parametrize(
    "command",
    [
        "cat link",
        "cat out/.env",
        "cat ./*",
        "cd ./sub && cat link",
        "cd ./missing || cat link",
        "cd ./out; cat .env",
        "date -flink",
        "diff --from-file=link notes.md",
    ],
)
def test_a_link_out_of_the_workspace_is_not_a_shell_read(linked: Path, command: str) -> None:
    assert not is_shell_read(command, linked)


def test_a_file_inside_the_workspace_is_a_shell_read_beside_a_link(linked: Path) -> None:
    assert is_shell_read("cat notes.md; cd ./sub && ls", linked)


STATE = Path("/instance/.state")
OUTPUTS = STATE / "saved-outputs" / "turn"


@pytest.mark.parametrize(
    "command",
    [
        f"grep -c x {OUTPUTS}/call.txt",
        f"jq '.items | length' {OUTPUTS}/call.txt",
        f"head -c 100 {OUTPUTS}/call.txt | wc -c",
        f"cd {OUTPUTS} && tail -n 5 call.txt",
    ],
)
def test_a_read_of_the_turns_saved_outputs_is_a_shell_read(command: str) -> None:
    assert is_shell_read(command, WORKSPACE, saved_outputs=OUTPUTS)


@pytest.mark.parametrize(
    "command",
    [
        f"grep -c x {STATE}/saved-outputs/other/call.txt",
        f"jq . {OUTPUTS}/../other/call.txt",
        f"grep x {STATE}/events.sqlite",
        f"cat {STATE}/../.env",
    ],
)
def test_a_read_of_another_turns_outputs_or_the_state_directory_is_not(command: str) -> None:
    assert not is_shell_read(command, WORKSPACE, saved_outputs=OUTPUTS)


ALICE = "00000000-0000-0000-0000-000000000001"


@pytest.mark.parametrize(
    "command",
    [
        "kinby hub read instance.list",
        f"kinby hub read routine.list instance_id={ALICE}",
        f"kinby hub read instance.logs instance_id={ALICE} tail=20 --table",
        "kinby hub read library.skill.file.read name=tdd path=../../.env",
        "kinby hub read instance.list --help",
        "kinby hub read instance.list | jq -c .instances",
        "kinby hub methods",
        "kinby hub help instance_create",
        f"kinby hub wait {ALICE} --timeout 60",
    ],
)
def test_a_hub_read_through_the_cli_is_a_shell_read(command: str) -> None:
    assert is_shell_read(command, WORKSPACE)


@pytest.mark.parametrize(
    "command",
    [
        f"kinby hub read instance.remove instance_id={ALICE}",
        "kinby hub read hub.write",
        "kinby hub read instance.relay.read",
        f"kinby hub read routine.delete instance_id={ALICE} name=news",
        "kinby hub read",
        "kinby hub read --table instance.list",
        "kinby hub read instance.list > out.json",
        "kinby hub update --connect wss://hub/ws x --revision y",
        "kinby hub ./hub",
        "kinby thread list",
    ],
)
def test_another_kinby_command_is_not_a_shell_read(command: str) -> None:
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
