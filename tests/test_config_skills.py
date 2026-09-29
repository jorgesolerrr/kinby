import asyncio
from datetime import UTC, datetime
from importlib import import_module
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import JsonValue

from kinby.contracts import ErrorCode, ErrorEnvelope
from kinby.instance import Instance, load_instance
from kinby.plugins import ToolContext, tool
from kinby.plugins.instance_tools import instance_tools
from tests.test_instance_config import EMPTY_HASH, SKILL
from tests.test_routines import instance_at
from tests.test_scheduler import FakeClock, call, runtime


def _skill(root: Path, name: str, body: str) -> Path:
    path = root / name / "SKILL.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\nname: {name}\ndescription: {body}\n---\n{body}\n", encoding="utf-8")
    return path


@pytest.fixture
def tiers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Instance:
    """An instance with a skill in each tier, some shadowing others.

    ``planning`` is in the instance and the package, ``review`` in the package and the workspace.
    """
    path = tmp_path / "instance"
    path.mkdir()
    (path / "kinby.toml").write_text(
        'id = "test"\n[models]\nmain = "openai:gpt-5"\nrecap = "custom:unpriced"\n'
        "[tools]\ndefaults = false\n[workspace.conventions]\nenabled = true\n",
        encoding="utf-8",
    )
    _skill(path / "skills", "planning", "Plan my way.")
    _skill(path / "skills", "notes", "Take notes.")
    package = tmp_path / "package"
    _skill(package, "planning", "Plan the package way.")
    _skill(package, "review", "Review the package way.")
    (package / "review" / "checklist.md").write_text("- tests\n", encoding="utf-8")
    workspace = path / "workspace" / ".agents" / "skills"
    _skill(workspace, "review", "Review the workspace way.")
    _skill(workspace, "drafting", "Draft.")
    factory = SimpleNamespace(
        name="factory",
        value="kinby_factory:SKILLS",
        dist=SimpleNamespace(name="kinby-factory", version="0.3.0"),
        load=lambda: package,
    )
    monkeypatch.setattr(
        import_module("kinby.plugins.skills"), "entry_points", lambda *, group: (factory,)
    )
    return load_instance(path)


def _dispatcher(instance: Instance):
    return runtime(instance, FakeClock(datetime(2026, 9, 28, tzinfo=UTC)))


def test_skill_list_shows_every_tier_with_the_shadowed_under_the_winner(tiers: Instance) -> None:
    async def scenario() -> None:
        listed = await call(_dispatcher(tiers), "skill.list")

        assert [
            (skill.name, skill.tier, skill.description, skill.source, skill.shadowed_by)
            for skill in listed.skills
        ] == [
            ("drafting", "workspace", "Draft.", "workspace", None),
            ("notes", "instance", "Take notes.", "instance", None),
            ("planning", "instance", "Plan my way.", "instance", None),
            ("planning", "package", "Plan the package way.", "kinby-factory 0.3.0", "instance"),
            ("review", "package", "Review the package way.", "kinby-factory 0.3.0", None),
            ("review", "workspace", "Review the workspace way.", "workspace", "package"),
        ]
        assert listed.warnings == ()

    asyncio.run(scenario())


def _winners(listed) -> dict[str, str]:
    return {skill.name: skill.tier for skill in listed.skills if skill.shadowed_by is None}


def test_skill_read_returns_any_tier_with_its_files_and_hash(tiers: Instance) -> None:
    async def scenario() -> None:
        dispatcher = _dispatcher(tiers)

        package = await call(dispatcher, "skill.read", name="review", tier="package")
        workspace = await call(dispatcher, "skill.read", name="review", tier="workspace")
        missing = await call(dispatcher, "skill.read", name="notes", tier="package")

        assert package.content == (
            "---\nname: review\ndescription: Review the package way.\n---\n"
            "Review the package way.\n"
        )
        assert package.files == ["checklist.md"]
        assert workspace.content.endswith("Review the workspace way.\n")
        assert workspace.files == []
        assert package.hash != workspace.hash
        assert isinstance(missing, ErrorEnvelope)
        assert missing.code is ErrorCode.NOT_FOUND

    asyncio.run(scenario())


def test_customize_copies_the_winning_skill_into_the_instance_to_edit(tiers: Instance) -> None:
    async def scenario() -> None:
        dispatcher = _dispatcher(tiers)

        copied = await call(dispatcher, "skill.customize", name="review")
        edited = SKILL.replace("Plan", "Review").replace("planning", "review")
        written = await call(
            dispatcher, "skill.write", name="review", content=edited, hash=copied.hash
        )

        copy = tiers.path / "skills" / "review"
        assert copied.content.endswith("Review the package way.\n")
        assert copied.files == ["checklist.md"]
        assert (copy / "checklist.md").read_text(encoding="utf-8") == "- tests\n"
        assert (copy / "SKILL.md").read_text(encoding="utf-8") == edited
        assert (written.content, written.files) == (edited, ["checklist.md"])
        assert written.hash != copied.hash
        listed = await call(dispatcher, "skill.list")
        assert [(s.tier, s.shadowed_by) for s in listed.skills if s.name == "review"] == [
            ("instance", None),
            ("package", "instance"),
            ("workspace", "instance"),
        ]
        history = await call(dispatcher, "config.history", file="skills/review", limit=10)
        assert [change.actor for change in history.changes] == ["app", "app"]
        assert "+Write a plan." in history.changes[0].diff.splitlines()
        assert "+- tests" in history.changes[1].diff.splitlines()

    asyncio.run(scenario())


def test_customize_is_refused_when_the_instance_has_a_copy(tiers: Instance) -> None:
    async def scenario() -> None:
        dispatcher = _dispatcher(tiers)

        copied = await call(dispatcher, "skill.customize", name="planning")
        unknown = await call(dispatcher, "skill.customize", name="unknown")

        assert isinstance(copied, ErrorEnvelope)
        assert copied.code is ErrorCode.STALE
        assert (
            (tiers.path / "skills" / "planning" / "SKILL.md")
            .read_text(encoding="utf-8")
            .endswith("Plan my way.\n")
        )
        assert isinstance(unknown, ErrorEnvelope)
        assert unknown.code is ErrorCode.NOT_FOUND

    asyncio.run(scenario())


def test_delete_removes_the_instance_copy_and_brings_the_original_back(tiers: Instance) -> None:
    async def scenario() -> None:
        dispatcher = _dispatcher(tiers)
        read = await call(dispatcher, "skill.read", name="planning", tier="instance")

        stale = await call(dispatcher, "skill.delete", name="planning", hash=EMPTY_HASH)
        deleted = await call(dispatcher, "skill.delete", name="planning", hash=read.hash)

        assert isinstance(stale, ErrorEnvelope)
        assert stale.code is ErrorCode.STALE
        assert not (tiers.path / "skills" / "planning").exists()
        assert [(s.tier, s.shadowed_by) for s in deleted.skills if s.name == "planning"] == [
            ("package", None)
        ]
        history = await call(dispatcher, "config.history", file="skills/planning", limit=10)
        [change] = history.changes
        assert change.actor == "app"
        assert "-Plan my way." in change.diff.splitlines()

    asyncio.run(scenario())


def test_a_write_or_delete_of_a_package_skill_is_refused(tiers: Instance) -> None:
    async def scenario() -> None:
        dispatcher = _dispatcher(tiers)
        read = await call(dispatcher, "skill.read", name="review", tier="package")
        package_file = tiers.path.parent / "package" / "review" / "SKILL.md"
        before = package_file.read_text(encoding="utf-8")

        written = await call(
            dispatcher, "skill.write", name="review", content="Mine.", hash=read.hash
        )
        deleted = await call(dispatcher, "skill.delete", name="review", hash=read.hash)

        for refused in (written, deleted):
            assert isinstance(refused, ErrorEnvelope)
            assert refused.code is ErrorCode.INVALID_ARGUMENT
            assert refused.fields == {
                "name": '"review" is a package skill, which is read-only. '
                "Customize it to edit a copy in the instance."
            }
        assert package_file.read_text(encoding="utf-8") == before
        assert not (tiers.path / "skills" / "review").exists()
        history = await call(dispatcher, "config.history", limit=10)
        assert history.changes == []

    asyncio.run(scenario())


def test_skill_write_with_no_hash_creates_an_instance_skill(tiers: Instance) -> None:
    async def scenario() -> None:
        dispatcher = _dispatcher(tiers)
        content = SKILL.replace("planning", "outlining")

        created = await call(
            dispatcher, "skill.write", name="outlining", content=content, hash=None
        )
        again = await call(dispatcher, "skill.write", name="outlining", content=content, hash=None)
        invalid = await call(
            dispatcher, "skill.write", name="sketching", content="No frontmatter.", hash=None
        )
        unsafe = await call(dispatcher, "skill.write", name="../escape", content=SKILL, hash=None)

        assert (created.content, created.files) == (content, [])
        assert _winners(await call(dispatcher, "skill.list"))["outlining"] == "instance"
        assert isinstance(again, ErrorEnvelope)
        assert again.code is ErrorCode.STALE
        assert isinstance(invalid, ErrorEnvelope)
        assert (invalid.code, invalid.fields) == (
            ErrorCode.INVALID_ARGUMENT,
            {"content": "Skill frontmatter is missing."},
        )
        assert not (tiers.path / "skills" / "sketching").exists()
        assert isinstance(unsafe, ErrorEnvelope)
        assert unsafe.code is ErrorCode.INVALID_ARGUMENT
        assert list(unsafe.fields) == ["name"]
        assert not (tiers.path / "escape").exists()

    asyncio.run(scenario())


def test_skill_write_with_a_stale_hash_leaves_the_skill_alone(tiers: Instance) -> None:
    async def scenario() -> None:
        dispatcher = _dispatcher(tiers)
        read = await call(dispatcher, "skill.read", name="notes", tier="instance")
        (tiers.path / "skills" / "notes" / "extra.md").write_text("Agent's.", encoding="utf-8")

        refused = await call(dispatcher, "skill.write", name="notes", content=SKILL, hash=read.hash)

        assert isinstance(refused, ErrorEnvelope)
        assert refused.code is ErrorCode.STALE
        assert (
            (tiers.path / "skills" / "notes" / "SKILL.md")
            .read_text(encoding="utf-8")
            .endswith("Take notes.\n")
        )

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        ("skill_write", {"name": "planning", "content": SKILL}),
        ("skill_delete", {"name": "notes"}),
    ],
)
def test_the_agent_skill_tools_wait_for_the_skill_lock(
    tiers: Instance, tool: str, arguments: dict[str, JsonValue]
) -> None:
    async def scenario() -> None:
        selected = next(each for each in instance_tools(tiers) if each.name == tool)
        before = _directory_files_text(tiers.path / "skills")

        async with tiers.skill_lock:
            running = asyncio.create_task(
                selected.ainvoke(arguments, ToolContext(instance=tiers, thread_id=uuid4()))
            )
            await asyncio.sleep(0.05)
            assert not running.done()
            assert _directory_files_text(tiers.path / "skills") == before
        await running

        assert _directory_files_text(tiers.path / "skills") != before

    asyncio.run(scenario())


def _directory_files_text(directory: Path) -> dict[str, str]:
    return {
        path.relative_to(directory).as_posix(): path.read_text(encoding="utf-8")
        for path in directory.rglob("*")
        if path.is_file()
    }


@tool(write=True)
def deploy(target: str) -> str:
    """Deploy to a target."""
    return target


def test_tool_list_shows_each_tool_with_its_source_and_rule(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        instance = instance_at(tmp_path)
        weather = tmp_path / "tools" / "weather.py"
        weather.parent.mkdir()
        weather.write_text(
            "from kinby.plugins import tool\n\n\n"
            "@tool(write=False)\n"
            "def weather(city: str) -> str:\n"
            '    """Report the weather."""\n'
            '    return "sunny"\n',
            encoding="utf-8",
        )
        (tmp_path / "permissions.toml").write_text(
            '[tools]\nweather = "allow"\nskill_write = "ask"\ndeploy = "deny"\n',
            encoding="utf-8",
        )
        factory = SimpleNamespace(
            name="factory",
            value="kinby_factory:TOOLS",
            dist=SimpleNamespace(name="kinby-factory", version="0.3.0"),
            load=lambda: (deploy,),
        )
        monkeypatch.setattr(
            import_module("kinby.plugins.registry"), "entry_points", lambda *, group: (factory,)
        )

        listed = await call(_dispatcher(instance), "tool.list")

        tools = {each.name: (each.source, each.write, each.rule) for each in listed.tools}
        assert [each.name for each in listed.tools] == sorted(tools)
        assert tools["weather"] == ("tools/weather.py", False, "allow")
        assert tools["deploy"] == ("kinby-factory 0.3.0", True, "deny")
        assert tools["skill_write"] == ("core", True, "ask")
        assert tools["skill"] == ("core", False, "mode")
        assert tools["routine_delete"] == ("core", True, "mode")
        assert listed.warnings == ()

    asyncio.run(scenario())
