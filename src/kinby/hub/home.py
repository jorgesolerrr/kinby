"""The writes the home instance may ask the hub for, one per hub tool (ADR 0096)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from uuid import UUID

from pydantic import JsonValue, TypeAdapter, ValidationError

from kinby.contracts import (
    PROMPT_SET,
    ROUTINE_DELETE,
    ROUTINE_RUN,
    ROUTINE_SET_ENABLED,
    ROUTINE_WRITE,
    ContractModel,
    FactoryEditCommand,
    InstanceCreateCommand,
    InstancePicksSetCommand,
    InstanceRecreateCommand,
    InstanceStartCommand,
    InstanceStopCommand,
    LibraryGitAddCommand,
    LibrarySkillMoveCommand,
    LibrarySkillWriteCommand,
    Method,
)
from kinby.core.errors import PermissionDenied
from kinby.plugins.hub import (
    LifecycleAction,
    factory_write,
    instance_ask,
    instance_create,
    instance_lifecycle,
    instance_picks_set,
    instance_prompt_set,
    instance_routine_delete,
    instance_routine_run,
    instance_routine_set_enabled,
    instance_routine_write,
    library_git_add,
    library_move,
    library_skill_write,
)


@dataclass(frozen=True)
class RelayedWrite:
    """An instance method the hub calls on the target with the target's own control token."""

    instance_id: UUID
    method: Method
    command: ContractModel


class InstanceAsk(ContractModel):
    """A turn the hub starts in the target, on a new thread, with the home instance as origin."""

    instance_id: UUID
    message: str


class LibraryMove(LibrarySkillMoveCommand):
    """A move the hub runs only when it brings the diff the user approved."""

    diff: str


type HomeWrite = (
    InstanceCreateCommand
    | InstanceStartCommand
    | InstanceStopCommand
    | InstanceRecreateCommand
    | InstancePicksSetCommand
    | RelayedWrite
    | InstanceAsk
    | LibrarySkillWriteCommand
    | LibraryGitAddCommand
    | LibraryMove
    | FactoryEditCommand
)

#: The hub tools whose write runs in the target instance, and the instance method each calls.
_RELAYED_WRITES: Mapping[str, Method] = {
    instance_prompt_set.name: PROMPT_SET,
    instance_routine_write.name: ROUTINE_WRITE,
    instance_routine_set_enabled.name: ROUTINE_SET_ENABLED,
    instance_routine_run.name: ROUTINE_RUN,
    instance_routine_delete.name: ROUTINE_DELETE,
}

_INSTANCE_ID = TypeAdapter(UUID)


class _LifecycleCall(ContractModel):
    instance_id: UUID
    action: LifecycleAction


def home_write(tool: str, arguments: Mapping[str, JsonValue]) -> HomeWrite:
    """The hub operation one hub tool call asks for. A secret's value never comes this way."""
    try:
        match tool:
            case instance_create.name:
                create = InstanceCreateCommand.model_validate(arguments)
                if create.secrets or create.save_to_hub:
                    raise PermissionDenied(
                        "The home instance names hub secrets, and never sends a secret's value."
                    )
                return create
            case instance_lifecycle.name:
                call = _LifecycleCall.model_validate(arguments)
                match call.action:
                    case LifecycleAction.START:
                        return InstanceStartCommand(instance_id=call.instance_id)
                    case LifecycleAction.STOP:
                        return InstanceStopCommand(instance_id=call.instance_id)
                    case LifecycleAction.RECREATE:
                        return InstanceRecreateCommand(instance_id=call.instance_id)
            case instance_picks_set.name:
                return InstancePicksSetCommand.model_validate(arguments)
            case instance_ask.name:
                return InstanceAsk.model_validate(arguments)
            case library_skill_write.name:
                return LibrarySkillWriteCommand.model_validate(arguments)
            case library_git_add.name:
                return LibraryGitAddCommand.model_validate(arguments)
            case library_move.name:
                return LibraryMove.model_validate(arguments)
            case factory_write.name:
                return FactoryEditCommand.model_validate(arguments)
            case _ if tool in _RELAYED_WRITES:
                method = _RELAYED_WRITES[tool]
                own = {name: value for name, value in arguments.items() if name != "instance_id"}
                return RelayedWrite(
                    _INSTANCE_ID.validate_python(arguments.get("instance_id")),
                    method,
                    method.command.model_validate(own),
                )
    except ValidationError as exc:
        raise PermissionDenied(f'The hub cannot run "{tool}" with these arguments: {exc}') from exc
    raise PermissionDenied(f'"{tool}" is not a write the hub runs for the home instance.')
