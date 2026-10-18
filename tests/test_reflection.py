import asyncio
from collections.abc import AsyncIterator, Callable, Sequence
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import Self
from uuid import UUID, uuid4

import pytest
from langchain_core.messages import AIMessageChunk, BaseMessage, HumanMessage
from langchain_core.tools import StructuredTool

from kinby.contracts import (
    AcceptedResult,
    ApprovalDecision,
    ApprovalRequested,
    CompletionOutcome,
    Event,
    MemoryRecapped,
    NodeId,
    RoutineListResult,
    RoutineName,
    RoutineOrigin,
    RoutineSummary,
    RoutineTrigger,
    ThreadApprovalRespondCommand,
    ToolResult,
    TurnCompleted,
    TurnStarted,
    UserOrigin,
    is_turn_closing,
)
from kinby.core.dispatcher import ScheduledTurnConfig, TurnConfig, build_dispatcher
from kinby.core.errors import InstanceBusy
from kinby.core.events import EventLog
from kinby.core.scheduler import SchedulerConfig
from kinby.core.threads import ThreadStore
from kinby.core.turn_runner import LangGraphRunner
from kinby.core.turns import ClosedTurnHook, Turns
from kinby.instance import Instance, init_instance, load_instance
from kinby.instance.init import DEFAULT_REFLECTION_LENS
from kinby.memory import Fact, GraphStore, RecapWriter
from kinby.plugins.routines import is_dreaming_stage, load_routine
from tests.test_consolidation import (
    ConsolidatingModel,
    consolidated,
    consolidation_stage,
    episode,
    learned,
)
from tests.test_consolidation import fire as fire_consolidation
from tests.test_routines import instance_at, no_recap, routine_file, unused_recap
from tests.test_scheduler import FakeClock, call, runtime

_LENS = "Look for workflows the user repeats."
_REPORT = "Dream report: I proposed one skill."
_SKILL = (
    "---\nname: send-invoices\ndescription: Send the month's invoices\n---\n"
    "Export the invoices and email them.\n"
)
_ROUTINE = "---\ndescription: Send the invoices\nschedule: 0 9 1 * *\n---\nSend the invoices.\n"
_CODE = '''from kinby.plugins import tool
@tool(write=False)
def check() -> None:
    """Check."""
'''
_PROPOSALS: dict[str, dict[str, object]] = {
    "remember": {
        "description": "The user sends invoices on the first",
        "subjects": ["invoicing"],
        "body": "The user sends the month's invoices on the first.",
    },
    "skill_write": {"name": "send-invoices", "content": _SKILL},
    "routine_write": {"name": "invoices", "content": _ROUTINE},
}


class ReflectingModel:
    """Make *proposals* in the first response of a turn, then end it with the dream report."""

    def __init__(self, *proposals: tuple[str, dict[str, object]]) -> None:
        self.proposals = proposals
        self.messages: list[list[BaseMessage]] = []
        self.tools: list[str] = []

    def bind_tools(self, tools: Sequence[StructuredTool]) -> Self:
        self.tools = [tool.name for tool in tools]
        return self

    @property
    def wake(self) -> str:
        return str(self.messages[0][-1].content)

    async def astream(self, messages: Sequence[BaseMessage]) -> AsyncIterator[AIMessageChunk]:
        self.messages.append(list(messages))
        usage = {"input_tokens": 4, "output_tokens": 2, "total_tokens": 6}
        if self.proposals and isinstance(messages[-1], HumanMessage):
            yield AIMessageChunk(
                content="",
                tool_calls=[
                    {"id": f"proposal-{uuid4()}", "name": name, "args": args, "type": "tool_call"}
                    for name, args in self.proposals
                ],
                usage_metadata=usage,
            )
        else:
            yield AIMessageChunk(content=_REPORT, usage_metadata=usage)


def reflection_stage(instance: Instance, mode: str = "ask") -> None:
    routine_file(
        instance,
        f"description: Reflect on the week\nrun: reflect\nmode: {mode}",
        _LENS,
        name="reflection",
    )


def consolidate(instance: Instance, description: str) -> tuple[NodeId, NodeId]:
    """Consolidate one new episode into one fact, as the nightly stage does. The fact, then its
    source."""
    consolidation_stage(instance)
    source = episode(instance, f"{description}, again")
    [covering] = consolidated(
        fire_consolidation(instance, ConsolidatingModel([learned(description, source)]))
    )
    return covering.facts[0], source


def stage_turns(
    instance: Instance, model: ReflectingModel, after_turn: ClosedTurnHook = no_recap
) -> tuple[Turns, ThreadStore, EventLog]:
    log = EventLog(instance.manifest.state_dir)
    store = ThreadStore(instance.manifest.state_dir)
    runner = LangGraphRunner(instance, event_log=log, model_factory=lambda _: model)
    turns = Turns(
        store,
        log,
        runner,
        runner.prepare_for_turn,
        runner.permission_ceiling,
        after_turn,
        is_dreaming_stage=partial(is_dreaming_stage, instance),
    )
    return turns, store, log


async def wake_reflection(turns: Turns, store: ThreadStore) -> UUID:
    thread = store.create(None)
    await turns.wake(
        thread.id,
        "",
        RoutineOrigin(name=RoutineName("reflection"), trigger=RoutineTrigger.SCHEDULED),
    )
    return thread.id


async def until(log: EventLog, thread_id: UUID, stop: Callable[[Event], bool]) -> list[Event]:
    subscription = (await log.subscribe(thread_id)).items
    events: list[Event] = []
    async with asyncio.timeout(5):
        async for event in subscription:
            events.append(event)
            if stop(event):
                break
    await subscription.aclose()
    return events


def parked_or_closed(event: Event) -> bool:
    return isinstance(event.payload, ApprovalRequested) or is_turn_closing(event.payload)


def reflect(
    instance: Instance, model: ReflectingModel, after_turn: ClosedTurnHook = no_recap
) -> list[Event]:
    """Fire the reflection stage until its turn closes or parks on an approval."""

    async def scenario() -> list[Event]:
        turns, store, log = stage_turns(instance, model, after_turn)
        return await until(log, await wake_reflection(turns, store), parked_or_closed)

    return asyncio.run(scenario())


def respond(
    instance: Instance,
    model: ReflectingModel,
    approval: Event,
    decision: ApprovalDecision,
    reason: str | None = None,
) -> list[Event]:
    """Answer a parked reflection's approval in a restarted runtime, and wait for the turn."""

    async def scenario() -> list[Event]:
        assert isinstance(approval.payload, ApprovalRequested)
        turns, _, log = stage_turns(instance, model)
        await turns.respond(
            ThreadApprovalRespondCommand(
                thread_id=approval.thread_id,
                approval_id=approval.payload.approval_id,
                decision=decision,
                reason=reason,
            )
        )
        await turns.wait_idle()
        return log.stored(approval.thread_id)

    return asyncio.run(scenario())


def outcome(events: list[Event]) -> CompletionOutcome:
    closing = events[-1].payload
    assert isinstance(closing, TurnCompleted)
    return closing.outcome


def test_a_reflection_with_no_new_fact_since_the_last_one_is_no_work(tmp_path: Path) -> None:
    instance = instance_at(tmp_path)
    reflection_stage(instance, mode="full-access")
    nothing_consolidated = ReflectingModel()
    assert outcome(reflect(instance, nothing_consolidated)) is CompletionOutcome.NO_WORK
    consolidation_stage(instance)
    episode(instance, "Booked a dentist")
    fire_consolidation(instance, ConsolidatingModel())
    no_fact_written = ReflectingModel()
    assert outcome(reflect(instance, no_fact_written)) is CompletionOutcome.NO_WORK
    written, _ = consolidate(instance, "Invoices go out late")
    first = ReflectingModel()
    assert outcome(reflect(instance, first)) is CompletionOutcome.WORK
    again = ReflectingModel()

    events = reflect(instance, again)

    assert nothing_consolidated.messages == no_fact_written.messages == again.messages == []
    assert written in first.wake
    assert outcome(events) is CompletionOutcome.NO_WORK
    closing = events[-1].payload
    assert isinstance(closing, TurnCompleted)
    assert closing.input_tokens == closing.output_tokens == 0


def test_the_frame_holds_the_new_facts_and_their_episodes_under_the_lens(tmp_path: Path) -> None:
    instance = instance_at(tmp_path)
    reflection_stage(instance, mode="full-access")
    older, _ = consolidate(instance, "Invoices go out late")
    reflect(instance, ReflectingModel())
    newer, source = consolidate(instance, "Reports go out on Fridays")
    model = ReflectingModel()

    reflect(instance, model)

    assert _LENS in model.wake
    assert newer in model.wake
    assert source in model.wake
    assert "Reports go out on Fridays, again" in model.wake
    assert older not in model.wake
    assert {"remember", "skill_write", "routine_write"} <= set(model.tools)
    assert "consolidate" not in model.tools


def _remembered(instance: Instance) -> bool:
    return any(
        isinstance(node, Fact) and node.description == _PROPOSALS["remember"]["description"]
        for node in GraphStore(instance.path).nodes()
    )


def _skill_written(instance: Instance) -> bool:
    return (instance.path / "skills" / "send-invoices" / "SKILL.md").is_file()


def _routine_written(instance: Instance) -> bool:
    return (instance.path / "routines" / "invoices" / "ROUTINE.md").is_file()


@pytest.mark.parametrize(
    "proposal,written",
    [
        ("remember", _remembered),
        ("skill_write", _skill_written),
        ("routine_write", _routine_written),
    ],
)
def test_every_proposal_waits_on_the_users_approval(
    tmp_path: Path, proposal: str, written: Callable[[Instance], bool]
) -> None:
    instance = instance_at(tmp_path)
    reflection_stage(instance)
    consolidate(instance, "Invoices go out on the first")
    model = ReflectingModel((proposal, _PROPOSALS[proposal]))

    parked = reflect(instance, model)

    approval = parked[-1]
    assert isinstance(approval.payload, ApprovalRequested)
    assert approval.payload.name == proposal
    assert approval.payload.rule == "mode.ask.write"
    assert not written(instance)
    events = respond(instance, model, approval, ApprovalDecision.APPROVE)
    assert outcome(events) is CompletionOutcome.WORK
    assert written(instance)


def test_a_declined_proposal_is_in_the_next_frame(tmp_path: Path) -> None:
    instance = instance_at(tmp_path)
    reflection_stage(instance)
    consolidate(instance, "Invoices go out on the first")
    declined = ReflectingModel(("skill_write", _PROPOSALS["skill_write"]))
    [approval] = [
        event
        for event in reflect(instance, declined)
        if isinstance(event.payload, ApprovalRequested)
    ]
    respond(instance, declined, approval, ApprovalDecision.DENY, "I send them by hand.")
    consolidate(instance, "Invoices go out late")
    model = ReflectingModel()

    reflect(instance, model)

    assert _REPORT in model.wake
    assert "send-invoices" in model.wake
    assert '"declined"' in model.wake
    assert "I send them by hand." in model.wake
    assert not _skill_written(instance)


@pytest.mark.parametrize(
    "code_step",
    [
        {"content": _ROUTINE, "code": _CODE},
        {"content": _ROUTINE.replace("schedule:", "run: memory_search\nschedule:")},
    ],
    ids=["run.py", "run"],
)
def test_routine_write_with_a_code_step_is_refused_on_a_stage_turn(
    tmp_path: Path, code_step: dict[str, object]
) -> None:
    instance = instance_at(tmp_path)
    reflection_stage(instance, mode="full-access")
    consolidate(instance, "Invoices go out on the first")
    model = ReflectingModel(("routine_write", {"name": "invoices", **code_step}))

    events = reflect(instance, model)

    [result] = [
        event.payload
        for event in events
        if isinstance(event.payload, ToolResult) and event.payload.name == "routine_write"
    ]
    assert result.error
    assert "code step" in result.output
    assert not (instance.path / "routines" / "invoices").exists()


def test_a_stage_that_rewrites_its_own_routine_still_writes_no_code_step(
    tmp_path: Path,
) -> None:
    instance = instance_at(tmp_path)
    reflection_stage(instance, mode="full-access")
    consolidate(instance, "Invoices go out on the first")
    unstaged = "---\ndescription: Reflect on the week\nmode: full-access\n---\nReflect.\n"
    model = ReflectingModel(
        ("routine_write", {"name": "reflection", "content": unstaged}),
        ("routine_write", {"name": "invoices", "content": _ROUTINE, "code": _CODE}),
    )

    events = reflect(instance, model)

    rewrite, with_code = [
        event.payload
        for event in events
        if isinstance(event.payload, ToolResult) and event.payload.name == "routine_write"
    ]
    assert not rewrite.error
    assert with_code.error
    assert "code step" in with_code.output
    assert not (instance.path / "routines" / "invoices").exists()


class HeldModel:
    """Answer once the test releases it."""

    def __init__(self) -> None:
        self.release = asyncio.Event()

    def bind_tools(self, tools: Sequence[StructuredTool]) -> Self:
        return self

    async def astream(self, messages: Sequence[BaseMessage]) -> AsyncIterator[AIMessageChunk]:
        await self.release.wait()
        usage = {"input_tokens": 4, "output_tokens": 2, "total_tokens": 6}
        yield AIMessageChunk(content="Hello.", usage_metadata=usage)


def test_an_approved_reflection_resumes_only_once_the_instance_is_free(tmp_path: Path) -> None:
    instance = instance_at(tmp_path)
    reflection_stage(instance)
    consolidate(instance, "Invoices go out on the first")
    proposing = ReflectingModel(("remember", _PROPOSALS["remember"]))
    approval = reflect(instance, proposing)[-1]
    assert isinstance(approval.payload, ApprovalRequested)
    approve = ThreadApprovalRespondCommand(
        thread_id=approval.thread_id,
        approval_id=approval.payload.approval_id,
        decision=ApprovalDecision.APPROVE,
    )

    async def scenario() -> list[Event]:
        held = HeldModel()
        models = iter([held, proposing])
        log = EventLog(instance.manifest.state_dir)
        store = ThreadStore(instance.manifest.state_dir)
        runner = LangGraphRunner(instance, event_log=log, model_factory=lambda _: next(models))
        turns = Turns(
            store,
            log,
            runner,
            runner.prepare_for_turn,
            runner.permission_ceiling,
            no_recap,
            is_dreaming_stage=partial(is_dreaming_stage, instance),
        )
        await turns.wake(store.create(None).id, "Hello", UserOrigin())
        with pytest.raises(InstanceBusy):
            await turns.respond(approve)
        held.release.set()
        await turns.wait_idle()
        await turns.respond(approve)
        await turns.wait_idle()
        return log.stored(approval.thread_id)

    events = asyncio.run(scenario())

    assert outcome(events) is CompletionOutcome.WORK
    assert _remembered(instance)


def test_a_reflection_waiting_on_approval_does_not_block_a_consolidation(tmp_path: Path) -> None:
    instance = instance_at(tmp_path)
    reflection_stage(instance)
    consolidate(instance, "Invoices go out on the first")
    routine_file(
        instance,
        "description: Consolidate nightly\nschedule: * * * * *\nrun: consolidate",
        name="consolidation",
    )
    thursday = episode(instance, "Sent the invoices on Thursday")

    async def scenario() -> tuple[list[Event], list[Event]]:
        models = iter(
            [
                ReflectingModel(("remember", _PROPOSALS["remember"])),
                ConsolidatingModel([learned("Invoices go out on Thursdays", thursday)]),
            ]
        )
        clock = FakeClock(datetime(2026, 9, 6, 9, tzinfo=UTC))
        log = EventLog(instance.manifest.state_dir, clock=clock)
        runner = LangGraphRunner(instance, event_log=log, model_factory=lambda _: next(models))
        dispatcher = build_dispatcher(
            instance.manifest.state_dir,
            event_log=log,
            turns=ScheduledTurnConfig(
                TurnConfig(
                    runner.prepare_for_turn,
                    runner.permission_ceiling,
                    runner,
                    is_dreaming_stage=partial(is_dreaming_stage, instance),
                ),
                SchedulerConfig(instance, clock),
            ),
        )
        await dispatcher.scheduler.tick()
        accepted = await call(dispatcher, "routine.run", name="reflection")
        assert isinstance(accepted, AcceptedResult)
        parked = await until(log, accepted.thread_id, parked_or_closed)
        clock.now = datetime(2026, 9, 6, 9, 1, tzinfo=UTC)
        async with asyncio.timeout(5):
            await dispatcher.scheduler.tick()
        *_, nightly = [
            event.thread_id
            for event in log.all_events()
            if isinstance(event.payload, TurnStarted)
            and isinstance(event.payload.origin, RoutineOrigin)
            and event.payload.origin.name == "consolidation"
        ]
        return parked, log.stored(nightly)

    parked, nightly = asyncio.run(scenario())

    assert isinstance(parked[-1].payload, ApprovalRequested)
    assert outcome(nightly) is CompletionOutcome.WORK
    assert len(consolidated(nightly)) == 1


def test_a_reflection_turn_writes_no_episode(tmp_path: Path) -> None:
    instance = instance_at(tmp_path)
    reflection_stage(instance, mode="full-access")
    consolidate(instance, "Invoices go out on the first")
    before = GraphStore(instance.path).nodes()
    writer = RecapWriter(
        EventLog(instance.manifest.state_dir),
        GraphStore(instance.path),
        instance,
        model_factory=unused_recap,
    )

    async def scenario() -> list[Event]:
        turns, store, log = stage_turns(instance, ReflectingModel(), writer.schedule)
        thread_id = await wake_reflection(turns, store)
        await until(log, thread_id, lambda event: is_turn_closing(event.payload))
        await writer.drain()
        return log.stored(thread_id)

    stored = asyncio.run(scenario())

    [marker] = [event.payload for event in stored if isinstance(event.payload, MemoryRecapped)]
    assert marker.node is None
    assert marker.total == 0
    assert GraphStore(instance.path).nodes() == before


def test_a_new_instance_ships_a_weekly_reflection_stage_at_ask(tmp_path: Path) -> None:
    async def scenario() -> RoutineSummary:
        instance = load_instance(init_instance(tmp_path / "alice", model="openai:gpt-5"))
        clock = FakeClock(datetime(2026, 9, 3, 12, tzinfo=UTC))
        listed = await call(runtime(instance, clock), "routine.list")
        assert isinstance(listed, RoutineListResult)
        assert listed.warnings == ()
        [stage] = [routine for routine in listed.routines if routine.name == "reflection"]
        return stage

    stage = asyncio.run(scenario())

    assert stage.enabled
    assert stage.mode == "ask"
    assert stage.next_run == datetime(2026, 9, 6, 4, tzinfo=UTC)
    instance = load_instance(tmp_path / "alice")
    routine = load_routine(instance, RoutineName("reflection"))
    assert routine is not None
    assert routine.is_dreaming_stage
    consolidate(instance, "Invoices go out on the first")
    model = ReflectingModel()
    reflect(instance, model)
    assert DEFAULT_REFLECTION_LENS in model.wake
