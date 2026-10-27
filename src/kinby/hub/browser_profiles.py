"""Browser profiles: each instance's named signed-in states. The hub keeps them and applies them
on its browser route, so not one cookie of a profile enters the instance (ADR 0113)."""

from __future__ import annotations

import asyncio
import itertools
import json
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

import aiohttp
from aiohttp import WSMsgType, web
from pydantic import JsonValue, TypeAdapter

from kinby.contracts import BrowserProfile, BrowserProfileListResult, BrowserProfileName
from kinby.core.errors import BrowserNotOpen, BrowserProfileNameTaken, BrowserProfileNotFound
from kinby.hub.secrets import replace_protected

#: Playwright's storage state: a context's cookies, and each origin's local storage.
type StorageState = dict[str, JsonValue]

#: The calls that would hand a context's cookies to the turn.
_COOKIE_READS = frozenset({"storageState", "cookies"})
_COOKIES_KEPT = "The hub keeps a browser's cookies to itself."
#: The hub numbers its own calls above any a Playwright client reaches.
_FIRST_CALL_ID = 1 << 40
_STATE_SECONDS = 30


@dataclass(frozen=True)
class _Kept:
    site: str
    state: StorageState


_KEPT = TypeAdapter(dict[BrowserProfileName, _Kept])


class BrowserProfiles:
    """Every instance's browser profiles, each instance's in one protected file of its own."""

    def __init__(self, directory: Path) -> None:
        self._directory = directory

    def list(self, instance_id: UUID) -> BrowserProfileListResult:
        return BrowserProfileListResult(
            profiles=tuple(
                BrowserProfile(name=name, site=kept.site)
                for name, kept in sorted(self._read(instance_id).items())
            )
        )

    def state(self, instance_id: UUID, name: BrowserProfileName) -> StorageState:
        return self._profile(self._read(instance_id), name).state

    def save(
        self, instance_id: UUID, name: BrowserProfileName, site: str, state: StorageState
    ) -> BrowserProfile:
        """Create the profile, or replace the one by that name."""
        self._write(instance_id, self._read(instance_id) | {name: _Kept(site, state)})
        return BrowserProfile(name=name, site=site)

    def refresh(self, instance_id: UUID, name: BrowserProfileName, state: StorageState) -> None:
        """Keep the profile's newer *state*, unless the user deleted or renamed it since."""
        profiles = self._read(instance_id)
        if (kept := profiles.get(name)) is not None:
            self._write(instance_id, profiles | {name: _Kept(kept.site, state)})

    def rename(
        self, instance_id: UUID, name: BrowserProfileName, new_name: BrowserProfileName
    ) -> BrowserProfileListResult:
        profiles = self._read(instance_id)
        kept = self._profile(profiles, name)
        if new_name in profiles:
            raise BrowserProfileNameTaken(f'The instance has a browser profile named "{new_name}".')
        del profiles[name]
        self._write(instance_id, profiles | {new_name: kept})
        return self.list(instance_id)

    def delete(self, instance_id: UUID, name: BrowserProfileName) -> BrowserProfileListResult:
        profiles = self._read(instance_id)
        self._profile(profiles, name)
        del profiles[name]
        self._write(instance_id, profiles)
        return self.list(instance_id)

    def _profile(self, profiles: dict[BrowserProfileName, _Kept], name: str) -> _Kept:
        if name not in profiles:
            raise BrowserProfileNotFound(f'The instance has no browser profile named "{name}".')
        return profiles[name]

    def _read(self, instance_id: UUID) -> dict[BrowserProfileName, _Kept]:
        path = self._path(instance_id)
        return _KEPT.validate_json(path.read_bytes()) if path.exists() else {}

    def _write(self, instance_id: UUID, profiles: dict[BrowserProfileName, _Kept]) -> None:
        self._directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        replace_protected(self._path(instance_id), _KEPT.dump_json(profiles).decode())

    def _path(self, instance_id: UUID) -> Path:
        return self._directory / f"{instance_id}.json"


class BrowserSession:
    """One turn's browser on the route. Its context opens with the turn's profile, and its state
    is saved back to the profile when the context closes. The turn never reads the state."""

    def __init__(
        self, profiles: BrowserProfiles, instance_id: UUID, profile: BrowserProfileName | None
    ) -> None:
        self._profiles = profiles
        self._instance_id = instance_id
        self._profile = profile
        self._service: aiohttp.ClientWebSocketResponse | None = None
        #: The id of the client's call that opens the context, until the service answers it.
        self._opening: JsonValue = None
        self._context: str | None = None
        self._ids = itertools.count(_FIRST_CALL_ID)
        self._answers: dict[int, asyncio.Future[dict[str, JsonValue]]] = {}

    async def carry(
        self, client: web.WebSocketResponse, service: aiohttp.ClientWebSocketResponse
    ) -> None:
        """Carry the Playwright client's frames to the service and back, until either closes."""
        self._service = service
        both = (
            asyncio.create_task(self._from_client(client, service)),
            asyncio.create_task(self._from_service(service, client)),
        )
        _, pending = await asyncio.wait(both, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)

    async def save_as(self, name: BrowserProfileName, site: str) -> BrowserProfile:
        """Save the context's state as the profile *name*, which it is saved back to from now."""
        profile = self._profiles.save(self._instance_id, name, site, await self._state())
        self._profile = name
        return profile

    async def _from_client(
        self, client: web.WebSocketResponse, service: aiohttp.ClientWebSocketResponse
    ) -> None:
        async for message in client:
            if message.type is WSMsgType.BINARY:
                await service.send_bytes(message.data)
                continue
            if message.type is not WSMsgType.TEXT:
                continue
            call = _parsed(message.data)
            if call is None:
                await service.send_str(message.data)
                continue
            method = call.get("method")
            if method in _COOKIE_READS:
                await client.send_json(_refusal(call.get("id"), _COOKIES_KEPT))
                continue
            if method == "newContext":
                self._opening = call.get("id")
                if self._profile is not None:
                    try:
                        state = self._profiles.state(self._instance_id, self._profile)
                    except BrowserProfileNotFound as missing:
                        await client.send_json(_refusal(call.get("id"), str(missing)))
                        continue
                    call["params"] = {**_object(call.get("params")), "storageState": state}
            if (
                method == "close"
                and self._context is not None
                and call.get("guid") == self._context
            ):
                await self._save_back()
            await service.send_str(json.dumps(call))
        await service.close()

    async def _from_service(
        self, service: aiohttp.ClientWebSocketResponse, client: web.WebSocketResponse
    ) -> None:
        try:
            async for message in service:
                if message.type is WSMsgType.BINARY:
                    await client.send_bytes(message.data)
                    continue
                if message.type is not WSMsgType.TEXT:
                    continue
                if self._answers or self._opening is not None:
                    answer = _parsed(message.data)
                    if answer is not None and self._answered(answer):
                        continue
                await client.send_str(message.data)
            await client.close()
        finally:
            for waiting in self._answers.values():
                if not waiting.done():
                    waiting.set_exception(BrowserNotOpen("The turn's browser closed."))

    def _answered(self, answer: dict[str, JsonValue]) -> bool:
        """Note what *answer* says, and whether it answers a call of the hub's own."""
        call_id = answer.get("id")
        if isinstance(call_id, int) and (waiting := self._answers.get(call_id)) is not None:
            waiting.set_result(answer)
            return True
        if call_id is not None and call_id == self._opening:
            self._opening = None
            context = _object(_object(answer.get("result")).get("context")).get("guid")
            self._context = context if isinstance(context, str) else None
        return False

    async def _save_back(self) -> None:
        if self._profile is not None:
            self._profiles.refresh(self._instance_id, self._profile, await self._state())

    async def _state(self) -> StorageState:
        """The context's state, which the hub asks the service for itself."""
        if self._service is None or self._context is None:
            raise BrowserNotOpen("The turn's browser has no page open.")
        call_id = next(self._ids)
        waiting = asyncio.get_running_loop().create_future()
        self._answers[call_id] = waiting
        try:
            await self._service.send_json(
                {
                    "id": call_id,
                    "guid": self._context,
                    "method": "storageState",
                    "params": {},
                    "metadata": {},
                }
            )
            answer = await asyncio.wait_for(waiting, _STATE_SECONDS)
        finally:
            del self._answers[call_id]
        if "result" not in answer:
            raise BrowserNotOpen(f"The browser did not give its state: {answer.get('error')}")
        return _object(answer["result"])


def _parsed(message: str) -> dict[str, JsonValue] | None:
    """The protocol message in *message*, or None for a frame that is not one."""
    try:
        parsed = json.loads(message)
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _object(value: JsonValue) -> dict[str, JsonValue]:
    return value if isinstance(value, dict) else {}


def _refusal(call_id: JsonValue, message: str) -> dict[str, JsonValue]:
    """An answer that fails the client's call *call_id* with *message*."""
    return {"id": call_id, "error": {"error": {"name": "Error", "message": message}}}
