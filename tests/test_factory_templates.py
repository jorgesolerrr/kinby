"""Each role of a factory writes its instances from its own instance template."""

import asyncio
from pathlib import Path
from uuid import UUID

from kinby.cli.client import ContractClient
from kinby.contracts import (
    FACTORY_DESCRIBE,
    INSTANCE_CREATE,
    INSTANCE_LIST,
    INSTANCE_REMOVE,
    INSTANCE_RESTORE,
    INSTANCE_UPDATE,
    ErrorCode,
    ErrorEnvelope,
    FactoryDescribeCommand,
    FactoryDescription,
    FactoryRole,
    InstanceCreateCommand,
    InstanceListCommand,
    InstanceListResult,
    InstanceRemoveCommand,
    InstanceRestoreCommand,
    InstanceUpdateCommand,
    LifecycleOperationResult,
    OperationState,
)
from kinby.hub import Hub, ImageSelection
from kinby.instance import inspect_instance
from tests.test_hub import FakeImages, FakeRuntime, finished_operation, hub_client
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


def factory_hub(
    tmp_path: Path,
    files: dict[str, str] = FILES,
    *,
    images: FakeImages | None = None,
    runtime: FakeRuntime | None = None,
) -> Hub:
    """A hub whose factories hold the tickets factory, written from *files*."""
    write_factory(tmp_path / "hub" / "factories" / "tickets", files)
    return Hub(
        tmp_path / "hub",
        runtime=runtime or FakeRuntime(),
        images=images or FakeImages(),
        shipped_factories=tmp_path / "shipped",
    )


async def created_roles(client: ContractClient) -> dict[str, UUID]:
    """Create an instance for each role of the tickets factory, and wait for each to be created."""
    created: dict[str, UUID] = {}
    for role in ("coder", "reviewer"):
        accepted = await client.call(
            INSTANCE_CREATE,
            InstanceCreateCommand(
                factory_role=FactoryRole(factory="tickets", name=role),
                model="openai:gpt-5",
                secrets={"api_key": "sk-test"},
            ),
        )
        assert isinstance(accepted, LifecycleOperationResult), accepted
        assert (await finished_operation(client, accepted)).state is OperationState.SUCCEEDED
        created[role] = accepted.instance_id
    return created


async def listed(client: ContractClient, *, removed: bool = False) -> set[UUID]:
    result = await client.call(INSTANCE_LIST, InstanceListCommand(removed=removed))
    assert isinstance(result, InstanceListResult)
    return {summary.instance_id for summary in result.instances}


def test_each_roles_instance_is_written_from_its_own_template(tmp_path):
    images = FakeImages()
    hub = factory_hub(tmp_path, images=images)
    client = hub_client(hub)

    async def scenario() -> None:
        created = await created_roles(client)

        coder = hub.instances_directory / str(created["coder"])
        reviewer = hub.instances_directory / str(created["reviewer"])
        assert (coder / "SYSTEM.md").read_text() == "You implement issues.\n"
        assert inspect_instance(coder).manifest.feedback.ask == "off"
        assert inspect_instance(reviewer).manifest.feedback.ask == "every-turn"
        assert (coder / "routines" / "scan" / "ROUTINE.md").is_file()
        assert (reviewer / "SYSTEM.md").read_text() == "You review branches.\n"
        assert not (reviewer / "tools" / "github.py").exists()
        assert sorted(selection.recipe or "" for selection in images.selections) == ["", "coder"]

    asyncio.run(scenario())


def test_a_template_is_written_past_the_bytecode_python_left_in_it(tmp_path):
    hub = factory_hub(tmp_path)
    cache = tmp_path / "hub" / "factories" / "tickets" / "instances" / "coder" / "hooks"
    (cache / "__pycache__").mkdir()
    (cache / "__pycache__" / "record.cpython-314.pyc").write_bytes(b"\xcb\r\r\n\x00\xe7\xff")
    client = hub_client(hub)

    async def scenario() -> None:
        created = await created_roles(client)

        coder = hub.instances_directory / str(created["coder"])
        assert (coder / "hooks" / "record.py").read_text() == HOOKS
        assert not (coder / "hooks" / "__pycache__").exists()

    asyncio.run(scenario())


def test_each_template_asks_for_the_built_in_fields_first_then_its_own_and_its_logins(tmp_path):
    hub = factory_hub(tmp_path, WITH_SETUP)
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
        ("behavior_prompt", "You implement issues.\n"),
    ]
    assert [login.id for login in coder.logins] == ["claude"]
    reviewer = described.instances["reviewer"]
    assert [field.name for field in reviewer.setup_fields] == [
        "model",
        "api_key",
        "behavior_prompt",
    ]
    assert reviewer.logins == []


def test_an_update_of_a_factory_instance_keeps_its_templates_image_recipe(tmp_path):
    images = FakeImages()
    hub = factory_hub(tmp_path, images=images)
    client = hub_client(hub)

    async def scenario() -> None:
        created = await created_roles(client)
        update = await client.call(
            INSTANCE_UPDATE, InstanceUpdateCommand(instance_id=created["coder"], revision="v2")
        )
        assert isinstance(update, LifecycleOperationResult)

        assert (await finished_operation(client, update)).state is OperationState.SUCCEEDED
        assert images.selections[-1] == ImageSelection("v2", recipe="coder")

    asyncio.run(scenario())


async def removed(client: ContractClient, instances: dict[str, UUID]) -> None:
    """Remove each instance, and wait for each removal to finish."""
    for instance_id in instances.values():
        removal = await client.call(INSTANCE_REMOVE, InstanceRemoveCommand(instance_id=instance_id))
        assert isinstance(removal, LifecycleOperationResult)
        assert (await finished_operation(client, removal)).state is OperationState.SUCCEEDED


def test_a_removed_instance_is_not_restored_beside_one_of_the_same_name(tmp_path):
    hub = factory_hub(tmp_path)
    client = hub_client(hub)

    async def scenario() -> None:
        first = await created_roles(client)
        await removed(client, first)
        second = await created_roles(client)

        refused = await client.call(
            INSTANCE_RESTORE, InstanceRestoreCommand(instance_id=first["coder"])
        )

        assert isinstance(refused, ErrorEnvelope)
        assert refused.code is ErrorCode.INVALID_ARGUMENT
        assert refused.message == (
            'An instance named "tickets-coder" exists already. Remove it before restoring this one.'
        )
        assert await listed(client) == set(second.values())

    asyncio.run(scenario())
