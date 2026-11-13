"""An agent step's kinby turn on a workbench, which runs with what a workbench has instead of an
instance: the run's folder, the shipped gate policy, and the skills it is offered."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from pathlib import Path
from uuid import uuid4

from kinby.contracts import (
    ApiUse,
    ApprovalDecision,
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
)
from kinby.core.errors import CoreError
from kinby.core.events import EventLog
from kinby.core.pricing import price_map, token_cost
from kinby.core.prompt import render_step_message
from kinby.core.steps import last_message, step_failed
from kinby.core.turn_runner import LangGraphRunner, ModelFactory, init_chat_model
from kinby.core.turns import (
    ApprovalAnswer,
    ParkedTurn,
    PreparedTurnRequest,
    TurnContext,
    TurnOutcome,
)
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

#: The answer every ask of a workbench's turn gets, until an ask reaches the user (#1352).
_REFUSED = ApprovalAnswer(
    ApprovalDecision.DENY,
    "no one can answer an ask on a workbench yet, so every ask is refused. Go on without it.",
)
#: The default tools a workbench's turn is offered: bash and the file tools.
_TOOLS = Tools(off=frozenset({"web_search", "web_fetch"}))

_logger = logging.getLogger(__name__)


async def run_turn(
    model: str,
    prompt: str,
    command: StepRunCommand,
    folder: Path,
    skills: Sequence[Skill],
    state: Path,
    transcript: Path,
    model_factory: ModelFactory | None,
) -> StepResult:
    """Run the step's turn in *folder* with *model*, its events kept in *state* and appended to
    *transcript*, calling it through *model_factory* or else as an instance's turn does. The turn
    is on a thread of its own, which no instance holds.

    What the agent said last is only the summary, and its model calls are what the step spent.
    """
    instance = Instance(
        path=state, manifest=_manifest(state, folder, model), matching_rule="explicit directory"
    )
    state.mkdir(parents=True, exist_ok=True)
    log = EventLog(state)
    runner = LangGraphRunner(
        instance,
        event_log=log,
        model_factory=model_factory or init_chat_model,
        gate_policy=SHIPPED_POLICY,
        workbench_skills=tuple(skills),
    )
    preparation = runner.prepare_for_turn()
    thread_id, turn_id = uuid4(), uuid4()
    events: list[Event] = []

    async def emit(payload: Payload) -> Event:
        event = await log.append(thread_id, turn_id, payload)
        events.append(event)
        line = event.model_dump_json() + "\n"
        await asyncio.to_thread(_append, transcript, line)
        return event

    message = render_step_message(prompt, command.work_item, command.results)
    turn = PreparedTurnRequest(
        thread_id=thread_id,
        turn_id=turn_id,
        message=message,
        model=preparation.model,
        permission_mode=preparation.default_mode,
        origin=command.origin,
        system_prompt=preparation.system_prompt,
    )
    await emit(
        TurnStarted(
            message=message,
            model=turn.model,
            permission_mode=turn.permission_mode,
            origin=turn.origin,
            prompt_version=preparation.prompt_version,
        )
    )
    context = TurnContext(preparation.budgets, emit)
    try:
        outcome = await _refusing_asks(runner, turn, context)
    except CoreError as exc:
        code, reason = exc.code, str(exc)
    except Exception:
        _logger.exception("The model turn failed.")
        code, reason = ErrorCode.INTERNAL, "The model turn failed unexpectedly."
    else:
        usage, cost = _spent(events, model)
        await emit(TurnCompleted(outcome=outcome.outcome, cost=cost, **usage.model_dump()))
        return StepResult(
            ending=StepEnding.CLEAN,
            summary=last_message(events),
            usage=StepUsage(api=ApiUse(**usage.model_dump(), cost=cost)),
        )
    usage, cost = _spent(events, model)
    await emit(TurnFailed(code=code, message=reason, **usage.model_dump()))
    said = StepResult(
        ending=StepEnding.FAILED,
        summary=last_message(events),
        usage=StepUsage(api=ApiUse(**usage.model_dump(), cost=cost)),
    )
    return step_failed(f"The turn failed: {reason}", said)


async def _refusing_asks(
    runner: LangGraphRunner, turn: PreparedTurnRequest, context: TurnContext
) -> TurnOutcome:
    """Run the turn to its end, resuming it with a refusal each time it parks on an ask."""
    outcome = await runner.run(turn, context)
    while isinstance(outcome, ParkedTurn):
        outcome = await runner.resume(turn, _REFUSED, context)
    return outcome


def _spent(events: Sequence[Event], model: str) -> tuple[TokenTotals, float | None]:
    """The tokens of the turn's model calls, added up, however the turn ended, and their cost at
    *model*'s price. None when kinby knows no price for it."""
    calls = [event.payload for event in events if isinstance(event.payload, ModelCompleted)]
    usage = TokenTotals(
        input_tokens=sum(call.input_tokens for call in calls),
        output_tokens=sum(call.output_tokens for call in calls),
        cache_read_tokens=sum(call.cache_read_tokens for call in calls),
        cache_creation_tokens=sum(call.cache_creation_tokens for call in calls),
    )
    price = price_map().get(model)
    return usage, None if price is None else token_cost(usage, price)


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
