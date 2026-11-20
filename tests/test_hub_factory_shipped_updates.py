import asyncio

from kinby.contracts import (
    FACTORY_STEP_RESET,
    ErrorCode,
    ErrorEnvelope,
    FactoryResult,
    FactoryStepResetCommand,
    FactoryVersionSource,
)
from kinby.hub import Hub
from tests.test_hub import FakeImages, FakeRuntime, hub_client
from tests.test_hub_factories import FILES, factory_hub, renamed, write_factory
from tests.test_hub_factory_step_off import turned_off
from tests.test_hub_factory_versions import STEPS, edit, read, sources, versions

SOFTWARE = renamed(FILES, "software")
IMPLEMENT = {"prompts/implement.md": "Implement issue {{issue}} test-first.\n"}
REVIEWED = {"prompts/review.md": "Review it.\n"}


def upgraded(hub: Hub, files: dict[str, str]) -> Hub:
    """Reopen *hub* on a kinby that ships the software factory as *files*."""
    hub.close()
    shipped = hub.directory.parent / "shipped"
    write_factory(shipped / "software", files)
    return Hub(hub.directory, runtime=FakeRuntime(), images=FakeImages(), shipped_factories=shipped)


def marks(factory: FactoryResult) -> tuple[bool, bool, list[str]]:
    return factory.differs_from_shipped, factory.shipped_update_waiting, factory.differing_steps


async def reset_step(client, step: str, name: str = "software") -> FactoryResult | ErrorEnvelope:
    before = await read(client, name)
    result = await client.call(
        FACTORY_STEP_RESET, FactoryStepResetCommand(name=name, step=step, hash=before.hash)
    )
    assert isinstance(result, FactoryResult | ErrorEnvelope)
    return result


def test_kinbys_update_to_steps_the_user_never_changed_lands_by_itself(tmp_path):
    hub = factory_hub(tmp_path, shipped={"software": SOFTWARE})
    update = SOFTWARE | IMPLEMENT
    update["factory.yaml"] = update["factory.yaml"].replace("timeout: 60m", "timeout: 90m")

    async def scenario() -> None:
        unchanged = await read(hub_client(hub), "software")
        await edit(hub_client(hub), REVIEWED, "software")
        client = hub_client(upgraded(hub, update))
        merged = await read(client, "software")
        listed = await versions(client, "software")

        assert marks(unchanged) == (False, False, [])
        assert merged.files == update | REVIEWED
        assert sources(listed) == [
            (FactoryVersionSource.SHIPPED_UPDATE, ["implement"]),
            (FactoryVersionSource.WEB, ["review"]),
            (FactoryVersionSource.SHIPPED, STEPS),
        ]
        assert marks(merged) == (True, False, ["review"])

    asyncio.run(scenario())


def test_kinbys_update_that_conflicts_keeps_the_users_version_and_waits(tmp_path):
    hub = factory_hub(tmp_path, shipped={"software": SOFTWARE})

    async def scenario() -> None:
        changed = await edit(hub_client(hub), REVIEWED, "software")
        client = hub_client(
            upgraded(hub, SOFTWARE | IMPLEMENT | {"prompts/review.md": "Review it closely.\n"})
        )
        kept = await read(client, "software")

        assert kept.files == changed.files
        assert kept.version == changed.version
        assert marks(kept) == (True, True, ["implement", "review"])

    asyncio.run(scenario())


def test_kinbys_update_that_fails_the_check_keeps_the_users_version_and_waits(tmp_path):
    hub = factory_hub(tmp_path, shipped={"software": SOFTWARE})
    off = {"factory.yaml": turned_off(SOFTWARE["factory.yaml"], "review")}
    # Merges cleanly, but sends a failure back to the step the user turned off.
    routed = SOFTWARE["factory.yaml"].replace(
        '    run: ["uv run pytest"]\n',
        '    run: ["uv run pytest"]\n    failed: { back: review, max: 2 }\n',
    )

    async def scenario() -> None:
        changed = await edit(hub_client(hub), off, "software")
        client = hub_client(upgraded(hub, SOFTWARE | {"factory.yaml": routed}))
        kept = await read(client, "software")

        assert kept.files == changed.files
        assert kept.version == changed.version
        assert marks(kept) == (True, True, ["review", "checks"])

    asyncio.run(scenario())


def test_resetting_a_step_takes_kinbys_version_of_it_as_one_version(tmp_path):
    hub = factory_hub(tmp_path, shipped={"software": SOFTWARE})
    review = {
        "factory.yaml": SOFTWARE["factory.yaml"].replace("max: 3 }", "max: 2 }"),
        "prompts/review.md": "Review it closely.\n",
    }
    update = SOFTWARE | IMPLEMENT | review
    write_factory(tmp_path / "hub" / "factories" / "tickets", FILES)

    async def scenario() -> None:
        await edit(hub_client(hub), REVIEWED, "software")
        client = hub_client(upgraded(hub, update))
        reviewed = await reset_step(client, "review")
        listed = await versions(client, "software")
        implemented = await reset_step(client, "implement")
        refused = await reset_step(client, "review", "tickets")

        assert isinstance(reviewed, FactoryResult)
        assert reviewed.files == SOFTWARE | review
        assert sources(listed)[:2] == [
            (FactoryVersionSource.WEB, ["review"]),
            (FactoryVersionSource.WEB, ["review"]),
        ]
        assert marks(reviewed) == (True, True, ["implement"])
        assert isinstance(implemented, FactoryResult)
        assert implemented.files == update
        assert marks(implemented) == (False, False, [])
        assert isinstance(refused, ErrorEnvelope)
        assert refused.code is ErrorCode.INVALID_ARGUMENT

    asyncio.run(scenario())


def test_resetting_a_step_lines_kinbys_step_up_with_the_users_steps(tmp_path):
    hub = factory_hub(tmp_path, shipped={"software": SOFTWARE})
    head, steps = SOFTWARE["factory.yaml"].split("steps:\n")
    indentless = f"{head}steps:\n{steps.replace('\n  ', '\n')[2:]}"
    update = SOFTWARE | {"factory.yaml": SOFTWARE["factory.yaml"].replace("max: 3 }", "max: 2 }")}

    async def scenario() -> None:
        await edit(hub_client(hub), {"factory.yaml": indentless}, "software")
        client = hub_client(upgraded(hub, update))
        reviewed = await reset_step(client, "review")

        assert isinstance(reviewed, FactoryResult), reviewed
        assert reviewed.files["factory.yaml"] == indentless.replace("max: 3 }", "max: 2 }")
        assert reviewed.differing_steps == []

    asyncio.run(scenario())
