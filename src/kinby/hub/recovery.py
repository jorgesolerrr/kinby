"""Lifecycle recovery: restore the intended state of the containers that are still there.

Recovery inspects before it acts, and acts on one case only: an existing container whose
instance is recorded as intended running and whose last operation that changed the
container succeeded. A secrets replacement leaves the container where it is, so it does
not count. Recovery never adopts a container it does not know, never creates one, and
never repeats an operation that failed. See ADR 0051.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from uuid import UUID

from kinby.contracts import IntendedState, OperationState
from kinby.hub.models import (
    ContainerRuntime,
    LifecycleRecovery,
    RecoveredInstance,
    RecoveredState,
    RuntimeStatus,
)
from kinby.hub.registry import HubRegistry, ManagedInstance

#: Start one instance again and answer with the outcome of that lifecycle operation.
type RestoreStart = Callable[[UUID], Awaitable[OperationState]]
_STOPPED_STATES = frozenset({"absent", "created", "stopped", "failed"})


async def recover_lifecycle(
    registry: HubRegistry,
    runtime: ContainerRuntime,
    start: RestoreStart,
) -> LifecycleRecovery:
    """Reconcile every managed instance once, and report what each one came back as."""
    records = registry.managed_instances()
    owned = {record.runtime_id for record in records}
    labeled = await _labeled(runtime)
    recovered = [await _recover(record, registry, runtime, start) for record in records]
    return LifecycleRecovery(
        instances=tuple(recovered),
        unknown_containers=tuple(runtime_id for runtime_id in labeled if runtime_id not in owned),
    )


async def _labeled(runtime: ContainerRuntime) -> tuple[str, ...]:
    """A runtime the hub cannot reach names nothing: each instance reports that unavailability."""
    try:
        return tuple(await runtime.list())
    except Exception:
        return ()


async def _recover(
    record: ManagedInstance,
    registry: HubRegistry,
    runtime: ContainerRuntime,
    start: RestoreStart,
) -> RecoveredInstance:
    conflict = registry.conflicting_storage(record.instance_id, record.storage)
    if conflict is not None:
        return _at(
            record,
            RecoveredState.CONFLICTED,
            f'Storage source "{conflict.source}" is owned by another instance.',
        )
    try:
        status = await runtime.status(record.runtime_id)
    except Exception as exc:
        return _at(
            record,
            RecoveredState.UNAVAILABLE,
            f"The container runtime could not be reached: {exc or type(exc).__name__}",
        )
    if not record.prepared:
        return _finish_create(record, registry, status)
    if status.state == "absent":
        return _at(
            record,
            RecoveredState.MISSING,
            "The container is gone. Recreate it to bring it back.",
        )
    if status.state not in _STOPPED_STATES:
        return _running(record, status)
    if record.intended_state is IntendedState.STOPPED:
        return _at(record, RecoveredState.STOPPED, "The container is stopped, as intended.")
    return await _restore(record, registry, start)


def _running(record: ManagedInstance, status: RuntimeStatus) -> RecoveredInstance:
    if status.state == "running" and status.healthy is False:
        return _at(
            record,
            RecoveredState.UNHEALTHY,
            "The container is running and the instance reports itself unhealthy.",
        )
    if record.intended_state is IntendedState.STOPPED:
        return _at(
            record,
            RecoveredState.UNSTOPPED,
            "The container is still running against a recorded intent to stop it. "
            "Stop it again to take it down.",
        )
    return _at(record, RecoveredState.RUNNING, "The container is still running.")


def _finish_create(
    record: ManagedInstance,
    registry: HubRegistry,
    status: RuntimeStatus,
) -> RecoveredInstance:
    """Creation that reached its container only lost the outcome. Record it, do not repeat it."""
    if status.state == "absent":
        return _at(
            record,
            RecoveredState.INCOMPLETE,
            "Creation never reached this instance's container. Create the instance again.",
        )
    registry.mark_prepared(record.instance_id)
    return _at(
        record,
        RecoveredState.STOPPED,
        "Creation reached this instance's container before the hub stopped. "
        "The record now says so.",
    )


async def _restore(
    record: ManagedInstance,
    registry: HubRegistry,
    start: RestoreStart,
) -> RecoveredInstance:
    last = registry.last_operation(record.instance_id)
    if last is not None and last.state is OperationState.FAILED:
        return _at(
            record,
            RecoveredState.FAILED,
            f"The last {last.kind.value} operation failed, so the container was left as it was "
            "found. Ask for the operation again.",
        )
    outcome = await start(record.instance_id)
    if outcome is not OperationState.SUCCEEDED:
        return _at(
            record,
            RecoveredState.FAILED,
            "The container was stopped and could not be started again.",
        )
    return _at(
        record,
        RecoveredState.STARTED,
        "The container was stopped and is running again.",
    )


def _at(record: ManagedInstance, state: RecoveredState, detail: str) -> RecoveredInstance:
    return RecoveredInstance(instance_id=record.instance_id, state=state, detail=detail)
