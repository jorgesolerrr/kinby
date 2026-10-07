import asyncio
from pathlib import Path
from uuid import UUID

from kinby.cli.client import ContractClient
from kinby.contracts import (
    FACTORY_DESCRIBE,
    FACTORY_INSTALL,
    FACTORY_REMOVE,
    INSTANCE_LIST,
    INSTANCE_RESTORE,
    INSTANCE_STATUS,
    INSTANCE_UPDATE,
    ErrorCode,
    ErrorEnvelope,
    FactoryDescribeCommand,
    FactoryDescription,
    FactoryInstallCommand,
    FactoryInstallResult,
    FactoryInstanceSetup,
    FactoryRemoveCommand,
    FactoryRemoveResult,
    InstanceListCommand,
    InstanceListResult,
    InstanceRestoreCommand,
    InstanceStatusCommand,
    InstanceStatusResult,
    InstanceUpdateCommand,
    LifecycleOperationResult,
    LoginState,
    OperationKind,
    OperationState,
    SecretSetup,
    StorageItem,
    StorageKind,
)
from kinby.hub import Hub, ImageSelection
from kinby.instance import inspect_instance
from tests.test_hub import (
    FakeImages,
    FakeRuntime,
    created_instance,
    finished_operation,
    hub_client,
    instance_environment,
)
from tests.test_hub_factories import HOOKS, TOOLS, write_factory

FACTORY = """\
name: tickets
instances:
  coder: { image: coder }
  reviewer: {}
intake: { instance: coder, routine: scan }
work_item: { issue: int }
steps:
  - id: implement
    kind: client
    in: coder
    client: claude
    prompt: prompts/implement.md
    hook: record_branch
    results: { branch: str }
  - id: review
    kind: agent
    in: reviewer
    prompt: prompts/review.md
    hook: read_verdict
    requires: [branch]
"""
FILES = {
    "factory.yaml": FACTORY,
    "prompts/implement.md": "Implement issue {{issue}}.\n",
    "prompts/review.md": "Review the branch.\n",
    "instances/coder/SYSTEM.md": "You implement issues.\n",
    "instances/coder/kinby.toml": '[feedback]\nask = "off"\n',
    "instances/coder/routines/scan/ROUTINE.md": "---\ndescription: Scan\n---\nScan.\n",
    "instances/coder/tools/github.py": TOOLS,
    "instances/coder/hooks/record.py": HOOKS,
    "instances/reviewer/SYSTEM.md": "You review branches.\n",
    "instances/reviewer/hooks/record.py": HOOKS,
}
#: The coder asks for a GitHub token and a step budget, signs in to Claude, and gives the model
#: a default of its own.
CODER_SETUP = """\
  coder:
    image: coder
    setup_fields:
      - name: GITHUB_TOKEN
        label: GitHub token
        description: Opens pull requests.
        kind: secret
        type: text
        required: true
      - name: model
        label: Model
        description: The coder's model.
        kind: config
        type: text
        required: true
        default: anthropic:claude-sonnet-5
      - name: steps
        label: Step budget
        description: How many steps a turn may take.
        kind: config
        type: integer
        required: false
        default: 40
        target: { file: kinby.toml, key: budgets.steps }
    logins:
      - id: claude
        label: Claude
        description: Signs Claude Code in with your plan.
        command: [claude, login]
        volume: /root/.claude
        prompt_pattern: '(?P<url>https://\\S+) (?P<code>[A-Z0-9-]+)'
"""
WITH_SETUP = FILES | {"factory.yaml": FACTORY.replace("  coder: { image: coder }\n", CODER_SETUP)}
SETUP = FactoryInstanceSetup(model="openai:gpt-5", secrets={"api_key": "sk-test"})


def install_hub(
    tmp_path: Path,
    files: dict[str, str] = FILES,
    *,
    images: FakeImages | None = None,
    runtime: FakeRuntime | None = None,
) -> Hub:
    write_factory(tmp_path / "hub" / "factories" / "tickets", files)
    return Hub(
        tmp_path / "hub",
        runtime=runtime or FakeRuntime(),
        images=images or FakeImages(),
        shipped_factories=tmp_path / "shipped",
    )


async def installed(
    client: ContractClient,
    instances: dict[str, FactoryInstanceSetup] | None = None,
) -> FactoryInstallResult:
    """Install the tickets factory and wait for each of its instances to be created."""
    accepted = await client.call(
        FACTORY_INSTALL,
        FactoryInstallCommand(
            name="tickets", instances=instances or {"coder": SETUP, "reviewer": SETUP}
        ),
    )
    assert isinstance(accepted, FactoryInstallResult)
    for creation in accepted.instances.values():
        assert (await finished_operation(client, creation)).state is OperationState.SUCCEEDED
    return accepted


async def listed(client: ContractClient, *, removed: bool = False) -> set[UUID]:
    result = await client.call(INSTANCE_LIST, InstanceListCommand(removed=removed))
    assert isinstance(result, InstanceListResult)
    return {summary.instance_id for summary in result.instances}


def ids(operations: dict[str, LifecycleOperationResult]) -> set[UUID]:
    return {operation.instance_id for operation in operations.values()}


def test_installing_a_factory_creates_each_instance_from_its_template(tmp_path):
    images = FakeImages()
    hub = install_hub(tmp_path, images=images)
    client = hub_client(hub)

    async def scenario() -> None:
        accepted = await installed(client)
        listed = await client.call(INSTANCE_LIST, InstanceListCommand())

        assert set(accepted.instances) == {"coder", "reviewer"}
        operation = await finished_operation(client, accepted.instances["coder"])
        assert [step.name for step in operation.steps] == ["image", "initialize", "publish"]
        assert isinstance(listed, InstanceListResult)
        assert {
            (summary.manifest_id, summary.factory, summary.instance_id)
            for summary in listed.instances
        } == {
            ("tickets-coder", "tickets", accepted.instances["coder"].instance_id),
            ("tickets-reviewer", "tickets", accepted.instances["reviewer"].instance_id),
        }
        coder = hub.instances_directory / str(accepted.instances["coder"].instance_id)
        reviewer = hub.instances_directory / str(accepted.instances["reviewer"].instance_id)
        assert (coder / "SYSTEM.md").read_text() == "You implement issues.\n"
        assert inspect_instance(coder).manifest.feedback.ask == "off"
        assert inspect_instance(reviewer).manifest.feedback.ask == "every-turn"
        assert (coder / "tools" / "github.py").read_text() == TOOLS
        assert (coder / "hooks" / "record.py").read_text() == HOOKS
        assert (coder / "routines" / "scan" / "ROUTINE.md").is_file()
        assert (reviewer / "SYSTEM.md").read_text() == "You review branches.\n"
        assert not (reviewer / "tools" / "github.py").exists()
        assert sorted(selection.recipe or "" for selection in images.selections) == ["", "coder"]

    asyncio.run(scenario())


def test_each_template_asks_for_the_built_in_fields_first_then_its_own_and_its_logins(tmp_path):
    hub = install_hub(tmp_path, WITH_SETUP)
    client = hub_client(hub)

    async def scenario() -> FactoryDescription:
        described = await client.call(FACTORY_DESCRIBE, FactoryDescribeCommand(name="tickets"))
        assert isinstance(described, FactoryDescription)
        return described

    described = asyncio.run(scenario())

    coder = described.instances["coder"]
    assert [(field.name, field.default) for field in coder.setup_fields] == [
        ("model", "anthropic:claude-sonnet-5"),
        ("api_key", None),
        ("GITHUB_TOKEN", None),
        ("steps", 40),
    ]
    assert [login.id for login in coder.logins] == ["claude"]
    reviewer = described.instances["reviewer"]
    assert [field.name for field in reviewer.setup_fields] == ["model", "api_key"]
    assert reviewer.logins == []


def test_an_instance_is_created_with_its_templates_defaults_secrets_and_logins(tmp_path):
    hub = install_hub(tmp_path, WITH_SETUP)
    client = hub_client(hub)
    coder_values = FactoryInstanceSetup(
        model="", secrets={"api_key": "sk-test", "GITHUB_TOKEN": "ghp-test"}
    )

    async def scenario() -> None:
        accepted = await installed(client, {"coder": coder_values, "reviewer": SETUP})
        coder = accepted.instances["coder"].instance_id
        status = await client.call(INSTANCE_STATUS, InstanceStatusCommand(instance_id=coder))

        manifest = inspect_instance(hub.instances_directory / str(coder)).manifest
        assert manifest.models.main == "anthropic:claude-sonnet-5"
        assert manifest.budgets.steps == 40
        assert instance_environment(hub, coder)["GITHUB_TOKEN"] == "ghp-test"
        assert instance_environment(hub, coder)["ANTHROPIC_API_KEY"] == "sk-test"
        assert isinstance(status, InstanceStatusResult)
        assert [(login.id, login.state) for login in status.setup.logins] == [
            ("claude", LoginState.PENDING)
        ]
        assert (
            SecretSetup(
                name="GITHUB_TOKEN",
                variable="GITHUB_TOKEN",
                label="GitHub token",
                required=True,
                is_set=True,
            )
            in status.setup.secrets
        )
        record = hub.registry.instance(coder)
        assert record is not None
        assert (
            StorageItem(
                kind=StorageKind.VOLUME,
                source=f"kinby-{coder}-claude",
                destination="/root/.claude",
                writable=True,
            )
            in record.storage
        )

    asyncio.run(scenario())


def test_an_install_with_a_missing_setup_value_creates_nothing(tmp_path):
    runtime = FakeRuntime()
    hub = install_hub(tmp_path, WITH_SETUP, runtime=runtime)
    client = hub_client(hub)

    async def scenario() -> None:
        refused = await client.call(
            FACTORY_INSTALL,
            FactoryInstallCommand(name="tickets", instances={"coder": SETUP, "extra": SETUP}),
        )
        listed = await client.call(INSTANCE_LIST, InstanceListCommand())

        assert isinstance(refused, ErrorEnvelope)
        assert refused.code is ErrorCode.INVALID_SETUP
        assert refused.fields == {
            "extra": "The factory declares no instance by this name.",
            "coder.GITHUB_TOKEN": "GitHub token is required.",
            "reviewer": "Send this instance's setup values.",
        }
        assert isinstance(listed, InstanceListResult)
        assert listed.instances == []
        assert runtime.created == []

    asyncio.run(scenario())


def test_an_install_whose_factory_check_fails_creates_nothing(tmp_path):
    images = FakeImages()
    runtime = FakeRuntime()
    broken = FILES | {"factory.yaml": FACTORY.replace("hook: read_verdict", "hook: read_review")}
    hub = install_hub(tmp_path, broken, images=images, runtime=runtime)
    client = hub_client(hub)

    async def scenario() -> None:
        refused = await client.call(
            FACTORY_INSTALL,
            FactoryInstallCommand(name="tickets", instances={"coder": SETUP, "reviewer": SETUP}),
        )

        assert isinstance(refused, ErrorEnvelope)
        assert refused.code is ErrorCode.INVALID_ARGUMENT
        assert (
            'Step "review" names hook "read_review", which instance "reviewer" does not have.'
            in refused.message
        )
        assert await listed(client) == set()
        assert hub.registry.managed_instances() == []
        assert images.selections == []
        assert runtime.created == []

    asyncio.run(scenario())


def test_removing_a_factory_removes_each_of_its_instances_and_no_other(tmp_path):
    runtime = FakeRuntime()
    hub = install_hub(tmp_path, runtime=runtime)
    client = hub_client(hub)

    async def scenario() -> None:
        accepted = await installed(client)
        outside = await created_instance(client)

        removal = await client.call(FACTORY_REMOVE, FactoryRemoveCommand(name="tickets"))

        assert isinstance(removal, FactoryRemoveResult)
        assert ids(removal.instances) == ids(accepted.instances)
        for removed in removal.instances.values():
            outcome = await finished_operation(client, removed)
            assert (outcome.kind, outcome.state) == (OperationKind.REMOVE, OperationState.SUCCEEDED)
        assert await listed(client) == {outside.instance_id}
        assert await listed(client, removed=True) == ids(accepted.instances)
        assert {runtime_id for runtime_id, _ in runtime.removed} == {
            str(instance_id) for instance_id in ids(accepted.instances)
        }

    asyncio.run(scenario())


def test_a_factory_is_installed_again_only_once_its_instances_are_removed(tmp_path):
    hub = install_hub(tmp_path)
    client = hub_client(hub)
    command = FactoryInstallCommand(name="tickets", instances={"coder": SETUP, "reviewer": SETUP})

    async def scenario() -> None:
        first = await installed(client)
        again = await client.call(FACTORY_INSTALL, command)
        removal = await client.call(FACTORY_REMOVE, FactoryRemoveCommand(name="tickets"))
        assert isinstance(removal, FactoryRemoveResult)
        for removed in removal.instances.values():
            await finished_operation(client, removed)
        second = await installed(client)

        assert isinstance(again, ErrorEnvelope)
        assert again.code is ErrorCode.INVALID_ARGUMENT
        assert again.message == (
            'Factory "tickets" is installed already. Remove it before installing it.'
        )
        assert await listed(client) == ids(second.instances)
        assert await listed(client, removed=True) == ids(first.instances)

    asyncio.run(scenario())


def test_an_update_of_a_factory_instance_keeps_its_templates_image_recipe(tmp_path):
    images = FakeImages()
    hub = install_hub(tmp_path, images=images)
    client = hub_client(hub)

    async def scenario() -> None:
        accepted = await installed(client)
        update = await client.call(
            INSTANCE_UPDATE,
            InstanceUpdateCommand(
                instance_id=accepted.instances["coder"].instance_id, revision="v2"
            ),
        )
        assert isinstance(update, LifecycleOperationResult)

        assert (await finished_operation(client, update)).state is OperationState.SUCCEEDED
        assert images.selections[-1] == ImageSelection("v2", recipe="coder")

    asyncio.run(scenario())


def test_a_removed_factory_instance_is_not_restored_beside_one_of_the_same_name(tmp_path):
    hub = install_hub(tmp_path)
    client = hub_client(hub)

    async def scenario() -> None:
        first = await installed(client)
        removal = await client.call(FACTORY_REMOVE, FactoryRemoveCommand(name="tickets"))
        assert isinstance(removal, FactoryRemoveResult)
        for removed in removal.instances.values():
            await finished_operation(client, removed)
        second = await installed(client)

        refused = await client.call(
            INSTANCE_RESTORE,
            InstanceRestoreCommand(instance_id=first.instances["coder"].instance_id),
        )

        assert isinstance(refused, ErrorEnvelope)
        assert refused.code is ErrorCode.INVALID_ARGUMENT
        assert refused.message == (
            'Factory "tickets" has an instance "coder" already. '
            "Remove it before restoring this one."
        )
        assert await listed(client) == ids(second.instances)

    asyncio.run(scenario())
