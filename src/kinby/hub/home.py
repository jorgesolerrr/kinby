"""The writes the home instance may ask the hub for, one per hub tool (ADR 0096)."""

from __future__ import annotations

from collections.abc import Mapping
from uuid import UUID

from pydantic import JsonValue, ValidationError

from kinby.contracts import (
    ContractModel,
    InstanceCreateCommand,
    InstanceRecreateCommand,
    InstanceStartCommand,
    InstanceStopCommand,
)
from kinby.core.errors import PermissionDenied
from kinby.plugins.hub import LifecycleAction, instance_create, instance_lifecycle

type HomeWrite = (
    InstanceCreateCommand | InstanceStartCommand | InstanceStopCommand | InstanceRecreateCommand
)


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
    except ValidationError as exc:
        raise PermissionDenied(f'The hub cannot run "{tool}" with these arguments: {exc}') from exc
    raise PermissionDenied(f'"{tool}" is not a write the hub runs for the home instance.')
