"""Hub tools: the home instance reads the hub and its instances on its hub route."""

from __future__ import annotations

import json
import os

import aiohttp
from pydantic import JsonValue

from kinby.contracts import (
    INSTANCE_RELAY_READ,
    RELAYED_READS,
    CallFrame,
    ErrorFrame,
    FrameId,
    InstanceRelayReadResult,
    ResultFrame,
    parse_server_frame,
)
from kinby.plugins.tools import Tool, tool

#: This instance's route on its hub. A hub sets it only in an instance its registry grants a scope.
HUB_URL_VARIABLE = "KINBY_HUB_URL"


def hub_tools() -> tuple[Tool, ...]:
    """The hub tools, in an instance its hub gave a hub route."""
    return (hub_read,) if os.environ.get(HUB_URL_VARIABLE) else ()


@tool(write=False)
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
