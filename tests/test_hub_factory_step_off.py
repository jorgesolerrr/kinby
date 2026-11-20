import asyncio
from pathlib import Path

from kinby.contracts import (
    FACTORY_STEP_DELETE,
    FACTORY_STEP_OFF_SET,
    ErrorCode,
    ErrorEnvelope,
    FactoryResult,
    FactoryStepDeleteCommand,
    FactoryStepOffSetCommand,
    FactoryVersionSource,
)
from tests.test_hub import hub_client
from tests.test_hub_factories import FACTORY, FILES, factory_hub, problems, write_factory
from tests.test_hub_factory_step_save import commented_hub
from tests.test_hub_factory_versions import read, sources, versions

REVIEW = """\
  - id: review
    kind: agent
    engine: kinby
    prompt: prompts/review.md
    check: read_verdict
    requires: [branch]
    outcomes: { clean: next, changes: { back: implement, max: 3 } }
"""


def turned_off(factory: str, step: str) -> str:
    return factory.replace(f"  - id: {step}\n", f"  - id: {step}\n    off: true\n")


def plain_hub(tmp_path: Path, files: dict[str, str] = FILES):
    hub = factory_hub(tmp_path)
    write_factory(tmp_path / "hub" / "factories" / "tickets", files)
    return hub_client(hub)


async def set_off(client, step: str, off: bool = True) -> FactoryResult | ErrorEnvelope:
    before = await read(client)
    result = await client.call(
        FACTORY_STEP_OFF_SET,
        FactoryStepOffSetCommand(name="tickets", step=step, off=off, hash=before.hash),
    )
    assert isinstance(result, FactoryResult | ErrorEnvelope)
    return result


async def delete(client, step: str) -> FactoryResult | ErrorEnvelope:
    before = await read(client)
    result = await client.call(
        FACTORY_STEP_DELETE, FactoryStepDeleteCommand(name="tickets", step=step, hash=before.hash)
    )
    assert isinstance(result, FactoryResult | ErrorEnvelope)
    return result


def test_the_check_passes_an_off_step_nothing_on_needs(tmp_path):
    assert problems(tmp_path, FILES | {"factory.yaml": turned_off(FACTORY, "review")}) == []


def test_the_check_treats_an_off_step_as_missing_and_names_each_step_that_needs_it(tmp_path):
    assert problems(tmp_path, FILES | {"factory.yaml": turned_off(FACTORY, "implement")}) == [
        'Step "review" sends work back to "implement", which is off.',
        'Step "review" takes "branch", which only off step "implement" records.',
        'Step "checks" takes "branch", which only off step "implement" records.',
        'Step "open-pr" takes "branch", which only off step "implement" records.',
    ]


def test_the_check_refuses_a_step_that_resumes_an_off_step(tmp_path):
    factory = FACTORY.replace(
        "  - id: review\n",
        "  - { id: draft, kind: agent, engine: claude, prompt: prompts/implement.md, off: true }\n"
        "  - { id: fix, kind: agent, engine: claude, prompt: prompts/implement.md, "
        "resume: draft }\n"
        "  - id: review\n",
    )

    assert problems(tmp_path, FILES | {"factory.yaml": factory}) == [
        'Step "fix" resumes "draft", which is off.'
    ]


def test_the_check_refuses_a_factory_whose_every_step_is_off(tmp_path):
    factory = """\
name: tickets
work_item: { issue: int }
steps:
  - { id: lint, kind: command, run: ["make lint"], off: true }
"""

    assert problems(tmp_path, {"factory.yaml": factory}) == [
        "Every step is off, so a run has no step to take."
    ]


def test_turning_a_step_off_and_on_is_one_version_each_that_keeps_the_rest_of_the_file(
    tmp_path,
):
    client = commented_hub(tmp_path)

    async def scenario() -> None:
        before = await read(client)
        off = await set_off(client, "review")
        on = await set_off(client, "review", off=False)
        listed = await versions(client)

        outcomes = "    outcomes: { clean: next, changes: { back: implement, max: 3 } }\n"
        rewritten = "    outcomes: {clean: next, changes: {back: implement, max: 3}}\n"
        assert isinstance(off, FactoryResult)
        assert off.files["factory.yaml"] == before.files["factory.yaml"].replace(
            outcomes, "    off: true\n" + rewritten
        )
        assert isinstance(on, FactoryResult)
        assert on.files == before.files | {
            "factory.yaml": before.files["factory.yaml"].replace(outcomes, rewritten)
        }
        assert sources(listed[:2]) == 2 * [(FactoryVersionSource.WEB, ["review"])]
        assert len(listed) == 3

    asyncio.run(scenario())


def test_turning_off_a_step_others_need_is_refused_naming_each_of_them(tmp_path):
    client = plain_hub(tmp_path)

    async def scenario() -> None:
        before = await read(client)
        refused = await set_off(client, "implement")

        assert isinstance(refused, ErrorEnvelope)
        assert refused.code is ErrorCode.INVALID_ARGUMENT
        assert refused.fields == {
            "review": 'sends work back to "implement", which is off. '
            'takes "branch", which only off step "implement" records.',
            "checks": 'takes "branch", which only off step "implement" records.',
            "open-pr": 'takes "branch", which only off step "implement" records.',
        }
        assert 'Step "checks" takes "branch"' in refused.message
        assert await read(client) == before
        assert len(await versions(client)) == 1

    asyncio.run(scenario())


def test_a_step_that_is_on_is_not_deleted(tmp_path):
    client = plain_hub(tmp_path)

    async def scenario() -> None:
        before = await read(client)
        refused = await delete(client, "review")

        assert isinstance(refused, ErrorEnvelope)
        assert refused.code is ErrorCode.INVALID_ARGUMENT
        assert await read(client) == before

    asyncio.run(scenario())


def test_deleting_an_off_step_removes_it_and_its_prompt_as_one_version(tmp_path):
    client = plain_hub(tmp_path, FILES | {"factory.yaml": turned_off(FACTORY, "review")})

    async def scenario() -> None:
        deleted = await delete(client, "review")
        listed = await versions(client)

        assert isinstance(deleted, FactoryResult)
        assert deleted.files == {
            name: content for name, content in FILES.items() if name != "prompts/review.md"
        } | {"factory.yaml": FACTORY.replace(REVIEW, "")}
        assert sources(listed[:1]) == [(FactoryVersionSource.WEB, ["review"])]
        assert len(listed) == 2

    asyncio.run(scenario())


def test_deleting_an_off_step_keeps_a_prompt_another_step_uses(tmp_path):
    shared = turned_off(FACTORY, "review").replace("prompts/review.md", "prompts/implement.md")
    client = plain_hub(tmp_path, FILES | {"factory.yaml": shared})

    async def scenario() -> None:
        deleted = await delete(client, "review")

        assert isinstance(deleted, FactoryResult)
        assert deleted.files["prompts/implement.md"] == FILES["prompts/implement.md"]

    asyncio.run(scenario())
