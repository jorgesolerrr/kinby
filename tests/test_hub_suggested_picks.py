"""A factory role suggests picks, creating an instance takes them, and an update turns the skills
an older template supplied into picks."""

import asyncio
import json
import shutil
from pathlib import Path
from uuid import UUID

from kinby.contracts import (
    FACTORY_DESCRIBE,
    INSTANCE_CREATE,
    INSTANCE_PICKS_GET,
    ErrorCode,
    ErrorEnvelope,
    FactoryDescribeCommand,
    FactoryDescription,
    InstanceCreateCommand,
    InstancePicksGetCommand,
    InstancePicksResult,
    LibrarySource,
    LifecycleOperationResult,
    OperationState,
    PickState,
    SkillPick,
)
from kinby.instance.skills_lock import skill_hash
from kinby.library import SHIPPED_SKILLS
from tests.test_factory_templates import FACTORY, FILES, created_roles, factory_hub
from tests.test_hub import FakeRuntime, finished_operation, hub_client, prepared
from tests.test_hub_factory_roles import CODER, CODER_SECRETS
from tests.test_hub_template_sync import _directory, _factory, _template_step, _update

OWN_SKILL = "---\nname: own\ndescription: The template's own.\n---\nOurs.\n"
SUGGESTING = FILES | {
    "factory.yaml": FACTORY.replace(
        "  coder: { image: coder }\n",
        "  coder:\n    image: coder\n    suggested_picks: [tdd, unslop]\n",
    )
}


def _coder(picks: list[str], name: str | None = None) -> InstanceCreateCommand:
    return InstanceCreateCommand(
        manifest_id=name,
        factory_role=CODER,
        model="openai:gpt-5",
        secrets=CODER_SECRETS,
        picks=picks,
    )


def _skills(directory: Path) -> set[str]:
    return {folder.name for folder in (directory / "skills").iterdir() if folder.is_dir()}


def _lock(directory: Path) -> dict[str, dict[str, str]]:
    return json.loads((directory / "skills-lock.json").read_text(encoding="utf-8"))["skills"]


def _shipped(name: str) -> dict[str, str]:
    """The files of the shipped skill *name*, as a template that carries it supplies them."""
    folder = SHIPPED_SKILLS / name
    return {
        f"instances/coder/skills/{name}/{path.relative_to(folder).as_posix()}": path.read_text(
            encoding="utf-8"
        )
        for path in folder.rglob("*")
        if path.is_file()
    }


def test_describing_a_factory_returns_each_roles_suggested_picks(tmp_path):
    client = hub_client(factory_hub(tmp_path, SUGGESTING))

    async def scenario() -> FactoryDescription:
        described = await client.call(FACTORY_DESCRIBE, FactoryDescribeCommand(name="tickets"))
        assert isinstance(described, FactoryDescription)
        return described

    assert asyncio.run(scenario()).suggested_picks == {"coder": ["tdd", "unslop"], "reviewer": []}


def test_a_role_created_with_picks_holds_them_beside_its_template_and_a_shorter_list_holds_less(
    tmp_path,
):
    hub = factory_hub(tmp_path, SUGGESTING | {"instances/coder/skills/own/SKILL.md": OWN_SKILL})
    client = hub_client(hub)

    async def scenario() -> tuple[UUID, UUID]:
        every = await client.call(INSTANCE_CREATE, _coder(["tdd", "unslop"]))
        fewer = await client.call(INSTANCE_CREATE, _coder(["tdd"], name="billing-coder"))
        for accepted in (every, fewer):
            assert isinstance(accepted, LifecycleOperationResult), accepted
            finished = await finished_operation(client, accepted)
            assert finished.state is OperationState.SUCCEEDED, finished.detail
        assert isinstance(every, LifecycleOperationResult)
        assert isinstance(fewer, LifecycleOperationResult)
        return every.instance_id, fewer.instance_id

    every, fewer = asyncio.run(scenario())

    every_directory = hub.instances_directory / str(every)
    fewer_directory = hub.instances_directory / str(fewer)
    assert _skills(every_directory) == {"own", "tdd", "unslop"}
    assert set(_lock(every_directory)) == {"tdd", "unslop"}
    assert _skills(fewer_directory) == {"own", "tdd"}
    assert set(_lock(fewer_directory)) == {"tdd"}


def test_a_vanilla_instance_is_created_with_picks(tmp_path):
    hub = factory_hub(tmp_path)
    client = hub_client(hub)

    async def scenario() -> UUID:
        assert (await prepared(client)).state is OperationState.SUCCEEDED
        accepted = await client.call(
            INSTANCE_CREATE,
            InstanceCreateCommand(
                manifest_id="alice",
                model="openai:gpt-5",
                secrets={"api_key": "sk-test"},
                picks=["unslop"],
            ),
        )
        assert isinstance(accepted, LifecycleOperationResult), accepted
        finished = await finished_operation(client, accepted)
        assert finished.state is OperationState.SUCCEEDED, finished.detail
        return accepted.instance_id

    directory = hub.instances_directory / str(asyncio.run(scenario()))

    assert _skills(directory) == {"unslop"}
    assert _lock(directory)["unslop"]["computedHash"] == skill_hash(directory / "skills" / "unslop")


def test_a_create_whose_picks_the_library_or_the_template_refuses_creates_nothing(tmp_path):
    runtime = FakeRuntime()
    hub = factory_hub(
        tmp_path, SUGGESTING | {"instances/coder/skills/tdd/SKILL.md": OWN_SKILL}, runtime=runtime
    )
    client = hub_client(hub)

    async def scenario() -> None:
        refused = await client.call(INSTANCE_CREATE, _coder(["tdd", "absent", "unslop"]))

        assert isinstance(refused, ErrorEnvelope)
        assert refused.code is ErrorCode.INVALID_SETUP
        assert 'The library has no skill "absent".' in refused.fields["picks"]
        assert 'own skill "tdd"' in refused.fields["picks"]
        assert hub.registry.managed_instances() == []
        assert runtime.created == []

    asyncio.run(scenario())


def test_an_update_makes_the_library_skills_an_older_template_supplied_picks(tmp_path):
    files = (
        FILES
        | _shipped("tdd")
        | _shipped("unslop")
        | {"instances/coder/skills/own/SKILL.md": OWN_SKILL}
    )
    hub = factory_hub(tmp_path, files)
    client = hub_client(hub)

    async def scenario() -> None:
        coder = (await created_roles(client))["coder"]
        directory = _directory(tmp_path, coder)
        edited = directory / "skills" / "unslop" / "SKILL.md"
        edited.write_text(edited.read_text(encoding="utf-8") + "Mine too.\n", encoding="utf-8")
        for name in ("tdd", "unslop", "own"):
            shutil.rmtree(_factory(tmp_path) / "instances" / "coder" / "skills" / name)

        outcome = await _update(client, coder)
        picks = await client.call(INSTANCE_PICKS_GET, InstancePicksGetCommand(instance_id=coder))

        assert _template_step(outcome) == "Removed skills/own/SKILL.md."
        [step] = [step for step in outcome.steps if step.name == "picks"]
        assert step.detail == "Made tdd, unslop picks. No pick is behind the library."
        assert _skills(directory) == {"tdd", "unslop"}
        assert (SHIPPED_SKILLS / "tdd" / "SKILL.md").read_bytes() == (
            directory / "skills" / "tdd" / "SKILL.md"
        ).read_bytes()
        assert edited.read_text(encoding="utf-8").endswith("Mine too.\n")
        lock = _lock(directory)
        assert lock["tdd"]["computedHash"] == skill_hash(directory / "skills" / "tdd")
        assert lock["unslop"]["computedHash"] != skill_hash(directory / "skills" / "unslop")
        assert isinstance(picks, InstancePicksResult)
        assert picks.picks == [
            SkillPick(name="tdd", source=LibrarySource.SHIPPED, state=PickState.UP_TO_DATE),
            SkillPick(name="unslop", source=LibrarySource.SHIPPED, state=PickState.EDITED),
        ]

    asyncio.run(scenario())


def test_an_update_after_one_interrupted_past_the_lock_keeps_the_skills_it_made_picks(tmp_path):
    client = hub_client(install_hub(tmp_path, FILES | _shipped("tdd")))

    async def scenario() -> None:
        coder = (await installed(client)).instances["coder"].instance_id
        directory = _directory(tmp_path, coder)
        held = directory / "skills" / "tdd"
        # The lock an update writes before it stores the baseline without the new picks.
        (directory / "skills-lock.json").write_text(
            json.dumps(
                {
                    "version": 1,
                    "skills": {
                        "tdd": {
                            "source": "kinby",
                            "sourceType": "shipped",
                            "computedHash": skill_hash(held),
                        }
                    },
                }
            ),
            encoding="utf-8",
        )
        shutil.rmtree(_factory(tmp_path) / "instances" / "coder" / "skills")

        outcome = await _update(client, coder)

        assert _template_step(outcome) == 'The files already match template "coder".'
        assert (held / "SKILL.md").read_bytes() == (
            SHIPPED_SKILLS / "tdd" / "SKILL.md"
        ).read_bytes()
        assert set(_lock(directory)) == {"tdd"}

    asyncio.run(scenario())


def test_an_update_never_reads_through_a_link_in_a_skill_it_makes_a_pick(tmp_path, monkeypatch):
    client = hub_client(install_hub(tmp_path, FILES | _shipped("tdd")))
    outside = tmp_path / "outside.md"
    outside.write_text((SHIPPED_SKILLS / "tdd" / "tests.md").read_text(encoding="utf-8"))
    read_bytes = Path.read_bytes

    def denied(path: Path) -> bytes:
        if path.resolve() == outside:
            raise PermissionError(f"Permission denied: {path}")
        return read_bytes(path)

    async def scenario() -> None:
        coder = (await installed(client)).instances["coder"].instance_id
        directory = _directory(tmp_path, coder)
        linked = directory / "skills" / "tdd" / "tests.md"
        linked.unlink()
        linked.symlink_to(outside)
        shutil.rmtree(_factory(tmp_path) / "instances" / "coder" / "skills")
        monkeypatch.setattr(Path, "read_bytes", denied)

        outcome = await _update(client, coder)

        assert outcome.state is OperationState.SUCCEEDED, outcome.detail
        assert linked.is_symlink()
        assert _lock(directory)["tdd"]["computedHash"] == skill_hash(SHIPPED_SKILLS / "tdd")

    asyncio.run(scenario())
