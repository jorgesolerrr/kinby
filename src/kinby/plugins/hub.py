"""Hub tools: the home instance reads the hub and its instances on its hub route, and asks the
hub for the writes the user approves."""

from __future__ import annotations

import json
import os
from enum import StrEnum
from uuid import UUID

import aiohttp
from pydantic import JsonValue

from kinby.contracts import (
    HUB_WRITE,
    INSTANCE_RELAY_READ,
    RELAYED_READS,
    CallFrame,
    ErrorFrame,
    FactoryRole,
    FrameId,
    HubSecretName,
    HubWriteCommand,
    HubWriteResult,
    InstanceCreateCommand,
    InstanceRelayReadResult,
    ResultFrame,
    Scope,
    SetupValue,
    SkillName,
    parse_server_frame,
)
from kinby.plugins.tools import Tool, ToolContext, tool

#: This instance's route on its hub. A hub sets it only in an instance its registry grants a scope.
HUB_URL_VARIABLE = "KINBY_HUB_URL"
#: The scopes the hub grants this instance on that route, comma-separated.
HUB_SCOPES_VARIABLE = "KINBY_HUB_SCOPES"


class LifecycleAction(StrEnum):
    START = "start"
    STOP = "stop"
    RECREATE = "recreate"


def hub_tools() -> tuple[Tool, ...]:
    """The hub tools, in the instance its hub grants hub:home on its hub route."""
    granted = os.environ.get(HUB_SCOPES_VARIABLE, "").split(",")
    if not os.environ.get(HUB_URL_VARIABLE) or Scope.HUB_HOME not in granted:
        return ()
    return (hub_read, instance_create, instance_lifecycle)


@tool(write=False, hub=True)
async def hub_read(method: str, params: dict[str, JsonValue]) -> str:
    """Read the hub or one of its instances, and return the answer as JSON.

    Hub reads include instance.list, instance.status, instance.logs, stats.summary,
    factory.list, factory.run.list, library.list and hub.secret.list. An instance's own reads
    are memory.list, memory.open, thread.list, thread.turn.list, routine.list, routine.read,
    usage.get, stats.get, prompt.get, manifest.get, permissions.get and config.history: name
    the instance in params as instance_id, beside the read's own params.
    """
    if method not in RELAYED_READS:
        return json.dumps(await call_hub(method, params, "the read"), ensure_ascii=False)
    own = {name: value for name, value in params.items() if name != "instance_id"}
    relayed = await call_hub(
        INSTANCE_RELAY_READ.name,
        {"instance_id": params.get("instance_id"), "method": method, "params": own},
        "the read",
    )
    return json.dumps(InstanceRelayReadResult.model_validate(relayed).result, ensure_ascii=False)


@tool(write=True, hub=True)
async def instance_create(
    model: str,
    context: ToolContext,
    manifest_id: str | None = None,
    persona_name: str | None = None,
    factory_role: FactoryRole | None = None,
    config: dict[str, SetupValue] | None = None,
    hub_secrets: dict[str, HubSecretName] | None = None,
    picks: list[SkillName] | None = None,
) -> str:
    """Create an instance through onboarding, and return its lifecycle operation as JSON.

    model is the instance's model, like "anthropic:claude-sonnet-5". A factory_role, a factory
    and the name of one of its roles, makes the instance that role, named after it; leave it out
    for a vanilla instance. config holds the setup fields the image or the role declares. A
    secret field takes only a hub secret, named in hub_secrets by field name: never put a
    secret's value in any argument. A secret field left out waits as pending setup. picks names
    the library skills the instance picks.
    """
    command = InstanceCreateCommand(
        manifest_id=manifest_id,
        persona_name=persona_name,
        factory_role=factory_role,
        model=model,
        config=config or {},
        hub_secrets=hub_secrets or {},
        picks=picks or [],
    )
    return await _write(instance_create, command.model_dump(mode="json"), context)


@tool(write=True, hub=True)
async def instance_lifecycle(
    instance_id: UUID, action: LifecycleAction, context: ToolContext
) -> str:
    """Start, stop or recreate an instance, and return its lifecycle operation as JSON."""
    arguments: dict[str, JsonValue] = {"instance_id": str(instance_id), "action": action.value}
    return await _write(instance_lifecycle, arguments, context)


async def _write(write: Tool, arguments: dict[str, JsonValue], context: ToolContext) -> str:
    """Ask the hub to run the call, with the approval the user gave it."""
    if context.approval_id is None:
        raise PermissionError(f"The hub runs {write.name} only with the user's approval.")
    command = HubWriteCommand(approval_id=context.approval_id, tool=write.name, arguments=arguments)
    written = await call_hub(HUB_WRITE.name, command.model_dump(mode="json"), "the write")
    return json.dumps(HubWriteResult.model_validate(written).result, ensure_ascii=False)


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
            session.ws_connect(os.environ[HUB_URL_VARIABLE]) as socket,
        ):
            await socket.send_str(call.model_dump_json())
            answer = await socket.receive_str()
    except aiohttp.ClientError as exc:
        raise ConnectionError(f"The hub refused {named}: {exc}") from exc
    match parse_server_frame(answer):
        case ResultFrame(result=result):
            return result
        case ErrorFrame(error=error):
            raise ValueError(f"The hub refused {named}: {error.message}")
        case other:
            raise ConnectionError(f"The hub answered {named} with {other!r}.")
