import asyncio
from collections.abc import Sequence
from pathlib import Path
from threading import Event as ThreadEvent
from threading import get_ident
from typing import Self
from uuid import UUID, uuid4

from langchain_core.messages import AIMessage, BaseMessage

from kinby.contracts import (
    AcceptedResult,
    ErrorEnvelope,
    Event,
    EventType,
    MemoryRecapped,
    MessageDelta,
    PermissionMode,
    Scope,
    ThreadCreateResult,
    ToolCall,
    ToolResult,
    TurnCompleted,
    TurnStarted,
    Warning,
)
from kinby.core.dispatcher import TurnConfig, build_dispatcher
from kinby.core.events import EventLog
from kinby.core.turns import Emit, TurnOutcome, TurnRequest
from kinby.instance import load_instance
from kinby.memory import GraphStore, MemoryNode, NodeId, RecapDraft, RecapWriter
from tests.helpers import (
    cannot_restore,
    does_not_park,
    fixed_permission_ceiling,
    fixed_turn_preparation,
)

_EXPECTED_TOOL_RESULT_MAX_CHARS = 800
_EXPECTED_DEFAULT_RECAP_LENS = (
    "Describe the turn's concrete outcome and decisions. "
    "Name one honest way the work could have gone differently."
)


class ScriptedRecapModel:
    def __init__(self, draft: RecapDraft, input_tokens: int, output_tokens: int) -> None:
        self._draft = draft
        self._input_tokens = input_tokens
        self._output_tokens = output_tokens
        self.calls: list[tuple[BaseMessage, ...]] = []

    def with_structured_output(
        self,
        schema: type[RecapDraft],
        *,
        include_raw: bool,
    ) -> Self:
        assert schema is RecapDraft
        assert include_raw
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
        include_raw: bool,
    ) -> Self:
        assert schema is RecapDraft
        assert include_raw
        return self

    async def ainvoke(self, messages: Sequence[BaseMessage]) -> object:
        raise RuntimeError("recap provider unavailable")


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

        closing_events = event_log.subscribe(created.id, accepted.sequence)
        for _ in range(7):
            await anext(closing_events)
        await closing_events.aclose()
        await asyncio.wait_for(recap.drain(), timeout=1)
        events = event_log.stored(created.id)
        marker = events[-1]
        assert marker.turn_id == accepted.turn_id
        assert isinstance(marker.payload, MemoryRecapped)
        assert marker.payload == MemoryRecapped(
            node=marker.payload.node,
            input_tokens=17,
            output_tokens=9,
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


def test_recap_model_receives_the_harness_owned_turn_frame(tmp_path: Path) -> None:
    class FrameRunner:
        async def run(self, turn: TurnRequest, emit: Emit) -> TurnOutcome:
            await emit(MessageDelta(text="Compared "))
            await emit(MessageDelta(text="both cities."))
            await emit(
                ToolCall(
                    call_id="weather-1",
                    name="weather",
                    arguments={"city": "Quito"},
                )
            )
            await emit(
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
        closing_events = event_log.subscribe(created.id, accepted.sequence)
        for _ in range(5):
            await anext(closing_events)
        await closing_events.aclose()

        await asyncio.wait_for(recap.drain(), timeout=1)

        assert len(model.calls) == 1
        prompt = model.calls[0][0].text
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
        closing_events = event_log.subscribe(created.id, accepted.sequence)
        for _ in range(7):
            await anext(closing_events)
        await closing_events.aclose()

        await asyncio.wait_for(recap.drain(), timeout=1)

        assert event_log.stored(created.id)[-1].payload == MemoryRecapped(
            node=None,
            input_tokens=8,
            output_tokens=3,
        )
        assert not list((tmp_path / "memory" / "graph").glob("*.md"))

    asyncio.run(scenario())


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
        closing_events = event_log.subscribe(created.id, accepted.sequence)
        for _ in range(7):
            await anext(closing_events)
        await closing_events.aclose()

        await asyncio.wait_for(recap.drain(), timeout=1)

        events = event_log.stored(created.id)
        assert events[-2].payload == TurnCompleted(input_tokens=4, output_tokens=2)
        assert events[-1].turn_id == accepted.turn_id
        assert events[-1].payload == Warning(
            sources=("recap",),
            message=("The turn recap failed: RuntimeError: recap provider unavailable"),
        )
        assert not any(isinstance(event.payload, MemoryRecapped) for event in events)
        assert not list((tmp_path / "memory" / "graph").glob("*.md"))

    asyncio.run(scenario())


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
        closing_events = event_log.subscribe(created.id, accepted.sequence)
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
        first_closing = event_log.subscribe(created.id, first.sequence)
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
        second_closing = event_log.subscribe(created.id, second.sequence)
        for _ in range(2):
            await anext(second_closing)
        await second_closing.aclose()
        await asyncio.wait_for(recap.drain(), timeout=1)

        assert selected_models == ["openai:main", "anthropic:recap"]

    asyncio.run(scenario())


class ToolRunner:
    async def run(self, turn: TurnRequest, emit: Emit) -> TurnOutcome:
        for call_id, name, arguments, output in (
            ("weather-1", "weather", {"city": "Quito"}, "18 C"),
            ("weather-2", "weather", {"city": "Cuenca"}, "16 C"),
            ("read-1", "read", {"path": "notes.md"}, "Trip notes"),
        ):
            await emit(ToolCall(call_id=call_id, name=name, arguments=arguments))
            await emit(
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

    async def run(self, turn: TurnRequest, emit: Emit) -> TurnOutcome:
        if turn.message == "Chat only":
            return TurnOutcome()
        await emit(
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
        closing_events = event_log.subscribe(created.id, newest.sequence)
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
            {"thread_id": created.id, "message": "Check the trip weather"},
            {Scope.THREAD_OPERATE},
        )
        assert isinstance(accepted, AcceptedResult)

        await asyncio.wait_for(recap.drain(), timeout=1)
        subscription = dispatcher.subscribe(
            "thread.subscribe",
            {"thread_id": created.id},
            {Scope.THREAD_READ},
        )
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
        )
        assert marker_event.payload.node is not None
        episode_path = tmp_path / "memory" / "graph" / f"{marker_event.payload.node}.md"
        assert episode_path.read_text(encoding="utf-8") == (
            "---\n"
            f"date: {marker_event.timestamp.date().isoformat()}\n"
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
        replay = dispatcher.subscribe(
            "thread.subscribe",
            {"thread_id": created.id},
            {Scope.THREAD_READ},
        )
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
        async def run(self, turn: TurnRequest, emit: Emit) -> TurnOutcome:
            await emit(
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

        closing_events = event_log.subscribe(created.id)
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

        closing_events = event_log.subscribe(created.id)
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
        subscription = dispatcher.subscribe(
            "thread.subscribe",
            {"thread_id": created.id},
            {Scope.THREAD_READ},
        )
        events = [await anext(subscription) for _ in range(3)]
        await subscription.aclose()

        marker = events[-1]
        assert isinstance(marker, Event)
        assert marker.payload == MemoryRecapped(node=None, input_tokens=0, output_tokens=0)
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
            subscription = dispatcher.subscribe(
                "thread.subscribe",
                {"thread_id": created.id},
                {Scope.THREAD_READ},
            )
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

        subscription = dispatcher.subscribe(
            "thread.subscribe",
            {"thread_id": created.id},
            {Scope.THREAD_READ},
        )
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
