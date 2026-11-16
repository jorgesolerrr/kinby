import asyncio
import logging
from collections.abc import Sequence
from pathlib import Path
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage, BaseMessage

from kinby.contracts import (
    AcceptedResult,
    ErrorCode,
    ErrorEnvelope,
    Event,
    RecapPolicy,
    Scope,
    ThreadCreateResult,
    ThreadListResult,
    ThreadStatus,
    ThreadSummary,
    ThreadTitled,
    TurnCompleted,
)
from kinby.core.dispatcher import Dispatcher, TurnConfig, build_dispatcher
from kinby.core.events import EventLog
from kinby.core.threads import TITLE_LENGTH, ThreadStore
from kinby.core.titles import TitleWriter
from kinby.instance import load_instance
from tests.helpers import fixed_permission_ceiling, fixed_turn_preparation, thread_events
from tests.test_thread_status import ScriptedRunner


def _dispatcher(tmp_path: Path) -> Dispatcher:
    return build_dispatcher(
        tmp_path,
        turns=TurnConfig(fixed_turn_preparation, fixed_permission_ceiling, ScriptedRunner()),
    )


async def _create(dispatcher: Dispatcher, title: str | None = None) -> ThreadCreateResult:
    created = await dispatcher.dispatch("thread.create", {"title": title}, {Scope.THREAD_OPERATE})
    assert isinstance(created, ThreadCreateResult)
    return created


async def _start(
    dispatcher: Dispatcher, created: ThreadCreateResult, message: str
) -> AcceptedResult:
    started = await dispatcher.dispatch(
        "thread.turn.start", {"thread_id": created.id, "message": message}, {Scope.THREAD_OPERATE}
    )
    assert isinstance(started, AcceptedResult)
    return started


async def _listed(dispatcher: Dispatcher) -> list[ThreadSummary]:
    listed = await dispatcher.dispatch("thread.list", {}, {Scope.THREAD_READ})
    assert isinstance(listed, ThreadListResult)
    return listed.threads


@pytest.mark.parametrize(
    ("message", "title"),
    [
        ("What's on my calendar today?", "What's on my calendar today?"),
        (
            "Summarize the quarterly report and draft a reply to the finance team about it",
            "Summarize the quarterly report and draft a reply to the…",
        ),
        ("Fix the build\n\n  then   run\tthe tests\n", "Fix the build then run the tests"),
    ],
)
def test_the_first_message_titles_an_untitled_thread(
    tmp_path: Path, message: str, title: str
) -> None:
    async def scenario() -> None:
        dispatcher = _dispatcher(tmp_path)
        created = await _create(dispatcher)

        await _start(dispatcher, created, message)
        [thread] = await _listed(dispatcher)

        assert thread.title == title

    asyncio.run(scenario())


def test_a_thread_created_with_a_title_keeps_it(tmp_path: Path) -> None:
    async def scenario() -> None:
        dispatcher = _dispatcher(tmp_path)
        created = await _create(dispatcher, "Launch notes")

        await _start(dispatcher, created, "What's on my calendar today?")
        [thread] = await _listed(dispatcher)

        assert thread.title == "Launch notes"

    asyncio.run(scenario())


def test_a_rename_titles_the_thread_and_returns_its_summary(tmp_path: Path) -> None:
    async def scenario() -> None:
        created = await _create(build_dispatcher(tmp_path), "Launch notes")

        renamed = await build_dispatcher(tmp_path).dispatch(
            "thread.rename",
            {"thread_id": created.id, "title": "  Launch plan  "},
            {Scope.THREAD_OPERATE},
        )
        [listed] = await _listed(build_dispatcher(tmp_path))

        assert isinstance(renamed, ThreadSummary)
        assert (renamed.id, renamed.title, renamed.created_at) == (
            created.id,
            "Launch plan",
            created.created_at,
        )
        assert listed == renamed

    asyncio.run(scenario())


@pytest.mark.parametrize("title", ["", "   ", "x" * 201])
def test_a_rename_refuses_an_empty_or_overlong_title(tmp_path: Path, title: str) -> None:
    async def scenario() -> None:
        dispatcher = build_dispatcher(tmp_path)
        created = await _create(dispatcher, "Launch notes")

        refused = await dispatcher.dispatch(
            "thread.rename", {"thread_id": created.id, "title": title}, {Scope.THREAD_OPERATE}
        )
        [listed] = await _listed(dispatcher)

        assert isinstance(refused, ErrorEnvelope)
        assert refused.code is ErrorCode.INVALID_ARGUMENT
        assert listed.title == "Launch notes"

    asyncio.run(scenario())


def test_a_rename_takes_a_title_of_two_hundred_characters(tmp_path: Path) -> None:
    async def scenario() -> None:
        dispatcher = build_dispatcher(tmp_path)
        created = await _create(dispatcher)

        renamed = await dispatcher.dispatch(
            "thread.rename",
            {"thread_id": created.id, "title": f" {'x' * 200} "},
            {Scope.THREAD_OPERATE},
        )

        assert isinstance(renamed, ThreadSummary)
        assert renamed.title == "x" * 200

    asyncio.run(scenario())


def test_a_rename_of_an_unknown_thread_is_not_found(tmp_path: Path) -> None:
    thread_id = uuid4()

    refused = asyncio.run(
        build_dispatcher(tmp_path).dispatch(
            "thread.rename", {"thread_id": thread_id, "title": "Launch"}, {Scope.THREAD_OPERATE}
        )
    )

    assert refused == ErrorEnvelope(
        code=ErrorCode.NOT_FOUND, message=f'Thread "{thread_id}" was not found.', retryable=False
    )


def test_a_rename_needs_the_operate_scope(tmp_path: Path) -> None:
    async def scenario() -> None:
        dispatcher = build_dispatcher(tmp_path)
        created = await _create(dispatcher)

        refused = await dispatcher.dispatch(
            "thread.rename", {"thread_id": created.id, "title": "Launch"}, {Scope.THREAD_READ}
        )

        assert isinstance(refused, ErrorEnvelope)
        assert refused.code is ErrorCode.PERMISSION_DENIED

    asyncio.run(scenario())


class ScriptedTitleModel:
    """The recap model, answering a title request with *answer* once released, or raising it."""

    def __init__(self, answer: str | Exception = "Today's calendar") -> None:
        self.answer = answer
        self.prompts: list[str] = []
        self.asked = asyncio.Event()
        self.release = asyncio.Event()

    async def ainvoke(self, messages: Sequence[BaseMessage]) -> AIMessage:
        [message] = messages
        self.prompts.append(message.text)
        self.asked.set()
        await self.release.wait()
        if isinstance(self.answer, Exception):
            raise self.answer
        return AIMessage(
            content=self.answer,
            usage_metadata={"input_tokens": 40, "output_tokens": 4, "total_tokens": 44},
        )


def _titling(
    tmp_path: Path,
    model: ScriptedTitleModel,
    policy: RecapPolicy = RecapPolicy.EVERY_TURN,
    runner: ScriptedRunner | None = None,
) -> tuple[Dispatcher, TitleWriter, EventLog]:
    """A dispatcher whose new threads *model* titles, as the recap model of an instance that
    recaps by *policy*."""
    instance_path = tmp_path / "instance"
    instance_path.mkdir()
    (instance_path / "kinby.toml").write_text(
        'id = "test"\n\n[models]\nmain = "openai:main"\nrecap = "openai:recap"\n\n'
        f'[memory]\nrecap = "{policy}"\n',
        encoding="utf-8",
    )
    event_log = EventLog(tmp_path)
    writer = TitleWriter(
        ThreadStore(tmp_path),
        event_log,
        load_instance(instance_path),
        model_factory={"openai:recap": model}.__getitem__,
    )
    dispatcher = build_dispatcher(
        tmp_path,
        event_log=event_log,
        turns=TurnConfig(
            fixed_turn_preparation,
            fixed_permission_ceiling,
            runner or ScriptedRunner(),
            title=writer,
        ),
    )
    return dispatcher, writer, event_log


def _titles(event_log: EventLog, created: ThreadCreateResult) -> list[Event]:
    return [
        event
        for event in event_log.thread_events(created.id)
        if isinstance(event.payload, ThreadTitled)
    ]


def test_the_recap_model_titles_a_new_thread_while_its_first_turn_runs(tmp_path: Path) -> None:
    async def scenario() -> None:
        model = ScriptedTitleModel("Today's calendar")
        runner = ScriptedRunner()
        dispatcher, writer, event_log = _titling(tmp_path, model, runner=runner)
        created = await _create(dispatcher)

        started = await _start(dispatcher, created, "hold")
        await asyncio.gather(runner.started.wait(), model.asked.wait())
        [stand_in] = await _listed(dispatcher)
        model.release.set()
        await writer.drain()
        [titled] = await _listed(dispatcher)

        assert stand_in.title == "hold"
        assert (titled.title, titled.status) == ("Today's calendar", ThreadStatus.RUNNING)
        [event] = _titles(event_log, created)
        assert event.turn_id == started.turn_id
        assert event.payload == ThreadTitled(
            title="Today's calendar", model="openai:recap", input_tokens=40, output_tokens=4
        )
        runner.release.set()

    asyncio.run(scenario())


def test_the_recap_model_reads_the_opening_of_a_long_prompt_and_its_title_fits(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        model = ScriptedTitleModel(f"The spring launch plan {'and more ' * 20}")
        model.release.set()
        dispatcher, writer, _ = _titling(tmp_path, model)
        created = await _create(dispatcher)
        message = f"Plan the spring launch. {'Here is the log: line. ' * 500}"

        await _start(dispatcher, created, message)
        await writer.drain()
        [thread] = await _listed(dispatcher)

        [prompt] = model.prompts
        assert "Plan the spring launch." in prompt
        assert len(prompt) < len(message)
        assert thread.title is not None
        assert thread.title.startswith("The spring launch plan")
        assert len(thread.title) <= TITLE_LENGTH

    asyncio.run(scenario())


def test_a_rename_while_the_recap_model_titles_the_thread_wins(tmp_path: Path) -> None:
    async def scenario() -> None:
        model = ScriptedTitleModel("Today's calendar")
        dispatcher, writer, _ = _titling(tmp_path, model)
        created = await _create(dispatcher)

        await _start(dispatcher, created, "What's on my calendar today?")
        await model.asked.wait()
        renamed = await dispatcher.dispatch(
            "thread.rename", {"thread_id": created.id, "title": "My day"}, {Scope.THREAD_OPERATE}
        )
        model.release.set()
        await writer.drain()
        [thread] = await _listed(dispatcher)

        assert isinstance(renamed, ThreadSummary)
        assert thread.title == "My day"

    asyncio.run(scenario())


def test_the_recap_model_leaves_a_thread_created_with_a_title_alone(tmp_path: Path) -> None:
    async def scenario() -> None:
        model = ScriptedTitleModel()
        model.release.set()
        dispatcher, writer, event_log = _titling(tmp_path, model)
        created = await _create(dispatcher, "Launch notes")

        await _start(dispatcher, created, "What's on my calendar today?")
        await writer.drain()
        [thread] = await _listed(dispatcher)

        assert thread.title == "Launch notes"
        assert model.prompts == []
        assert _titles(event_log, created) == []

    asyncio.run(scenario())


def test_a_trace_only_instance_keeps_the_first_message_title(tmp_path: Path) -> None:
    async def scenario() -> None:
        model = ScriptedTitleModel()
        model.release.set()
        dispatcher, writer, event_log = _titling(tmp_path, model, RecapPolicy.TRACE_ONLY)
        created = await _create(dispatcher)

        await _start(dispatcher, created, "What's on my calendar today?")
        await writer.drain()
        [thread] = await _listed(dispatcher)

        assert thread.title == "What's on my calendar today?"
        assert model.prompts == []
        assert _titles(event_log, created) == []

    asyncio.run(scenario())


@pytest.mark.parametrize("answer", [RuntimeError("provider unavailable"), "  \n "])
def test_a_failed_title_keeps_the_first_message_title_and_warns(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, answer: str | Exception
) -> None:
    async def scenario() -> None:
        model = ScriptedTitleModel(answer)
        model.release.set()
        dispatcher, writer, event_log = _titling(tmp_path, model)
        created = await _create(dispatcher)

        started = await _start(dispatcher, created, "What's on my calendar today?")
        await writer.drain()
        events = await thread_events(
            dispatcher, {"thread_id": created.id, "after_sequence": started.sequence}
        )
        closing = await asyncio.wait_for(anext(events), timeout=1)
        await events.aclose()
        [thread] = await _listed(dispatcher)

        assert isinstance(closing, Event)
        assert isinstance(closing.payload, TurnCompleted)
        assert thread.title == "What's on my calendar today?"
        assert _titles(event_log, created) == []
        assert [
            record.levelno for record in caplog.records if record.name == "kinby.core.titles"
        ] == [logging.WARNING]

    with caplog.at_level(logging.WARNING, logger="kinby.core.titles"):
        asyncio.run(scenario())


def test_a_title_written_while_the_first_turn_waits_on_an_approval_leaves_it_waiting(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        model = ScriptedTitleModel("Notes file")
        dispatcher, writer, _ = _titling(tmp_path, model)
        created = await _create(dispatcher)

        await _start(dispatcher, created, "park")
        await model.asked.wait()
        model.release.set()
        await writer.drain()
        [thread] = await _listed(dispatcher)

        assert (thread.title, thread.status) == ("Notes file", ThreadStatus.AWAITING_APPROVAL)

    asyncio.run(scenario())
