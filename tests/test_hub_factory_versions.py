import asyncio
from pathlib import Path

from kinby.contracts import (
    FACTORY_EDIT,
    FACTORY_GET,
    FACTORY_LIST,
    FACTORY_REMOVE,
    FACTORY_RESET,
    FACTORY_REVERT,
    FACTORY_VERSION_DIFF,
    FACTORY_VERSION_LIST,
    ErrorCode,
    ErrorEnvelope,
    FactoryEditCommand,
    FactoryGetCommand,
    FactoryListCommand,
    FactoryListResult,
    FactoryRemoveCommand,
    FactoryResetCommand,
    FactoryResult,
    FactoryRevertCommand,
    FactorySource,
    FactoryVersion,
    FactoryVersionDiff,
    FactoryVersionDiffCommand,
    FactoryVersionListCommand,
    FactoryVersionListResult,
    FactoryVersionSource,
)
from tests.test_hub import hub_client
from tests.test_hub_factories import FACTORY, FILES, factory_hub, renamed, write_factory

STEPS = ["implement", "review", "checks", "open-pr", "babysit", "merge"]


async def read(client, name: str = "tickets") -> FactoryResult:
    result = await client.call(FACTORY_GET, FactoryGetCommand(name=name))
    assert isinstance(result, FactoryResult)
    return result


async def versions(client, name: str = "tickets") -> list[FactoryVersion]:
    result = await client.call(FACTORY_VERSION_LIST, FactoryVersionListCommand(name=name))
    assert isinstance(result, FactoryVersionListResult)
    return result.versions


async def edit(client, files: dict[str, str], name: str = "tickets") -> FactoryResult:
    before = await read(client, name)
    result = await client.call(
        FACTORY_EDIT, FactoryEditCommand(name=name, files=files, hash=before.hash)
    )
    assert isinstance(result, FactoryResult)
    return result


async def diff(client, version: str, name: str = "tickets") -> str:
    result = await client.call(
        FACTORY_VERSION_DIFF, FactoryVersionDiffCommand(name=name, version=version)
    )
    assert isinstance(result, FactoryVersionDiff)
    return result.diff


def sources(listed: list[FactoryVersion]) -> list[tuple[FactoryVersionSource, list[str]]]:
    return [(version.source, version.steps) for version in listed]


def hand_written_hub(tmp_path: Path):
    hub = factory_hub(tmp_path)
    write_factory(tmp_path / "hub" / "factories" / "tickets", FILES)
    return hub_client(hub)


def test_each_edit_that_passes_the_check_is_one_web_version(tmp_path):
    client = hand_written_hub(tmp_path)
    timeout = FACTORY.replace("timeout: 60m", "timeout: 90m")

    async def scenario() -> None:
        reviewed = await edit(client, {"prompts/review.md": "Review it.\n"})
        timed = await edit(client, {"factory.yaml": timeout})
        before = await read(client)
        refused = await client.call(
            FACTORY_EDIT,
            FactoryEditCommand(
                name="tickets", files={"factory.yaml": "steps: [\n"}, hash=before.hash
            ),
        )
        listed = await versions(client)

        assert isinstance(refused, ErrorEnvelope)
        assert sources(listed) == [
            (FactoryVersionSource.WEB, ["implement"]),
            (FactoryVersionSource.WEB, ["review"]),
            (FactoryVersionSource.BY_HAND, STEPS),
        ]
        assert [version.version for version in listed[:2]] == [timed.version, reviewed.version]
        assert before.version == timed.version
        assert listed[0].time >= listed[1].time >= listed[2].time

    asyncio.run(scenario())


def test_a_hand_change_is_a_version_the_next_time_the_hub_reads_the_factory(tmp_path):
    client = hand_written_hub(tmp_path)
    folder = tmp_path / "hub" / "factories" / "tickets"

    async def scenario() -> None:
        before = await read(client)
        (folder / "prompts" / "review.md").write_text("Review it by hand.\n", encoding="utf-8")
        after = await read(client)
        listed = await versions(client)

        assert after.files["prompts/review.md"] == "Review it by hand.\n"
        assert after.version != before.version
        assert after.version == listed[0].version
        assert sources(listed) == [
            (FactoryVersionSource.BY_HAND, ["review"]),
            (FactoryVersionSource.BY_HAND, STEPS),
        ]

    asyncio.run(scenario())


def test_a_hand_change_that_fails_the_check_leaves_the_last_good_version_served(tmp_path):
    client = hand_written_hub(tmp_path)
    folder = tmp_path / "hub" / "factories" / "tickets"

    async def scenario() -> None:
        before = await read(client)
        (folder / "factory.yaml").write_text("steps: [\n", encoding="utf-8")
        (folder / "prompts" / "extra.md").write_text("Extra.\n", encoding="utf-8")
        after = await read(client)

        assert after == before
        assert len(await versions(client)) == 1

    asyncio.run(scenario())


def test_the_diff_read_shows_one_versions_change(tmp_path):
    client = hand_written_hub(tmp_path)

    async def scenario() -> None:
        reviewed = await edit(client, {"prompts/review.md": "Review it.\n"})
        assert reviewed.version is not None
        shown = await diff(client, reviewed.version)

        assert "prompts/review.md" in shown
        assert "-Review the branch.\n+Review it.\n" in shown
        assert "factory.yaml" not in shown

    asyncio.run(scenario())


def test_revert_makes_a_new_version_with_an_earlier_versions_files(tmp_path):
    client = hand_written_hub(tmp_path)

    async def scenario() -> None:
        first = await read(client)
        await edit(client, {"prompts/review.md": "Review it.\n"})
        await edit(client, {"prompts/implement.md": "Implement it.\n"})
        assert first.version is not None
        reverted = await client.call(
            FACTORY_REVERT, FactoryRevertCommand(name="tickets", version=first.version)
        )
        listed = await versions(client)

        assert isinstance(reverted, FactoryResult)
        assert reverted.files == first.files
        assert reverted.version == listed[0].version
        assert sources(listed) == [
            (FactoryVersionSource.REVERT, ["implement", "review"]),
            (FactoryVersionSource.WEB, ["implement"]),
            (FactoryVersionSource.WEB, ["review"]),
            (FactoryVersionSource.BY_HAND, STEPS),
        ]

    asyncio.run(scenario())


def test_revert_refuses_a_version_the_factory_never_had(tmp_path):
    hub = factory_hub(tmp_path)
    write_factory(tmp_path / "hub" / "factories" / "tickets", FILES)
    write_factory(tmp_path / "hub" / "factories" / "other", renamed(FILES, "other"))
    client = hub_client(hub)

    async def scenario() -> None:
        other = await read(client, "other")
        assert other.version is not None
        refused = await client.call(
            FACTORY_REVERT, FactoryRevertCommand(name="tickets", version=other.version)
        )
        unknown = await client.call(
            FACTORY_VERSION_DIFF, FactoryVersionDiffCommand(name="tickets", version="0" * 40)
        )

        assert isinstance(refused, ErrorEnvelope)
        assert refused.code is ErrorCode.NOT_FOUND
        assert isinstance(unknown, ErrorEnvelope)
        assert unknown.code is ErrorCode.NOT_FOUND

    asyncio.run(scenario())


def test_the_first_edit_of_a_shipped_factory_has_the_shipped_version_as_its_parent(tmp_path):
    shipped = renamed(FILES, "software")
    client = hub_client(factory_hub(tmp_path, shipped={"software": shipped}))

    async def scenario() -> None:
        before = await read(client, "software")
        unedited = await versions(client, "software")
        edited = await edit(client, {"prompts/review.md": "Review it.\n"}, "software")
        listed = await versions(client, "software")
        summaries = await client.call(FACTORY_LIST, FactoryListCommand())

        assert before.version is None
        assert unedited == []
        assert sources(listed) == [
            (FactoryVersionSource.WEB, ["review"]),
            (FactoryVersionSource.SHIPPED, STEPS),
        ]
        assert edited.version == listed[0].version
        assert isinstance(summaries, FactoryListResult)
        assert [summary.version for summary in summaries.factories] == [edited.version]
        assert "-Review the branch.\n+Review it.\n" in await diff(
            client, listed[0].version, "software"
        )

    asyncio.run(scenario())


def test_reset_to_shipped_makes_a_new_version_with_kinbys_files(tmp_path):
    shipped = renamed(FILES, "software")
    hub = factory_hub(tmp_path, shipped={"software": shipped})
    write_factory(tmp_path / "hub" / "factories" / "tickets", FILES)
    client = hub_client(hub)

    async def scenario() -> None:
        await edit(client, {"prompts/review.md": "Review it.\n"}, "software")
        reset = await client.call(FACTORY_RESET, FactoryResetCommand(name="software"))
        listed = await versions(client, "software")
        refused = await client.call(FACTORY_RESET, FactoryResetCommand(name="tickets"))

        assert isinstance(reset, FactoryResult)
        assert reset.source is FactorySource.HUB
        assert reset.files == shipped
        assert sources(listed) == [
            (FactoryVersionSource.SHIPPED, ["review"]),
            (FactoryVersionSource.WEB, ["review"]),
            (FactoryVersionSource.SHIPPED, STEPS),
        ]
        assert isinstance(refused, ErrorEnvelope)
        assert refused.code is ErrorCode.INVALID_ARGUMENT

    asyncio.run(scenario())


def test_a_factory_made_again_under_a_removed_ones_name_starts_its_own_history(tmp_path):
    client = hand_written_hub(tmp_path)

    async def scenario() -> None:
        await edit(client, {"prompts/review.md": "Review it.\n"})
        await client.call(FACTORY_REMOVE, FactoryRemoveCommand(name="tickets"))
        made = await client.call(
            FACTORY_EDIT, FactoryEditCommand(name="tickets", files=FILES, hash=None)
        )
        listed = await versions(client)

        assert isinstance(made, FactoryResult)
        assert sources(listed) == [(FactoryVersionSource.WEB, STEPS)]

    asyncio.run(scenario())
