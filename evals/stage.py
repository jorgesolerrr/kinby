"""Run one dreaming stage over a fixture instance, as the stage evals do."""

from contextlib import aclosing
from datetime import date
from pathlib import Path

from kinby.cli.client import ContractClient
from kinby.contracts import (
    ROUTINE_RUN,
    THREAD_SUBSCRIBE,
    ErrorEnvelope,
    MessageDelta,
    RoutineName,
    RoutineRunCommand,
    Scope,
    ThreadSubscribeCommand,
    TurnCompleted,
    TurnFailed,
    TurnInterrupted,
)
from kinby.core.dispatcher import ScheduledTurnConfig, TurnConfig, build_dispatcher
from kinby.core.events import EventLog
from kinby.core.scheduler import SchedulerConfig
from kinby.core.turn_runner import LangGraphRunner, ModelFactory
from kinby.instance import load_instance
from kinby.instance.layout import ROUTINE_FILE, ROUTINES_DIR

_SCOPES = tuple(Scope)


class StageEvalError(RuntimeError):
    """Report a failure while running a dreaming stage under an eval."""


def write_stage(
    instance_path: Path, stage: RoutineName, *, description: str, run: str, lens: str
) -> None:
    """The stage with no schedule, so only the eval fires it."""
    path = instance_path / ROUTINES_DIR / stage / ROUTINE_FILE
    path.parent.mkdir(parents=True)
    path.write_text(f"---\ndescription: {description}\nrun: {run}\n---\n{lens}\n", encoding="utf-8")


async def run_stage(
    instance_path: Path, stage: RoutineName, *, today: date, model_factory: ModelFactory
) -> str:
    """Run *stage* once as of *today* and return its dream report."""
    instance = load_instance(instance_path)
    event_log = EventLog(instance.manifest.state_dir)
    runner = LangGraphRunner(
        instance, event_log=event_log, model_factory=model_factory, today=lambda: today
    )
    dispatcher = build_dispatcher(
        instance.manifest.state_dir,
        event_log=event_log,
        turns=ScheduledTurnConfig(
            TurnConfig(runner.prepare_for_turn, runner.permission_ceiling, runner),
            SchedulerConfig(instance),
        ),
    )
    client = ContractClient(dispatcher.dispatch, dispatcher.subscribe, _SCOPES)
    accepted = await client.call(ROUTINE_RUN, RoutineRunCommand(name=stage))
    if isinstance(accepted, ErrorEnvelope):
        raise StageEvalError(f"Could not run {stage}: {accepted}")

    stream = await client.subscribe(
        THREAD_SUBSCRIBE,
        ThreadSubscribeCommand(thread_id=accepted.thread_id, after_sequence=accepted.sequence),
    )
    if isinstance(stream, ErrorEnvelope):
        raise StageEvalError(f"Could not subscribe to {stage}: {stream.message}")
    report: list[str] = []
    async with aclosing(stream.items) as items:
        async for item in items:
            if isinstance(item, ErrorEnvelope):
                raise StageEvalError(f"The stream of {stage} failed: {item.message}")
            if item.turn_id != accepted.turn_id:
                continue
            if isinstance(item.payload, MessageDelta):
                report.append(item.payload.text)
            elif isinstance(item.payload, TurnCompleted):
                return "".join(report)
            elif isinstance(item.payload, TurnFailed):
                raise StageEvalError(f"{stage} failed: {item.payload.message}")
            elif isinstance(item.payload, TurnInterrupted):
                raise StageEvalError(f"{stage} was interrupted.")
    raise StageEvalError(f"The stream of {stage} ended before its turn closed.")
