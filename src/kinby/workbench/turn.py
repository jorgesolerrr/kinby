"""An agent step's kinby turn on a workbench, which runs with what a workbench has instead of an
instance: the run's folder, the shipped gate policy, and the skills it is offered."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from pathlib import Path
from uuid import UUID, uuid4

from kinby.contracts import (
    AnsweredAsk,
    ApiUse,
    ApprovalRequested,
    ErrorCode,
    Event,
    FeedbackPolicy,
    ModelCompleted,
    Payload,
    RecapPolicy,
    StepEnding,
    StepResult,
    StepRunCommand,
    StepUsage,
    TokenTotals,
    TurnCompleted,
    TurnFailed,
    TurnStarted,
    WaitingApproval,
)
from kinby.core.errors import CoreError
from kinby.core.events import EventLog
from kinby.core.pricing import price_map, token_cost
from kinby.core.prompt import render_step_message
from kinby.core.steps import last_message, step_failed
from kinby.core.turn_runner import LangGraphRunner, ModelFactory
from kinby.core.turns import ApprovalAnswer, ParkedTurn, PreparedTurnRequest, TurnContext
from kinby.instance import (
    Budgets,
    Conventions,
    Feedback,
    Instance,
    Manifest,
    Memory,
    Models,
    Tools,
    Workspace,
)
from kinby.instance.dataclasses import Compaction
from kinby.instance.permissions import SHIPPED_POLICY
from kinby.plugins.skills import Skill

#: The default tools a workbench's turn is offered: bash and the file tools.
_TOOLS = Tools(off=frozenset({"web_search", "web_fetch"}))

_logger = logging.getLogger(__name__)


async def run_turn(
    model: str,
    prompt: str,
    answered: AnsweredAsk | None,
    command: StepRunCommand,
    folder: Path,
    skills: Sequence[Skill],
    state: Path,
    transcript: Path,
    model_factory: ModelFactory,
) -> StepResult:
    """Run the step's turn in *folder* with *model*, its events kept in *state* and appended to
    *transcript*. The turn is on a thread of its own, which no instance holds. An *answered* ask
    resumes the turn that parked on it, from its checkpoint.

    A turn the gate parks on an ask ends the attempt asked. What the agent said last is only the
    summary, and the model calls of the attempt are what it spent.
    """
    instance = Instance(
        path=state, manifest=_manifest(state, folder, model), matching_rule="explicit directory"
    )
    state.mkdir(parents=True, exist_ok=True)
    log = EventLog(state)
    runner = LangGraphRunner(
        instance,
        event_log=log,
        model_factory=model_factory,
        gate_policy=SHIPPED_POLICY,
        workbench_skills=tuple(skills),
    )
    preparation = runner.prepare_for_turn()
    if answered is None:
        # The thread holds this turn alone, so the turn's id is all an answer needs to resume it.
        turn_id = uuid4()
        turn = PreparedTurnRequest(
            thread_id=turn_id,
            turn_id=turn_id,
            message=render_step_message(prompt, command.work_item, command.results),
            model=preparation.model,
            permission_mode=preparation.default_mode,
            origin=command.origin,
            system_prompt=preparation.system_prompt,
        )
    else:
        turn_id = answered.ask.turn_id
        restored = await runner.restore(turn_id, turn_id)
        if restored is None:
            return step_failed("The turn this answer resumes is parked on no ask any more.")
        turn = restored
    events: list[Event] = []

    async def emit(payload: Payload) -> Event:
        event = await log.append(turn.thread_id, turn.turn_id, payload)
        events.append(event)
        line = event.model_dump_json() + "\n"
        await asyncio.to_thread(_append, transcript, line)
        return event

    context = TurnContext(preparation.budgets, emit)
    try:
        if answered is None:
            await emit(
                TurnStarted(
                    message=turn.message,
                    model=turn.model,
                    permission_mode=turn.permission_mode,
                    origin=turn.origin,
                    prompt_version=preparation.prompt_version,
                )
            )
            outcome = await runner.run(turn, context)
        else:
            answer = ApprovalAnswer(answered.answer.decision, answered.answer.reason)
            outcome = await runner.resume(turn, answer, context)
    except CoreError as exc:
        code, reason = exc.code, str(exc)
    except Exception:
        _logger.exception("The model turn failed.")
        code, reason = ErrorCode.INTERNAL, "The model turn failed unexpectedly."
    else:
        usage, cost = _spent(events, instance, turn.model)
        spent = StepUsage(api=ApiUse(**usage.model_dump(), cost=cost))
        if isinstance(outcome, ParkedTurn):
            return _asked(turn_id, events, spent)
        await emit(TurnCompleted(outcome=outcome.outcome, cost=cost, **usage.model_dump()))
        return StepResult(ending=StepEnding.CLEAN, summary=last_message(events), usage=spent)
    usage, cost = _spent(events, instance, turn.model)
    await emit(TurnFailed(code=code, message=reason, **usage.model_dump()))
    said = StepResult(
        ending=StepEnding.FAILED,
        summary=last_message(events),
        usage=StepUsage(api=ApiUse(**usage.model_dump(), cost=cost)),
    )
    return step_failed(f"The turn failed: {reason}", said)


def _asked(turn_id: UUID, events: Sequence[Event], usage: StepUsage) -> StepResult:
    """The attempt of a turn that parked on the gate's ask, which the user answers."""
    request = next(
        (
            event.payload
            for event in reversed(events)
            if isinstance(event.payload, ApprovalRequested)
        ),
        None,
    )
    if request is None:
        return step_failed("The turn parked on something other than an ask of the gate.")
    return StepResult(
        ending=StepEnding.ASKED,
        summary=f"The turn asks to call {request.name}.",
        usage=usage,
        ask=WaitingApproval(turn_id=turn_id, request=request),
    )


def _spent(
    events: Sequence[Event], instance: Instance, model: str
) -> tuple[TokenTotals, float | None]:
    """The tokens of the turn's model calls and what they cost, None for a model with no price."""
    usage = _usage(events)
    price = price_map(instance.manifest.prices).get(model)
    return usage, None if price is None else token_cost(usage, price)


def _usage(events: Sequence[Event]) -> TokenTotals:
    """The tokens of the turn's model calls, added up."""
    calls = [event.payload for event in events if isinstance(event.payload, ModelCompleted)]
    return TokenTotals(
        input_tokens=sum(call.input_tokens for call in calls),
        output_tokens=sum(call.output_tokens for call in calls),
        cache_read_tokens=sum(call.cache_read_tokens for call in calls),
        cache_creation_tokens=sum(call.cache_creation_tokens for call in calls),
    )


def _manifest(state: Path, folder: Path, model: str) -> Manifest:
    """What an instance's manifest would hold for the turn: its model, the run's folder as its
    workspace, bash and the file tools, and no budget of its own, which is the line's."""
    return Manifest(
        id="workbench",
        persona_name=None,
        state_dir=state,
        models=Models(main=model, recap=model, embed=None),
        workspace=Workspace(
            path=folder, source=None, conventions=Conventions((), ()), snapshots=False
        ),
        memory=Memory(recap=RecapPolicy.TRACE_ONLY),
        feedback=Feedback(ask=FeedbackPolicy.OFF),
        compaction=Compaction(trigger=None),
        tools=_TOOLS,
        budgets=Budgets(),
        prices={},
        windows={},
    )


def _append(path: Path, line: str) -> None:
    with path.open("a", encoding="utf-8") as file:
        file.write(line)
