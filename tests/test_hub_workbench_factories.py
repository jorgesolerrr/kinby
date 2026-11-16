import pytest

from tests.test_hub_factories import TOOLS, problems

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


def test_a_worktree_call_names_the_branch_and_base_the_steps_after_it_read(tmp_path):
    named = FACTORY.replace('"git status"', '"git switch {{branch}}"').replace(
        "requires: [repo]", "requires: [repo, base]"
    )
    files = FILES | {"factory.yaml": named + "worktree: { call: open_pull_request }\n"}
    missing = FILES | {"factory.yaml": named + "worktree: { call: name_branch }\n"}

    assert problems(tmp_path / "named", files) == []
    assert problems(tmp_path / "missing", missing) == [
        'worktree calls tool "name_branch", which the factory\'s folder does not hold.'
    ]


def test_a_factory_suggests_only_picks_the_shipped_library_holds(tmp_path):
    suggesting = _with("browser: true", "browser: true\n  suggested_picks: [tdd, house-style]")

    assert problems(tmp_path, suggesting) == [
        'The factory suggests skill "house-style", which the shipped library does not hold.'
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


def test_the_check_refuses_a_file_that_declares_instances_and_names_requires(tmp_path):
    files = _with("requires:\n", "instances:\n  coder: {}\nrequires:\n")

    assert problems(tmp_path, files) == [
        "factory.yaml declares instances, which no factory runs in any more. Declare what the "
        "factory needs of a workbench under requires."
    ]


@pytest.mark.parametrize(
    ("old", "new", "refusal"),
    [
        ("kind: command\n", "kind: command\n    in: coder\n", 'Step "checks" names in'),
        (
            "kind: command\n",
            "kind: command\n    branch: '{{issue}}'\n",
            'Step "checks" names branch',
        ),
        ("kind: code\n", "kind: code\n    in: coder\n", 'Step "open-pr" names in'),
    ],
)
def test_the_check_refuses_in_and_branch_on_a_step_which_runs_in_the_runs_folder(
    tmp_path, old, new, refusal
):
    assert problems(tmp_path, _with(old, new)) == [
        f"{refusal}, but every step runs in the run's folder on the line's workbench. Drop it."
    ]


def test_the_check_refuses_a_hook_and_names_check(tmp_path):
    files = _with("kind: code\n", "kind: code\n    hook: record_pr\n")

    assert problems(tmp_path, files) == [
        'Step "open-pr" names a hook. Name it as check, a hook of the factory\'s folder that '
        "records the step's values and names its outcome."
    ]


def test_the_check_refuses_the_client_kind_and_names_agent_with_an_engine(tmp_path):
    files = FILES | {
        "factory.yaml": FACTORY.replace(
            "  - id: checks\n",
            "  - { id: implement, kind: client, client: claude, prompt: prompts/implement.md }\n"
            "  - id: checks\n",
        ),
        "prompts/implement.md": "Implement it.\n",
    }

    assert problems(tmp_path, files) == [
        'Step "implement" is of kind client. Make it kind agent with engine claude or codex.'
    ]


def test_a_run_call_names_no_instance(tmp_path):
    files = _with("needs_human: { call", "needs_human: { in: coder, call")

    assert problems(tmp_path, files) == [
        "factory.yaml: needs_human.in: Unexpected keyword argument"
    ]


def test_the_check_holds_a_command_steps_run_values_to_what_earlier_steps_declare(tmp_path):
    files = _with('"git status"', '"git switch {{branch}}"')

    assert problems(tmp_path, files) == [
        'Step "checks" names "branch", which no earlier step declares in its results and the '
        "work item does not carry."
    ]


def test_the_check_holds_an_approve_summarys_run_values_to_what_earlier_steps_declare(tmp_path):
    files = _with("Merge the pull request.", "Merge pull request {{pr}} of {{repo}}, {{title}}.")

    assert problems(tmp_path, files) == [
        'Step "merge" names "title", which no earlier step declares in its results and the '
        "work item does not carry."
    ]


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


def test_the_check_refuses_a_check_the_factorys_folder_does_not_hold(tmp_path):
    files = _with("kind: code\n", "kind: code\n    check: passed\n") | {"hooks/checked.py": CHECK}

    assert problems(tmp_path, files) == [
        'Step "open-pr" names check "passed", which the factory\'s folder does not hold.'
    ]


def test_a_failed_step_runs_again_or_goes_back_but_never_both(tmp_path):
    again = _with("kind: command\n", "kind: command\n    failed: { retry: 2 }\n")
    both = _with("kind: command\n", "kind: command\n    failed: { retry: 2 }\n    retry: 1\n")

    assert problems(tmp_path / "again", again) == []
    assert problems(tmp_path / "both", both) == [
        'Step "checks" declares both retry and failed, but a failed attempt runs again only '
        "under failed."
    ]


AGENTS = """\
name: tickets
work_item: { issue: int }
steps:
  - id: implement
    kind: agent
    engine: claude
    prompt: prompts/implement.md
    model: claude-opus-5-5
    effort: high
    timeout: 90m
    check: checked
  - id: review
    kind: agent
    engine: kinby
    prompt: prompts/implement.md
  - id: fix
    kind: agent
    engine: claude
    prompt: prompts/implement.md
    resume: implement
"""
AGENT_FILES = {
    "factory.yaml": AGENTS,
    "prompts/implement.md": "Implement the issue.\n",
    "hooks/checked.py": CHECK,
}


def _agents(old: str, new: str) -> dict[str, str]:
    return AGENT_FILES | {"factory.yaml": AGENTS.replace(old, new)}


def test_an_agent_step_runs_a_prompt_of_the_factory_on_an_engine(tmp_path):
    assert problems(tmp_path, AGENT_FILES) == []


def test_the_check_refuses_an_agent_steps_prompt_the_factory_does_not_hold(tmp_path):
    files = _agents("prompts/implement.md\n    model", "prompts/draft.md\n    model")

    assert problems(tmp_path, files) == [
        'Step "implement" names prompt "prompts/draft.md", which is not a file in the factory.'
    ]


def test_the_check_refuses_an_unknown_engine(tmp_path):
    [problem] = problems(tmp_path, _agents("engine: kinby", "engine: gemini"))

    assert problem.startswith("factory.yaml: steps.1.agent.engine: Input should be")


@pytest.mark.parametrize(
    ("old", "new", "refusal"),
    [
        (
            "resume: implement",
            "resume: review",
            'Step "fix" resumes "review", which is not an earlier claude step.',
        ),
        (
            "engine: kinby\n",
            "engine: kinby\n    resume: implement\n",
            'Step "review" resumes "implement", but the kinby engine keeps no session.',
        ),
        (
            "engine: claude\n    prompt: prompts/implement.md\n    resume",
            "engine: codex\n    prompt: prompts/implement.md\n    resume",
            'Step "fix" resumes "implement", which is not an earlier codex step.',
        ),
    ],
)
def test_an_agent_step_resumes_only_an_earlier_session_of_its_own_engine(
    tmp_path, old, new, refusal
):
    assert problems(tmp_path, _agents(old, new)) == [refusal]


INTAKE = """\
intake:
  call: open_pull_request
  schedule: 0 * * * *
  delivery: { auth: hmac-sha256, secret: GITHUB_WEBHOOK_SECRET,
              signature_header: X-Hub-Signature-256 }
work_item:"""


@pytest.mark.parametrize(
    "intake",
    [INTAKE, "intake: { call: open_pull_request, schedule: 0 * * * * }\nwork_item:"],
    ids=["schedule-and-delivery", "schedule"],
)
def test_an_intake_calls_a_tool_of_the_factorys_folder_on_a_trigger(tmp_path, intake):
    assert problems(tmp_path, _with("work_item:", intake)) == []


def test_the_check_refuses_an_intake_whose_tool_the_factorys_folder_does_not_hold(tmp_path):
    files = _with("work_item:", INTAKE.replace("call: open_pull_request", "call: scan_issues"))

    assert problems(tmp_path, files) == [
        'intake calls tool "scan_issues", which the factory\'s folder does not hold.'
    ]


@pytest.mark.parametrize(
    ("old", "new", "refusal"),
    [
        (
            "secret: GITHUB_WEBHOOK_SECRET",
            "secret: github-webhook",
            'The delivery secret "github-webhook" is not an environment variable name.',
        ),
        (
            ",\n              signature_header: X-Hub-Signature-256",
            "",
            "An hmac-sha256 delivery names its signature_header.",
        ),
    ],
    ids=["secret-name", "signature-header"],
)
def test_the_check_refuses_a_delivery_the_hub_could_not_check(tmp_path, old, new, refusal):
    assert problems(tmp_path, _with("work_item:", INTAKE.replace(old, new))) == [refusal]


def test_the_check_refuses_an_intake_with_no_trigger(tmp_path):
    files = _with("work_item:", "intake: { call: open_pull_request }\nwork_item:")

    assert problems(tmp_path, files) == [
        "The intake declares no trigger: give it a schedule, a delivery or both."
    ]
