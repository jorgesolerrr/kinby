import asyncio
import random
import threading
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Self
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessageChunk, BaseMessage
from langchain_core.tools import StructuredTool

from kinby.contracts import (
    ApprovalDecision,
    ApprovalRequested,
    CompletionOutcome,
    Event,
    MemoryConsolidated,
    MemoryRecapped,
    PermissionMode,
    RoutineName,
    RoutineOrigin,
    RoutineTrigger,
    ThreadApprovalRespondCommand,
    ThreadTurnInterruptCommand,
    ToolCall,
    ToolResult,
    TurnCompleted,
    TurnStarted,
    UserOrigin,
    is_turn_closing,
)
from kinby.core.events import EventLog
from kinby.core.threads import ThreadStore
from kinby.core.turn_runner import LangGraphRunner
from kinby.core.turns import ClosedTurnHook, Turns
from kinby.instance import Instance
from kinby.memory import (
    Episode,
    Fact,
    GraphStore,
    MemoryNode,
    NodeId,
    RecappedTurn,
    RecapWriter,
)
from kinby.memory.facade import new_node_id
from kinby.memory.graph import NodeNotFound
from tests.test_routines import instance_at, no_recap, routine_file, unused_recap

_LENS = "Look for lessons the episodes repeat."
_DAY = date(2026, 9, 1)


class ConsolidatingModel:
    """Call consolidate *calls* times in one response with *facts*, then end the turn."""

    def __init__(self, facts: list[dict[str, object]] | None = None, *, calls: int = 1) -> None:
        self.facts = facts or []
        self.calls = calls
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
        if len(self.messages) == 1:
            yield AIMessageChunk(
                content="",
                tool_calls=[
                    {
                        "id": f"consolidate-{uuid4()}",
                        "name": "consolidate",
                        "args": {"facts": self.facts},
                        "type": "tool_call",
                    }
                    for _ in range(self.calls)
                ],
                usage_metadata=usage,
            )
        else:
            yield AIMessageChunk(content="Consolidated.", usage_metadata=usage)


def consolidation_stage(instance: Instance, mode: str = "full-access") -> None:
    routine_file(
        instance,
        f"description: Consolidate episodes\nrun: consolidate\nmode: {mode}",
        _LENS,
        name="consolidation",
    )


def episode(
    instance: Instance,
    description: str,
    recorded_on: date = _DAY,
    subjects: tuple[str, ...] = ("billing",),
) -> NodeId:
    return GraphStore(instance.path).remember(
        Episode(
            node=new_node_id(recorded_on, description),
            date=recorded_on,
            description=description,
            subjects=subjects,
            body=f"## What happened\n{description}.",
            recaps=RecappedTurn(uuid4(), uuid4()),
            tools=(),
        )
    )


def fact(
    instance: Instance,
    description: str,
    subjects: tuple[str, ...],
    sources: tuple[NodeId, ...] = (),
) -> NodeId:
    return GraphStore(instance.path).remember(
        Fact(
            node=new_node_id(_DAY, description),
            date=_DAY,
            description=description,
            subjects=subjects,
            body=f"{description}.",
            thread=None,
            sources=sources,
        )
    )


def learned(description: str, *sources: NodeId) -> dict[str, object]:
    return {
        "description": description,
        "subjects": ["billing"],
        "body": f"{description}.",
        "sources": list(sources),
    }


async def _fire(
    instance: Instance,
    model: ConsolidatingModel,
    after_turn: ClosedTurnHook = no_recap,
) -> list[Event]:
    log = EventLog(instance.manifest.state_dir)
    store = ThreadStore(instance.manifest.state_dir)
    runner = LangGraphRunner(
        instance, event_log=log, model_factory=lambda _: model, today=lambda: _DAY
    )
    turns = Turns(
        store, log, runner, runner.prepare_for_turn, runner.permission_ceiling, after_turn
    )
    thread = store.create(None)
    await turns.wake(
        thread.id,
        "",
        RoutineOrigin(name=RoutineName("consolidation"), trigger=RoutineTrigger.SCHEDULED),
    )
    subscription = (await log.subscribe(thread.id)).items
    events: list[Event] = []
    async with asyncio.timeout(5):
        async for event in subscription:
            events.append(event)
            if is_turn_closing(event.payload):
                break
    await subscription.aclose()
    return events


def fire(instance: Instance, model: ConsolidatingModel) -> list[Event]:
    return asyncio.run(_fire(instance, model))


def consolidated(events: list[Event]) -> list[MemoryConsolidated]:
    return [event.payload for event in events if isinstance(event.payload, MemoryConsolidated)]


def completed(events: list[Event]) -> TurnCompleted:
    closing = events[-1].payload
    assert isinstance(closing, TurnCompleted)
    return closing


def test_a_firing_with_no_uncovered_episodes_is_no_work_at_zero_tokens(tmp_path: Path) -> None:
    instance = instance_at(tmp_path)
    consolidation_stage(instance)
    fact(instance, "Invoices go out on the first", ("billing",))
    model = ConsolidatingModel()

    events = fire(instance, model)

    assert model.messages == []
    assert completed(events).outcome is CompletionOutcome.NO_WORK
    assert completed(events).input_tokens == completed(events).output_tokens == 0
    assert consolidated(events) == []


def test_a_consolidation_writes_cited_facts_and_covers_the_whole_batch(tmp_path: Path) -> None:
    instance = instance_at(tmp_path)
    consolidation_stage(instance)
    monday = episode(instance, "Sent the invoices late on Monday")
    thursday = episode(instance, "Sent the invoices late on Thursday", _DAY + timedelta(days=3))
    unrelated = episode(instance, "Booked a dentist", subjects=("health",))
    model = ConsolidatingModel([learned("Invoices go out late", monday, thursday)])

    events = fire(instance, model)

    assert completed(events).outcome is CompletionOutcome.WORK
    [covering] = consolidated(events)
    assert covering.episodes == (monday, unrelated, thursday)
    [written] = covering.facts
    memory = GraphStore(instance.path)
    learned_fact = memory.open(written)
    assert isinstance(learned_fact, Fact)
    assert learned_fact.description == "Invoices go out late"
    assert learned_fact.subjects == ("billing",)
    assert learned_fact.sources == (monday, thursday)
    assert learned_fact.thread == events[0].thread_id
    assert [hit.node for hit in memory.recall("invoices")] == [written]
    assert completed(fire(instance, ConsolidatingModel())).outcome is CompletionOutcome.NO_WORK


def test_the_frame_groups_the_batch_by_subject_with_the_facts_on_it(tmp_path: Path) -> None:
    instance = instance_at(tmp_path)
    consolidation_stage(instance)
    old = episode(instance, "Moved billing to the new provider", _DAY - timedelta(days=30))
    fire(instance, ConsolidatingModel())
    area = fact(instance, "Billing lives in the payments service", ("billing",), (old,))
    elsewhere = fact(instance, "The dentist is on Main Street", ("health",))
    new = episode(instance, "Billing retries failed cards")
    model = ConsolidatingModel([learned("Billing lives in payments and retries cards", old, new)])

    events = fire(instance, model)

    assert _LENS in model.wake
    assert new in model.wake
    assert area in model.wake
    assert old in model.wake
    assert elsewhere not in model.wake
    assert "consolidate" in model.tools
    [covering] = consolidated(events)
    assert covering.episodes == (new,)
    learned_fact = GraphStore(instance.path).open(covering.facts[0])
    assert isinstance(learned_fact, Fact)
    assert learned_fact.sources == (old, new)


def test_a_batch_holds_the_fifty_oldest_and_the_next_firing_the_rest(tmp_path: Path) -> None:
    instance = instance_at(tmp_path)
    consolidation_stage(instance)
    days = list(range(60))
    random.Random(771).shuffle(days)
    by_day = {
        day: episode(instance, f"Episode of day {day}", _DAY + timedelta(days=day)) for day in days
    }
    oldest_first = [by_day[day] for day in range(60)]

    first = consolidated(fire(instance, ConsolidatingModel()))
    second = consolidated(fire(instance, ConsolidatingModel()))
    third = fire(instance, ConsolidatingModel())

    assert [covering.episodes for covering in first] == [tuple(oldest_first[:50])]
    assert [covering.episodes for covering in second] == [tuple(oldest_first[50:])]
    assert completed(third).outcome is CompletionOutcome.NO_WORK


def test_an_episode_recapped_late_with_an_older_date_is_still_offered(tmp_path: Path) -> None:
    instance = instance_at(tmp_path)
    consolidation_stage(instance)
    episode(instance, "Sent the invoices late")
    fire(instance, ConsolidatingModel())
    late = episode(instance, "Recapped at startup", _DAY - timedelta(days=5))
    model = ConsolidatingModel()

    events = fire(instance, model)

    assert late in model.wake
    assert [covering.episodes for covering in consolidated(events)] == [(late,)]


@pytest.mark.parametrize("cited", ["covered episode", "fact", "unknown node", "no source"])
def test_a_refused_call_writes_nothing_and_covers_nothing(tmp_path: Path, cited: str) -> None:
    instance = instance_at(tmp_path)
    consolidation_stage(instance)
    covered = episode(instance, "Moved billing", _DAY - timedelta(days=30))
    fire(instance, ConsolidatingModel())
    known = fact(instance, "Billing lives in payments", ("billing",))
    monday = episode(instance, "Sent the invoices late on Monday")
    thursday = episode(instance, "Sent the invoices late on Thursday", _DAY + timedelta(days=3))
    outside = {
        "covered episode": (covered,),
        "fact": (known,),
        "unknown node": (NodeId("2026-09-01-nothing-here"),),
        "no source": (),
    }[cited]
    memory = GraphStore(instance.path)
    before = memory.nodes()
    model = ConsolidatingModel(
        [learned("Invoices go out late", monday), learned("Billing is broken", *outside)]
    )

    events = fire(instance, model)

    [result] = [
        event.payload
        for event in events
        if isinstance(event.payload, ToolResult) and event.payload.name == "consolidate"
    ][1:]
    assert result.error
    assert all(source in result.output for source in outside)
    assert consolidated(events) == []
    assert memory.nodes() == before
    retry = ConsolidatingModel()
    [covering] = consolidated(fire(instance, retry))
    assert covering.episodes == (monday, thursday)


_FORGOTTEN_ON = _DAY - timedelta(days=10)


def forgotten(instance: Instance, *sources: NodeId) -> NodeId:
    """Cover *sources*, learn a billing fact from them, then forget it on _FORGOTTEN_ON."""
    fire(instance, ConsolidatingModel())
    node = fact(instance, "Invoices go out late", ("billing",), sources)
    GraphStore(instance.path).forget(node, _FORGOTTEN_ON)
    return node


def test_the_frame_lists_the_forgotten_facts_on_the_batch_subjects(tmp_path: Path) -> None:
    instance = instance_at(tmp_path)
    consolidation_stage(instance)
    old = episode(instance, "Sent the invoices late", _DAY - timedelta(days=30))
    gone = forgotten(instance, old)
    elsewhere = fact(instance, "The dentist is on Main Street", ("health",))
    GraphStore(instance.path).forget(elsewhere, _FORGOTTEN_ON)
    episode(instance, "Billing retries failed cards")
    model = ConsolidatingModel()

    fire(instance, model)

    assert gone in model.wake
    assert old in model.wake
    assert "forgotten" in model.wake
    assert elsewhere not in model.wake


@pytest.mark.parametrize("cited", ["all", "some", "an episode before the forget"])
def test_relearning_a_forgotten_fact_from_its_sources_is_refused(
    tmp_path: Path, cited: str
) -> None:
    instance = instance_at(tmp_path)
    consolidation_stage(instance)
    monday = episode(instance, "Sent the invoices late on Monday", _DAY - timedelta(days=30))
    thursday = episode(instance, "Sent the invoices late on Thursday", _DAY - timedelta(days=27))
    forgotten(instance, monday, thursday)
    late = episode(instance, "Sent the invoices late again", _FORGOTTEN_ON - timedelta(days=1))
    new = episode(instance, "Billing retries failed cards")
    memory = GraphStore(instance.path)
    before = memory.nodes()
    sources = {
        "all": (monday, thursday),
        "some": (monday,),
        "an episode before the forget": (monday, late),
    }[cited]
    model = ConsolidatingModel(
        [learned("Billing retries cards", new), learned("Invoices go out late", *sources)]
    )

    events = fire(instance, model)

    [result] = [
        event.payload
        for event in events
        if isinstance(event.payload, ToolResult) and event.payload.name == "consolidate"
    ][1:]
    assert result.error
    assert "Invoices go out late" in result.output
    assert consolidated(events) == []
    assert memory.nodes() == before


def test_a_forgotten_fact_is_learned_again_from_an_episode_after_the_forget(
    tmp_path: Path,
) -> None:
    instance = instance_at(tmp_path)
    consolidation_stage(instance)
    monday = episode(instance, "Sent the invoices late on Monday", _DAY - timedelta(days=30))
    forgotten(instance, monday)
    again = episode(instance, "Sent the invoices late again")
    model = ConsolidatingModel([learned("Invoices go out late", monday, again)])

    events = fire(instance, model)

    [covering] = consolidated(events)
    assert covering.episodes == (again,)
    learned_fact = GraphStore(instance.path).open(covering.facts[0])
    assert isinstance(learned_fact, Fact)
    assert learned_fact.sources == (monday, again)


def test_a_parked_consolidation_covers_its_batch_after_a_restart(tmp_path: Path) -> None:
    async def scenario() -> tuple[list[Event], NodeId, NodeId]:
        instance = instance_at(tmp_path)
        consolidation_stage(instance, mode="ask")
        monday = episode(instance, "Sent the invoices late on Monday")
        model = ConsolidatingModel([learned("Invoices go out late", monday)])
        log = EventLog(instance.manifest.state_dir)
        store = ThreadStore(instance.manifest.state_dir)
        runner = LangGraphRunner(instance, event_log=log, model_factory=lambda _: model)
        turns = Turns(
            store, log, runner, runner.prepare_for_turn, runner.permission_ceiling, no_recap
        )
        thread = store.create(None)
        await turns.wake(
            thread.id,
            "",
            RoutineOrigin(name=RoutineName("consolidation"), trigger=RoutineTrigger.SCHEDULED),
        )
        subscription = (await log.subscribe(thread.id)).items
        async with asyncio.timeout(5):
            while True:
                event = await anext(subscription)
                if isinstance(event.payload, ApprovalRequested):
                    approval = event.payload
                    break
        await subscription.aclose()
        waited = episode(instance, "Written while it waited", _DAY - timedelta(days=1))
        log = EventLog(instance.manifest.state_dir)
        restarted = LangGraphRunner(instance, event_log=log, model_factory=lambda _: model)
        resumed = Turns(
            store,
            log,
            restarted,
            restarted.prepare_for_turn,
            restarted.permission_ceiling,
            no_recap,
        )
        await resumed.respond(
            ThreadApprovalRespondCommand(
                thread_id=thread.id,
                approval_id=approval.approval_id,
                decision=ApprovalDecision.APPROVE,
            )
        )
        await resumed.wait_idle()
        return log.stored(thread.id), monday, waited

    events, monday, waited = asyncio.run(scenario())

    [covering] = consolidated(events)
    assert covering.episodes == (monday,)
    assert len(covering.facts) == 1
    instance = instance_at(tmp_path)
    consolidation_stage(instance)
    [later] = consolidated(fire(instance, ConsolidatingModel()))
    assert later.episodes == (waited,)


def test_consolidate_is_offered_only_on_a_stage_turn(tmp_path: Path) -> None:
    async def scenario() -> list[str]:
        instance = instance_at(tmp_path)
        model = ConsolidatingModel()
        log = EventLog(instance.manifest.state_dir)
        store = ThreadStore(instance.manifest.state_dir)
        runner = LangGraphRunner(instance, event_log=log, model_factory=lambda _: model)
        turns = Turns(
            store, log, runner, runner.prepare_for_turn, runner.permission_ceiling, no_recap
        )
        await turns.wake(store.create(None).id, "Hello", UserOrigin())
        await turns.wait_idle()
        return model.tools

    tools = asyncio.run(scenario())

    assert "remember" in tools
    assert "consolidate" not in tools


def test_a_stage_turn_writes_no_episode(tmp_path: Path) -> None:
    async def scenario() -> list[Event]:
        instance = instance_at(tmp_path)
        consolidation_stage(instance)
        monday = episode(instance, "Sent the invoices late on Monday")
        log = EventLog(instance.manifest.state_dir)
        writer = RecapWriter(log, GraphStore(instance.path), instance, model_factory=unused_recap)
        model = ConsolidatingModel([learned("Invoices go out late", monday)])
        events = await _fire(instance, model, writer.schedule)
        await writer.drain()
        return EventLog(instance.manifest.state_dir).stored(events[0].thread_id)

    stored = asyncio.run(scenario())

    markers = [event.payload for event in stored if isinstance(event.payload, MemoryRecapped)]
    assert len(markers) == 1
    assert markers[0].node is None
    assert markers[0].total == 0
    graph = GraphStore(instance_at(tmp_path).path).nodes()
    assert sorted(type(node).__name__ for node in graph) == ["Episode", "Fact"]


def test_a_firing_prunes_old_covered_episodes_no_live_fact_cites(tmp_path: Path) -> None:
    instance = instance_at(tmp_path)
    consolidation_stage(instance)
    old = _DAY - timedelta(days=100)
    uncited = episode(instance, "Sent the invoices late", old)
    cited = episode(instance, "Moved billing to the new provider", old)
    cited_by_forgotten = episode(instance, "Retried the failed cards", old)
    recent = episode(instance, "Billing retries failed cards", _DAY - timedelta(days=30))
    fire(instance, ConsolidatingModel())
    fact(instance, "Billing lives in payments", ("billing",), (cited,))
    GraphStore(instance.path).forget(
        fact(instance, "Cards are retried", ("billing",), (cited_by_forgotten,)), _FORGOTTEN_ON
    )
    uncovered = episode(instance, "Billed a new customer", old)

    events = fire(instance, ConsolidatingModel())

    memory = GraphStore(instance.path)
    episodes = {node.node for node in memory.nodes() if isinstance(node, Episode)}
    assert episodes == {cited, recent, uncovered}
    for pruned in (uncited, cited_by_forgotten):
        with pytest.raises(NodeNotFound, match="was not found"):
            memory.open(pruned)
    assert [covering.episodes for covering in consolidated(events)] == [(uncovered,)]


def test_pruning_runs_on_a_firing_with_no_work(tmp_path: Path) -> None:
    instance = instance_at(tmp_path)
    consolidation_stage(instance)
    episode(instance, "Sent the invoices late", _DAY - timedelta(days=100))
    fire(instance, ConsolidatingModel())

    events = fire(instance, ConsolidatingModel())

    assert completed(events).outcome is CompletionOutcome.NO_WORK
    assert GraphStore(instance.path).nodes() == []


def test_a_pruned_episode_is_not_written_again_by_the_recap(tmp_path: Path) -> None:
    async def scenario() -> None:
        instance = instance_at(tmp_path)
        with (instance.path / "kinby.toml").open("a") as manifest:
            manifest.write('[memory]\nrecap = "off"\n')
        consolidation_stage(instance)
        recorded = datetime(2026, 5, 1, tzinfo=UTC)
        log = EventLog(instance.manifest.state_dir, clock=lambda: recorded)
        thread_id, turn_id = uuid4(), uuid4()
        for payload in (
            TurnStarted(
                message="Send the invoices",
                model="test-model",
                permission_mode=PermissionMode.FULL_ACCESS,
            ),
            ToolCall(call_id="send-1", name="send", arguments={}),
            TurnCompleted(input_tokens=4, output_tokens=2),
        ):
            await log.append(thread_id, turn_id, payload)
        writer = RecapWriter(log, GraphStore(instance.path), instance)
        await writer.catch_up()
        await writer.drain()
        [recapped] = GraphStore(instance.path).nodes()
        await _fire(instance, ConsolidatingModel())
        await _fire(instance, ConsolidatingModel())
        restarted = EventLog(instance.manifest.state_dir)
        retry = RecapWriter(restarted, GraphStore(instance.path), instance)

        await retry.catch_up()
        await retry.drain()

        assert isinstance(recapped, Episode)
        assert recapped.date == recorded.date()
        assert GraphStore(instance.path).nodes() == []

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["ask", "read-only"])
def test_a_stage_that_may_not_write_unasked_prunes_nothing(tmp_path: Path, mode: str) -> None:
    instance = instance_at(tmp_path)
    consolidation_stage(instance)
    old = episode(instance, "Sent the invoices late", _DAY - timedelta(days=100))
    fire(instance, ConsolidatingModel())
    consolidation_stage(instance, mode=mode)

    events = fire(instance, ConsolidatingModel())

    assert completed(events).outcome is CompletionOutcome.NO_WORK
    assert [node.node for node in GraphStore(instance.path).nodes()] == [old]


def test_a_second_consolidate_call_in_one_turn_is_refused(tmp_path: Path) -> None:
    instance = instance_at(tmp_path)
    consolidation_stage(instance)
    monday = episode(instance, "Sent the invoices late on Monday")
    model = ConsolidatingModel([learned("Invoices go out late", monday)], calls=2)

    events = fire(instance, model)

    first, second = [
        event.payload
        for event in events
        if isinstance(event.payload, ToolResult) and event.payload.name == "consolidate"
    ][1:]
    assert not first.error
    assert second.error
    assert "already" in second.output
    [covering] = consolidated(events)
    facts = [node for node in GraphStore(instance.path).nodes() if isinstance(node, Fact)]
    assert [fact.node for fact in facts] == list(covering.facts)


class WaitingGraphStore(GraphStore):
    """Hold each write until the test lets it finish."""

    writing = threading.Event()
    release = threading.Event()

    def remember(self, memory: MemoryNode) -> NodeId:
        if isinstance(memory, Fact):
            self.writing.set()
            self.release.wait()
        return super().remember(memory)


def test_an_interrupt_during_the_writes_still_covers_the_batch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> tuple[MemoryConsolidated, NodeId]:
        instance = instance_at(tmp_path)
        consolidation_stage(instance)
        monday = episode(instance, "Sent the invoices late on Monday")
        monkeypatch.setattr("kinby.core.turn_runner.GraphStore", WaitingGraphStore)
        model = ConsolidatingModel([learned("Invoices go out late", monday)])
        log = EventLog(instance.manifest.state_dir)
        store = ThreadStore(instance.manifest.state_dir)
        runner = LangGraphRunner(instance, event_log=log, model_factory=lambda _: model)
        turns = Turns(
            store, log, runner, runner.prepare_for_turn, runner.permission_ceiling, no_recap
        )
        thread = store.create(None)
        await turns.wake(
            thread.id,
            "",
            RoutineOrigin(name=RoutineName("consolidation"), trigger=RoutineTrigger.SCHEDULED),
        )
        await asyncio.to_thread(WaitingGraphStore.writing.wait)
        await turns.interrupt(ThreadTurnInterruptCommand(thread_id=thread.id))
        subscription = (await log.subscribe(thread.id)).items
        WaitingGraphStore.release.set()
        async with asyncio.timeout(5):
            while True:
                event = await anext(subscription)
                if isinstance(event.payload, MemoryConsolidated):
                    break
        await subscription.aclose()
        await turns.wait_idle()
        return event.payload, monday

    covering, monday = asyncio.run(scenario())

    assert covering.episodes == (monday,)
    [written] = covering.facts
    assert GraphStore(tmp_path).open(written).description == "Invoices go out late"
