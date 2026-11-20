import asyncio

import pytest
from pydantic import JsonValue

from kinby.contracts import (
    FACTORY_STEP_ADD,
    ErrorCode,
    ErrorEnvelope,
    FactoryResult,
    FactoryStepAddCommand,
    FactoryVersionSource,
)
from tests.test_hub import hub_client
from tests.test_hub_factories import FACTORY, FILES, factory_hub, write_factory
from tests.test_hub_factory_step_save import COMMENTED, commented_hub
from tests.test_hub_factory_versions import read, sources, versions

DOCS: dict[str, JsonValue] = {
    "id": "docs",
    "kind": "agent",
    "engine": "claude",
    "prompt": "prompts/docs.md",
    "requires": ["branch"],
}


async def add(
    client, after: str, step: dict[str, JsonValue], prompt: str | None = None
) -> FactoryResult | ErrorEnvelope:
    before = await read(client)
    result = await client.call(
        FACTORY_STEP_ADD,
        FactoryStepAddCommand(
            name="tickets", after=after, step=step, prompt=prompt, hash=before.hash
        ),
    )
    assert isinstance(result, FactoryResult | ErrorEnvelope)
    return result


def test_an_added_step_goes_right_after_its_step_with_its_new_prompt_as_one_version(tmp_path):
    client = commented_hub(tmp_path)

    async def scenario() -> None:
        before = await read(client)
        added = await add(client, "implement", DOCS, "Update the docs.\n")
        listed = await versions(client)

        assert isinstance(added, FactoryResult)
        assert added.files == before.files | {
            "factory.yaml": COMMENTED.replace(
                "  # Review comes before the pull request opens.\n",
                "  - id: docs\n"
                "    kind: agent\n"
                "    engine: claude\n"
                "    prompt: prompts/docs.md\n"
                "    requires:\n"
                "      - branch\n"
                "  # Review comes before the pull request opens.\n",
            ),
            "prompts/docs.md": "Update the docs.\n",
        }
        assert sources(listed[:1]) == [(FactoryVersionSource.WEB, ["docs"])]
        assert len(listed) == 2

    asyncio.run(scenario())


def test_a_step_added_after_the_last_one_goes_before_what_follows_the_steps(tmp_path):
    client = commented_hub(tmp_path)
    tidy: dict[str, JsonValue] = {"id": "tidy", "kind": "command", "run": ["make tidy"]}

    async def scenario() -> None:
        added = await add(client, "merge", tidy)

        assert isinstance(added, FactoryResult)
        assert added.files["factory.yaml"] == COMMENTED.replace(
            "done_requires:",
            "  - id: tidy\n    kind: command\n    run:\n      - make tidy\ndone_requires:",
        )

    asyncio.run(scenario())


def test_an_add_the_check_refuses_changes_nothing_and_names_its_problems(tmp_path):
    client = commented_hub(tmp_path)
    later = DOCS | {"failed": {"back": "merge", "max": 1}}

    async def scenario() -> None:
        before = await read(client)
        refused = await add(client, "implement", later, "Update the docs.\n")

        assert isinstance(refused, ErrorEnvelope)
        assert refused.code is ErrorCode.INVALID_ARGUMENT
        assert 'Step "docs"' in refused.message
        assert '"merge"' in refused.message
        assert await read(client) == before
        assert len(await versions(client)) == 1

    asyncio.run(scenario())


def test_an_add_after_a_step_the_factory_lacks_is_refused(tmp_path):
    client = commented_hub(tmp_path)

    async def scenario() -> None:
        before = await read(client)
        refused = await add(client, "triage", DOCS, "Update the docs.\n")

        assert isinstance(refused, ErrorEnvelope)
        assert refused.code is ErrorCode.INVALID_ARGUMENT
        assert await read(client) == before

    asyncio.run(scenario())


def test_an_added_steps_prompt_is_a_new_file_in_the_factorys_folder(tmp_path):
    client = commented_hub(tmp_path)

    async def scenario() -> None:
        before = await read(client)
        taken = await add(client, "implement", DOCS | {"prompt": "prompts/review.md"}, "Mine.\n")
        outside = await add(client, "implement", DOCS | {"prompt": "../outside.md"}, "Mine.\n")

        assert isinstance(taken, ErrorEnvelope)
        assert taken.code is ErrorCode.INVALID_ARGUMENT
        assert isinstance(outside, ErrorEnvelope)
        assert outside.code is ErrorCode.INVALID_ARGUMENT
        assert await read(client) == before
        assert not (tmp_path / "hub" / "factories" / "outside.md").exists()

    asyncio.run(scenario())


def test_an_add_from_a_stale_read_is_refused(tmp_path):
    client = commented_hub(tmp_path)

    async def scenario() -> None:
        before = await read(client)
        await add(client, "implement", DOCS, "Update the docs.\n")
        stale = await client.call(
            FACTORY_STEP_ADD,
            FactoryStepAddCommand(
                name="tickets",
                after="implement",
                step=DOCS | {"id": "docs-again", "prompt": "prompts/again.md"},
                prompt="Again.\n",
                hash=before.hash,
            ),
        )

        assert isinstance(stale, ErrorEnvelope)
        assert stale.code is ErrorCode.STALE

    asyncio.run(scenario())


def shifted(factory: str, by: int) -> str:
    """The factory file with its steps' lines indented *by* more columns, or fewer."""
    head, rest = factory.split("steps:\n")
    steps, tail = rest.split("done_requires:")
    lines = steps.splitlines(keepends=True)
    moved = [" " * by + line if by > 0 else line[-by:] for line in lines]
    return head + "steps:\n" + "".join(moved) + "done_requires:" + tail


@pytest.mark.parametrize("by", [-2, 2])
def test_an_added_step_takes_the_indentation_of_the_steps_around_it(tmp_path, by):
    hub = factory_hub(tmp_path)
    write_factory(
        tmp_path / "hub" / "factories" / "tickets", FILES | {"factory.yaml": shifted(FACTORY, by)}
    )
    client = hub_client(hub)
    tidy: dict[str, JsonValue] = {"id": "tidy", "kind": "command", "run": ["make tidy"]}

    async def scenario() -> None:
        added = await add(client, "implement", tidy)

        assert isinstance(added, FactoryResult), added
        assert added.files["factory.yaml"] == shifted(
            FACTORY.replace(
                "  - id: review\n",
                "  - id: tidy\n    kind: command\n    run:\n      - make tidy\n  - id: review\n",
            ),
            by,
        )

    asyncio.run(scenario())
