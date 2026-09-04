import asyncio
import json
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from langchain_core.messages import AIMessageChunk, BaseMessage, ToolMessage
from langchain_core.tools import StructuredTool

from kinby.contracts import (
    ApprovalRequested,
    Event,
    MessageDelta,
    Payload,
    PermissionMode,
    ToolResult,
)
from kinby.core import LangGraphRunner
from kinby.core.turns import ApprovalDecision, ParkedTurn, TurnContext, TurnOutcome, TurnRequest
from kinby.instance import init_instance, load_instance
from kinby.memory import Episode, Fact, GraphStore, NodeId, memory_tools
from kinby.plugins import ToolContext

_MODEL = "openai:gpt-5"
_NODE = "2026-08-30-fixed-deployment"
_TRACE = "Found the stale image tag, rebuilt the image, then restarted the container."
_TURN_ID = UUID("22222222-2222-2222-2222-222222222222")


class ScriptedModel:
    def __init__(self, responses: Sequence[AIMessageChunk]) -> None:
        self._responses = iter(responses)
        self.bound_tools: list[tuple[StructuredTool, ...]] = []
        self.messages: list[tuple[BaseMessage, ...]] = []

    def bind_tools(self, tools: Sequence[StructuredTool]) -> ScriptedModel:
        self.bound_tools.append(tuple(tools))
        return self

    async def astream(self, messages: Sequence[BaseMessage]) -> AsyncIterator[AIMessageChunk]:
        self.messages.append(tuple(messages))
        yield next(self._responses)


def _instance_with_episode(tmp_path: Path):
    instance_path = tmp_path / "instance"
    init_instance(instance_path, model=_MODEL)
    graph_path = instance_path / "memory" / "graph"
    (graph_path / f"{_NODE}.md").write_text(
        (
            "---\n"
            "date: 2026-08-30\n"
            "thread: 11111111-1111-1111-1111-111111111111\n"
            f"turn: {_TURN_ID}\n"
            "description: Fixed the deployment\n"
            "subjects: [kinby, deployment]\n"
            "tools: [grep, bash, edit]\n"
            "---\n"
            f"{_TRACE}\n"
        ),
        encoding="utf-8",
    )
    return load_instance(instance_path)


def _empty_instance(tmp_path: Path):
    instance_path = tmp_path / "instance"
    init_instance(instance_path, model=_MODEL)
    return load_instance(instance_path)


def test_memory_open_returns_the_turn_of_a_round_tripped_episode(tmp_path: Path) -> None:
    async def scenario() -> None:
        instance = _empty_instance(tmp_path)
        memory = GraphStore(instance.path)
        episode = Episode(
            node=NodeId("2026-09-01-checked-the-weather"),
            date=date(2026, 9, 1),
            thread=UUID("11111111-1111-1111-1111-111111111111"),
            turn=_TURN_ID,
            description="Checked the weather",
            subjects=(),
            tools=("weather",),
            body='## Path taken\n1. weather {"city": "Quito"}',
        )

        memory.remember(episode)
        opened = memory.open(episode.node)
        open_tool = next(tool for tool in memory_tools(memory) if tool.name == "memory_open")
        result = await open_tool.ainvoke(
            {"node": episode.node},
            ToolContext(instance=instance, thread_id=episode.thread),
        )

        assert opened == episode
        assert json.loads(result) == {
            "node": episode.node,
            "body": episode.body,
            "turn": str(_TURN_ID),
            "tools": ["weather"],
        }

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", list(PermissionMode))
def test_model_walks_search_then_open_without_approval(
    tmp_path: Path,
    mode: PermissionMode,
) -> None:
    async def scenario() -> None:
        instance = _instance_with_episode(tmp_path)
        model = ScriptedModel(
            [
                AIMessageChunk(
                    content="",
                    tool_calls=[
                        {
                            "name": "memory_search",
                            "args": {"query": "deployment"},
                            "id": "search-1",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessageChunk(
                    content="",
                    tool_calls=[
                        {
                            "name": "memory_open",
                            "args": {"node": _NODE},
                            "id": "open-1",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessageChunk(content="The deployment was fixed on August 30."),
            ]
        )
        runner = LangGraphRunner(instance, model_factory=lambda _: model)
        preparation = runner.prepare_for_turn()
        thread_id = uuid4()
        turn_id = uuid4()
        payloads: list[Payload] = []

        async def emit(payload: Payload) -> Event:
            payloads.append(payload)
            return Event(
                sequence=len(payloads),
                thread_id=thread_id,
                turn_id=turn_id,
                payload=payload,
                timestamp=datetime.now(UTC),
            )

        result = await runner.run(
            TurnRequest(
                thread_id=thread_id,
                turn_id=turn_id,
                message="What happened with the deployment?",
                model=preparation.model,
                permission_mode=mode,
            ),
            TurnContext(preparation.budgets, emit),
        )

        assert isinstance(result, TurnOutcome)
        assert not any(isinstance(payload, ApprovalRequested) for payload in payloads)
        assert {tool.name for tool in model.bound_tools[0]} >= {
            "memory_search",
            "memory_open",
        }
        results = [payload for payload in payloads if isinstance(payload, ToolResult)]
        assert [tool_result.name for tool_result in results] == [
            "memory_search",
            "memory_open",
        ]
        assert all(not tool_result.error for tool_result in results)
        assert _NODE in results[0].output
        assert _TRACE in results[1].output
        assert any(
            isinstance(message, ToolMessage) and message.content == results[0].output
            for message in model.messages[1]
        )
        assert any(
            isinstance(message, ToolMessage) and message.content == results[1].output
            for message in model.messages[2]
        )
        assert MessageDelta(text="The deployment was fixed on August 30.") in payloads

    asyncio.run(scenario())


def test_approved_remember_is_recalled_in_a_later_thread(tmp_path: Path) -> None:
    async def scenario() -> None:
        instance = _empty_instance(tmp_path)
        events_path = instance.manifest.state_dir / "events.jsonl"
        events_path.write_bytes(b"canonical transcript\n")
        model = ScriptedModel(
            [
                AIMessageChunk(
                    content="",
                    tool_calls=[
                        {
                            "name": "remember",
                            "args": {
                                "description": "Jorge prefers small modules",
                                "subjects": ["Jorge", "coding preferences"],
                                "body": "Jorge prefers small modules with one reason to change.",
                            },
                            "id": "remember-1",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessageChunk(content="I saved that preference."),
                AIMessageChunk(
                    content="",
                    tool_calls=[
                        {
                            "name": "memory_search",
                            "args": {"query": "coding preferences"},
                            "id": "search-1",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessageChunk(content="You prefer small modules."),
            ]
        )
        runner = LangGraphRunner(instance, model_factory=lambda _: model)
        preparation = runner.prepare_for_turn()
        thread_id = uuid4()
        turn_id = uuid4()
        payloads: list[Payload] = []

        async def emit(payload: Payload) -> Event:
            payloads.append(payload)
            return Event(
                sequence=len(payloads),
                thread_id=thread_id,
                turn_id=turn_id,
                payload=payload,
                timestamp=datetime.now(UTC),
            )

        turn = TurnRequest(
            thread_id=thread_id,
            turn_id=turn_id,
            message="Remember that I prefer small modules.",
            model=preparation.model,
            permission_mode=PermissionMode.ASK,
        )
        context = TurnContext(preparation.budgets, emit)
        parked = await runner.run(turn, context)

        assert isinstance(parked, ParkedTurn)
        assert any(isinstance(payload, ApprovalRequested) for payload in payloads)
        assert GraphStore(instance.path).recall("coding preferences") == ()

        completed = await runner.resume(turn, ApprovalDecision.APPROVE, context)

        assert isinstance(completed, TurnOutcome)
        hits = GraphStore(instance.path).recall("coding preferences")
        assert len(hits) == 1
        remembered = GraphStore(instance.path).open(hits[0].node)
        assert isinstance(remembered, Fact)
        assert remembered.date == date.today()
        assert remembered.thread == thread_id
        assert remembered.description == "Jorge prefers small modules"
        assert remembered.subjects == ("Jorge", "coding preferences")
        assert remembered.body == "Jorge prefers small modules with one reason to change."
        result = next(payload for payload in payloads if isinstance(payload, ToolResult))
        assert result.name == "remember"
        assert not result.error
        assert MessageDelta(text="I saved that preference.") in payloads

        later_thread_id = uuid4()
        later_turn_id = uuid4()
        later_payloads: list[Payload] = []

        async def emit_later(payload: Payload) -> Event:
            later_payloads.append(payload)
            return Event(
                sequence=len(later_payloads),
                thread_id=later_thread_id,
                turn_id=later_turn_id,
                payload=payload,
                timestamp=datetime.now(UTC),
            )

        later = await runner.run(
            TurnRequest(
                thread_id=later_thread_id,
                turn_id=later_turn_id,
                message="What are my coding preferences?",
                model=preparation.model,
                permission_mode=PermissionMode.READ_ONLY,
            ),
            TurnContext(preparation.budgets, emit_later),
        )

        assert isinstance(later, TurnOutcome)
        search = next(payload for payload in later_payloads if isinstance(payload, ToolResult))
        assert search.name == "memory_search"
        assert remembered.node in search.output
        assert MessageDelta(text="You prefer small modules.") in later_payloads
        assert events_path.read_bytes() == b"canonical transcript\n"

    asyncio.run(scenario())


def test_same_day_facts_are_recalled_in_creation_order(tmp_path: Path) -> None:
    async def scenario() -> None:
        instance = _empty_instance(tmp_path)
        memory = GraphStore(instance.path)
        remember = next(tool for tool in memory_tools(memory) if tool.name == "remember")
        context = ToolContext(instance=instance, thread_id=uuid4())

        await remember.ainvoke(
            {
                "description": "Picked SQLite for memory",
                "subjects": ["memory backend"],
                "body": "The memory backend uses SQLite.",
            },
            context,
        )
        await remember.ainvoke(
            {
                "description": "Picked markdown for memory",
                "subjects": ["memory backend"],
                "body": "The memory backend uses markdown.",
            },
            context,
        )

        hits = memory.recall("memory backend")

        assert [hit.description for hit in hits] == [
            "Picked markdown for memory",
            "Picked SQLite for memory",
        ]
        assert memory.open(hits[0].node).body == "The memory backend uses markdown."

    asyncio.run(scenario())


def test_denied_remember_returns_an_error_and_the_turn_continues(tmp_path: Path) -> None:
    async def scenario() -> None:
        instance = _empty_instance(tmp_path)
        events_path = instance.manifest.state_dir / "events.jsonl"
        events_path.write_bytes(b"canonical transcript\n")
        model = ScriptedModel(
            [
                AIMessageChunk(
                    content="",
                    tool_calls=[
                        {
                            "name": "remember",
                            "args": {
                                "description": "Jorge prefers small modules",
                                "subjects": ["Jorge", "coding preferences"],
                                "body": "Jorge prefers small modules with one reason to change.",
                            },
                            "id": "remember-1",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessageChunk(content="I did not save that preference."),
            ]
        )
        runner = LangGraphRunner(instance, model_factory=lambda _: model)
        preparation = runner.prepare_for_turn()
        thread_id = uuid4()
        turn_id = uuid4()
        payloads: list[Payload] = []

        async def emit(payload: Payload) -> Event:
            payloads.append(payload)
            return Event(
                sequence=len(payloads),
                thread_id=thread_id,
                turn_id=turn_id,
                payload=payload,
                timestamp=datetime.now(UTC),
            )

        turn = TurnRequest(
            thread_id=thread_id,
            turn_id=turn_id,
            message="Remember that I prefer small modules.",
            model=preparation.model,
            permission_mode=PermissionMode.ASK,
        )
        context = TurnContext(preparation.budgets, emit)
        parked = await runner.run(turn, context)
        completed = await runner.resume(turn, ApprovalDecision.DENY, context)

        assert isinstance(parked, ParkedTurn)
        assert isinstance(completed, TurnOutcome)
        assert GraphStore(instance.path).recall("coding preferences") == ()
        result = next(payload for payload in payloads if isinstance(payload, ToolResult))
        assert result == ToolResult(
            call_id="remember-1",
            name="remember",
            output='Tool "remember" was denied by the user.',
            error=True,
        )
        assert any(
            isinstance(message, ToolMessage)
            and message.content == result.output
            and message.status == "error"
            for message in model.messages[-1]
        )
        assert MessageDelta(text="I did not save that preference.") in payloads
        assert events_path.read_bytes() == b"canonical transcript\n"

    asyncio.run(scenario())


def test_forget_hides_the_node_from_a_later_search_in_the_same_turn(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        instance = _instance_with_episode(tmp_path)
        events_path = instance.manifest.state_dir / "events.jsonl"
        events_path.write_bytes(b"canonical transcript\n")
        model = ScriptedModel(
            [
                AIMessageChunk(
                    content="",
                    tool_calls=[
                        {
                            "name": "forget",
                            "args": {"node": _NODE},
                            "id": "forget-1",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessageChunk(
                    content="",
                    tool_calls=[
                        {
                            "name": "memory_search",
                            "args": {"query": "deployment"},
                            "id": "search-1",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessageChunk(content="That memory is gone."),
            ]
        )
        runner = LangGraphRunner(instance, model_factory=lambda _: model)
        preparation = runner.prepare_for_turn()
        thread_id = uuid4()
        turn_id = uuid4()
        payloads: list[Payload] = []

        async def emit(payload: Payload) -> Event:
            payloads.append(payload)
            return Event(
                sequence=len(payloads),
                thread_id=thread_id,
                turn_id=turn_id,
                payload=payload,
                timestamp=datetime.now(UTC),
            )

        completed = await runner.run(
            TurnRequest(
                thread_id=thread_id,
                turn_id=turn_id,
                message="Forget the deployment memory and check that it is gone.",
                model=preparation.model,
                permission_mode=PermissionMode.FULL_ACCESS,
            ),
            TurnContext(preparation.budgets, emit),
        )

        assert isinstance(completed, TurnOutcome)
        results = [payload for payload in payloads if isinstance(payload, ToolResult)]
        assert [result.name for result in results] == ["forget", "memory_search"]
        assert all(not result.error for result in results)
        assert results[1].output == "[]"
        node_path = instance.path / "memory" / "graph" / f"{_NODE}.md"
        assert node_path.is_file()
        assert "tombstone: true\n" in node_path.read_text(encoding="utf-8")
        assert MessageDelta(text="That memory is gone.") in payloads
        assert events_path.read_bytes() == b"canonical transcript\n"

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        (
            "remember",
            {
                "description": "Jorge prefers small modules",
                "subjects": ["Jorge", "coding preferences"],
                "body": "Jorge prefers small modules with one reason to change.",
            },
        ),
        ("forget", {"node": _NODE}),
    ],
)
def test_read_only_denies_memory_write_tools(
    tmp_path: Path,
    name: str,
    arguments: dict[str, object],
) -> None:
    async def scenario() -> None:
        instance = _instance_with_episode(tmp_path)
        events_path = instance.manifest.state_dir / "events.jsonl"
        events_path.write_bytes(b"canonical transcript\n")
        model = ScriptedModel(
            [
                AIMessageChunk(
                    content="",
                    tool_calls=[
                        {
                            "name": name,
                            "args": arguments,
                            "id": f"{name}-1",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessageChunk(content="The memory write was denied."),
            ]
        )
        runner = LangGraphRunner(instance, model_factory=lambda _: model)
        preparation = runner.prepare_for_turn()
        thread_id = uuid4()
        turn_id = uuid4()
        payloads: list[Payload] = []

        async def emit(payload: Payload) -> Event:
            payloads.append(payload)
            return Event(
                sequence=len(payloads),
                thread_id=thread_id,
                turn_id=turn_id,
                payload=payload,
                timestamp=datetime.now(UTC),
            )

        completed = await runner.run(
            TurnRequest(
                thread_id=thread_id,
                turn_id=turn_id,
                message="Change memory.",
                model=preparation.model,
                permission_mode=PermissionMode.READ_ONLY,
            ),
            TurnContext(preparation.budgets, emit),
        )

        assert isinstance(completed, TurnOutcome)
        assert not any(isinstance(payload, ApprovalRequested) for payload in payloads)
        assert next(
            payload for payload in payloads if isinstance(payload, ToolResult)
        ) == ToolResult(
            call_id=f"{name}-1",
            name=name,
            output=f'Tool "{name}" was denied by policy rule "mode.read-only.write".',
            error=True,
        )
        assert GraphStore(instance.path).recall("deployment")
        assert GraphStore(instance.path).recall("coding preferences") == ()
        assert MessageDelta(text="The memory write was denied.") in payloads
        assert events_path.read_bytes() == b"canonical transcript\n"

    asyncio.run(scenario())
