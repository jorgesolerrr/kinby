import asyncio
import json
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from threading import Event as ThreadEvent
from threading import get_ident
from typing import Self
from uuid import UUID, uuid4

import pytest
from langchain_core.exceptions import ModelNotFoundError
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from pydantic import ValidationError

from kinby.contracts import (
    AcceptedResult,
    CodingSessionId,
    CompletionOutcome,
    DelegatedRun,
    DelegatedRunOutcome,
    ErrorEnvelope,
    Event,
    EventType,
    FactoryRunStatus,
    MemoryRecapped,
    MessageDelta,
    ModelCompleted,
    Payload,
    PermissionMode,
    RunDelegated,
    RunStop,
    RunStopped,
    Scope,
    StepAttempt,
    StepEnding,
    ThreadCreateResult,
    ToolCall,
    ToolResult,
    TurnCompleted,
    TurnStarted,
    UsageSource,
    Warning,
)
from kinby.core.dispatcher import Dispatcher, TurnConfig, build_dispatcher
from kinby.core.events import EventLog
from kinby.core.turns import PreparedTurnRequest, TurnContext, TurnOutcome
from kinby.instance import load_instance
from kinby.memory import (
    Episode,
    Fact,
    GraphStore,
    MemoryNode,
    NodeId,
    RecapDraft,
    RecappedRun,
    RecappedTurn,
    RecapWriter,
)
from kinby.memory.recap import RecapModel
from tests.helpers import (
    cannot_restore,
    does_not_park,
    fixed_permission_ceiling,
    fixed_turn_preparation,
    thread_events,
)

_EXPECTED_TOOL_RESULT_MAX_CHARS = 800
_EXPECTED_DEFAULT_RECAP_LENS = (
    "Describe the turn's concrete outcome and decisions. "
    "Name one honest way the work could have gone differently."
)


class ScriptedRecapModel:
    def __init__(
        self,
        draft: RecapDraft,
        input_tokens: int,
        output_tokens: int,
        *,
        cache_read_tokens: int = 0,
        cache_creation_tokens: int = 0,
    ) -> None:
        self._draft = draft
        self._input_tokens = input_tokens
        self._output_tokens = output_tokens
        self._cache_read_tokens = cache_read_tokens
        self._cache_creation_tokens = cache_creation_tokens
        self.calls: list[tuple[BaseMessage, ...]] = []
        self.method: str | None = None

    def with_structured_output(
        self,
        schema: type[RecapDraft],
        *,
        method: str,
        include_raw: bool,
    ) -> Self:
        assert schema is RecapDraft
        assert include_raw
        self.method = method
        return self

    async def ainvoke(self, messages: Sequence[BaseMessage]) -> object:
        self.calls.append(tuple(messages))
        return {
            "raw": AIMessage(
                content="",
                usage_metadata={
                    "input_tokens": self._input_tokens,
                    "output_tokens": self._output_tokens,
                    "total_tokens": self._input_tokens + self._output_tokens,
                    "input_token_details": {
                        "cache_read": self._cache_read_tokens,
                        "cache_creation": self._cache_creation_tokens,
                    },
                },
            ),
            "parsed": self._draft,
            "parsing_error": None,
        }


class FailingRecapModel:
    def with_structured_output(
        self,
        schema: type[RecapDraft],
        *,
        method: str,
        include_raw: bool,
    ) -> Self:
        assert schema is RecapDraft
        assert include_raw
        return self

    async def ainvoke(self, messages: Sequence[BaseMessage]) -> object:
        return {
            "raw": AIMessage(
                content="",
                usage_metadata={
                    "input_tokens": 6,
                    "output_tokens": 2,
                    "total_tokens": 8,
                    "input_token_details": {"cache_read": 4, "cache_creation": 1},
                },
            ),
            "parsed": None,
            "parsing_error": RuntimeError("recap response could not be parsed"),
        }


class UnknownRecapModel:
    def with_structured_output(
        self,
        schema: type[RecapDraft],
        *,
        method: str,
        include_raw: bool,
    ) -> Self:
        return self

    async def ainvoke(self, messages: Sequence[BaseMessage]) -> object:
        raise ModelNotFoundError("Error code: 404 - {'type': 'not_found_error'}")


def _instance(tmp_path: Path, *, policy: str, recap_model: str | None = None):
    models = '[models]\nmain = "openai:main"\n'
    if recap_model is not None:
        models += f'recap = "{recap_model}"\n'
    (tmp_path / "kinby.toml").write_text(
        f'id = "test"\n\n{models}\n[memory]\nrecap = "{policy}"\n',
        encoding="utf-8",
    )
    return load_instance(tmp_path)


def _trace_only_writer(
    tmp_path: Path,
    event_log: EventLog,
    memory: GraphStore,
) -> RecapWriter:
    return RecapWriter(
        event_log,
        memory,
        _instance(tmp_path, policy="off"),
    )


def test_kept_draft_writes_narrative_episode_and_token_marker(tmp_path: Path) -> None:
    async def scenario() -> None:
        state_dir = tmp_path / ".state"
        event_log = EventLog(state_dir)
        model = ScriptedRecapModel(
            RecapDraft(
                keep=True,
                description="Compared the trip weather",
                subjects=("Quito", "Cuenca"),
                happened="Compared forecasts for the planned stops.",
                decided="Pack for cool weather.",
                retrospective="Check rain as well as temperature next time.",
            ),
            input_tokens=17,
            output_tokens=9,
            cache_read_tokens=11,
            cache_creation_tokens=2,
        )
        recap = RecapWriter(
            event_log,
            GraphStore(tmp_path),
            _instance(tmp_path, policy="every-turn"),
            model_factory=lambda _: model,
        )
        dispatcher = build_dispatcher(
            state_dir,
            event_log=event_log,
            turns=TurnConfig(
                fixed_turn_preparation,
                fixed_permission_ceiling,
                ToolRunner(),
                recap,
            ),
        )
        created = await dispatcher.dispatch("thread.create", {}, {Scope.THREAD_OPERATE})
        assert isinstance(created, ThreadCreateResult)
        accepted = await dispatcher.dispatch(
            "thread.turn.start",
            {"thread_id": created.id, "message": "Check the trip weather"},
            {Scope.THREAD_OPERATE},
        )
        assert isinstance(accepted, AcceptedResult)

        closing_events = (await event_log.subscribe(created.id, accepted.sequence)).items
        for _ in range(7):
            await anext(closing_events)
        await closing_events.aclose()
        await asyncio.wait_for(recap.drain(), timeout=1)
        events = event_log.stored(created.id)
        marker = events[-1]
        call = events[-2]
        assert call.turn_id == accepted.turn_id
        assert isinstance(call.payload, ModelCompleted)
        assert call.payload == ModelCompleted(
            model="openai:main",
            input_tokens=17,
            output_tokens=9,
            cache_read_tokens=11,
            cache_creation_tokens=2,
            duration_ms=call.payload.duration_ms,
        )
        assert call.payload.duration_ms >= 0
        assert marker.turn_id == accepted.turn_id
        assert isinstance(marker.payload, MemoryRecapped)
        assert marker.payload == MemoryRecapped(
            node=marker.payload.node,
            input_tokens=17,
            output_tokens=9,
            cache_read_tokens=11,
            cache_creation_tokens=2,
            model="openai:main",
        )
        assert marker.payload.node is not None
        episode = GraphStore(tmp_path).open(marker.payload.node)
        assert episode.description == "Compared the trip weather"
        assert episode.subjects == ("Quito", "Cuenca")
        assert episode.body == (
            "## What happened\n"
            "Compared forecasts for the planned stops.\n"
            "## What was decided\n"
            "Pack for cool weather.\n"
            "## What should have gone differently\n"
            "Check rain as well as temperature next time.\n"
            "## Path taken\n"
            '1. weather: {"city": "Quito"}\n'
            '2. weather: {"city": "Cuenca"}\n'
            '3. read: {"path": "notes.md"}'
        )

    asyncio.run(scenario())


def test_recap_model_receives_the_turn_frame_as_a_user_message(tmp_path: Path) -> None:
    class FrameRunner:
        async def run(self, turn: PreparedTurnRequest, context: TurnContext) -> TurnOutcome:
            await context.emit(MessageDelta(text="Compared "))
            await context.emit(MessageDelta(text="both cities."))
            await context.emit(
                ToolCall(
                    call_id="weather-1",
                    name="weather",
                    arguments={"city": "Quito"},
                )
            )
            await context.emit(
                ToolResult(
                    call_id="weather-1",
                    name="weather",
                    output="x" * (_EXPECTED_TOOL_RESULT_MAX_CHARS + 50),
                    error=False,
                )
            )
            return TurnOutcome(input_tokens=4, output_tokens=2)

        resume = does_not_park
        restore = cannot_restore

    async def scenario() -> None:
        state_dir = tmp_path / ".state"
        event_log = EventLog(state_dir)
        model = ScriptedRecapModel(
            RecapDraft(
                keep=False,
                description="Routine weather check",
                subjects=(),
                happened="",
                decided="",
                retrospective="",
            ),
            input_tokens=3,
            output_tokens=1,
        )
        recap = RecapWriter(
            event_log,
            GraphStore(tmp_path),
            _instance(tmp_path, policy="every-turn"),
            model_factory=lambda _: model,
        )
        dispatcher = build_dispatcher(
            state_dir,
            event_log=event_log,
            turns=TurnConfig(
                fixed_turn_preparation,
                fixed_permission_ceiling,
                FrameRunner(),
                recap,
            ),
        )
        created = await dispatcher.dispatch("thread.create", {}, {Scope.THREAD_OPERATE})
        assert isinstance(created, ThreadCreateResult)
        accepted = await dispatcher.dispatch(
            "thread.turn.start",
            {"thread_id": created.id, "message": "Compare Quito and Cuenca"},
            {Scope.THREAD_OPERATE},
        )
        assert isinstance(accepted, AcceptedResult)
        closing_events = (await event_log.subscribe(created.id, accepted.sequence)).items
        for _ in range(5):
            await anext(closing_events)
        await closing_events.aclose()

        await asyncio.wait_for(recap.drain(), timeout=1)

        assert model.method == "json_schema"
        assert len(model.calls) == 1
        (message,) = model.calls[0]
        assert isinstance(message, HumanMessage)
        prompt = message.text
        assert _EXPECTED_DEFAULT_RECAP_LENS in prompt
        assert "# User message\nCompare Quito and Cuenca" in prompt
        assert "# Assistant text\nCompared both cities." in prompt
        assert (
            '# Tool call weather\nArguments: {"city": "Quito"}\nResult: '
            + "x" * _EXPECTED_TOOL_RESULT_MAX_CHARS
            + "..."
        ) in prompt
        assert "x" * (_EXPECTED_TOOL_RESULT_MAX_CHARS + 1) not in prompt
        assert "# Closing event\nCompleted: input_tokens=4 output_tokens=2" in prompt
        assert "# Deterministic trace\n## Path taken" in prompt
        assert "# Output contract" in prompt

    asyncio.run(scenario())


def test_what_the_home_instance_reads_through_the_hub_stays_out_of_its_recap(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        event_log = EventLog(tmp_path / ".state")
        model = ScriptedRecapModel(
            RecapDraft(
                keep=True,
                description="Read alice's memory",
                subjects=("alice",),
                happened="Read alice's memory.",
                decided="Nothing.",
                retrospective="Nothing.",
            ),
            input_tokens=3,
            output_tokens=1,
        )
        recap = RecapWriter(
            event_log,
            GraphStore(tmp_path),
            _instance(tmp_path, policy="every-turn"),
            model_factory=lambda _: model,
        )
        thread_id, turn_id = uuid4(), uuid4()
        for payload in (
            TurnStarted(message="What does alice know about my dentist?", model="openai:main"),
            ToolCall(
                call_id="read-1",
                name="hub_read",
                arguments={"method": "memory.open", "params": {"node": "dentist"}},
            ),
            ToolResult(
                call_id="read-1",
                name="hub_read",
                output='{"body": "The dentist is Dr. Ruiz."}',
                error=False,
            ),
            TurnCompleted(input_tokens=4, output_tokens=2),
        ):
            await event_log.append(thread_id, turn_id, payload)

        recap.schedule(thread_id, turn_id)
        await asyncio.wait_for(recap.drain(), timeout=1)

        (message,) = model.calls[0]
        assert "Dr. Ruiz" not in message.text
        assert '"node": "dentist"' in message.text

    asyncio.run(scenario())


def test_discarded_draft_writes_marker_without_episode(tmp_path: Path) -> None:
    async def scenario() -> None:
        state_dir = tmp_path / ".state"
        event_log = EventLog(state_dir)
        model = ScriptedRecapModel(
            RecapDraft(
                keep=False,
                description="Routine weather check",
                subjects=("weather",),
                happened="Checked the weather.",
                decided="Nothing changed.",
                retrospective="Nothing to change.",
            ),
            input_tokens=8,
            output_tokens=3,
        )
        recap = RecapWriter(
            event_log,
            GraphStore(tmp_path),
            _instance(tmp_path, policy="every-turn"),
            model_factory=lambda _: model,
        )
        dispatcher = build_dispatcher(
            state_dir,
            event_log=event_log,
            turns=TurnConfig(
                fixed_turn_preparation,
                fixed_permission_ceiling,
                ToolRunner(),
                recap,
            ),
        )
        created = await dispatcher.dispatch("thread.create", {}, {Scope.THREAD_OPERATE})
        assert isinstance(created, ThreadCreateResult)
        accepted = await dispatcher.dispatch(
            "thread.turn.start",
            {"thread_id": created.id, "message": "Check the weather"},
            {Scope.THREAD_OPERATE},
        )
        assert isinstance(accepted, AcceptedResult)
        closing_events = (await event_log.subscribe(created.id, accepted.sequence)).items
        for _ in range(7):
            await anext(closing_events)
        await closing_events.aclose()

        await asyncio.wait_for(recap.drain(), timeout=1)

        assert event_log.stored(created.id)[-1].payload == MemoryRecapped(
            node=None,
            input_tokens=8,
            output_tokens=3,
            model="openai:main",
        )
        assert not list((tmp_path / "memory" / "graph").glob("*.md"))

    asyncio.run(scenario())


def test_recap_draft_rejects_an_empty_description_outside_its_json_schema() -> None:
    # OpenAI's strict JSON schema mode refuses string length keywords.
    assert "minLength" not in json.dumps(RecapDraft.model_json_schema())
    with pytest.raises(ValidationError):
        RecapDraft(
            keep=True,
            description="",
            subjects=(),
            happened="",
            decided="",
            retrospective="",
        )


def test_recap_model_error_warns_without_changing_the_closed_turn(tmp_path: Path) -> None:
    async def scenario() -> None:
        state_dir = tmp_path / ".state"
        event_log = EventLog(state_dir)
        recap = RecapWriter(
            event_log,
            GraphStore(tmp_path),
            _instance(tmp_path, policy="every-turn"),
            model_factory=lambda _: FailingRecapModel(),
        )
        dispatcher = build_dispatcher(
            state_dir,
            event_log=event_log,
            turns=TurnConfig(
                fixed_turn_preparation,
                fixed_permission_ceiling,
                ToolRunner(),
                recap,
            ),
        )
        created = await dispatcher.dispatch("thread.create", {}, {Scope.THREAD_OPERATE})
        assert isinstance(created, ThreadCreateResult)
        accepted = await dispatcher.dispatch(
            "thread.turn.start",
            {"thread_id": created.id, "message": "Check the weather"},
            {Scope.THREAD_OPERATE},
        )
        assert isinstance(accepted, AcceptedResult)
        closing_events = (await event_log.subscribe(created.id, accepted.sequence)).items
        for _ in range(7):
            await anext(closing_events)
        await closing_events.aclose()

        await asyncio.wait_for(recap.drain(), timeout=1)

        events = event_log.stored(created.id)
        assert events[-3].payload == TurnCompleted(input_tokens=4, output_tokens=2)
        call = events[-2].payload
        assert isinstance(call, ModelCompleted)
        assert call == ModelCompleted(
            model="openai:main",
            input_tokens=6,
            output_tokens=2,
            cache_read_tokens=4,
            cache_creation_tokens=1,
            duration_ms=call.duration_ms,
        )
        assert events[-1].turn_id == accepted.turn_id
        assert events[-1].payload == Warning(
            sources=("recap",),
            message=("The turn recap failed: RuntimeError: recap response could not be parsed"),
        )
        assert not any(isinstance(event.payload, MemoryRecapped) for event in events)
        assert not list((tmp_path / "memory" / "graph").glob("*.md"))

    asyncio.run(scenario())


def test_unknown_recap_model_warns_that_the_model_does_not_exist(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    async def scenario() -> None:
        state_dir = tmp_path / ".state"
        event_log = EventLog(state_dir)
        recap = RecapWriter(
            event_log,
            GraphStore(tmp_path),
            _instance(
                tmp_path,
                policy="every-turn",
                recap_model="anthropic:claude-no-such-model-9",
            ),
            model_factory=lambda _: UnknownRecapModel(),
        )
        dispatcher = build_dispatcher(
            state_dir,
            event_log=event_log,
            turns=TurnConfig(
                fixed_turn_preparation,
                fixed_permission_ceiling,
                ClosingRunner(),
                recap,
            ),
        )
        created = await dispatcher.dispatch("thread.create", {}, {Scope.THREAD_OPERATE})
        assert isinstance(created, ThreadCreateResult)
        accepted = await dispatcher.dispatch(
            "thread.turn.start",
            {"thread_id": created.id, "message": "First"},
            {Scope.THREAD_OPERATE},
        )
        assert isinstance(accepted, AcceptedResult)
        closing_events = (await event_log.subscribe(created.id, accepted.sequence)).items
        for _ in range(2):
            await anext(closing_events)
        await closing_events.aclose()

        await asyncio.wait_for(recap.drain(), timeout=1)

        events = event_log.stored(created.id)
        warnings = [event.payload for event in events if isinstance(event.payload, Warning)]
        assert warnings == [
            Warning(
                sources=("recap",),
                message="The recap model anthropic:claude-no-such-model-9 doesn't exist.",
            )
        ]
        assert not any(isinstance(event.payload, MemoryRecapped) for event in events)

    asyncio.run(scenario())

    [record] = [record for record in caplog.records if record.name == "kinby.memory.recap"]
    assert record.levelname == "ERROR"
    assert record.exc_info is not None


def test_catch_up_retries_a_failed_recap_and_writes_its_marker(tmp_path: Path) -> None:
    async def scenario() -> None:
        state_dir = tmp_path / ".state"
        event_log = EventLog(state_dir)
        failed_recap = RecapWriter(
            event_log,
            GraphStore(tmp_path),
            _instance(tmp_path, policy="every-turn"),
            model_factory=lambda _: FailingRecapModel(),
        )
        dispatcher = build_dispatcher(
            state_dir,
            event_log=event_log,
            turns=TurnConfig(
                fixed_turn_preparation,
                fixed_permission_ceiling,
                ClosingRunner(),
                failed_recap,
            ),
        )
        created = await dispatcher.dispatch("thread.create", {}, {Scope.THREAD_OPERATE})
        assert isinstance(created, ThreadCreateResult)
        accepted = await dispatcher.dispatch(
            "thread.turn.start",
            {"thread_id": created.id, "message": "First"},
            {Scope.THREAD_OPERATE},
        )
        assert isinstance(accepted, AcceptedResult)
        closing_events = (await event_log.subscribe(created.id, accepted.sequence)).items
        for _ in range(2):
            await anext(closing_events)
        await closing_events.aclose()
        await asyncio.wait_for(failed_recap.drain(), timeout=1)
        assert isinstance(event_log.stored(created.id)[-1].payload, Warning)

        model = ScriptedRecapModel(
            RecapDraft(
                keep=True,
                description="Retried the failed recap",
                subjects=(),
                happened="The turn ran a test command.",
                decided="Keep the result.",
                retrospective="The first recap provider failed.",
            ),
            input_tokens=5,
            output_tokens=3,
        )
        retry = RecapWriter(
            event_log,
            GraphStore(tmp_path),
            _instance(tmp_path, policy="every-turn"),
            model_factory=lambda _: model,
        )

        await retry.catch_up()
        await asyncio.wait_for(retry.drain(), timeout=1)

        markers = [
            event
            for event in event_log.stored(created.id)
            if isinstance(event.payload, MemoryRecapped)
        ]
        assert len(model.calls) == 1
        assert len(markers) == 1
        assert markers[0].turn_id == accepted.turn_id
        marker = markers[0].payload
        assert isinstance(marker, MemoryRecapped)
        assert marker.node is not None

    asyncio.run(scenario())


def test_recap_model_selection_reloads_the_manifest_for_each_turn(tmp_path: Path) -> None:
    async def scenario() -> None:
        state_dir = tmp_path / ".state"
        event_log = EventLog(state_dir)
        model = ScriptedRecapModel(
            RecapDraft(
                keep=False,
                description="Routine turn",
                subjects=(),
                happened="",
                decided="",
                retrospective="",
            ),
            input_tokens=1,
            output_tokens=1,
        )
        selected_models: list[str] = []

        def model_factory(model_name: str) -> ScriptedRecapModel:
            selected_models.append(model_name)
            return model

        recap = RecapWriter(
            event_log,
            GraphStore(tmp_path),
            _instance(tmp_path, policy="every-turn"),
            model_factory=model_factory,
        )
        dispatcher = build_dispatcher(
            state_dir,
            event_log=event_log,
            turns=TurnConfig(
                fixed_turn_preparation,
                fixed_permission_ceiling,
                ClosingRunner(),
                recap,
            ),
        )
        created = await dispatcher.dispatch("thread.create", {}, {Scope.THREAD_OPERATE})
        assert isinstance(created, ThreadCreateResult)

        first = await dispatcher.dispatch(
            "thread.turn.start",
            {"thread_id": created.id, "message": "First"},
            {Scope.THREAD_OPERATE},
        )
        assert isinstance(first, AcceptedResult)
        first_closing = (await event_log.subscribe(created.id, first.sequence)).items
        for _ in range(2):
            await anext(first_closing)
        await first_closing.aclose()
        await asyncio.wait_for(recap.drain(), timeout=1)

        (tmp_path / "kinby.toml").write_text(
            (
                'id = "test"\n\n'
                '[models]\nmain = "openai:changed"\n'
                'recap = "anthropic:recap"\n\n'
                '[memory]\nrecap = "every-turn"\n'
            ),
            encoding="utf-8",
        )
        second = await dispatcher.dispatch(
            "thread.turn.start",
            {"thread_id": created.id, "message": "Second"},
            {Scope.THREAD_OPERATE},
        )
        assert isinstance(second, AcceptedResult)
        second_closing = (await event_log.subscribe(created.id, second.sequence)).items
        for _ in range(2):
            await anext(second_closing)
        await second_closing.aclose()
        await asyncio.wait_for(recap.drain(), timeout=1)

        assert selected_models == ["openai:main", "anthropic:recap"]

    asyncio.run(scenario())


def test_recap_lens_reloads_for_each_turn(tmp_path: Path) -> None:
    async def scenario() -> None:
        state_dir = tmp_path / ".state"
        event_log = EventLog(state_dir)
        model = ScriptedRecapModel(
            RecapDraft(
                keep=False,
                description="Routine turn",
                subjects=(),
                happened="",
                decided="",
                retrospective="",
            ),
            input_tokens=1,
            output_tokens=1,
        )
        recap = RecapWriter(
            event_log,
            GraphStore(tmp_path),
            _instance(tmp_path, policy="every-turn"),
            model_factory=lambda _: model,
        )
        dispatcher = build_dispatcher(
            state_dir,
            event_log=event_log,
            turns=TurnConfig(
                fixed_turn_preparation,
                fixed_permission_ceiling,
                ClosingRunner(),
                recap,
            ),
        )
        created = await dispatcher.dispatch("thread.create", {}, {Scope.THREAD_OPERATE})
        assert isinstance(created, ThreadCreateResult)

        for message, lens in (
            ("First", "Ask whether the result solved the user's actual problem."),
            ("Second", "Name every workspace assumption that shaped the work."),
        ):
            (tmp_path / "RECAP.md").write_text(lens, encoding="utf-8")
            accepted = await dispatcher.dispatch(
                "thread.turn.start",
                {"thread_id": created.id, "message": message},
                {Scope.THREAD_OPERATE},
            )
            assert isinstance(accepted, AcceptedResult)
            closing = (await event_log.subscribe(created.id, accepted.sequence)).items
            for _ in range(2):
                await anext(closing)
            await closing.aclose()
            await asyncio.wait_for(recap.drain(), timeout=1)

        prompts = [call[0].text for call in model.calls]
        assert (
            "# Recap lens\n"
            "Ask whether the result solved the user's actual problem.\n\n"
            "# Turn events"
        ) in prompts[0]
        assert (
            "# Recap lens\nName every workspace assumption that shaped the work.\n\n# Turn events"
        ) in prompts[1]

    asyncio.run(scenario())


class ToolRunner:
    async def run(self, turn: PreparedTurnRequest, context: TurnContext) -> TurnOutcome:
        for call_id, name, arguments, output in (
            ("weather-1", "weather", {"city": "Quito"}, "18 C"),
            ("weather-2", "weather", {"city": "Cuenca"}, "16 C"),
            ("read-1", "read", {"path": "notes.md"}, "Trip notes"),
        ):
            await context.emit(ToolCall(call_id=call_id, name=name, arguments=arguments))
            await context.emit(
                ToolResult(
                    call_id=call_id,
                    name=name,
                    output=output,
                    error=False,
                )
            )
        return TurnOutcome(input_tokens=4, output_tokens=2)

    resume = does_not_park
    restore = cannot_restore


class ClosingRunner:
    def __init__(self) -> None:
        self.interruptible_started = asyncio.Event()

    async def run(self, turn: PreparedTurnRequest, context: TurnContext) -> TurnOutcome:
        if turn.message == "Chat only":
            return TurnOutcome()
        await context.emit(
            ToolCall(
                call_id=f"{turn.message}-call",
                name="bash",
                arguments={"command": "uv run pytest"},
            )
        )
        if turn.message == "Fail":
            raise RuntimeError("runner failed")
        if turn.message == "Interrupt":
            self.interruptible_started.set()
            await asyncio.Event().wait()
        return TurnOutcome()

    resume = does_not_park
    restore = cannot_restore


class BlockingGraphStore(GraphStore):
    def __init__(self, instance_path: Path) -> None:
        super().__init__(instance_path)
        self.write_started = ThreadEvent()
        self.release_write = ThreadEvent()

    def remember(self, memory: MemoryNode) -> NodeId:
        self.write_started.set()
        if not self.release_write.wait(timeout=5):
            raise TimeoutError("recap write stayed blocked")
        return super().remember(memory)


class FailingGraphStore(GraphStore):
    def remember(self, memory: MemoryNode) -> NodeId:
        raise RuntimeError("graph write failed")


class BackgroundReadEventLog(EventLog):
    def __init__(self, state_dir: Path) -> None:
        super().__init__(state_dir)
        self.event_loop_thread = get_ident()
        self.require_background_read = False
        self.background_read_seen = False

    def stored(self, thread_id: UUID) -> list[Event]:
        if self.require_background_read:
            if get_ident() == self.event_loop_thread and not self.background_read_seen:
                raise AssertionError("recap read the transcript on the event-loop thread")
            self.background_read_seen = True
        return super().stored(thread_id)


def test_schedule_reads_the_transcript_in_the_worker(tmp_path: Path) -> None:
    async def scenario() -> None:
        event_log = BackgroundReadEventLog(tmp_path / ".state")
        recap = _trace_only_writer(tmp_path, event_log, GraphStore(tmp_path))
        thread_id = uuid4()
        turn_id = uuid4()
        await event_log.append(
            thread_id,
            turn_id,
            TurnStarted(
                message="Check the weather",
                model="test-model",
                permission_mode=PermissionMode.READ_ONLY,
            ),
        )
        await event_log.append(
            thread_id,
            turn_id,
            ToolCall(call_id="weather-1", name="weather", arguments={"city": "Quito"}),
        )
        await event_log.append(
            thread_id,
            turn_id,
            TurnCompleted(input_tokens=0, output_tokens=0),
        )
        event_log.require_background_read = True

        recap.schedule(thread_id, turn_id)
        await asyncio.wait_for(recap.drain(), timeout=1)

        assert event_log.background_read_seen
        event_log.require_background_read = False
        assert isinstance(event_log.stored(thread_id)[-1].payload, MemoryRecapped)

    asyncio.run(scenario())


def test_catch_up_recaps_a_closed_uncovered_turn(tmp_path: Path) -> None:
    async def scenario() -> None:
        event_log = EventLog(tmp_path / ".state")
        thread_id = uuid4()
        turn_id = uuid4()
        await event_log.append(
            thread_id,
            turn_id,
            TurnStarted(
                message="Check the weather",
                model="test-model",
                permission_mode=PermissionMode.READ_ONLY,
            ),
        )
        await event_log.append(
            thread_id,
            turn_id,
            ToolCall(call_id="weather-1", name="weather", arguments={"city": "Quito"}),
        )
        await event_log.append(
            thread_id,
            turn_id,
            TurnCompleted(input_tokens=4, output_tokens=2),
        )
        recap = _trace_only_writer(tmp_path, event_log, GraphStore(tmp_path))

        await recap.catch_up()
        await asyncio.wait_for(recap.drain(), timeout=1)

        marker = event_log.stored(thread_id)[-1]
        assert marker.turn_id == turn_id
        assert isinstance(marker.payload, MemoryRecapped)
        assert marker.payload.node is not None

    asyncio.run(scenario())


def test_catch_up_deletes_no_work_traces_and_keeps_other_nodes(tmp_path: Path) -> None:
    recorded_on = datetime(2026, 9, 10, 9, tzinfo=UTC)

    async def close_turn(
        event_log: EventLog,
        turn_id: UUID,
        outcome: CompletionOutcome,
        marker: NodeId | None,
        *,
        recapped: bool = True,
    ) -> None:
        thread_id = uuid4()
        for payload in (
            TurnStarted(
                message="Check for new issues",
                model="test-model",
                permission_mode=PermissionMode.READ_ONLY,
            ),
            ToolCall(call_id="fetch-1", name="fetch", arguments={}),
            TurnCompleted(input_tokens=0, output_tokens=0, outcome=outcome),
        ):
            await event_log.append(thread_id, turn_id, payload)
        if recapped:
            await event_log.append(
                thread_id,
                turn_id,
                MemoryRecapped(node=marker, input_tokens=0, output_tokens=0, model=None),
            )

    def trace(turn_id: UUID) -> NodeId:
        return NodeId(f"2026-09-10-{turn_id.hex}-trace")

    def episode(node: NodeId, turn_id: UUID, description: str) -> Episode:
        return Episode(
            node=node,
            date=recorded_on.date(),
            description=description,
            subjects=(),
            body="## Path taken\n1. fetch: {}",
            recaps=RecappedTurn(thread=uuid4(), turn=turn_id),
            tools=("fetch",),
        )

    def graph_files() -> set[str]:
        return {path.stem for path in (tmp_path / "memory" / "graph").glob("*.md")}

    async def scenario() -> None:
        event_log = EventLog(tmp_path / ".state", clock=lambda: recorded_on)
        graph = GraphStore(tmp_path)
        no_work, already_gone, unmarked, work = uuid4(), uuid4(), uuid4(), uuid4()
        await close_turn(event_log, no_work, CompletionOutcome.NO_WORK, trace(no_work))
        graph.remember(episode(trace(no_work), no_work, "Check for new issues"))
        await close_turn(event_log, already_gone, CompletionOutcome.NO_WORK, trace(already_gone))
        # A turn whose trace was written before the process stopped, ahead of its marker.
        await close_turn(event_log, unmarked, CompletionOutcome.NO_WORK, None, recapped=False)
        graph.remember(episode(trace(unmarked), unmarked, "Check for new issues"))
        await close_turn(event_log, work, CompletionOutcome.WORK, None)
        kept = {
            graph.remember(
                episode(
                    NodeId(f"2026-09-10-{uuid4().hex}-read-the-stack-trace"),
                    work,
                    "Read the stack trace",
                )
            ),
            graph.remember(
                episode(
                    NodeId(f"2026-09-10-{uuid4().hex}-fixed-the-build"), work, "Fixed the build"
                )
            ),
            graph.remember(
                Fact(
                    node=NodeId(f"2026-09-10-{uuid4().hex}-ci-runs-on-linux"),
                    date=recorded_on.date(),
                    description="CI runs on Linux",
                    subjects=("CI",),
                    body="The gate imports fcntl.",
                    thread=None,
                )
            ),
        }
        before = list(event_log.all_events())
        recap = _trace_only_writer(tmp_path, event_log, graph)

        await recap.catch_up()
        await asyncio.wait_for(recap.drain(), timeout=1)

        assert graph_files() == kept
        after = list(event_log.all_events())
        assert after[:-1] == before
        assert after[-1].turn_id == unmarked
        assert after[-1].payload == MemoryRecapped(
            node=None, input_tokens=0, output_tokens=0, model=None
        )

        await recap.catch_up()
        await asyncio.wait_for(recap.drain(), timeout=1)

        assert graph_files() == kept
        assert list(event_log.all_events()) == after

    asyncio.run(scenario())


def test_catch_up_queues_oldest_turns_before_a_newly_closed_turn(tmp_path: Path) -> None:
    async def close_turn(event_log: EventLog, thread_id: UUID, turn_id: UUID) -> None:
        await event_log.append(
            thread_id,
            turn_id,
            TurnStarted(
                message=f"Old turn {turn_id}",
                model="test-model",
                permission_mode=PermissionMode.READ_ONLY,
            ),
        )
        await event_log.append(
            thread_id,
            turn_id,
            ToolCall(
                call_id=f"{turn_id}-call",
                name="bash",
                arguments={"command": "uv run pytest"},
            ),
        )
        await event_log.append(
            thread_id,
            turn_id,
            TurnCompleted(input_tokens=4, output_tokens=2),
        )

    async def scenario() -> None:
        state_dir = tmp_path / ".state"
        event_log = EventLog(state_dir)
        oldest_thread_id = uuid4()
        oldest_turn_id = uuid4()
        second_thread_id = uuid4()
        second_turn_id = uuid4()
        await close_turn(event_log, oldest_thread_id, oldest_turn_id)
        await close_turn(event_log, second_thread_id, second_turn_id)
        memory = BlockingGraphStore(tmp_path)
        recap = _trace_only_writer(tmp_path, event_log, memory)
        dispatcher = build_dispatcher(
            state_dir,
            event_log=event_log,
            turns=TurnConfig(
                fixed_turn_preparation,
                fixed_permission_ceiling,
                ClosingRunner(),
                recap,
            ),
        )

        await recap.catch_up()
        await asyncio.wait_for(asyncio.to_thread(memory.write_started.wait), timeout=1)

        created = await dispatcher.dispatch("thread.create", {}, {Scope.THREAD_OPERATE})
        assert isinstance(created, ThreadCreateResult)
        newest = await dispatcher.dispatch(
            "thread.turn.start",
            {"thread_id": created.id, "message": "New turn"},
            {Scope.THREAD_OPERATE},
        )
        assert isinstance(newest, AcceptedResult)
        closing_events = (await event_log.subscribe(created.id, newest.sequence)).items
        for _ in range(2):
            await anext(closing_events)
        await closing_events.aclose()
        memory.release_write.set()
        await asyncio.wait_for(recap.drain(), timeout=1)

        markers = [
            event for event in event_log.all_events() if isinstance(event.payload, MemoryRecapped)
        ]
        assert [marker.turn_id for marker in markers] == [
            oldest_turn_id,
            second_turn_id,
            newest.turn_id,
        ]

    asyncio.run(scenario())


def test_completed_tool_turn_writes_trace_episode_and_marker(tmp_path: Path) -> None:
    async def scenario() -> None:
        event_time = datetime(2026, 9, 10, 23, 30, tzinfo=UTC)
        state_dir = tmp_path / ".state"
        event_log = EventLog(state_dir, clock=lambda: event_time)
        recap = _trace_only_writer(tmp_path, event_log, GraphStore(tmp_path))
        dispatcher = build_dispatcher(
            state_dir,
            event_log=event_log,
            turns=TurnConfig(
                fixed_turn_preparation,
                fixed_permission_ceiling,
                ToolRunner(),
                recap,
            ),
        )
        created = await dispatcher.dispatch("thread.create", {}, {Scope.THREAD_OPERATE})
        assert isinstance(created, ThreadCreateResult)
        accepted = await dispatcher.dispatch(
            "thread.turn.start",
            {"thread_id": created.id, "message": "Check the trip weather"},
            {Scope.THREAD_OPERATE},
        )
        assert isinstance(accepted, AcceptedResult)

        await asyncio.wait_for(recap.drain(), timeout=1)
        subscription = await thread_events(dispatcher, {"thread_id": created.id})
        events = [await anext(subscription) for _ in range(9)]
        await subscription.aclose()

        marker_event = events[-1]
        assert isinstance(marker_event, Event)
        assert marker_event.turn_id == accepted.turn_id
        assert isinstance(marker_event.payload, MemoryRecapped)
        assert marker_event.payload == MemoryRecapped(
            node=marker_event.payload.node,
            input_tokens=0,
            output_tokens=0,
            model="openai:main",
        )
        assert marker_event.payload.node is not None
        first_event = events[0]
        assert isinstance(first_event, Event)
        assert isinstance(first_event.payload, TurnStarted)
        assert first_event.timestamp == event_time
        episode_path = tmp_path / "memory" / "graph" / f"{marker_event.payload.node}.md"
        assert episode_path.read_text(encoding="utf-8") == (
            "---\n"
            f"date: {event_time.date().isoformat()}\n"
            f"thread: {created.id}\n"
            f"turn: {accepted.turn_id}\n"
            'description: "Check the trip weather"\n'
            "subjects: []\n"
            'tools: ["weather", "read"]\n'
            "---\n"
            "## Path taken\n"
            '1. weather: {"city": "Quito"}\n'
            '2. weather: {"city": "Cuenca"}\n'
            '3. read: {"path": "notes.md"}\n'
        )

        recap.schedule(created.id, accepted.turn_id)
        await asyncio.wait_for(recap.drain(), timeout=1)
        replay = await thread_events(dispatcher, {"thread_id": created.id})
        replayed = [await anext(replay) for _ in range(9)]
        await replay.aclose()
        assert (
            sum(
                isinstance(event, Event) and isinstance(event.payload, MemoryRecapped)
                for event in replayed
            )
            == 1
        )

    asyncio.run(scenario())


def test_tool_call_summary_is_one_bounded_line(tmp_path: Path) -> None:
    class LongArgumentRunner:
        async def run(self, turn: PreparedTurnRequest, context: TurnContext) -> TurnOutcome:
            await context.emit(
                ToolCall(
                    call_id="write-1",
                    name="write",
                    arguments={"content": "x" * 300},
                )
            )
            return TurnOutcome()

        resume = does_not_park
        restore = cannot_restore

    async def scenario() -> None:
        state_dir = tmp_path / ".state"
        event_log = EventLog(state_dir)
        recap = _trace_only_writer(tmp_path, event_log, GraphStore(tmp_path))
        dispatcher = build_dispatcher(
            state_dir,
            event_log=event_log,
            turns=TurnConfig(
                fixed_turn_preparation,
                fixed_permission_ceiling,
                LongArgumentRunner(),
                recap,
            ),
        )
        created = await dispatcher.dispatch("thread.create", {}, {Scope.THREAD_OPERATE})
        assert isinstance(created, ThreadCreateResult)
        await dispatcher.dispatch(
            "thread.turn.start",
            {"thread_id": created.id, "message": "Write the note"},
            {Scope.THREAD_OPERATE},
        )

        closing_events = (await event_log.subscribe(created.id)).items
        for _ in range(3):
            await anext(closing_events)
        await closing_events.aclose()
        await asyncio.wait_for(recap.drain(), timeout=1)
        [episode_path] = (tmp_path / "memory" / "graph").glob("*.md")
        path_line = episode_path.read_text(encoding="utf-8").splitlines()[-1]

        assert path_line.startswith('1. write: {"content": "')
        assert path_line.endswith("...")
        assert len(path_line) == 163

    asyncio.run(scenario())


def test_trace_description_is_one_bounded_line_of_a_webhook_body(tmp_path: Path) -> None:
    webhook_body = (
        '{"action": "opened", "issue": {"title": "' + "x" * 300 + '"}}\n'
        '{"sender": {"login": "jorge"}}\n'
        '{"repository": {"name": "kinby"}}'
    )

    async def scenario() -> None:
        state_dir = tmp_path / ".state"
        event_log = EventLog(state_dir)
        recap = _trace_only_writer(tmp_path, event_log, GraphStore(tmp_path))
        dispatcher = build_dispatcher(
            state_dir,
            event_log=event_log,
            turns=TurnConfig(
                fixed_turn_preparation,
                fixed_permission_ceiling,
                ToolRunner(),
                recap,
            ),
        )
        created = await dispatcher.dispatch("thread.create", {}, {Scope.THREAD_OPERATE})
        assert isinstance(created, ThreadCreateResult)
        accepted = await dispatcher.dispatch(
            "thread.turn.start",
            {"thread_id": created.id, "message": webhook_body},
            {Scope.THREAD_OPERATE},
        )
        assert isinstance(accepted, AcceptedResult)
        closing_events = (await event_log.subscribe(created.id, accepted.sequence)).items
        for _ in range(7):
            await anext(closing_events)
        await closing_events.aclose()

        await asyncio.wait_for(recap.drain(), timeout=1)

        marker = event_log.stored(created.id)[-1].payload
        assert isinstance(marker, MemoryRecapped)
        assert marker.node is not None
        episode = GraphStore(tmp_path).open(marker.node)
        assert episode.description.startswith('{"action": "opened", "issue": {"title": "xxx')
        assert episode.description.endswith("...")
        assert len(episode.description) == 200
        assert "\n" not in episode.description
        assert "jorge" not in episode.description
        assert episode.body == (
            "## Path taken\n"
            '1. weather: {"city": "Quito"}\n'
            '2. weather: {"city": "Cuenca"}\n'
            '3. read: {"path": "notes.md"}'
        )

    asyncio.run(scenario())


def test_failed_recap_appends_warning_without_marker(tmp_path: Path) -> None:
    async def scenario() -> None:
        state_dir = tmp_path / ".state"
        event_log = EventLog(state_dir)
        recap = _trace_only_writer(tmp_path, event_log, FailingGraphStore(tmp_path))
        dispatcher = build_dispatcher(
            state_dir,
            event_log=event_log,
            turns=TurnConfig(
                fixed_turn_preparation,
                fixed_permission_ceiling,
                ToolRunner(),
                recap,
            ),
        )
        created = await dispatcher.dispatch("thread.create", {}, {Scope.THREAD_OPERATE})
        assert isinstance(created, ThreadCreateResult)
        await dispatcher.dispatch(
            "thread.turn.start",
            {"thread_id": created.id, "message": "Check the trip weather"},
            {Scope.THREAD_OPERATE},
        )

        closing_events = (await event_log.subscribe(created.id)).items
        for _ in range(8):
            await anext(closing_events)
        await closing_events.aclose()
        await asyncio.wait_for(recap.drain(), timeout=1)
        events = event_log.stored(created.id)

        assert events[-1].payload == Warning(
            sources=("recap",),
            message="The turn recap failed: RuntimeError: graph write failed",
        )
        assert not any(isinstance(event.payload, MemoryRecapped) for event in events)

    asyncio.run(scenario())


def test_chat_only_turn_writes_marker_without_episode(tmp_path: Path) -> None:
    async def scenario() -> None:
        state_dir = tmp_path / ".state"
        event_log = EventLog(state_dir)
        recap = _trace_only_writer(tmp_path, event_log, GraphStore(tmp_path))
        dispatcher = build_dispatcher(
            state_dir,
            event_log=event_log,
            turns=TurnConfig(
                fixed_turn_preparation,
                fixed_permission_ceiling,
                ClosingRunner(),
                recap,
            ),
        )
        created = await dispatcher.dispatch("thread.create", {}, {Scope.THREAD_OPERATE})
        assert isinstance(created, ThreadCreateResult)
        accepted = await dispatcher.dispatch(
            "thread.turn.start",
            {"thread_id": created.id, "message": "Chat only"},
            {Scope.THREAD_OPERATE},
        )
        assert isinstance(accepted, AcceptedResult)

        await asyncio.wait_for(recap.drain(), timeout=1)
        subscription = await thread_events(dispatcher, {"thread_id": created.id})
        events = [await anext(subscription) for _ in range(3)]
        await subscription.aclose()

        marker = events[-1]
        assert isinstance(marker, Event)
        assert marker.payload == MemoryRecapped(
            node=None,
            input_tokens=0,
            output_tokens=0,
            model="openai:main",
        )
        assert not (tmp_path / "memory" / "graph").exists()

    asyncio.run(scenario())


def test_no_work_turn_writes_marker_without_node(tmp_path: Path) -> None:
    class NoWorkRunner:
        async def run(self, turn: PreparedTurnRequest, context: TurnContext) -> TurnOutcome:
            await context.emit(
                ToolCall(call_id="fetch-1", name="fetch", arguments={"owner": "jorge"})
            )
            await context.emit(
                ToolResult(call_id="fetch-1", name="fetch", output="null", error=False)
            )
            return TurnOutcome(outcome=CompletionOutcome.NO_WORK)

        resume = does_not_park
        restore = cannot_restore

    def no_model(model: str) -> ScriptedRecapModel:
        raise AssertionError(f"a no-work turn called the recap model {model}")

    async def scenario() -> None:
        state_dir = tmp_path / ".state"
        event_log = EventLog(state_dir)
        recap = RecapWriter(
            event_log,
            GraphStore(tmp_path),
            _instance(tmp_path, policy="every-turn"),
            model_factory=no_model,
        )
        dispatcher = build_dispatcher(
            state_dir,
            event_log=event_log,
            turns=TurnConfig(
                fixed_turn_preparation,
                fixed_permission_ceiling,
                NoWorkRunner(),
                recap,
            ),
        )
        created = await dispatcher.dispatch("thread.create", {}, {Scope.THREAD_OPERATE})
        assert isinstance(created, ThreadCreateResult)
        accepted = await dispatcher.dispatch(
            "thread.turn.start",
            {"thread_id": created.id, "message": "Check for new issues"},
            {Scope.THREAD_OPERATE},
        )
        assert isinstance(accepted, AcceptedResult)
        closing_events = (await event_log.subscribe(created.id, accepted.sequence)).items
        for _ in range(4):
            await anext(closing_events)
        await closing_events.aclose()

        await asyncio.wait_for(recap.drain(), timeout=1)

        marker = event_log.stored(created.id)[-1]
        assert marker.turn_id == accepted.turn_id
        assert marker.payload == MemoryRecapped(
            node=None,
            input_tokens=0,
            output_tokens=0,
            model=None,
        )
        assert not (tmp_path / "memory" / "graph").exists()

    asyncio.run(scenario())


def test_failed_and_interrupted_tool_turns_are_recapped(tmp_path: Path) -> None:
    async def scenario() -> None:
        state_dir = tmp_path / ".state"
        event_log = EventLog(state_dir)
        recap = _trace_only_writer(tmp_path, event_log, GraphStore(tmp_path))
        runner = ClosingRunner()
        dispatcher = build_dispatcher(
            state_dir,
            event_log=event_log,
            turns=TurnConfig(
                fixed_turn_preparation,
                fixed_permission_ceiling,
                runner,
                recap,
            ),
        )

        closing_types = []
        markers = []
        for message in ("Fail", "Interrupt"):
            created = await dispatcher.dispatch("thread.create", {}, {Scope.THREAD_OPERATE})
            assert isinstance(created, ThreadCreateResult)
            accepted = await dispatcher.dispatch(
                "thread.turn.start",
                {"thread_id": created.id, "message": message},
                {Scope.THREAD_OPERATE},
            )
            assert isinstance(accepted, AcceptedResult)
            if message == "Interrupt":
                await asyncio.wait_for(runner.interruptible_started.wait(), timeout=1)
                interrupted = await dispatcher.dispatch(
                    "thread.turn.interrupt",
                    {"thread_id": created.id},
                    {Scope.THREAD_OPERATE},
                )
                assert not isinstance(interrupted, ErrorEnvelope)
            await asyncio.wait_for(recap.drain(), timeout=1)
            subscription = await thread_events(dispatcher, {"thread_id": created.id})
            events = [await anext(subscription) for _ in range(4)]
            await subscription.aclose()
            closing = events[-2]
            marker = events[-1]
            assert isinstance(closing, Event)
            assert isinstance(marker, Event)
            closing_types.append(closing.type)
            markers.append(marker.payload)

        assert closing_types == [EventType.TURN_FAILED, EventType.TURN_INTERRUPTED]
        assert all(
            isinstance(marker, MemoryRecapped) and marker.node is not None for marker in markers
        )
        assert len(list((tmp_path / "memory" / "graph").glob("*.md"))) == 2

    asyncio.run(scenario())


def test_next_turn_starts_while_previous_recap_is_blocked(tmp_path: Path) -> None:
    async def scenario() -> None:
        state_dir = tmp_path / ".state"
        event_log = EventLog(state_dir)
        memory = BlockingGraphStore(tmp_path)
        recap = _trace_only_writer(tmp_path, event_log, memory)
        dispatcher = build_dispatcher(
            state_dir,
            event_log=event_log,
            turns=TurnConfig(
                fixed_turn_preparation,
                fixed_permission_ceiling,
                ClosingRunner(),
                recap,
            ),
        )
        created = await dispatcher.dispatch("thread.create", {}, {Scope.THREAD_OPERATE})
        assert isinstance(created, ThreadCreateResult)
        first = await dispatcher.dispatch(
            "thread.turn.start",
            {"thread_id": created.id, "message": "First"},
            {Scope.THREAD_OPERATE},
        )
        assert isinstance(first, AcceptedResult)
        await asyncio.wait_for(asyncio.to_thread(memory.write_started.wait), timeout=1)

        second = await dispatcher.dispatch(
            "thread.turn.start",
            {"thread_id": created.id, "message": "Second"},
            {Scope.THREAD_OPERATE},
        )
        assert isinstance(second, AcceptedResult)
        memory.release_write.set()
        await asyncio.wait_for(recap.drain(), timeout=1)

        subscription = await thread_events(dispatcher, {"thread_id": created.id})
        events = [await anext(subscription) for _ in range(8)]
        await subscription.aclose()
        markers = [
            event
            for event in events
            if isinstance(event, Event) and isinstance(event.payload, MemoryRecapped)
        ]
        assert [marker.turn_id for marker in markers] == [first.turn_id, second.turn_id]
        assert len(list((tmp_path / "memory" / "graph").glob("*.md"))) == 2

    asyncio.run(scenario())


_RUN_ID = UUID("33333333-3333-3333-3333-333333333333")
_STARTED = datetime(2026, 10, 17, 9, tzinfo=UTC)
_RUN_DRAFT = RecapDraft(
    keep=True,
    description="Recapped factory runs in the memory recap",
    subjects=("memory recap", "src/kinby/memory/recap.py"),
    happened="Issue 7 is done: a run stop writes an episode.",
    decided="Share the recap worker between turns and runs.",
    retrospective="Run the linter before the checks step.",
)


def _attempt(
    step: str,
    attempt: int,
    summary: str,
    *,
    ending: StepEnding = StepEnding.CLEAN,
    outcome: str | None = None,
    values: dict[str, bool | int | str] | None = None,
    session: str | None = None,
) -> StepAttempt:
    return StepAttempt(
        step=step,
        attempt=attempt,
        ending=ending,
        outcome=outcome,
        values=values or {},
        summary=summary,
        session=None if session is None else CodingSessionId(session),
        started_at=_STARTED,
        ended_at=_STARTED,
    )


def _stop(
    generation: int,
    attempts: Sequence[StepAttempt],
    status: FactoryRunStatus = FactoryRunStatus.DONE,
) -> RunStop:
    return RunStop(
        run_id=_RUN_ID,
        factory="toy",
        work_item={"issue": 7},
        status=status,
        generation=generation,
        attempts=list(attempts),
    )


_REVIEWED_RUN = _stop(
    1,
    [
        _attempt("write", 1, "Wrote the change.", values={"title": "Recap runs"}),
        _attempt(
            "test",
            1,
            "2 tests failed in test_recap.py.",
            ending=StepEnding.FAILED,
            values={"failure": "2 tests failed in test_recap.py."},
        ),
        _attempt("write", 2, "Fixed the failing tests."),
        _attempt("test", 2, "Tests passed."),
        _attempt(
            "review",
            1,
            "One thread to answer.",
            outcome="changes",
            values={"feedback": "Name the frame after the run record."},
        ),
        _attempt("write", 3, "Answered review.", values={"replies": "Renamed it to run record."}),
        _attempt("test", 3, "Tests passed."),
        _attempt("review", 2, "Approved.", outcome="approved"),
    ],
)


def _run_recap(
    tmp_path: Path, event_log: EventLog, policy: str, model: RecapModel
) -> tuple[RecapWriter, Dispatcher]:
    recap = RecapWriter(
        event_log,
        GraphStore(tmp_path),
        _instance(tmp_path, policy=policy),
        model_factory=lambda _: model,
    )
    dispatcher = build_dispatcher(
        tmp_path / ".state",
        event_log=event_log,
        turns=TurnConfig(
            fixed_turn_preparation,
            fixed_permission_ceiling,
            ClosingRunner(),
            recap,
        ),
    )
    return recap, dispatcher


async def _tell(dispatcher: Dispatcher, stop: RunStop) -> None:
    told = await dispatcher.dispatch(
        "run.stopped", stop.model_dump(mode="json"), {Scope.INSTANCE_LIFECYCLE}
    )
    assert isinstance(told, AcceptedResult)


def _run_markers(event_log: EventLog) -> list[Event]:
    return [
        event for event in event_log.stored(_RUN_ID) if isinstance(event.payload, MemoryRecapped)
    ]


def test_a_run_stop_writes_one_episode_with_its_sections_subjects_and_run(tmp_path: Path) -> None:
    async def scenario() -> None:
        event_log = EventLog(tmp_path / ".state")
        model = ScriptedRecapModel(_RUN_DRAFT, input_tokens=30, output_tokens=12)
        recap, dispatcher = _run_recap(tmp_path, event_log, "every-turn", model)

        await _tell(dispatcher, _REVIEWED_RUN)
        await asyncio.wait_for(recap.drain(), timeout=1)

        [marker] = _run_markers(event_log)
        assert isinstance(marker.payload, MemoryRecapped)
        assert marker.payload == MemoryRecapped(
            node=marker.payload.node,
            input_tokens=30,
            output_tokens=12,
            model="openai:main",
        )
        assert marker.payload.node is not None
        assert GraphStore(tmp_path).open(marker.payload.node) == Episode(
            node=marker.payload.node,
            date=marker.timestamp.date(),
            description="Recapped factory runs in the memory recap",
            subjects=("memory recap", "src/kinby/memory/recap.py"),
            body=(
                "## What happened\n"
                "Issue 7 is done: a run stop writes an episode.\n"
                "## What was decided\n"
                "Share the recap worker between turns and runs.\n"
                "## What should have gone differently\n"
                "Run the linter before the checks step."
            ),
            recaps=RecappedRun(run=_RUN_ID, generation=1),
            tools=(),
        )

    asyncio.run(scenario())


def test_the_run_recap_frame_holds_the_record_its_send_backs_and_review(tmp_path: Path) -> None:
    async def scenario() -> None:
        event_log = EventLog(tmp_path / ".state")
        model = ScriptedRecapModel(_RUN_DRAFT, input_tokens=3, output_tokens=1)
        recap, dispatcher = _run_recap(tmp_path, event_log, "every-turn", model)

        await _tell(dispatcher, _REVIEWED_RUN)
        await asyncio.wait_for(recap.drain(), timeout=1)

        [(message,)] = model.calls
        assert isinstance(message, HumanMessage)
        prompt = message.text
        assert _EXPECTED_DEFAULT_RECAP_LENS in prompt
        assert "Factory: toy\nWork item: issue 7\nStatus: done" in prompt
        assert (
            "2. test, attempt 1: failed. 2 tests failed in test_recap.py.\n"
            '   Values: {"failure": "2 tests failed in test_recap.py."}'
        ) in prompt
        assert "8. review, attempt 2: clean, outcome approved. Approved." in prompt
        assert (
            "## Send-backs\n"
            "- test, attempt 1, sent the work back to write: 2 tests failed in test_recap.py.\n"
            "- review, attempt 1, sent the work back to write: One thread to answer."
        ) in prompt
        assert '{"feedback": "Name the frame after the run record."}' in prompt
        assert '{"replies": "Renamed it to run record."}' in prompt
        assert "# Output contract" in prompt

    asyncio.run(scenario())


def test_a_send_back_to_a_step_an_earlier_stop_reached_counts(tmp_path: Path) -> None:
    async def scenario() -> None:
        event_log = EventLog(tmp_path / ".state")
        model = ScriptedRecapModel(_RUN_DRAFT, input_tokens=3, output_tokens=1)
        recap, dispatcher = _run_recap(tmp_path, event_log, "every-turn", model)
        needs_human = _stop(
            1,
            [
                _attempt("write", 1, "Wrote the change."),
                _attempt("test", 1, "No checks list.", ending=StepEnding.NEEDS_HUMAN),
            ],
            FactoryRunStatus.NEEDS_HUMAN,
        )
        retried = _stop(
            2,
            [
                _attempt("test", 2, "Lint failed.", ending=StepEnding.FAILED),
                _attempt("write", 2, "Fixed the lint."),
                _attempt("test", 3, "Tests passed."),
            ],
        )

        await _tell(dispatcher, needs_human)
        await _tell(dispatcher, retried)
        await asyncio.wait_for(recap.drain(), timeout=1)

        first, second = (message.text for (message,) in model.calls)
        assert "## Send-backs\nNone." in first
        assert "## Send-backs\n- test, attempt 2, sent the work back to write: Lint failed." in (
            second
        )
        assert len(_run_markers(event_log)) == 2

    asyncio.run(scenario())


def test_catch_up_recaps_an_unrecapped_run_stop_once(tmp_path: Path) -> None:
    async def scenario() -> None:
        event_log = EventLog(tmp_path / ".state")
        await event_log.append(_RUN_ID, uuid4(), RunStopped(stop=_REVIEWED_RUN))
        model = ScriptedRecapModel(_RUN_DRAFT, input_tokens=3, output_tokens=1)
        recap, dispatcher = _run_recap(tmp_path, event_log, "every-turn", model)

        await recap.catch_up()
        await asyncio.wait_for(recap.drain(), timeout=1)
        await recap.catch_up()
        await _tell(dispatcher, _REVIEWED_RUN)
        await asyncio.wait_for(recap.drain(), timeout=1)

        assert len(model.calls) == 1
        assert len(_run_markers(event_log)) == 1
        assert len(list((tmp_path / "memory" / "graph").glob("*.md"))) == 1

    asyncio.run(scenario())


def test_a_trace_only_run_recap_writes_the_work_item_and_status_without_a_model(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        event_log = EventLog(tmp_path / ".state")
        recap, dispatcher = _run_recap(tmp_path, event_log, "off", UnknownRecapModel())

        await _tell(dispatcher, _REVIEWED_RUN)
        await asyncio.wait_for(recap.drain(), timeout=1)

        [marker] = _run_markers(event_log)
        assert isinstance(marker.payload, MemoryRecapped)
        assert marker.payload == MemoryRecapped(
            node=marker.payload.node, input_tokens=0, output_tokens=0, model="openai:main"
        )
        assert marker.payload.node is not None
        episode = GraphStore(tmp_path).open(marker.payload.node)
        assert isinstance(episode, Episode)
        assert (episode.description, episode.body, episode.recaps) == (
            "toy run on issue 7",
            "## What happened\nThe run stopped as done.",
            RecappedRun(run=_RUN_ID, generation=1),
        )

    asyncio.run(scenario())


def test_a_failed_run_recap_warns_and_the_next_start_retries_it(tmp_path: Path) -> None:
    async def scenario() -> None:
        event_log = EventLog(tmp_path / ".state")
        failing, dispatcher = _run_recap(tmp_path, event_log, "every-turn", FailingRecapModel())

        await _tell(dispatcher, _REVIEWED_RUN)
        await asyncio.wait_for(failing.drain(), timeout=1)

        assert event_log.stored(_RUN_ID)[-1].payload == Warning(
            sources=("recap",),
            message="The run recap failed: RuntimeError: recap response could not be parsed",
        )
        assert not _run_markers(event_log)
        model = ScriptedRecapModel(_RUN_DRAFT, input_tokens=3, output_tokens=1)
        retry, _ = _run_recap(tmp_path, event_log, "every-turn", model)
        await retry.catch_up()
        await asyncio.wait_for(retry.drain(), timeout=1)
        assert len(_run_markers(event_log)) == 1

    asyncio.run(scenario())


def test_a_run_stop_writes_its_episode_even_from_a_draft_not_kept(tmp_path: Path) -> None:
    async def scenario() -> None:
        event_log = EventLog(tmp_path / ".state")
        model = ScriptedRecapModel(
            _RUN_DRAFT.model_copy(update={"keep": False}), input_tokens=3, output_tokens=1
        )
        recap, dispatcher = _run_recap(tmp_path, event_log, "every-turn", model)

        await _tell(dispatcher, _REVIEWED_RUN)
        await asyncio.wait_for(recap.drain(), timeout=1)

        [marker] = _run_markers(event_log)
        assert isinstance(marker.payload, MemoryRecapped)
        assert marker.payload.node is not None
        assert GraphStore(tmp_path).open(marker.payload.node).description == (
            _RUN_DRAFT.description
        )

    asyncio.run(scenario())


class MarkerLosingEventLog(EventLog):
    """Fails to append a recap marker, as a process stopping right after the episode would."""

    async def append(self, thread_id: UUID, turn_id: UUID, payload: Payload) -> Event:
        if isinstance(payload, MemoryRecapped):
            raise OSError("stopped before the marker")
        return await super().append(thread_id, turn_id, payload)


def test_a_run_recap_retried_after_a_lost_marker_replaces_its_episode(tmp_path: Path) -> None:
    async def scenario() -> None:
        state_dir = tmp_path / ".state"
        model = ScriptedRecapModel(_RUN_DRAFT, input_tokens=3, output_tokens=1)
        lost, dispatcher = _run_recap(
            tmp_path, MarkerLosingEventLog(state_dir), "every-turn", model
        )
        await _tell(dispatcher, _REVIEWED_RUN)
        await asyncio.wait_for(lost.drain(), timeout=1)
        assert len(list((tmp_path / "memory" / "graph").glob("*.md"))) == 1

        event_log = EventLog(state_dir)
        retry, _ = _run_recap(tmp_path, event_log, "every-turn", model)
        await retry.catch_up()
        await asyncio.wait_for(retry.drain(), timeout=1)

        [episode] = (tmp_path / "memory" / "graph").glob("*.md")
        [marker] = _run_markers(event_log)
        assert isinstance(marker.payload, MemoryRecapped)
        assert marker.payload.node == episode.stem

    asyncio.run(scenario())


_SESSION = "11111111-aaaa-4aaa-8aaa-111111111111"
_RERUN_SESSION = "22222222-bbbb-4bbb-8bbb-222222222222"
_FAILED_OUTPUT = (
    "Exit code 1\nEARLY PYTEST OUTPUT\n" + "collected line\n" * 80 + "FAILED test_digest.py::test_x"
)


def _said(text: str) -> dict[str, object]:
    return {
        "type": "assistant",
        "message": {"role": "assistant", "content": [{"type": "text", "text": text}]},
    }


def _used(call_id: str, name: str, **arguments: object) -> dict[str, object]:
    return {
        "type": "assistant",
        "message": {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": call_id, "name": name, "input": arguments}],
        },
    }


def _returned(call_id: str, output: str, *, error: bool = False) -> dict[str, object]:
    return {
        "type": "user",
        "message": {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": call_id,
                    "content": output,
                    "is_error": error,
                }
            ],
        },
        "toolUseResult": output,
    }


def _claude_session(tmp_path: Path, session: str, entries: Sequence[dict[str, object]]) -> None:
    """A Claude session file where the instance runs Claude, under its state folder."""
    project = tmp_path / ".state" / "claude" / "projects" / "-instance-workspace"
    project.mkdir(parents=True, exist_ok=True)
    lines = (json.dumps(entry) for entry in entries)
    (project / f"{session}.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _implementing_session(tmp_path: Path) -> None:
    _claude_session(
        tmp_path,
        _SESSION,
        [
            {"type": "user", "message": {"role": "user", "content": "THE STEP PROMPT"}},
            _said("I will look at the recap first."),
            _used("1", "Bash", command='kinby memory search "session digest"'),
            _returned("1", "SEARCH HITS OUTPUT"),
            _used("2", "Read", file_path="/instance/workspace/src/recap.py"),
            _returned("2", "READ FILE CONTENT"),
            _used("3", "Grep", pattern="def _run_frame"),
            _returned("3", "GREP OUTPUT"),
            _used(
                "4",
                "Edit",
                file_path="/instance/workspace/src/recap.py",
                old_string="OLD EDIT TEXT",
                new_string="NEW EDIT TEXT",
            ),
            _returned("4", "EDIT RESULT OUTPUT"),
            _used(
                "5",
                "Write",
                file_path="/instance/workspace/src/digest.py",
                content="WRITTEN FILE CONTENT",
            ),
            _returned("5", "WRITE RESULT OUTPUT"),
            _used("6", "Read", file_path="/instance/workspace/src/recap.py"),
            _returned("6", "READ FILE CONTENT AGAIN"),
            _used("7", "Bash", command="uv run pytest"),
            _returned("7", _FAILED_OUTPUT, error=True),
            _used("8", "Bash", command="git status"),
            _returned("8", "PASSING COMMAND OUTPUT"),
            _used("9", "Bash", command="python3 - <<'EOF'\nHEREDOC FILE CONTENT\nEOF"),
            _returned("9", "Exit code 2\nTraceback", error=True),
            _used("10", "Read", file_path="/instance/workspace/src/gone.py"),
            _returned("10", "File does not exist.", error=True),
            _used(
                "11",
                "Edit",
                file_path="/instance/workspace/src/untouched.py",
                old_string="ABSENT",
                new_string="NEVER WRITTEN",
            ),
            _returned("11", "String to replace not found in file.", error=True),
            _said("Decided to digest each session once, because attempts share it."),
        ],
    )


_SESSION_RUN = _stop(
    1,
    [
        _attempt("implement", 1, "Implemented it.", session=_SESSION),
        _attempt("fix", 1, "Fixed the review.", session=_SESSION),
    ],
)


def _run_prompt(model: ScriptedRecapModel) -> str:
    [(message,)] = model.calls
    assert isinstance(message, HumanMessage)
    return message.text


def test_the_run_recap_frame_lists_what_the_claude_session_read_changed_and_failed(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        _implementing_session(tmp_path)
        event_log = EventLog(tmp_path / ".state")
        model = ScriptedRecapModel(_RUN_DRAFT, input_tokens=3, output_tokens=1)
        recap, dispatcher = _run_recap(tmp_path, event_log, "every-turn", model)

        await _tell(dispatcher, _SESSION_RUN)
        await asyncio.wait_for(recap.drain(), timeout=1)

        prompt = _run_prompt(model)
        assert (
            f"## Session {_SESSION}\n"
            "Files read:\n"
            "- /instance/workspace/src/recap.py\n"
            "Files changed:\n"
            "- /instance/workspace/src/recap.py\n"
            "- /instance/workspace/src/digest.py\n"
        ) in prompt
        assert "Failed commands:\n- uv run pytest\n" in prompt
        assert "FAILED test_digest.py::test_x" in prompt
        assert "EARLY PYTEST OUTPUT" not in prompt
        assert "- python3 - <<'EOF'\n  Exit code 2\n  Traceback\n" in prompt
        assert "Memory searches:\n- session digest\n" in prompt
        assert "/instance/workspace/src/gone.py" not in prompt
        assert "/instance/workspace/src/untouched.py" not in prompt
        assert (
            "Assistant text:\nI will look at the recap first.\n\n"
            "Decided to digest each session once, because attempts share it."
        ) in prompt
        [marker] = _run_markers(event_log)
        assert isinstance(marker.payload, MemoryRecapped)
        assert marker.payload.node is not None
        episode = GraphStore(tmp_path).open(marker.payload.node)
        assert isinstance(episode, Episode)
        assert episode.tools == ("Bash", "Read", "Grep", "Edit", "Write")

    asyncio.run(scenario())


def test_the_run_recap_frame_holds_no_tool_output_or_file_content(tmp_path: Path) -> None:
    async def scenario() -> None:
        _implementing_session(tmp_path)
        event_log = EventLog(tmp_path / ".state")
        model = ScriptedRecapModel(_RUN_DRAFT, input_tokens=3, output_tokens=1)
        recap, dispatcher = _run_recap(tmp_path, event_log, "every-turn", model)

        await _tell(dispatcher, _SESSION_RUN)
        await asyncio.wait_for(recap.drain(), timeout=1)

        prompt = _run_prompt(model)
        for withheld in (
            "THE STEP PROMPT",
            "SEARCH HITS OUTPUT",
            "READ FILE CONTENT",
            "GREP OUTPUT",
            "OLD EDIT TEXT",
            "NEW EDIT TEXT",
            "EDIT RESULT OUTPUT",
            "WRITTEN FILE CONTENT",
            "WRITE RESULT OUTPUT",
            "PASSING COMMAND OUTPUT",
            "HEREDOC FILE CONTENT",
        ):
            assert withheld not in prompt

    asyncio.run(scenario())


def test_each_session_of_a_run_is_digested_once(tmp_path: Path) -> None:
    async def scenario() -> None:
        _implementing_session(tmp_path)
        _claude_session(
            tmp_path,
            _RERUN_SESSION,
            [
                _used("1", "Read", file_path="/instance/workspace/src/rerun.py"),
                _returned("1", "RERUN FILE CONTENT"),
            ],
        )
        event_log = EventLog(tmp_path / ".state")
        model = ScriptedRecapModel(_RUN_DRAFT, input_tokens=3, output_tokens=1)
        recap, dispatcher = _run_recap(tmp_path, event_log, "every-turn", model)
        rerun = _stop(
            1,
            [
                _attempt("implement", 1, "Implemented it.", session=_SESSION),
                _attempt("answer", 1, "Answered review.", session=_SESSION),
                _attempt("fix", 1, "Session lost, ran in a new one.", session=_RERUN_SESSION),
            ],
        )

        await _tell(dispatcher, rerun)
        await asyncio.wait_for(recap.drain(), timeout=1)

        prompt = _run_prompt(model)
        assert prompt.count(f"## Session {_SESSION}\n") == 1
        assert f"## Session {_RERUN_SESSION}\nFiles read:\n- /instance/workspace/src/rerun.py" in (
            prompt
        )

    asyncio.run(scenario())


def test_a_missing_session_file_still_writes_an_episode(tmp_path: Path) -> None:
    async def scenario() -> None:
        event_log = EventLog(tmp_path / ".state")
        model = ScriptedRecapModel(_RUN_DRAFT, input_tokens=3, output_tokens=1)
        recap, dispatcher = _run_recap(tmp_path, event_log, "every-turn", model)

        await _tell(dispatcher, _SESSION_RUN)
        await asyncio.wait_for(recap.drain(), timeout=1)

        assert f"## Session {_SESSION}\nIts transcript is missing." in _run_prompt(model)
        [marker] = _run_markers(event_log)
        assert isinstance(marker.payload, MemoryRecapped)
        assert marker.payload.node is not None

    asyncio.run(scenario())


def test_a_codex_only_run_is_recapped_from_its_record(tmp_path: Path) -> None:
    async def scenario() -> None:
        _implementing_session(tmp_path)
        event_log = EventLog(tmp_path / ".state")
        codex = DelegatedRun(
            usage_source=UsageSource.CHATGPT_SUBSCRIPTION,
            client="codex",
            models=[],
            input_tokens=10,
            output_tokens=2,
            duration_ms=1,
            client_turns=1,
            outcome=DelegatedRunOutcome.COMPLETED,
            session=CodingSessionId(_SESSION),
        )
        await event_log.append(_RUN_ID, uuid4(), RunDelegated(run=codex))
        model = ScriptedRecapModel(_RUN_DRAFT, input_tokens=3, output_tokens=1)
        recap, dispatcher = _run_recap(tmp_path, event_log, "every-turn", model)

        await _tell(dispatcher, _SESSION_RUN)
        await asyncio.wait_for(recap.drain(), timeout=1)

        prompt = _run_prompt(model)
        assert "# Session digests\nNone." in prompt
        assert "/instance/workspace/src/recap.py" not in prompt
        [marker] = _run_markers(event_log)
        assert isinstance(marker.payload, MemoryRecapped)
        assert marker.payload.node is not None

    asyncio.run(scenario())


def test_a_trace_only_run_recap_lists_the_files_changed(tmp_path: Path) -> None:
    async def scenario() -> None:
        _implementing_session(tmp_path)
        event_log = EventLog(tmp_path / ".state")
        recap, dispatcher = _run_recap(tmp_path, event_log, "off", UnknownRecapModel())

        await _tell(dispatcher, _SESSION_RUN)
        await asyncio.wait_for(recap.drain(), timeout=1)

        [marker] = _run_markers(event_log)
        assert isinstance(marker.payload, MemoryRecapped)
        assert marker.payload.node is not None
        episode = GraphStore(tmp_path).open(marker.payload.node)
        assert isinstance(episode, Episode)
        assert (episode.body, episode.tools) == (
            "## What happened\n"
            "The run stopped as done.\n"
            "## Files changed\n"
            "- /instance/workspace/src/recap.py\n"
            "- /instance/workspace/src/digest.py",
            ("Bash", "Read", "Grep", "Edit", "Write"),
        )

    asyncio.run(scenario())
