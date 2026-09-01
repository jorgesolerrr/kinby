import asyncio
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

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
from kinby.core.turns import TurnOutcome, TurnRequest
from kinby.instance import init_instance, load_instance

_MODEL = "openai:gpt-5"
_NODE = "2026-08-30-fixed-deployment"
_TRACE = "Found the stale image tag, rebuilt the image, then restarted the container."


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
            "description: Fixed the deployment\n"
            "subjects: [kinby, deployment]\n"
            "tools: [grep, bash, edit]\n"
            "---\n"
            f"{_TRACE}\n"
        ),
        encoding="utf-8",
    )
    return load_instance(instance_path)


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
            emit,
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
