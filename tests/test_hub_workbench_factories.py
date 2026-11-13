import asyncio

import pytest

from kinby.contracts import INSTANCE_CREATE, ErrorEnvelope
from tests.test_factory_templates import factory_hub
from tests.test_hub import hub_client
from tests.test_hub_factories import TOOLS, problems
from tests.test_hub_factory_roles import create_command

FACTORY = """\
name: tickets
requires:
  image: coder
  secrets:
    - { name: GH_TOKEN, label: GitHub token, description: Opens pull requests., type: text,
        required: true }
  values:
    - { name: moodle_url, label: Moodle address, description: Where the course lives.,
        type: url, required: false, default: "https://moodle.example.com" }
  commands: [setup, checks]
  logins:
    - { id: claude, label: Claude, description: Signs in., command: [claude, login],
        volume: /root/.claude, prompt_pattern: '(?P<url>\\S+) (?P<code>\\S+)' }
  browser: true
work_item: { issue: int, repo: str }
steps:
  - id: checks
    kind: command
    run: ["git status", { commands: checks }]
  - id: open-pr
    kind: code
    call: open_pull_request
    requires: [repo]
    results: { pr: int }
  - id: babysit
    kind: wait
    signal:
      - routine: github
        body.pull_request.number: "{{pr}}"
    deadline: 7d
  - id: merge
    kind: approve
    summary: Merge the pull request.
done_requires: [pr]
needs_human: { call: open_pull_request }
ended: { call: open_pull_request }
closing:
  done:
    - body.pull_request.number: "{{pr}}"
"""
FILES = {"factory.yaml": FACTORY, "tools/github.py": TOOLS}


def _with(old: str, new: str) -> dict[str, str]:
    return FILES | {"factory.yaml": FACTORY.replace(old, new)}


def test_the_check_passes_a_factory_that_requires_what_its_steps_use(tmp_path):
    assert problems(tmp_path, FILES) == []


def test_a_factory_requires_nothing_when_it_leaves_requires_out(tmp_path):
    factory = "name: tickets\nwork_item: {}\nsteps:\n  - { id: go, kind: approve, summary: Go. }\n"

    assert problems(tmp_path, {"factory.yaml": factory}) == []


def test_the_check_refuses_a_command_list_or_a_tool_the_factory_does_not_hold(tmp_path):
    factory = FACTORY.replace("{ commands: checks }", "{ commands: lint }").replace(
        "call: open_pull_request\n    requires", "call: merge_pull_request\n    requires"
    )

    assert problems(tmp_path, FILES | {"factory.yaml": factory}) == [
        'Step "checks" runs command list "lint", which requires does not declare.',
        'Step "open-pr" calls tool "merge_pull_request", '
        "which the factory's folder does not hold.",
    ]


@pytest.mark.parametrize("key", ["needs_human", "ended"])
def test_a_run_call_calls_a_tool_of_the_factorys_folder(tmp_path, key):
    files = _with(f"{key}: {{ call: open_pull_request }}", f"{key}: {{ call: label_issue }}")

    assert problems(tmp_path, files) == [
        f'{key} calls tool "label_issue", which the factory\'s folder does not hold.'
    ]


def test_the_check_reports_a_tool_file_of_the_factorys_folder_that_does_not_load(tmp_path):
    found = problems(tmp_path, FILES | {"tools/github.py": "raise RuntimeError('no token')\n"})

    assert found[0] == "tools/github.py: RuntimeError: no token"


def test_a_named_value_takes_no_target_and_no_kind(tmp_path):
    targeted = _with(
        "required: false,", "required: false, target: { file: kinby.toml, key: course.url },"
    )
    kinded = _with("type: url,", "kind: config, type: url,")

    assert problems(tmp_path / "targeted", targeted) == [
        "factory.yaml: requires.values.0.target: Unexpected keyword argument"
    ]
    assert problems(tmp_path / "kinded", kinded) == [
        "factory.yaml: requires.values.0.kind: Unexpected keyword argument"
    ]


def test_the_check_holds_a_secret_to_a_secrets_rules_and_a_value_to_its_type(tmp_path):
    factory = (
        FACTORY.replace("name: GH_TOKEN", "name: GH-TOKEN")
        .replace("required: true }", "required: true, default: ghp_shipped }")
        .replace('default: "https://moodle.example.com"', "default: moodle")
    )

    assert problems(tmp_path, FILES | {"factory.yaml": factory}) == [
        'Secret field "GH-TOKEN" is not an environment variable name.',
        'Secret field "GH-TOKEN" has a default. A secret never ships in a template.',
        'Setup field "moodle_url" has a default that is not a URL with a scheme and a host, '
        "like https://example.com.",
    ]


def test_the_check_refuses_what_a_workbench_could_not_provide(tmp_path):
    factory = (
        FACTORY.replace("image: coder", "image: rust")
        .replace("name: moodle_url", "name: GH_TOKEN")
        .replace("volume: /root/.claude", "volume: claude")
    )

    assert problems(tmp_path, FILES | {"factory.yaml": factory}) == [
        'The factory requires image recipe "rust", which kinby does not ship.',
        'Setup field "GH_TOKEN" is declared more than once.',
        'Login "claude" mounts its volume at "claude", which is not an absolute path.',
    ]


@pytest.mark.parametrize(
    ("old", "new", "field"),
    [
        ("kind: command\n", "kind: command\n    in: coder\n", "steps.0.command.in"),
        ("kind: command\n", "kind: command\n    branch: '{{issue}}'\n", "steps.0.command.branch"),
        ("kind: code\n", "kind: code\n    in: coder\n", "steps.1.code.in"),
        ("needs_human: { call", "needs_human: { in: coder, call", "needs_human.in"),
    ],
)
def test_a_step_or_a_run_call_names_no_instance_or_branch(tmp_path, old, new, field):
    assert problems(tmp_path, _with(old, new)) == [
        f"factory.yaml: {field}: Unexpected keyword argument"
    ]


def test_the_check_holds_a_command_steps_run_values_to_what_earlier_steps_declare(tmp_path):
    files = _with('"git status"', '"git switch {{branch}}"')

    assert problems(tmp_path, files) == [
        'Step "checks" names "branch", which no earlier step declares in its results and the '
        "work item does not carry."
    ]


def test_a_factory_that_runs_on_a_workbench_offers_no_role_to_pick(tmp_path):
    client = hub_client(factory_hub(tmp_path, FILES))

    async def scenario() -> None:
        refused = await client.call(INSTANCE_CREATE, create_command())

        assert isinstance(refused, ErrorEnvelope)
        assert refused.fields == {"factory_role": 'Factory "tickets" has no role "coder".'}

    asyncio.run(scenario())


CHECK = """\
from kinby.plugins.hooks import HookResult, StepEnd, hook


@hook
def checked(end: StepEnd) -> HookResult:
    return HookResult()
"""


def test_a_command_or_code_step_checks_with_a_hook_of_the_factorys_folder(tmp_path):
    files = _with("kind: command\n", "kind: command\n    check: checked\n") | {
        "hooks/checked.py": CHECK
    }
    files["factory.yaml"] = files["factory.yaml"].replace(
        "kind: code\n", "kind: code\n    check: checked\n"
    )

    assert problems(tmp_path, files) == []


@pytest.mark.parametrize("check", ["passed", "record_branch"])
def test_the_check_refuses_a_check_the_factorys_folder_does_not_hold(tmp_path, check):
    files = _with("kind: code\n", f"kind: code\n    check: {check}\n") | {"hooks/checked.py": CHECK}

    assert problems(tmp_path, files) == [
        f'Step "open-pr" names check "{check}", which the factory\'s folder does not hold.'
    ]


def test_a_failed_step_runs_again_or_goes_back_but_never_both(tmp_path):
    again = _with("kind: command\n", "kind: command\n    failed: { retry: 2 }\n")
    both = _with("kind: command\n", "kind: command\n    failed: { retry: 2 }\n    retry: 1\n")

    assert problems(tmp_path / "again", again) == []
    assert problems(tmp_path / "both", both) == [
        'Step "checks" declares both retry and failed, but a failed attempt runs again only '
        "under failed."
    ]
