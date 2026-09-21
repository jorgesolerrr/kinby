"""Reach one instance's private server: its health probe and its control socket."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import aiohttp
from pydantic import ValidationError

from kinby.contracts import (
    INSTANCE_DRAIN,
    CallFrame,
    Capability,
    ControlToken,
    DrainState,
    ErrorEnvelope,
    ErrorFrame,
    FrameId,
    InstanceDrainCommand,
    InstanceDrainResult,
    InstanceProbeResult,
    ResultFrame,
    parse_server_frame,
)

#: How long the probe waits before it calls the lifecycle endpoint unreachable.
PROBE_SECONDS = 10
_FRAME_ID = FrameId("1")
_HEARTBEAT_SECONDS = 30


@dataclass(frozen=True)
class ControlEndpoint:
    """One instance's private server, and the secret that opens it."""

    address: str
    token: ControlToken


class ControlUnreachable(Exception):
    """The instance's private lifecycle endpoint did not answer."""


class ControlConnectionLost(ControlUnreachable):
    """The control socket dropped after the drain was sent.

    The instance keeps that drain running, so the hub calls again and waits.
    """


class IncompatibleLifecycleEndpoint(Exception):
    """The instance's lifecycle endpoint answers, but not with what the hub needs."""


class InstanceControl(Protocol):
    """The lifecycle channel a hub holds to one instance. Never reached through the relay."""

    async def probe(self, endpoint: ControlEndpoint) -> InstanceProbeResult: ...

    async def drain(self, endpoint: ControlEndpoint, *, force: bool) -> DrainState: ...


class HttpInstanceControl:
    """Speak the contract to an instance over its own HTTP server, one connection per call."""

    async def probe(self, endpoint: ControlEndpoint) -> InstanceProbeResult:
        """Read what the instance can do. The health route needs no token."""
        try:
            async with (
                aiohttp.ClientSession(
                    timeout=aiohttp.ClientTimeout(total=PROBE_SECONDS)
                ) as session,
                session.get(f"{endpoint.address}/health") as response,
            ):
                response.raise_for_status()
                body = await response.json()
        except (aiohttp.ClientError, TimeoutError, ValueError) as exc:
            raise ControlUnreachable(str(exc) or type(exc).__name__) from exc
        return _probed(body)

    async def drain(self, endpoint: ControlEndpoint, *, force: bool) -> DrainState:
        """Wait for the instance to finish draining. A drain has no deadline of its own."""
        call = CallFrame(
            id=_FRAME_ID,
            method=INSTANCE_DRAIN.name,
            params=InstanceDrainCommand(force=force).model_dump(mode="json"),
        )
        try:
            async with (
                aiohttp.ClientSession(
                    headers={"Authorization": f"Bearer {endpoint.token}"},
                    timeout=aiohttp.ClientTimeout(total=None, sock_connect=PROBE_SECONDS),
                ) as session,
                session.ws_connect(
                    f"{endpoint.address}/control",
                    heartbeat=_HEARTBEAT_SECONDS,
                ) as socket,
            ):
                await socket.send_str(call.model_dump_json())
                return await _finished(socket)
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise ControlUnreachable(str(exc) or type(exc).__name__) from exc


async def _finished(socket: aiohttp.ClientWebSocketResponse) -> DrainState:
    """Read the drain answer. A socket that dies after the call was sent is a lost connection."""
    try:
        message = await _answer(socket)
    except (aiohttp.ClientError, TimeoutError) as exc:
        raise ControlConnectionLost(str(exc) or type(exc).__name__) from exc
    return _drained(message)


async def _answer(socket: aiohttp.ClientWebSocketResponse) -> str:
    message = await socket.receive()
    if message.type is not aiohttp.WSMsgType.TEXT:
        raise ControlConnectionLost(
            f"The control socket closed before it answered ({message.type})."
        )
    return str(message.data)


def _probed(body: object) -> InstanceProbeResult:
    if not isinstance(body, dict):
        raise ControlUnreachable("The health route did not report a contract version.")
    reported = body.get("capabilities")
    version = body.get("contract_version")
    if not isinstance(reported, list) or not isinstance(version, str):
        raise ControlUnreachable("The health route did not report a contract version.")
    return InstanceProbeResult(
        contract_version=version,
        # An instance may report a capability this hub does not know yet.
        capabilities=[capability for capability in Capability if capability.value in reported],
    )


def _drained(message: str) -> DrainState:
    match parse_server_frame(message):
        case ResultFrame() as result:
            try:
                return InstanceDrainResult.model_validate(result.result).state
            except ValidationError as exc:
                raise ControlUnreachable(f"The drain answer could not be read: {exc}") from exc
        case ErrorFrame() as error:
            raise ControlUnreachable(f"The instance refused to drain: {error.error.message}")
        case ErrorEnvelope() as unreadable:
            raise ControlUnreachable(f"The drain answer could not be read: {unreadable.message}")
        case other:
            raise ControlUnreachable(f'The instance answered the drain with "{other.type.value}".')
