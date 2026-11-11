"""Hub tools: the home instance asks the hub for the writes the user approves, each an operation
of hub_write (ADR 0096). It reads the hub and its instances with `kinby hub read` (ADR 0120)."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, replace
from enum import StrEnum
from uuid import UUID

import aiohttp
from pydantic import ConfigDict, JsonValue, ValidationError
from pydantic.fields import FieldInfo

from kinby.contracts import (
    HUB_ROUTE_FRAME_BYTES,
    HUB_WRITE,
    INSTANCE_RELAY_READ,
    PROMPT_SET,
    RELAYED_READS,
    ROUTINE_DELETE,
    ROUTINE_RUN,
    ROUTINE_SET_ENABLED,
    ROUTINE_WRITE,
    AcceptedResult,
    CallFrame,
    ChannelMuteCommand,
    ContractModel,
    ErrorEnvelope,
    ErrorFrame,
    FactoryEditCommand,
    FrameId,
    HubWriteCommand,
    HubWriteResult,
    ImagePrepareCommand,
    InstanceCreateCommand,
    InstancePicksSetCommand,
    InstanceRecreateCommand,
    InstanceRelayReadResult,
    InstanceStartCommand,
    InstanceStopCommand,
    LibraryGitAddCommand,
    LibraryMcpServerWriteCommand,
    LibrarySkillMoveCommand,
    LibrarySkillWriteCommand,
    Method,
    ResultFrame,
    Scope,
    described_fields,
    parse_server_frame,
)
from kinby.core.errors import PermissionDenied
from kinby.plugins.tools import Tool, ToolContext, tool

#: This instance's route on its hub. A hub sets it only in an instance its registry grants a scope.
HUB_URL_VARIABLE = "KINBY_HUB_URL"
#: The scopes the hub grants this instance on that route, comma-separated.
HUB_SCOPES_VARIABLE = "KINBY_HUB_SCOPES"


class LifecycleAction(StrEnum):
    START = "start"
    STOP = "stop"
    RECREATE = "recreate"


class HubRefused(ValueError):
    """The hub answered a call with an error."""

    def __init__(self, named: str, error: ErrorEnvelope) -> None:
        super().__init__(f"The hub refused {named}: {error.explained}")
        self.error = error


def holds_hub_home() -> bool:
    """Whether this instance's hub grants it hub:home on its hub route."""
    granted = os.environ.get(HUB_SCOPES_VARIABLE, "").split(",")
    return bool(os.environ.get(HUB_URL_VARIABLE)) and Scope.HUB_HOME in granted


class HubWriteOperation(StrEnum):
    """The writes hub_write asks the hub for, each named as its own hub tool was (ADR 0120)."""

    INSTANCE_CREATE = "instance_create"
    INSTANCE_LIFECYCLE = "instance_lifecycle"
    INSTANCE_PICKS_SET = "instance_picks_set"
    INSTANCE_PROMPT_SET = "instance_prompt_set"
    INSTANCE_ROUTINE_WRITE = "instance_routine_write"
    INSTANCE_ROUTINE_SET_ENABLED = "instance_routine_set_enabled"
    INSTANCE_ROUTINE_RUN = "instance_routine_run"
    INSTANCE_ROUTINE_DELETE = "instance_routine_delete"
    INSTANCE_ASK = "instance_ask"
    IMAGE_PREPARE = "image_prepare"
    LIBRARY_SKILL_WRITE = "library_skill_write"
    LIBRARY_GIT_ADD = "library_git_add"
    LIBRARY_MOVE = "library_move"
    LIBRARY_MCP_SERVER_WRITE = "library_mcp_server_write"
    FACTORY_WRITE = "factory_write"
    CHANNEL_MUTE = "channel_mute"
    CHANNEL_UNMUTE = "channel_unmute"


def hub_tools() -> tuple[Tool, ...]:
    """The hub tools, in the instance its hub grants hub:home on its hub route."""
    if not holds_hub_home():
        return ()
    return (hub_write,)


async def read_hub(method: str, params: dict[str, JsonValue]) -> dict[str, JsonValue]:
    """Make one hub read, or a relayed read of the instance params names as instance_id."""
    if method not in RELAYED_READS:
        return await call_hub(method, params, "the read")
    own = {name: value for name, value in params.items() if name != "instance_id"}
    relayed = await call_hub(
        INSTANCE_RELAY_READ.name,
        {"instance_id": params.get("instance_id"), "method": method, "params": own},
        "the read",
    )
    return InstanceRelayReadResult.model_validate(relayed).result


@tool(write=True, hub=True)
async def hub_write(operation: str, arguments: dict[str, JsonValue], context: ToolContext) -> str:
    """Ask the hub for one write the user approves, and return the hub's answer as JSON.

    operation is one of instance_create, instance_lifecycle, instance_picks_set,
    instance_prompt_set, instance_routine_write, instance_routine_set_enabled,
    instance_routine_run, instance_routine_delete, instance_ask, image_prepare,
    library_skill_write, library_git_add, library_move, library_mcp_server_write, factory_write,
    channel_mute and channel_unmute. `kinby hub help <operation>` prints its arguments.
    """
    write = home_write(operation, arguments)
    if context.approval_id is None:
        raise PermissionError(f"The hub runs {operation} only with the user's approval.")
    command = HubWriteCommand(approval_id=context.approval_id, tool=operation, arguments=arguments)
    answer = await call_hub(HUB_WRITE.name, command.model_dump(mode="json"), "the write")
    result = HubWriteResult.model_validate(answer).result
    if isinstance(write, InstanceAsk):
        accepted = AcceptedResult.model_validate(result)
        link = f"/instances/{write.instance_id}/threads/{accepted.thread_id}"
        result = {**accepted.model_dump(mode="json"), "link": link}
    return json.dumps(result, ensure_ascii=False)


def _refusal(call: Mapping[str, JsonValue]) -> str | None:
    try:
        requested_write(call)
    except PermissionDenied as exc:
        return str(exc)
    return None


hub_write = replace(hub_write, check=_refusal)


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

    #: The diff the check showed.
    diff: str


class ChannelMute(ChannelMuteCommand):
    """A mute the hub adds to a channel."""


class ChannelUnmute(ChannelMuteCommand):
    """A mute the hub takes off a channel."""


type HomeWrite = (
    InstanceCreateCommand
    | InstanceStartCommand
    | InstanceStopCommand
    | InstanceRecreateCommand
    | InstancePicksSetCommand
    | RelayedWrite
    | InstanceAsk
    | ImagePrepareCommand
    | LibrarySkillWriteCommand
    | LibraryGitAddCommand
    | LibraryMove
    | LibraryMcpServerWriteCommand
    | FactoryEditCommand
    | ChannelMute
    | ChannelUnmute
)

#: The operations whose write runs in the target instance, and the instance method each calls.
_RELAYED_WRITES: Mapping[str, Method] = {
    HubWriteOperation.INSTANCE_PROMPT_SET: PROMPT_SET,
    HubWriteOperation.INSTANCE_ROUTINE_WRITE: ROUTINE_WRITE,
    HubWriteOperation.INSTANCE_ROUTINE_SET_ENABLED: ROUTINE_SET_ENABLED,
    HubWriteOperation.INSTANCE_ROUTINE_RUN: ROUTINE_RUN,
    HubWriteOperation.INSTANCE_ROUTINE_DELETE: ROUTINE_DELETE,
}

_NO_SECRET_VALUES = "the home instance names hub secrets in hub_secrets, never a secret's value"


class _LifecycleCall(ContractModel):
    instance_id: UUID
    action: LifecycleAction


class _Target(ContractModel):
    """The instance a relayed write runs in, beside the write's own arguments."""

    model_config = ConfigDict(extra="ignore")

    instance_id: UUID


#: The model each operation that runs on the hub reads its arguments as.
_HUB_RUN_WRITES: Mapping[str, type[ContractModel]] = {
    HubWriteOperation.INSTANCE_CREATE: InstanceCreateCommand,
    HubWriteOperation.INSTANCE_LIFECYCLE: _LifecycleCall,
    HubWriteOperation.INSTANCE_PICKS_SET: InstancePicksSetCommand,
    HubWriteOperation.INSTANCE_ASK: InstanceAsk,
    HubWriteOperation.IMAGE_PREPARE: ImagePrepareCommand,
    HubWriteOperation.LIBRARY_SKILL_WRITE: LibrarySkillWriteCommand,
    HubWriteOperation.LIBRARY_GIT_ADD: LibraryGitAddCommand,
    HubWriteOperation.LIBRARY_MOVE: LibraryMove,
    HubWriteOperation.LIBRARY_MCP_SERVER_WRITE: LibraryMcpServerWriteCommand,
    HubWriteOperation.FACTORY_WRITE: FactoryEditCommand,
    HubWriteOperation.CHANNEL_MUTE: ChannelMute,
    HubWriteOperation.CHANNEL_UNMUTE: ChannelUnmute,
}


def write_arguments(operation: HubWriteOperation) -> dict[str, FieldInfo]:
    """The arguments one hub_write operation takes, by name, as home_write reads them, each with
    its description."""
    if operation in _RELAYED_WRITES:
        return {
            **described_fields(_Target),
            **described_fields(_RELAYED_WRITES[operation].command),
        }
    fields = described_fields(_HUB_RUN_WRITES[operation])
    if operation is HubWriteOperation.INSTANCE_CREATE:
        fields["setup"] = fields.pop("config")
        for refused in ("secrets", "save_to_hub"):
            del fields[refused]
    return fields


def requested_write(call: Mapping[str, JsonValue]) -> HomeWrite:
    """The hub operation one hub_write call asks for, from its operation and its arguments."""
    operation, arguments = call.get("operation"), call.get("arguments")
    if not isinstance(operation, str) or not isinstance(arguments, dict):
        raise PermissionDenied("hub_write takes an operation and its arguments as an object.")
    return home_write(operation, arguments)


def home_write(operation: str, arguments: Mapping[str, JsonValue]) -> HomeWrite:
    """The hub operation one hub_write operation asks for. A refusal names each wrong field. A
    secret's value never comes this way."""
    try:
        match operation:
            case HubWriteOperation.INSTANCE_CREATE:
                # The tool took the config as setup: LangChain keeps a tool's config for itself.
                own = dict(arguments)
                if "setup" in own:
                    own["config"] = own.pop("setup") or {}
                create = InstanceCreateCommand.model_validate(own)
                sent = {"secrets": create.secrets, "save_to_hub": create.save_to_hub}
                if any(sent.values()):
                    raise _refused(
                        operation, {name: _NO_SECRET_VALUES for name in sent if sent[name]}
                    )
                return create
            case HubWriteOperation.INSTANCE_LIFECYCLE:
                call = _LifecycleCall.model_validate(arguments)
                match call.action:
                    case LifecycleAction.START:
                        return InstanceStartCommand(instance_id=call.instance_id)
                    case LifecycleAction.STOP:
                        return InstanceStopCommand(instance_id=call.instance_id)
                    case LifecycleAction.RECREATE:
                        return InstanceRecreateCommand(instance_id=call.instance_id)
            case HubWriteOperation.INSTANCE_PICKS_SET:
                return InstancePicksSetCommand.model_validate(arguments)
            case HubWriteOperation.INSTANCE_ASK:
                return InstanceAsk.model_validate(arguments)
            case HubWriteOperation.IMAGE_PREPARE:
                return ImagePrepareCommand.model_validate(arguments)
            case HubWriteOperation.LIBRARY_SKILL_WRITE:
                return LibrarySkillWriteCommand.model_validate(arguments)
            case HubWriteOperation.LIBRARY_GIT_ADD:
                return LibraryGitAddCommand.model_validate(arguments)
            case HubWriteOperation.LIBRARY_MOVE:
                return LibraryMove.model_validate(arguments)
            case HubWriteOperation.LIBRARY_MCP_SERVER_WRITE:
                return LibraryMcpServerWriteCommand.model_validate(arguments)
            case HubWriteOperation.FACTORY_WRITE:
                return FactoryEditCommand.model_validate(arguments)
            case HubWriteOperation.CHANNEL_MUTE:
                return ChannelMute.model_validate(arguments)
            case HubWriteOperation.CHANNEL_UNMUTE:
                return ChannelUnmute.model_validate(arguments)
            case _ if operation in _RELAYED_WRITES:
                return _relayed_write(operation, _RELAYED_WRITES[operation], arguments)
    except ValidationError as exc:
        raise _refused(operation, _fields(exc)) from exc
    raise PermissionDenied(f'"{operation}" is not a write the hub runs for the home instance.')


def _relayed_write(
    operation: str, method: Method, arguments: Mapping[str, JsonValue]
) -> RelayedWrite:
    """The relayed write, refused with what is wrong with both the target and the command."""
    own = {name: value for name, value in arguments.items() if name != "instance_id"}
    fields: dict[str, str] = {}
    target = command = None
    try:
        target = _Target.model_validate(arguments).instance_id
    except ValidationError as exc:
        fields |= _fields(exc)
    try:
        command = method.command.model_validate(own)
    except ValidationError as exc:
        fields |= _fields(exc)
    if target is None or command is None:
        raise _refused(operation, fields)
    return RelayedWrite(target, method, command)


def _fields(exc: ValidationError) -> dict[str, str]:
    return {".".join(str(part) for part in error["loc"]): error["msg"] for error in exc.errors()}


def _refused(operation: str, fields: Mapping[str, str]) -> PermissionDenied:
    named = "; ".join(f"{name}: {why}" for name, why in fields.items())
    return PermissionDenied(f'The hub cannot run "{operation}" with these arguments: {named}')


async def call_hub(method: str, params: dict[str, JsonValue], named: str) -> dict[str, JsonValue]:
    """Make one call on this instance's hub route, with its control token, and return the result.

    *named* is how an error names the call, like "the intake".
    """
    # kinby.core imports the plugins, so the server's names load on first use.
    from kinby.core.contract_server import CONTROL_TOKEN_VARIABLE

    call = CallFrame(id=FrameId("1"), method=method, params=params)
    headers = {"Authorization": f"Bearer {os.environ.get(CONTROL_TOKEN_VARIABLE, '')}"}
    try:
        async with (
            aiohttp.ClientSession(headers=headers) as session,
            session.ws_connect(
                os.environ[HUB_URL_VARIABLE], max_msg_size=HUB_ROUTE_FRAME_BYTES
            ) as socket,
        ):
            await socket.send_str(call.model_dump_json())
            answer = await socket.receive_str()
    except (aiohttp.ClientError, aiohttp.WSMessageTypeError) as exc:
        raise ConnectionError(f"The hub refused {named}: {exc}") from exc
    match parse_server_frame(answer):
        case ResultFrame(result=result):
            return result
        case ErrorFrame(error=error):
            raise HubRefused(named, error)
        case other:
            raise ConnectionError(f"The hub answered {named} with {other!r}.")
