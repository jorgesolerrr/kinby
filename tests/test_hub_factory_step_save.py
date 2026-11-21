import asyncio
from pathlib import Path

from kinby.contracts import (
    FACTORY_STEP_SAVE,
    ErrorCode,
    ErrorEnvelope,
    FactoryResult,
    FactoryStepSaveCommand,
    FactoryVersionSource,
)
from tests.test_hub import hub_client
from tests.test_hub_factories import FACTORY, FILES, HOOKS, factory_hub, write_factory
from tests.test_hub_factory_versions import diff, read, sources, versions

COMMENTED = "# Tickets become pull requests.\n" + FACTORY.replace(
    "  - id: review\n",
    "  # Review comes before the pull request opens.\n  - id: review\n",
).replace(
    "  - id: checks\n",
    "    # Changes go back to implement.\n  - id: checks\n",
)
REVIEW = {
    "id": "review",
    "kind": "agent",
    "engine": "kinby",
    "prompt": "prompts/review.md",
    "check": "read_verdict",
    "requires": ["branch"],
    "outcomes": {"clean": "next", "changes": {"back": "implement", "max": 3}},
}


def commented_hub(tmp_path: Path):
    hub = factory_hub(tmp_path)
    write_factory(tmp_path / "hub" / "factories" / "tickets", FILES | {"factory.yaml": COMMENTED})
    return hub_client(hub)


async def save(client, command: FactoryStepSaveCommand) -> FactoryResult | ErrorEnvelope:
    result = await client.call(FACTORY_STEP_SAVE, command)
    assert isinstance(result, FactoryResult | ErrorEnvelope)
    return result


def test_a_step_save_changes_only_that_step_its_prompt_and_its_hook(tmp_path):
    client = commented_hub(tmp_path)
    review = REVIEW | {
        "description": "Reviews the branch.",
        "outcomes": {"clean": "next", "changes": {"back": "implement", "max": 2}},
        "timeout": "15m",
    }
    hook = HOOKS.replace("    pass\n\n\n@hook", "    return None\n\n\n@hook")

    async def scenario() -> None:
        before = await read(client)
        saved = await save(
            client,
            FactoryStepSaveCommand(
                name="tickets",
                step=review,
                prompt="Review the branch again.\n",
                hook=hook,
                hash=before.hash,
            ),
        )
        listed = await versions(client)

        assert isinstance(saved, FactoryResult)
        assert saved.files == before.files | {
            "factory.yaml": COMMENTED.replace(
                "    outcomes: { clean: next, changes: { back: implement, max: 3 } }\n",
                "    description: Reviews the branch.\n"
                "    timeout: 15m\n"
                "    outcomes: { clean: next, changes: { back: implement, max: 2 } }\n",
            ),
            "prompts/review.md": "Review the branch again.\n",
            "hooks/record.py": hook,
        }
        assert sources(listed[:1]) == [(FactoryVersionSource.WEB, ["review"])]
        assert saved.version == listed[0].version

    asyncio.run(scenario())


def test_a_step_save_keeps_the_comment_after_the_steps_last_field(tmp_path):
    client = commented_hub(tmp_path)

    async def scenario() -> None:
        before = await read(client)
        saved = await save(
            client,
            FactoryStepSaveCommand(
                name="tickets",
                step=REVIEW | {"failed": {"retry": 2}, "outcomes": {"clean": "next"}},
                hash=before.hash,
            ),
        )

        assert isinstance(saved, FactoryResult)
        assert saved.files["factory.yaml"] == COMMENTED.replace(
            "    outcomes: { clean: next, changes: { back: implement, max: 3 } }\n",
            "    failed:\n      retry: 2\n    outcomes: { clean: next }\n",
        )

    asyncio.run(scenario())


def test_a_step_save_of_one_field_is_a_version_whose_diff_changes_that_line(tmp_path):
    client = commented_hub(tmp_path)
    implement = {
        "id": "implement",
        "kind": "agent",
        "engine": "claude",
        "prompt": "prompts/implement.md",
        "check": "record_branch",
        "results": {"branch": "str"},
        "timeout": "45m",
    }

    async def scenario() -> None:
        before = await read(client)
        saved = await save(
            client, FactoryStepSaveCommand(name="tickets", step=implement, hash=before.hash)
        )

        assert isinstance(saved, FactoryResult)
        assert saved.version is not None
        changed = [
            line
            for line in (await diff(client, saved.version)).splitlines()
            if line.startswith(("-", "+")) and not line.startswith(("---", "+++"))
        ]
        assert changed == ["-    timeout: 60m", "+    timeout: 45m"]

    asyncio.run(scenario())


def test_a_step_save_the_check_refuses_changes_nothing_and_names_its_problems(tmp_path):
    client = commented_hub(tmp_path)
    later = REVIEW | {"outcomes": {"clean": "next", "changes": {"back": "merge", "max": 3}}}

    async def scenario() -> None:
        before = await read(client)
        refused = await save(
            client,
            FactoryStepSaveCommand(name="tickets", step=later, prompt="Mine.\n", hash=before.hash),
        )

        assert isinstance(refused, ErrorEnvelope)
        assert refused.code is ErrorCode.INVALID_ARGUMENT
        assert 'Step "review"' in refused.message
        assert '"merge"' in refused.message
        assert await read(client) == before
        assert len(await versions(client)) == 1

    asyncio.run(scenario())


def test_a_step_save_from_a_stale_read_is_refused(tmp_path):
    client = commented_hub(tmp_path)

    async def scenario() -> None:
        before = await read(client)
        command = FactoryStepSaveCommand(
            name="tickets", step=REVIEW, prompt="Review it.\n", hash=before.hash
        )
        await save(client, command)
        stale = await save(client, command.model_copy(update={"prompt": "Mine.\n"}))

        assert isinstance(stale, ErrorEnvelope)
        assert stale.code is ErrorCode.STALE
        assert (await read(client)).files["prompts/review.md"] == "Review it.\n"

    asyncio.run(scenario())


def test_a_step_save_names_a_step_the_factory_has(tmp_path):
    client = commented_hub(tmp_path)

    async def scenario() -> None:
        before = await read(client)
        refused = await save(
            client,
            FactoryStepSaveCommand(
                name="tickets", step=REVIEW | {"id": "triage"}, hash=before.hash
            ),
        )

        assert isinstance(refused, ErrorEnvelope)
        assert refused.code is ErrorCode.INVALID_ARGUMENT
        assert await read(client) == before

    asyncio.run(scenario())


def test_a_step_save_writes_no_prompt_outside_the_factorys_folder(tmp_path):
    client = commented_hub(tmp_path)

    async def scenario() -> None:
        before = await read(client)
        refused = await save(
            client,
            FactoryStepSaveCommand(
                name="tickets",
                step=REVIEW | {"prompt": "../outside.md"},
                prompt="Escaped.\n",
                hash=before.hash,
            ),
        )

        assert isinstance(refused, ErrorEnvelope)
        assert refused.code is ErrorCode.INVALID_ARGUMENT
        assert not (tmp_path / "hub" / "factories" / "outside.md").exists()

    asyncio.run(scenario())


def test_a_factory_names_the_file_that_declares_each_check_and_tool(tmp_path):
    client = commented_hub(tmp_path)

    async def scenario() -> None:
        assert (await read(client)).declared_in == {
            "open_pull_request": "tools/github.py",
            "read_verdict": "hooks/record.py",
            "record_branch": "hooks/record.py",
        }

    asyncio.run(scenario())


def test_a_step_save_fills_a_mapping_the_step_declares_empty(tmp_path):
    hub = factory_hub(tmp_path)
    declared = FACTORY.replace(
        '    run: ["uv run pytest"]\n', '    run: ["uv run pytest"]\n    outcomes: {}\n'
    )
    write_factory(tmp_path / "hub" / "factories" / "tickets", FILES | {"factory.yaml": declared})
    client = hub_client(hub)
    checks = {
        "id": "checks",
        "kind": "command",
        "run": ["uv run pytest"],
        "requires": ["branch"],
        "outcomes": {"passed": "next"},
        "description": "Runs the tests.",
    }

    async def scenario() -> None:
        before = await read(client)
        saved = await save(
            client, FactoryStepSaveCommand(name="tickets", step=checks, hash=before.hash)
        )

        assert isinstance(saved, FactoryResult)
        assert saved.files["factory.yaml"] == declared.replace(
            "    outcomes: {}\n",
            "    outcomes:\n      passed: next\n    description: Runs the tests.\n",
        )

    asyncio.run(scenario())


def test_a_step_save_keeps_steps_written_as_an_indentless_sequence(tmp_path):
    hub = factory_hub(tmp_path)
    head, steps = FACTORY.split("steps:\n")
    body, tail = steps.split("done_requires:")
    indentless = (
        (head + "steps:\n" + "".join(line[2:] for line in body.splitlines(keepends=True)))
        + "done_requires:"
        + tail
    )
    write_factory(tmp_path / "hub" / "factories" / "tickets", FILES | {"factory.yaml": indentless})
    client = hub_client(hub)

    async def scenario() -> None:
        before = await read(client)
        saved = await save(
            client,
            FactoryStepSaveCommand(
                name="tickets", step=REVIEW | {"timeout": "15m"}, hash=before.hash
            ),
        )

        assert isinstance(saved, FactoryResult)
        assert saved.files["factory.yaml"] == indentless.replace(
            "  outcomes: { clean: next, changes: { back: implement, max: 3 } }\n",
            "  timeout: 15m\n  outcomes: { clean: next, changes: { back: implement, max: 3 } }\n",
        )

    asyncio.run(scenario())
