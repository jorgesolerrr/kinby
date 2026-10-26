"""A routine grant: the user's standing allow for one tool inside one routine (ADR 0112)."""

import asyncio
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Self
from uuid import UUID, uuid4

import pytest
from langchain_core.messages import AIMessageChunk, BaseMessage, ToolMessage
from langchain_core.tools import StructuredTool
from pydantic import JsonValue

from kinby.contracts import (
    ApprovalRequested,
    ErrorCode,
    ErrorEnvelope,
    Event,
    GateDecider,
    GateOutcome,
    RoutineGrant,
    RoutineName,
    ToolCall,
    ToolGated,
    is_turn_closing,
)
from kinby.core.dispatcher import Dispatcher, ScheduledDispatcher
from kinby.instance import Instance
from kinby.instance.routine_grants import RoutineGrants
from kinby.plugins import ToolContext
from kinby.plugins.instance_tools import instance_tools
from tests.helpers import thread_events
from tests.test_routines import instance_at, routine_file, signal_runtime
from tests.test_scheduler import FailingRunner, FakeClock, call, runtime

TOOLS = '''from kinby.plugins import tool


@tool(write=True)
def post(text: str) -> str:
    """Post one line."""
    return text


@tool(write=True)
def archive(text: str) -> str:
    """Archive one line."""
    return text
'''


class CallingModel:
    """Calls each of its tools at the first step of a turn, with arguments no earlier call used,
    then finishes."""

    def __init__(self, *tools: str) -> None:
        self._tools = tools
        self._steps = 0

    def bind_tools(self, tools: Sequence[StructuredTool]) -> Self:
        return self

    async def astream(self, messages: Sequence[BaseMessage]) -> AsyncIterator[AIMessageChunk]:
        if isinstance(messages[-1], ToolMessage):
            yield AIMessageChunk(content="Done")
            return
        self._steps += 1
        yield AIMessageChunk(
            content="",
            tool_calls=[
                {
                    "id": f"{name}-{self._steps}",
                    "name": name,
                    "args": {"text": f"line {self._steps}"},
                    "type": "tool_call",
                }
                for name in self._tools
            ],
        )


def news_instance(tmp_path: Path) -> Instance:
    instance = instance_at(tmp_path)
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "post.py").write_text(TOOLS, encoding="utf-8")
    routine_file(instance, "description: News")
    return instance


def news_runtime(tmp_path: Path, *tools: str) -> tuple[Instance, ScheduledDispatcher]:
    instance = news_instance(tmp_path)
    dispatcher, _ = signal_runtime(instance, CallingModel(*(tools or ("post",))))
    return instance, dispatcher


async def next_stop(dispatcher: Dispatcher, thread_id: UUID, after: int = 0) -> Event:
    """The thread's next approval request or closing event after the sequence *after*."""
    items = await thread_events(dispatcher, {"thread_id": thread_id, "after_sequence": after})
    async with asyncio.timeout(5):
        async for item in items:
            assert isinstance(item, Event)
            if isinstance(item.payload, ApprovalRequested) or is_turn_closing(item.payload):
                await items.aclose()
                return item
    raise AssertionError("The thread stopped streaming.")


async def turn_events(dispatcher: Dispatcher, thread_id: UUID) -> list[Event]:
    """The thread's events up to its turn's close."""
    items = await thread_events(dispatcher, {"thread_id": thread_id})
    events: list[Event] = []
    async with asyncio.timeout(5):
        async for item in items:
            assert isinstance(item, Event)
            events.append(item)
            if is_turn_closing(item.payload):
                break
    await items.aclose()
    return events


async def gates(dispatcher: Dispatcher, thread_id: UUID) -> list[ToolGated]:
    events = await turn_events(dispatcher, thread_id)
    return [event.payload for event in events if isinstance(event.payload, ToolGated)]


async def run_routine(dispatcher: Dispatcher, name: str = "news") -> Event:
    """Run the routine *name* and return where its turn stopped: an approval or its close."""
    accepted = await call(dispatcher, "routine.run", name=name)
    return await next_stop(dispatcher, accepted.thread_id)


async def answer(dispatcher: Dispatcher, parked: Event, decision: str) -> object:
    assert isinstance(parked.payload, ApprovalRequested)
    return await call(
        dispatcher,
        "thread.approval.respond",
        thread_id=parked.thread_id,
        approval_id=parked.payload.approval_id,
        decision=decision,
    )


async def allow_in_routine(dispatcher: Dispatcher) -> Event:
    """Run news until it asks for post, always allow post in it, and return the approval."""
    parked = await run_routine(dispatcher)
    assert isinstance(parked.payload, ApprovalRequested)
    assert parked.payload.name == "post"
    await answer(dispatcher, parked, "allow_in_routine")
    closed = await next_stop(dispatcher, parked.thread_id, parked.sequence)
    assert is_turn_closing(closed.payload)
    return parked


def test_a_routine_runs_the_tool_it_was_allowed_with_other_arguments_without_asking(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        _, dispatcher = news_runtime(tmp_path)
        parked = await allow_in_routine(dispatcher)

        second = await run_routine(dispatcher)

        assert is_turn_closing(second.payload)
        [gate] = await gates(dispatcher, second.thread_id)
        assert (gate.name, gate.action, gate.rule, gate.decided_by) == (
            "post",
            GateOutcome.ALLOW,
            "routine.grant",
            GateDecider.POLICY,
        )
        calls = [
            event.payload
            for thread_id in (parked.thread_id, second.thread_id)
            for event in await turn_events(dispatcher, thread_id)
            if isinstance(event.payload, ToolCall)
        ]
        assert [call.arguments for call in calls] == [{"text": "line 1"}, {"text": "line 2"}]

    asyncio.run(scenario())


def test_the_grant_list_names_each_grant_with_when_and_from_which_approval(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        _, dispatcher = news_runtime(tmp_path)
        before = datetime.now(UTC)
        parked = await allow_in_routine(dispatcher)
        assert isinstance(parked.payload, ApprovalRequested)

        listed = await call(dispatcher, "routine.grant.list", name="news")

        [grant] = listed.grants
        assert (grant.routine, grant.tool, grant.approval_id) == (
            "news",
            "post",
            parked.payload.approval_id,
        )
        assert before <= grant.granted_at <= datetime.now(UTC)

    asyncio.run(scenario())


def test_an_approval_answer_allowed_in_the_routine_runs_only_its_own_call(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        _, dispatcher = news_runtime(tmp_path, "post", "archive")
        parked = await run_routine(dispatcher)
        await answer(dispatcher, parked, "allow_in_routine")

        again = await next_stop(dispatcher, parked.thread_id, parked.sequence)

        assert isinstance(again.payload, ApprovalRequested)
        assert again.payload.name == "archive"

    asyncio.run(scenario())


def test_a_users_turn_still_asks_for_a_tool_allowed_in_a_routine_and_cannot_allow_it_there(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        _, dispatcher = news_runtime(tmp_path)
        await allow_in_routine(dispatcher)
        thread = await call(dispatcher, "thread.create")
        await call(dispatcher, "thread.turn.start", thread_id=thread.id, message="Post a line.")

        parked = await next_stop(dispatcher, thread.id)
        refused = await answer(dispatcher, parked, "allow_in_routine")

        assert isinstance(parked.payload, ApprovalRequested)
        assert isinstance(refused, ErrorEnvelope)
        assert refused.code is ErrorCode.INVALID_ARGUMENT

    asyncio.run(scenario())


def test_a_grant_does_not_allow_a_tool_the_ceiling_forbids(tmp_path: Path) -> None:
    async def scenario() -> None:
        instance, dispatcher = news_runtime(tmp_path)
        await allow_in_routine(dispatcher)
        (instance.path / "permissions.toml").write_text(
            'mode = "read-only"\nceiling = "read-only"\n', encoding="utf-8"
        )

        second = await run_routine(dispatcher)

        [gate] = await gates(dispatcher, second.thread_id)
        assert (gate.action, gate.rule) == (GateOutcome.DENY, "mode.read-only.write")

    asyncio.run(scenario())


def test_revoking_a_grant_makes_the_next_run_ask(tmp_path: Path) -> None:
    async def scenario() -> None:
        _, dispatcher = news_runtime(tmp_path)
        await allow_in_routine(dispatcher)

        revoked = await call(dispatcher, "routine.grant.revoke", name="news", tool="post")

        assert revoked.grants == []
        assert (await call(dispatcher, "routine.grant.list", name="news")).grants == []
        second = await run_routine(dispatcher)
        assert isinstance(second.payload, ApprovalRequested)

    asyncio.run(scenario())


def test_rewriting_the_routine_makes_the_next_run_ask(tmp_path: Path) -> None:
    async def scenario() -> None:
        _, dispatcher = news_runtime(tmp_path)
        await allow_in_routine(dispatcher)
        read = await call(dispatcher, "routine.read", name="news")

        await call(
            dispatcher,
            "routine.write",
            name="news",
            content="---\ndescription: News\n---\nPost the news.\n",
            hash=read.hash,
        )

        second = await run_routine(dispatcher)
        assert isinstance(second.payload, ApprovalRequested)

    asyncio.run(scenario())


def granted(instance: Instance, routine: str = "news") -> None:
    instance.manifest.state_dir.mkdir(exist_ok=True)
    RoutineGrants(instance.manifest.state_dir).grant(
        RoutineGrant(
            routine=RoutineName(routine),
            tool="post",
            granted_at=datetime.now(UTC),
            approval_id=uuid4(),
        )
    )


async def write_from_a_client(dispatcher: Dispatcher, instance: Instance) -> None:
    read = await call(dispatcher, "routine.read", name="news")
    await call(
        dispatcher,
        "routine.write",
        name="news",
        content="---\ndescription: Newer news\n---\nRead the news.\n",
        hash=read.hash,
    )


async def disable_from_a_client(dispatcher: Dispatcher, instance: Instance) -> None:
    await call(dispatcher, "routine.set_enabled", name="news", enabled=False)


async def enable_from_a_client(dispatcher: Dispatcher, instance: Instance) -> None:
    await call(dispatcher, "routine.set_enabled", name="news", enabled=True)


async def delete_from_a_client(dispatcher: Dispatcher, instance: Instance) -> None:
    read = await call(dispatcher, "routine.read", name="news")
    await call(dispatcher, "routine.delete", name="news", hash=read.hash)


async def rename_from_a_client(dispatcher: Dispatcher, instance: Instance) -> None:
    read = await call(dispatcher, "routine.read", name="news")
    await call(dispatcher, "routine.rename", name="news", new_name="headlines", hash=read.hash)


async def run_instance_tool(instance: Instance, name: str, **arguments: JsonValue) -> None:
    tool = next(tool for tool in instance_tools(instance) if tool.name == name)
    await tool.ainvoke({"name": "news", **arguments}, ToolContext(instance, thread_id=uuid4()))


async def write_from_a_tool(dispatcher: Dispatcher, instance: Instance) -> None:
    await run_instance_tool(
        instance, "routine_write", content="---\ndescription: Newer news\n---\nRead the news.\n"
    )


async def disable_from_a_tool(dispatcher: Dispatcher, instance: Instance) -> None:
    await run_instance_tool(instance, "routine_set_enabled", enabled=False)


async def delete_from_a_tool(dispatcher: Dispatcher, instance: Instance) -> None:
    await run_instance_tool(instance, "routine_delete")


@pytest.mark.parametrize(
    "edit",
    [
        write_from_a_client,
        disable_from_a_client,
        enable_from_a_client,
        delete_from_a_client,
        rename_from_a_client,
        write_from_a_tool,
        disable_from_a_tool,
        delete_from_a_tool,
    ],
)
def test_any_edit_to_a_routine_clears_its_grants(tmp_path: Path, edit) -> None:
    async def scenario() -> None:
        instance = news_instance(tmp_path)
        dispatcher = runtime(instance, FakeClock(datetime(2026, 10, 26, tzinfo=UTC)))
        granted(instance)

        await edit(dispatcher, instance)

        for name in ("news", "headlines"):
            assert (await call(dispatcher, "routine.grant.list", name=name)).grants == []

    asyncio.run(scenario())


def test_a_rename_leaves_no_grant_left_behind_under_the_new_name(tmp_path: Path) -> None:
    async def scenario() -> None:
        instance = news_instance(tmp_path)
        dispatcher = runtime(instance, FakeClock(datetime(2026, 10, 26, tzinfo=UTC)))
        granted(instance, "headlines")

        await rename_from_a_client(dispatcher, instance)

        assert (await call(dispatcher, "routine.grant.list", name="headlines")).grants == []

    asyncio.run(scenario())


def test_the_failure_policy_disabling_a_routine_clears_its_grants(tmp_path: Path) -> None:
    async def scenario() -> None:
        instance = news_instance(tmp_path)
        dispatcher = runtime(
            instance, FakeClock(datetime(2026, 10, 26, tzinfo=UTC)), FailingRunner()
        )
        granted(instance)
        for _ in range(10):
            await call(dispatcher, "routine.run", name="news")
            await dispatcher.scheduler.drain()

        assert (await call(dispatcher, "routine.grant.list", name="news")).grants == []

    asyncio.run(scenario())


def test_another_routines_grants_survive_an_edit(tmp_path: Path) -> None:
    async def scenario() -> None:
        instance = news_instance(tmp_path)
        dispatcher = runtime(instance, FakeClock(datetime(2026, 10, 26, tzinfo=UTC)))
        granted(instance, "weather")

        await write_from_a_client(dispatcher, instance)

        [grant] = (await call(dispatcher, "routine.grant.list", name="weather")).grants
        assert grant.tool == "post"

    asyncio.run(scenario())
