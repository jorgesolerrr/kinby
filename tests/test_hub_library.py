"""The hub's library: the skills kinby ships, copied onto the hub, beside its factories."""

import asyncio
import shutil

import pytest

from kinby.contracts import (
    LIBRARY_LIST,
    LIBRARY_SKILL_READ,
    ErrorCode,
    ErrorEnvelope,
    LibraryEntry,
    LibraryKind,
    LibraryListCommand,
    LibraryListResult,
    LibrarySkillReadCommand,
    LibrarySource,
    SkillResult,
)
from kinby.hub import Hub
from kinby.library import SHIPPED_SKILLS
from tests.test_hub import FakeImages, FakeRuntime, hub_client
from tests.test_hub_factories import FILES, factory_hub, renamed, write_factory

SHIPPED = [
    "adversarial-review",
    "api-and-interface-design",
    "codebase-design",
    "no-comments",
    "ponytail",
    "pr",
    "tdd",
    "unslop",
]


def library(hub: Hub) -> list[LibraryEntry]:
    async def listed() -> list[LibraryEntry]:
        result = await hub_client(hub).call(LIBRARY_LIST, LibraryListCommand())
        assert isinstance(result, LibraryListResult), result
        return result.entries

    return asyncio.run(listed())


def skills(hub: Hub) -> list[str]:
    return [entry.name for entry in library(hub) if entry.kind is LibraryKind.SKILL]


def reopened(hub: Hub) -> Hub:
    hub.close()
    return Hub(hub.directory, runtime=FakeRuntime(), images=FakeImages())


def test_a_fresh_hubs_library_holds_the_shipped_skills_and_the_software_factory(tmp_path):
    hub = Hub(tmp_path / "hub", runtime=FakeRuntime(), images=FakeImages())

    entries = library(hub)

    assert [(entry.kind, entry.name, entry.source) for entry in entries] == [
        (LibraryKind.FACTORY, "software", LibrarySource.SHIPPED),
        *((LibraryKind.SKILL, name, LibrarySource.SHIPPED) for name in SHIPPED),
    ]
    assert all(entry.description for entry in entries if entry.kind is LibraryKind.SKILL)


def test_opening_a_hub_from_before_the_shipped_skills_adds_them_once(tmp_path):
    hub = Hub(
        tmp_path / "hub",
        runtime=FakeRuntime(),
        images=FakeImages(),
        shipped_skills=tmp_path / "none",
    )
    assert skills(hub) == []

    hub = reopened(hub)
    edited = hub.directory / "skills" / "tdd" / "SKILL.md"
    edited.write_text("---\nname: tdd\ndescription: Mine.\n---\nMy way.\n")
    hub = reopened(hub)

    assert skills(hub) == SHIPPED
    assert edited.read_text() == "---\nname: tdd\ndescription: Mine.\n---\nMy way.\n"


def test_a_shipped_skill_the_user_deleted_does_not_come_back(tmp_path):
    hub = Hub(tmp_path / "hub", runtime=FakeRuntime(), images=FakeImages())
    shutil.rmtree(hub.directory / "skills" / "tdd")

    hub = reopened(hub)

    assert skills(hub) == [name for name in SHIPPED if name != "tdd"]


@pytest.mark.parametrize("name", SHIPPED)
def test_a_shipped_skill_reads_with_its_body_credits_and_licence(tmp_path, name):
    hub = Hub(tmp_path / "hub", runtime=FakeRuntime(), images=FakeImages())

    async def read() -> SkillResult:
        result = await hub_client(hub).call(LIBRARY_SKILL_READ, LibrarySkillReadCommand(name=name))
        assert isinstance(result, SkillResult), result
        return result

    skill = asyncio.run(read())

    assert skill.content == (SHIPPED_SKILLS / name / "SKILL.md").read_text(encoding="utf-8")
    assert {"CREDITS.md", "LICENSE"} <= set(skill.files)


def test_reading_a_skill_the_library_does_not_hold_is_not_found(tmp_path):
    hub = Hub(tmp_path / "hub", runtime=FakeRuntime(), images=FakeImages())

    async def read(name: str) -> object:
        return await hub_client(hub).call(LIBRARY_SKILL_READ, LibrarySkillReadCommand(name=name))

    for name in ("absent", "../factories"):
        answer = asyncio.run(read(name))
        assert isinstance(answer, ErrorEnvelope)
        assert answer.code is ErrorCode.NOT_FOUND


def test_a_factory_written_on_the_hub_is_written_and_a_copy_of_a_shipped_one_is_shipped(tmp_path):
    hub = factory_hub(tmp_path, shipped={"software": renamed(FILES, "software")})
    write_factory(tmp_path / "hub" / "factories" / "software", renamed(FILES, "software"))
    write_factory(tmp_path / "hub" / "factories" / "tickets", FILES)

    factories = [entry for entry in library(hub) if entry.kind is LibraryKind.FACTORY]

    assert factories == [
        LibraryEntry(
            kind=LibraryKind.FACTORY,
            name="software",
            description=None,
            source=LibrarySource.SHIPPED,
        ),
        LibraryEntry(
            kind=LibraryKind.FACTORY,
            name="tickets",
            description=None,
            source=LibrarySource.WRITTEN,
        ),
    ]
