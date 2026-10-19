import asyncio
from collections.abc import AsyncIterator, Callable, Sequence
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Self
from uuid import UUID, uuid4

import pytest
from langchain_core.messages import AIMessageChunk, BaseMessage
from langchain_core.tools import StructuredTool

from kinby.contracts import (
    CompletionOutcome,
    Event,
    FactoryRunOrigin,
    FactoryRunStatus,
    MemoryRecalled,
    NodeId,
    Origin,
    Payload,
    RoutineListResult,
    RoutineName,
    RoutineOrigin,
    RoutineSummary,
    RoutineTrigger,
    RunStop,
    RunStopped,
    StepAttempt,
    StepEnding,
    ToolResult,
    TurnCompleted,
    TurnRated,
    TurnStarted,
    TurnVerdict,
    UserOrigin,
    is_turn_closing,
)
from kinby.core.events import EventLog
from kinby.core.threads import ThreadStore
from kinby.core.turn_runner import LangGraphRunner
from kinby.core.turns import Turns
from kinby.instance import Instance, init_from_template, init_instance, load_instance
from kinby.instance.init import DEFAULT_LEARNING_REVIEW_LENS
from kinby.memory import GraphStore
from kinby.memory.facade import Episode, Fact, RecappedRun, RecappedTurn, new_node_id
from kinby.plugins.routines import load_routine
from tests.test_consolidation import fact
from tests.test_routines import RoutineModel, fire, instance_at, no_recap, routine_file
from tests.test_scheduler import FakeClock, call, runtime

_LENS = "Look for lessons the instance keeps forgetting."
_REPORT = "Dream report: one repeated mistake."
#: A Sunday. The week under review runs from the Sunday before through Saturday.
_TODAY = date(2026, 9, 6)
_IN_THE_WEEK = datetime(2026, 9, 2, 10, tzinfo=UTC)
_BEFORE_THE_WEEK = datetime(2026, 8, 25, 10, tzinfo=UTC)


class ReviewingModel:
    """End the turn with the dream report."""

    def __init__(self) -> None:
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
        yield AIMessageChunk(content=_REPORT, usage_metadata=usage)


def review_stage(instance: Instance) -> None:
    routine_file(
        instance,
        "description: Review the week's learning\nrun: learning_review",
        _LENS,
        name="review",
    )


def record(instance: Instance, at: datetime, thread_id: UUID, *payloads: Payload) -> UUID:
    """Append *payloads* as one turn of *thread_id* at *at*, and return the turn."""
    log = EventLog(instance.manifest.state_dir, clock=FakeClock(at))
    turn_id = uuid4()

    async def append() -> None:
        for payload in payloads:
            await log.append(thread_id, turn_id, payload)

    asyncio.run(append())
    return turn_id


def work_turn(
    instance: Instance,
    message: str,
    *payloads: Payload,
    at: datetime = _IN_THE_WEEK,
    origin: Origin | None = None,
) -> tuple[UUID, UUID]:
    """A closed turn on a new thread, with *payloads* between its start and its close."""
    thread_id = uuid4()
    turn_id = record(
        instance,
        at,
        thread_id,
        TurnStarted(message=message, model="openai:gpt-5", origin=origin or UserOrigin()),
        *payloads,
        TurnCompleted(input_tokens=4, output_tokens=2),
    )
    return thread_id, turn_id


def failed_tool(output: str) -> ToolResult:
    return ToolResult(call_id=str(uuid4()), name="bash", output=output, error=True)


def learned_on(instance: Instance, day: date, description: str, subject: str) -> NodeId:
    return GraphStore(instance.path).remember(
        Fact(
            node=new_node_id(day, description),
            date=day,
            description=description,
            subjects=(subject,),
            body=f"{description}.",
            thread=None,
        )
    )


def rated(verdict: TurnVerdict, reason: str | None = None) -> TurnRated:
    return TurnRated(verdict=verdict, reason=reason)


def turn_episode(
    instance: Instance, thread_id: UUID, turn_id: UUID, description: str, subject: str
) -> NodeId:
    return GraphStore(instance.path).remember(
        Episode(
            node=new_node_id(_IN_THE_WEEK.date(), description),
            date=_IN_THE_WEEK.date(),
            description=description,
            subjects=(subject,),
            body=f"## What happened\n{description}.",
            recaps=RecappedTurn(thread_id, turn_id),
            tools=("bash",),
        )
    )


def run_episode(instance: Instance, run_id: UUID, description: str, subject: str) -> NodeId:
    return GraphStore(instance.path).remember(
        Episode(
            node=new_node_id(_IN_THE_WEEK.date(), description),
            date=_IN_THE_WEEK.date(),
            description=description,
            subjects=(subject,),
            body=f"## What happened\n{description}.",
            recaps=RecappedRun(run_id, 1),
            tools=(),
        )
    )


def attempt(step: str, number: int, summary: str) -> StepAttempt:
    return StepAttempt(
        step=step,
        attempt=number,
        ending=StepEnding.CLEAN,
        outcome=None,
        values={},
        summary=summary,
        started_at=_IN_THE_WEEK,
        ended_at=_IN_THE_WEEK,
    )


def factory_run(instance: Instance, *attempts: StepAttempt, recalled: NodeId | None = None) -> UUID:
    """A run that stopped as done in the week after *attempts*, whose coding session may have
    recalled a fact."""
    run_id = uuid4()
    work_turn(
        instance,
        "Plan the ticket.",
        origin=FactoryRunOrigin(factory="software", run_id=run_id, step="plan"),
    )
    if recalled is not None:
        record(
            instance,
            _IN_THE_WEEK,
            run_id,
            MemoryRecalled(nodes=(recalled,), step="implement", attempt=1),
        )
    stop = RunStop(
        run_id=run_id,
        factory="software",
        work_item={},
        status=FactoryRunStatus.DONE,
        generation=1,
        attempts=list(attempts),
    )
    record(instance, _IN_THE_WEEK, run_id, RunStopped(stop=stop))
    return run_id


def review(instance: Instance, model: ReviewingModel) -> list[Event]:
    """Fire the learning review on _TODAY until its turn closes."""

    async def scenario() -> list[Event]:
        log = EventLog(instance.manifest.state_dir)
        store = ThreadStore(instance.manifest.state_dir)
        runner = LangGraphRunner(
            instance, event_log=log, model_factory=lambda _: model, today=lambda: _TODAY
        )
        turns = Turns(
            store, log, runner, runner.prepare_for_turn, runner.permission_ceiling, no_recap
        )
        thread = store.create(None)
        subscription = (await log.subscribe(thread.id)).items
        await turns.wake(
            thread.id,
            "",
            RoutineOrigin(
                name=RoutineName("review"), trigger=RoutineTrigger.SCHEDULED, dreaming_stage=True
            ),
        )
        events: list[Event] = []
        async with asyncio.timeout(5):
            async for event in subscription:
                events.append(event)
                if is_turn_closing(event.payload):
                    break
        await subscription.aclose()
        return events

    return asyncio.run(scenario())


def completed(events: list[Event]) -> TurnCompleted:
    closing = events[-1].payload
    assert isinstance(closing, TurnCompleted)
    return closing


def test_a_week_with_nothing_gone_badly_is_no_work_with_no_model_call(tmp_path: Path) -> None:
    instance = instance_at(tmp_path)
    review_stage(instance)
    work_turn(instance, "Deploy the site.", rated(TurnVerdict.GOOD))
    work_turn(instance, "Deploy the site.", failed_tool("denied"), at=_BEFORE_THE_WEEK)
    work_turn(
        instance,
        "Consolidate.",
        failed_tool("denied"),
        origin=RoutineOrigin(
            name=RoutineName("consolidation"),
            trigger=RoutineTrigger.SCHEDULED,
            dreaming_stage=True,
        ),
    )
    factory_run(instance, attempt("plan", 1, "Planned."), attempt("implement", 1, "Built."))
    model = ReviewingModel()

    events = review(instance, model)

    assert model.messages == []
    assert completed(events).outcome is CompletionOutcome.NO_WORK
    assert completed(events).input_tokens == completed(events).output_tokens == 0


def test_the_frame_holds_only_the_bad_turns_and_runs_with_their_facts_and_recalls(
    tmp_path: Path,
) -> None:
    instance = instance_at(tmp_path)
    review_stage(instance)
    deploys = fact(instance, "Deploys need the staging key", ("deploys",))
    invoices = fact(instance, "Invoices go out on the first", ("invoicing",))
    reviews = fact(instance, "Run the linter before review", ("reviews",))
    unrelated = fact(instance, "The user drinks tea", ("tea",))
    failed_thread, failed_turn = work_turn(
        instance, "Deploy the site.", failed_tool("permission denied: staging")
    )
    turn_episode(instance, failed_thread, failed_turn, "Deployed without the key", "deploys")
    rated_thread, rated_turn = work_turn(
        instance,
        "Send the invoices.",
        MemoryRecalled(nodes=(invoices,)),
        rated(TurnVerdict.BAD, "Sent them on the fifth."),
    )
    clean_thread, clean_turn = work_turn(instance, "Book a dentist.")
    sent_back = factory_run(
        instance,
        attempt("implement", 1, "Built it."),
        attempt("review", 1, "The linter fails."),
        attempt("implement", 2, "Fixed the lint."),
        recalled=reviews,
    )
    run_episode(instance, sent_back, "Review sent the change back for lint", "reviews")
    clean_run = factory_run(instance, attempt("implement", 1, "Built it."))
    model = ReviewingModel()

    events = review(instance, model)

    assert completed(events).outcome is CompletionOutcome.WORK
    assert _LENS in model.wake
    for bad in (failed_thread, failed_turn, rated_thread, rated_turn, sent_back):
        assert str(bad) in model.wake
    assert "permission denied: staging" in model.wake
    assert "Sent them on the fifth." in model.wake
    assert "The linter fails." in model.wake
    assert deploys in model.wake
    assert invoices in model.wake
    assert reviews in model.wake
    for clean in (clean_thread, clean_turn, clean_run, unrelated):
        assert str(clean) not in model.wake
    assert "repeated mistake" in model.wake
    assert "ignored lesson" in model.wake


def test_the_stage_is_offered_no_write_tool_and_writes_nothing(tmp_path: Path) -> None:
    instance = instance_at(tmp_path)
    review_stage(instance)
    fact(instance, "Deploys need the staging key", ("deploys",))
    work_turn(instance, "Deploy the site.", failed_tool("permission denied"))
    before = GraphStore(instance.path).nodes()
    model = ReviewingModel()

    review(instance, model)

    assert "memory_search" in model.tools
    assert not {"remember", "forget", "skill_write", "routine_write", "consolidate"} & set(
        model.tools
    )
    assert GraphStore(instance.path).nodes() == before


@pytest.mark.parametrize(
    "initialize",
    [
        lambda path: init_instance(path, model="openai:gpt-5"),
        lambda path: init_from_template(path, {}, model="openai:gpt-5", settings={}),
    ],
    ids=["kinby init", "instance template"],
)
def test_a_new_instance_ships_a_weekly_learning_review_after_reflection(
    tmp_path: Path, initialize: Callable[[Path], Path]
) -> None:
    async def scenario() -> tuple[RoutineSummary, RoutineSummary]:
        instance = load_instance(initialize(tmp_path / "alice"))
        clock = FakeClock(datetime(2026, 9, 3, 12, tzinfo=UTC))
        listed = await call(runtime(instance, clock), "routine.list")
        assert isinstance(listed, RoutineListResult)
        assert listed.warnings == ()
        stages = {routine.name: routine for routine in listed.routines}
        return stages[RoutineName("reflection")], stages[RoutineName("learning-review")]

    reflection, stage = asyncio.run(scenario())

    assert stage.enabled
    assert reflection.next_run is not None
    assert stage.next_run is not None
    assert reflection.next_run < stage.next_run < datetime(2026, 9, 7, tzinfo=UTC)
    instance = load_instance(tmp_path / "alice")
    routine = load_routine(instance, RoutineName("learning-review"))
    assert routine is not None
    assert routine.is_dreaming_stage
    assert routine.budgets.tokens is None
    assert routine.prompt.strip() == DEFAULT_LEARNING_REVIEW_LENS


def test_a_fact_learned_on_or_after_the_day_of_the_work_does_not_match_it(
    tmp_path: Path,
) -> None:
    instance = instance_at(tmp_path)
    review_stage(instance)
    before = learned_on(instance, date(2026, 9, 1), "Deploys need the staging key", "deploys")
    same_day = learned_on(instance, _IN_THE_WEEK.date(), "Deploys run from main", "deploys")
    later = learned_on(instance, date(2026, 9, 3), "Deploys need a tag", "deploys")
    thread_id, turn_id = work_turn(instance, "Deploy the site.", failed_tool("denied"))
    turn_episode(instance, thread_id, turn_id, "Deployed without the key", "deploys")
    model = ReviewingModel()

    review(instance, model)

    assert before in model.wake
    assert same_day not in model.wake
    assert later not in model.wake


def test_an_instance_tool_named_review_still_runs_as_a_routine_code_step(tmp_path: Path) -> None:
    instance = instance_at(tmp_path)
    routine_file(instance, "description: Review pull requests\nrun: review")
    tools = tmp_path / "tools"
    tools.mkdir()
    (tools / "review.py").write_text('''from kinby.plugins import tool
@tool(write=False)
def review() -> str:
    """Review the open pull requests."""
    return "Two pull requests wait on review"
''')
    model = RoutineModel()

    asyncio.run(fire(instance, model))

    assert "Two pull requests wait on review" in str(model.messages[0][-1].content)
