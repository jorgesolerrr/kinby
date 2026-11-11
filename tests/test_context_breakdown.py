"""The breakdown of a thread's context size: each category measured in characters as the model is
sent it, and scaled to add up to the provider's count for the thread's latest model call
(ADR 0124)."""

import asyncio
from pathlib import Path
from uuid import UUID

import pytest

from kinby.contracts import ContextCategory, ThreadContextResult
from kinby.core.dispatcher import ScheduledDispatcher
from kinby.instance import Instance
from kinby.instance.layout import MEMORY_DIR, PROFILE_NAME
from tests.test_authored_tools import expose, offline_uv  # noqa: F401 - an autouse fixture
from tests.test_compaction import (
    LONG,
    LONG_RESULTS,
    WINDOW,
    CompactingModel,
    Sized,
    compact,
    new_thread,
    turn,
    turns,
    with_manifest,
    with_skill,
)
from tests.test_mcp_servers import echo, pick, uvx  # noqa: F401 - an autouse fixture
from tests.test_routines import signal_runtime
from tests.test_saved_outputs import instance_at
from tests.test_scheduler import call

#: A compaction summary long enough that its row rounds to a precise share.
SUMMARY = "s" * 20_000


async def breakdown(dispatcher: ScheduledDispatcher, thread_id: UUID) -> ThreadContextResult:
    result = await call(dispatcher, "thread.context.get", thread_id=thread_id)
    assert isinstance(result, ThreadContextResult)
    return result


def tokens(result: ThreadContextResult) -> dict[ContextCategory, int]:
    return {row.category: row.tokens for row in result.rows}


def with_profile(instance: Instance) -> Instance:
    profile = instance.path / MEMORY_DIR / PROFILE_NAME
    profile.parent.mkdir(parents=True, exist_ok=True)
    profile.write_text("The user ships on Fridays.", encoding="utf-8")
    return instance


def test_the_rows_add_up_to_the_latest_model_calls_context_size(tmp_path: Path) -> None:
    instance = with_profile(with_skill(instance_at(tmp_path)))
    model = CompactingModel(
        [("skill", {"name": "deploying"}), ("report", {"size": 5_000})], Sized("Done.", 12_345)
    )
    dispatcher, log = signal_runtime(instance, model)

    async def scenario() -> tuple[ThreadContextResult, int, int, int]:
        thread_id = await new_thread(dispatcher)
        await turn(dispatcher, thread_id, "Deploy it.")
        requests, events = len(model.requests), len(log.thread_events(thread_id))
        result = await breakdown(dispatcher, thread_id)
        return result, requests, events, len(log.thread_events(thread_id))

    result, requests, events_before, events_after = asyncio.run(scenario())

    assert result.context_size == 12_345
    assert sum(row.tokens for row in result.rows) == 12_345
    rows = tokens(result)
    for category in (
        ContextCategory.SYSTEM_PROMPT,
        ContextCategory.PROFILE,
        ContextCategory.SKILLS_CATALOGUE,
        ContextCategory.CORE_TOOLS,
        ContextCategory.TEXT,
    ):
        assert rows[category] > 0, category
    assert rows[ContextCategory.TOOL_RESULTS] > rows[ContextCategory.SKILL_BODIES] > 0
    assert ContextCategory.COMPACTION_SUMMARY not in rows
    assert len(model.requests) == requests
    assert events_after == events_before


def test_the_tool_list_has_a_row_each_for_core_authored_and_mcp_tools(tmp_path: Path) -> None:
    instance = instance_at(tmp_path)
    expose(instance)
    pick(instance, echo=echo("look"))
    dispatcher, _ = signal_runtime(instance, CompactingModel(Sized("Done.", 30_000)))

    async def scenario() -> ThreadContextResult:
        thread_id = await new_thread(dispatcher)
        await turn(dispatcher, thread_id, "Go.")
        return await breakdown(dispatcher, thread_id)

    rows = tokens(asyncio.run(scenario()))

    assert rows[ContextCategory.CORE_TOOLS] > rows[ContextCategory.AUTHORED_TOOLS] > 0
    assert rows[ContextCategory.MCP_TOOLS] > 0


def test_a_compacted_thread_counts_the_summary_and_not_the_messages_before_its_boundary(
    tmp_path: Path,
) -> None:
    model = CompactingModel([], [], SUMMARY, Sized("Done.", 100_000))
    dispatcher, _ = signal_runtime(instance_at(tmp_path), model)

    async def scenario() -> ThreadContextResult:
        thread_id = await new_thread(dispatcher)
        await turn(dispatcher, thread_id, "a" * LONG)
        await turn(dispatcher, thread_id, "b" * LONG)
        await compact(dispatcher, thread_id, "the release date")
        await turn(dispatcher, thread_id, "Again.")
        return await breakdown(dispatcher, thread_id)

    result = asyncio.run(scenario())

    assert sum(row.tokens for row in result.rows) == 100_000
    rows = tokens(result)
    # The kept turn's message alone: the one before the boundary would double the ratio.
    ratio = rows[ContextCategory.TEXT] / rows[ContextCategory.COMPACTION_SUMMARY]
    assert ratio == pytest.approx(LONG / len(SUMMARY), rel=0.05)


def test_the_latest_calls_answer_is_not_part_of_what_it_was_sent(tmp_path: Path) -> None:
    model = CompactingModel(Sized("z" * LONG, 12_345))
    dispatcher, _ = signal_runtime(instance_at(tmp_path), model)

    async def scenario() -> ThreadContextResult:
        thread_id = await new_thread(dispatcher)
        await turn(dispatcher, thread_id, "Go.")
        return await breakdown(dispatcher, thread_id)

    rows = tokens(asyncio.run(scenario()))

    assert rows[ContextCategory.TEXT] < rows[ContextCategory.CORE_TOOLS]


def test_a_compaction_that_cut_into_its_turn_counts_only_the_messages_after_the_cut(
    tmp_path: Path,
) -> None:
    model = CompactingModel(
        Sized("", 10_000, [("report", {"size": 30_000})]),
        LONG_RESULTS,
        SUMMARY,
        Sized("Done.", 40_000),
    )
    dispatcher, _ = signal_runtime(with_manifest(instance_at(tmp_path), WINDOW), model)

    async def scenario() -> ThreadContextResult:
        thread_id = await new_thread(dispatcher)
        await turn(dispatcher, thread_id, "Go.")
        return await breakdown(dispatcher, thread_id)

    rows = tokens(asyncio.run(scenario()))

    # The three results after the cut: the one before it would add a third.
    ratio = rows[ContextCategory.TOOL_RESULTS] / rows[ContextCategory.COMPACTION_SUMMARY]
    assert ratio == pytest.approx(3 * 30_000 / len(SUMMARY), rel=0.05)


def test_a_turn_that_failed_after_its_latest_call_gives_its_size_and_no_rows(
    tmp_path: Path,
) -> None:
    instance = with_manifest(instance_at(tmp_path), "[budgets]\ntokens = 10000\n")
    model = CompactingModel(
        Sized("Done.", 1_000), Sized("", 50_000, [("report", {"size": 30_000})])
    )
    dispatcher, _ = signal_runtime(instance, model)

    async def scenario() -> ThreadContextResult:
        thread_id = await turns(dispatcher, "Go.", "Read the report.")
        return await breakdown(dispatcher, thread_id)

    result = asyncio.run(scenario())

    # The failed turn kept no checkpoint, so nothing holds what its latest call was sent.
    assert (result.context_size, result.rows) == (50_000, [])


def test_a_thread_with_no_model_call_has_no_rows(tmp_path: Path) -> None:
    dispatcher, _ = signal_runtime(instance_at(tmp_path), CompactingModel())

    async def scenario() -> ThreadContextResult:
        return await breakdown(dispatcher, await new_thread(dispatcher))

    result = asyncio.run(scenario())

    assert (result.context_size, result.rows) == (None, [])
