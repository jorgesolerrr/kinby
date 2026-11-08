"""A model call the provider refuses for a prompt too long fails the turn with
CONTEXT_OVERFLOW, and the message names the turn's largest tool results."""

import asyncio
from collections.abc import AsyncIterator, Sequence
from pathlib import Path
from typing import Self

from langchain_core.exceptions import ContextOverflowError
from langchain_core.messages import AIMessageChunk, BaseMessage
from langchain_core.tools import StructuredTool

from kinby.contracts import ErrorCode, Event, TurnFailed
from kinby.instance import Instance, load_instance
from tests.test_routines import signal_runtime
from tests.test_saved_outputs import Call, start, stopped

TOOLS = '''from kinby.plugins import tool


@tool(write=False)
def report(size: int) -> str:
    """A report of *size* characters."""
    return "x" * size


@tool(write=False)
def logs(size: int) -> str:
    """Logs of *size* characters."""
    return "x" * size
'''


class RefusedModel:
    """Makes each call in turn, then fails its next call with *error*."""

    def __init__(self, error: Exception, *calls: Call) -> None:
        self._error = error
        self._calls = list(calls)

    def bind_tools(self, tools: Sequence[StructuredTool]) -> Self:
        return self

    async def astream(self, messages: Sequence[BaseMessage]) -> AsyncIterator[AIMessageChunk]:
        if not self._calls:
            raise self._error
        name, arguments = self._calls.pop(0)
        yield AIMessageChunk(
            content="",
            tool_calls=[
                {
                    "id": f"call-{len(self._calls)}",
                    "name": name,
                    "args": arguments,
                    "type": "tool_call",
                }
            ],
        )


def instance_at(path: Path) -> Instance:
    (path / "workspace").mkdir(parents=True)
    (path / "tools").mkdir()
    (path / "tools" / "report.py").write_text(TOOLS, encoding="utf-8")
    (path / "kinby.toml").write_text(
        'id = "test"\n[models]\nmain = "openai:gpt-5"\nrecap = "custom:unpriced"\n',
        encoding="utf-8",
    )
    return load_instance(path)


def failed_turn(tmp_path: Path, model: RefusedModel) -> TurnFailed:
    dispatcher, _ = signal_runtime(instance_at(tmp_path), model)

    async def scenario() -> Event:
        accepted = await start(dispatcher)
        return await stopped(dispatcher, accepted.thread_id)

    closed = asyncio.run(scenario()).payload
    assert isinstance(closed, TurnFailed)
    return closed


def test_a_prompt_too_long_fails_the_turn_with_context_overflow(tmp_path: Path) -> None:
    overflow = ContextOverflowError("prompt is too long: 212000 tokens > 200000 maximum")
    model = RefusedModel(
        overflow,
        ("logs", {"size": 2_000}),
        ("report", {"size": 40_000}),
        ("logs", {"size": 10}),
        ("report", {"size": 500}),
    )

    failed = failed_turn(tmp_path, model)

    assert failed.code is ErrorCode.CONTEXT_OVERFLOW
    largest = "report (40,000 characters), logs (2,000 characters), report (500 characters)"
    assert largest in failed.message
    assert "(10 characters)" not in failed.message
    assert "Narrow the call" in failed.message


def test_any_other_model_failure_still_fails_the_turn_as_internal(tmp_path: Path) -> None:
    model = RefusedModel(RuntimeError("The model is down."), ("report", {"size": 40_000}))

    failed = failed_turn(tmp_path, model)

    assert failed.code is ErrorCode.INTERNAL
    assert failed.message == "The model turn failed unexpectedly."
