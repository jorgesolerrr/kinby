"""What an instance's summary tells the user to look at: a kinby revision behind the hub's, or
files behind its factory's template."""

import asyncio
import shutil
import sqlite3
from pathlib import Path

import pytest

from kinby.cli.client import ContractClient
from kinby.contracts import (
    FACTORY_EDIT,
    INSTANCE_LIST,
    FactoryEditCommand,
    FactoryResult,
    InstanceListCommand,
    InstanceListResult,
    InstanceNotice,
    RevisionBehind,
    TemplateBehind,
)
from tests.test_factory_install import install_hub, installed
from tests.test_hub import FakeImages, created_instance, hub_at, hub_client, started_instance
from tests.test_hub_factories import read

#: The revision FakeImages builds, which is the hub's own until a test moves the hub on.
BUILT = "a" * 40
SKILL = "---\nname: ponytail\ndescription: Be lazy.\n---\nBe lazy.\n"
CODER_BEHIND = TemplateBehind(
    message=(
        'Factory "tickets" changed template "coder" since the hub copied it. '
        "An instance update brings the files up to the factory's."
    )
)


def _folder(tmp_path: Path) -> Path:
    return tmp_path / "hub" / "factories" / "tickets"


async def _notices(client: ContractClient) -> list[InstanceNotice]:
    listed = await client.call(INSTANCE_LIST, InstanceListCommand())
    assert isinstance(listed, InstanceListResult)
    [instance] = listed.instances
    return instance.notices


def test_an_instance_on_the_hubs_revision_has_no_notice(tmp_path):
    async def scenario() -> None:
        hub = hub_at(tmp_path / "hub")
        client = hub_client(hub)
        await started_instance(client, hub)

        assert await _notices(client) == []

    asyncio.run(scenario())


def test_an_instance_behind_the_hubs_revision_says_so(tmp_path):
    async def scenario() -> None:
        images = FakeImages()
        hub = hub_at(tmp_path / "hub", images=images)
        client = hub_client(hub)
        await started_instance(client, hub)

        images.hub_revision = "b" * 40

        assert await _notices(client) == [
            RevisionBehind(
                message="The instance runs kinby aaaaaaa, and the hub is at bbbbbbb.",
                instance_revision=BUILT,
                hub_revision="b" * 40,
            )
        ]

    asyncio.run(scenario())


async def _notices_by_instance(client: ContractClient) -> dict[str, list[InstanceNotice]]:
    listed = await client.call(INSTANCE_LIST, InstanceListCommand())
    assert isinstance(listed, InstanceListResult)
    return {instance.manifest_id: instance.notices for instance in listed.instances}


async def _edit(client: ContractClient, files: dict[str, str]) -> None:
    before = await read(client)
    edited = await client.call(
        FACTORY_EDIT, FactoryEditCommand(name="tickets", files=files, hash=before.hash)
    )
    assert isinstance(edited, FactoryResult)


def test_a_freshly_installed_factory_instance_has_no_notice(tmp_path):
    async def scenario() -> None:
        client = hub_client(install_hub(tmp_path))
        await installed(client)

        assert await _notices_by_instance(client) == {"tickets-coder": [], "tickets-reviewer": []}

    asyncio.run(scenario())


def test_an_instance_whose_template_file_the_factory_changed_says_so(tmp_path):
    async def scenario() -> None:
        client = hub_client(install_hub(tmp_path))
        await installed(client)
        await created_instance(client)

        await _edit(client, {"instances/coder/SYSTEM.md": "You implement issues well.\n"})

        assert await _notices_by_instance(client) == {
            "tickets-coder": [CODER_BEHIND],
            "tickets-reviewer": [],
            "alice": [],
        }

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "files",
    [
        {"instances/coder/skills/ponytail/SKILL.md": SKILL},
        {"instances/coder/SYSTEM.md": "You implement issues well.\n"},
    ],
    ids=["added", "changed"],
)
def test_a_file_the_factory_added_or_changed_puts_its_instance_behind(tmp_path, files):
    async def scenario() -> None:
        client = hub_client(install_hub(tmp_path))
        await installed(client)

        await _edit(client, files)

        assert await _notices_by_instance(client) == {
            "tickets-coder": [CODER_BEHIND],
            "tickets-reviewer": [],
        }

    asyncio.run(scenario())


def test_a_file_the_factory_dropped_puts_its_instance_behind(tmp_path):
    async def scenario() -> None:
        client = hub_client(install_hub(tmp_path))
        await installed(client)

        (_folder(tmp_path) / "instances" / "coder" / "SYSTEM.md").unlink()

        assert await _notices_by_instance(client) == {
            "tickets-coder": [CODER_BEHIND],
            "tickets-reviewer": [],
        }

    asyncio.run(scenario())


def _remove(folder: Path) -> None:
    shutil.rmtree(folder)


def _break(folder: Path) -> None:
    (folder / "factory.yaml").write_text("name: tickets\n", encoding="utf-8")


def _undecodable_factory_file(folder: Path) -> None:
    (folder / "factory.yaml").write_bytes(b"name: tickets\n\xff\n")


def _unreadable_factory_file(folder: Path) -> None:
    (folder / "factory.yaml").unlink()
    (folder / "factory.yaml").mkdir()


def _undecodable_template_file(folder: Path) -> None:
    (folder / "instances" / "coder" / "SYSTEM.md").write_bytes(b"\xff\n")


@pytest.mark.parametrize(
    "unserve",
    [
        _remove,
        _break,
        _undecodable_factory_file,
        _unreadable_factory_file,
        _undecodable_template_file,
    ],
    ids=[
        "removed",
        "failing its check",
        "factory file not UTF-8",
        "factory file unreadable",
        "template file not UTF-8",
    ],
)
def test_an_instance_whose_factory_the_hub_does_not_serve_has_no_template_notice(tmp_path, unserve):
    async def scenario() -> None:
        client = hub_client(install_hub(tmp_path))
        await installed(client)
        await _edit(client, {"instances/coder/SYSTEM.md": "You implement issues well.\n"})

        unserve(_folder(tmp_path))

        assert await _notices_by_instance(client) == {"tickets-coder": [], "tickets-reviewer": []}

    asyncio.run(scenario())


def test_a_factory_instance_from_before_baselines_says_the_hub_has_no_record(tmp_path):
    async def scenario() -> None:
        hub = install_hub(tmp_path)
        await installed(hub_client(hub))
        hub.close()
        with sqlite3.connect(tmp_path / "hub" / "registry.sqlite") as connection:
            connection.execute("ALTER TABLE factory_members DROP COLUMN baseline")

        client = hub_client(install_hub(tmp_path))

        assert await _notices_by_instance(client) == {
            "tickets-coder": [_no_record("coder")],
            "tickets-reviewer": [_no_record("reviewer")],
        }

    asyncio.run(scenario())


def _no_record(template: str) -> TemplateBehind:
    return TemplateBehind(
        message=(
            f'The hub kept no record of the files template "{template}" of factory "tickets" '
            "supplied. An instance update brings the files up to the factory's."
        )
    )


def test_a_template_file_the_hub_cannot_read_leaves_the_list_whole(tmp_path, monkeypatch):
    unreadable = _folder(tmp_path) / "instances" / "coder" / "SYSTEM.md"
    read_text = Path.read_text

    def denied(path: Path, *args, **kwargs) -> str:
        if path == unreadable:
            raise PermissionError(13, "Permission denied", str(path))
        return read_text(path, *args, **kwargs)

    async def scenario() -> None:
        client = hub_client(install_hub(tmp_path))
        await installed(client)
        await _edit(client, {"instances/coder/SYSTEM.md": "You implement issues well.\n"})

        # The tests run as root, which reads past any file mode.
        monkeypatch.setattr(Path, "read_text", denied)

        assert await _notices_by_instance(client) == {"tickets-coder": [], "tickets-reviewer": []}

    asyncio.run(scenario())
