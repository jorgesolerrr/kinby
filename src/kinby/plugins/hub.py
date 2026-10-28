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
    AcceptedResult,
    CallFrame,
    ChannelMuteCommand,
    ContractModel,
    ErrorFrame,
    FactoryEditCommand,
    FactoryName,
    FactoryRole,
    FileHash,
    FrameId,
    GitCommit,
    HubSecretName,
    HubWriteCommand,
    HubWriteResult,
    InstanceCreateCommand,
    InstancePicksSetCommand,
    InstanceRelayReadResult,
    LibraryGitAddCommand,
    LibraryMcpServerWriteCommand,
    LibrarySkillMoveCommand,
    LibrarySkillWriteCommand,
    McpServer,
    McpServerChoice,
    McpServerName,
    PromptName,
    PromptSetCommand,
    ResultFrame,
    RoutineDeleteCommand,
    RoutineName,
    RoutineRunCommand,
    RoutineSetEnabledCommand,
    RoutineWriteCommand,
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
    return (
        hub_read,
        instance_create,
        instance_lifecycle,
        instance_picks_set,
        instance_prompt_set,
        instance_routine_write,
        instance_routine_set_enabled,
        instance_routine_run,
        instance_routine_delete,
        instance_ask,
        library_skill_write,
        library_git_add,
        library_move,
        library_mcp_server_write,
        factory_write,
        channel_mute,
        channel_unmute,
    )


@tool(write=False, hub=True)
async def hub_read(method: str, params: dict[str, JsonValue]) -> str:
    """Read the hub or one of its instances, and return the answer as JSON.

    Hub reads include instance.list, instance.status, instance.logs, instance.picks.get,
    stats.summary, stats.runs, factory.list, factory.get, factory.check, factory.run.list,
    library.list, library.skill.read, library.skill.check, library.git.preview,
    library.mcp_server.read, hub.secret.list and channel.list. An instance's own reads are
    memory.list, memory.open, thread.list, thread.turn.list, routine.list, routine.read,
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
    setup: dict[str, SetupValue] | None = None,
    hub_secrets: dict[str, HubSecretName] | None = None,
    picks: list[SkillName] | None = None,
) -> str:
    """Create an instance through onboarding, and return its lifecycle operation as JSON.

    model is the instance's model, like "anthropic:claude-sonnet-5". A factory_role, a factory
    and the name of one of its roles, makes the instance that role, named after it; leave it out
    for a vanilla instance. setup holds the setup fields the image or the role declares. A
    secret field takes only a hub secret, named in hub_secrets by field name: never put a
    secret's value in any argument. A secret field left out waits as pending setup. picks names
    the library skills the instance picks.
    """
    command = InstanceCreateCommand(
        manifest_id=manifest_id,
        persona_name=persona_name,
        factory_role=factory_role,
        model=model,
        config=setup or {},
        hub_secrets=hub_secrets or {},
        picks=picks or [],
    )
    arguments = command.model_dump(mode="json")
    arguments["setup"] = arguments.pop("config")
    return await _write(instance_create, arguments, context)


@tool(write=True, hub=True)
async def instance_lifecycle(
    instance_id: UUID, action: LifecycleAction, context: ToolContext
) -> str:
    """Start, stop or recreate an instance, and return its lifecycle operation as JSON."""
    arguments: dict[str, JsonValue] = {"instance_id": str(instance_id), "action": action.value}
    return await _write(instance_lifecycle, arguments, context)


@tool(write=True, hub=True)
async def instance_picks_set(
    instance_id: UUID,
    skills: list[SkillName],
    mcp_servers: list[McpServerChoice],
    hash: FileHash | None,
    context: ToolContext,
) -> str:
    """Set the library skills and MCP servers an instance picks, and return its picks as JSON.

    skills and mcp_servers name every pick the instance should hold: one left out is dropped.
    Each MCP server's trust_hints says whether its tools that declare themselves read-only run
    without asking; leave it false unless the user asked to trust them. hash is the one
    instance.picks.get returned.
    """
    command = InstancePicksSetCommand(
        instance_id=instance_id, skills=skills, mcp_servers=mcp_servers, hash=hash
    )
    return await _write(instance_picks_set, command.model_dump(mode="json"), context)


@tool(write=True, hub=True)
async def instance_prompt_set(
    instance_id: UUID, name: PromptName, content: str, hash: FileHash, context: ToolContext
) -> str:
    """Set one of an instance's prompts, and return it as JSON.

    hash is the one prompt.get returned for that prompt.
    """
    command = PromptSetCommand(name=name, content=content, hash=hash)
    return await _relayed_write(instance_prompt_set, instance_id, command, context)


@tool(write=True, hub=True)
async def instance_routine_write(
    instance_id: UUID, name: RoutineName, content: str, hash: FileHash | None, context: ToolContext
) -> str:
    """Write a routine's ROUTINE.md into an instance, and return the routine file as JSON.

    hash is the one routine.read returned, or null to create the routine.
    """
    command = RoutineWriteCommand(name=name, content=content, hash=hash)
    return await _relayed_write(instance_routine_write, instance_id, command, context)


@tool(write=True, hub=True)
async def instance_routine_set_enabled(
    instance_id: UUID, name: RoutineName, *, enabled: bool, context: ToolContext
) -> str:
    """Enable or disable one of an instance's routines, and return the routine file as JSON."""
    command = RoutineSetEnabledCommand(name=name, enabled=enabled)
    return await _relayed_write(instance_routine_set_enabled, instance_id, command, context)


@tool(write=True, hub=True)
async def instance_routine_run(instance_id: UUID, name: RoutineName, context: ToolContext) -> str:
    """Run one of an instance's routines once, now, in that instance."""
    command = RoutineRunCommand(name=name)
    return await _relayed_write(instance_routine_run, instance_id, command, context)


@tool(write=True, hub=True)
async def instance_routine_delete(
    instance_id: UUID, name: RoutineName, hash: FileHash, context: ToolContext
) -> str:
    """Delete one of an instance's routines.

    hash is the one routine.read returned.
    """
    command = RoutineDeleteCommand(name=name, hash=hash)
    return await _relayed_write(instance_routine_delete, instance_id, command, context)


@tool(write=True, hub=True)
async def instance_ask(instance_id: UUID, message: str, context: ToolContext) -> str:
    """Start a turn in an instance, on a new thread, with a request for it to work on there.

    Ask for work that needs that instance's own judgment or memory. Returns the thread and the
    turn as JSON, with the thread's link in the web app to give the user as a Markdown link.
    """
    arguments: dict[str, JsonValue] = {"instance_id": str(instance_id), "message": message}
    accepted = AcceptedResult.model_validate(await _written(instance_ask, arguments, context))
    link = f"/instances/{instance_id}/threads/{accepted.thread_id}"
    return json.dumps({**accepted.model_dump(mode="json"), "link": link}, ensure_ascii=False)


@tool(write=True, hub=True)
async def library_skill_write(
    name: SkillName, content: str, hash: FileHash | None, context: ToolContext
) -> str:
    """Write a library skill's SKILL.md, and return the skill as JSON.

    hash is the one library.skill.read returned, or null to add a new skill to the library.
    """
    command = LibrarySkillWriteCommand(name=name, content=content, hash=hash)
    return await _write(library_skill_write, command.model_dump(mode="json"), context)


@tool(write=True, hub=True)
async def library_git_add(
    address: str, commit: GitCommit, skills: list[SkillName], context: ToolContext
) -> str:
    """Add skills from a git repository to the library, and return their entries as JSON.

    address and commit are the ones library.git.preview took and returned, and skills names the
    previewed skills to add.
    """
    command = LibraryGitAddCommand(address=address, commit=commit, skills=skills)
    return await _write(library_git_add, command.model_dump(mode="json"), context)


@tool(write=True, hub=True)
async def library_move(
    name: SkillName,
    hash: FileHash,
    commit: GitCommit | None,
    diff: str,
    context: ToolContext,
    *,
    overwrite: bool = False,
) -> str:
    """Move a library skill to its newer version, and return the library as JSON.

    hash, commit and diff are the ones library.skill.check returned: the user approves the move
    reading the diff. overwrite replaces edits the user made to the skill since it landed.
    """
    command = LibrarySkillMoveCommand(name=name, hash=hash, commit=commit, overwrite=overwrite)
    return await _write(library_move, {**command.model_dump(mode="json"), "diff": diff}, context)


@tool(write=True, hub=True)
async def library_mcp_server_write(
    name: McpServerName, server: McpServer, hash: FileHash | None, context: ToolContext
) -> str:
    """Write an MCP server's definition into the library, and return it as JSON.

    server holds its transport, a url or a uvx package, the env variables a local server gets
    from an instance's .env, and include, the names of its tools a turn is offered. hash is the
    one library.mcp_server.read returned, or null to add a new server to the library.
    """
    command = LibraryMcpServerWriteCommand(name=name, server=server, hash=hash)
    return await _write(library_mcp_server_write, command.model_dump(mode="json"), context)


@tool(write=True, hub=True)
async def factory_write(
    name: FactoryName, files: dict[str, str], hash: FileHash | None, context: ToolContext
) -> str:
    """Write a factory's files, each whole, and return the factory as JSON.

    files holds each file to write by its path in the factory's folder; the others stay. hash
    is the one factory.get returned, or null to create a new factory. The hub keeps the write
    only if the factory check passes.
    """
    command = FactoryEditCommand(name=name, files=files, hash=hash)
    return await _write(factory_write, command.model_dump(mode="json"), context)


@tool(write=True, hub=True)
async def channel_mute(channel_id: UUID, instance_id: UUID, context: ToolContext) -> str:
    """Stop a channel sending an instance's notifications, and return the channel as JSON."""
    command = ChannelMuteCommand(channel_id=channel_id, instance_id=instance_id)
    return await _write(channel_mute, command.model_dump(mode="json"), context)


@tool(write=True, hub=True)
async def channel_unmute(channel_id: UUID, instance_id: UUID, context: ToolContext) -> str:
    """Let a channel send an instance's notifications again, and return the channel as JSON."""
    command = ChannelMuteCommand(channel_id=channel_id, instance_id=instance_id)
    return await _write(channel_unmute, command.model_dump(mode="json"), context)


async def _relayed_write(
    write: Tool, instance_id: UUID, command: ContractModel, context: ToolContext
) -> str:
    arguments = {"instance_id": str(instance_id), **command.model_dump(mode="json")}
    return await _write(write, arguments, context)


async def _write(write: Tool, arguments: dict[str, JsonValue], context: ToolContext) -> str:
    return json.dumps(await _written(write, arguments, context), ensure_ascii=False)


async def _written(
    write: Tool, arguments: dict[str, JsonValue], context: ToolContext
) -> dict[str, JsonValue]:
    """Ask the hub to run the call, with the approval the user gave it."""
    if context.approval_id is None:
        raise PermissionError(f"The hub runs {write.name} only with the user's approval.")
    command = HubWriteCommand(approval_id=context.approval_id, tool=write.name, arguments=arguments)
    written = await call_hub(HUB_WRITE.name, command.model_dump(mode="json"), "the write")
    return HubWriteResult.model_validate(written).result


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
    except (aiohttp.ClientError, aiohttp.WSMessageTypeError) as exc:
        raise ConnectionError(f"The hub refused {named}: {exc}") from exc
    match parse_server_frame(answer):
        case ResultFrame(result=result):
            return result
        case ErrorFrame(error=error):
            raise ValueError(f"The hub refused {named}: {error.message}")
        case other:
            raise ConnectionError(f"The hub answered {named} with {other!r}.")
