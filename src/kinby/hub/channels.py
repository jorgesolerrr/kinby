"""Channels: the kinds the hub finds, and the channels the user sets up and pairs (ADR 0103)."""

from __future__ import annotations

import hmac
import logging
import secrets
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from importlib.metadata import entry_points
from typing import ClassVar, Protocol, runtime_checkable
from uuid import UUID, uuid4

from aiohttp import web
from pydantic import SecretStr

from kinby.contracts import (
    Channel,
    ChannelCreateCommand,
    ChannelDeleteCommand,
    ChannelKindDescription,
    ChannelKindName,
    ChannelKindsCommand,
    ChannelKindsResult,
    ChannelListCommand,
    ChannelListResult,
    ChannelMuteCommand,
    ChannelTestCommand,
    ChannelUpdateCommand,
    HubSecretName,
    SetupField,
    SetupFieldKind,
    SetupValue,
)
from kinby.core.errors import (
    ChannelNotFound,
    ChannelUnpaired,
    InvalidSetup,
    ManagedInstanceNotFound,
)
from kinby.hub.models import ChannelNotification, ChannelUser, SentRef
from kinby.hub.registry import ChannelRecord, HubRegistry, Pairing
from kinby.hub.secrets import HubSecrets
from kinby.setup import resolved_values, value_problem

__all__ = [
    "ChannelKind",
    "ChannelNotification",
    "ChannelSetup",
    "ChannelUser",
    "Channels",
    "Inbound",
    "ReceivingKind",
    "RegisteringKind",
    "Reply",
    "SentRef",
    "installed_kinds",
]

_logger = logging.getLogger(__name__)

#: The entry-point group a package exposes a channel kind's class through.
ENTRY_POINT_GROUP = "kinby.channels"
PAIRING_SECONDS = 15 * 60


@dataclass(frozen=True)
class ChannelSetup:
    """What a channel of one kind asks for, and how much it sends at once."""

    #: The value of a secret field is kept as a hub secret.
    settings: tuple[SetupField, ...]
    #: A channel of it sends nothing until the user sends it the pairing code.
    pairs: bool
    #: The most characters of a notification's text one message carries.
    message_limit: int


@dataclass(frozen=True)
class Inbound:
    """What a user sent through a channel."""

    sender: ChannelUser
    text: str


@dataclass(frozen=True)
class Reply:
    """An option the user answered a message the channel sent with."""

    sender: ChannelUser
    message: SentRef
    #: Its place among the options the message was sent with.
    option: int


class ChannelKind(Protocol):
    """A sort of service the hub sends notifications through.

    The hub builds one for each send, from a channel's settings with its secrets' values, and the
    user it is paired with.
    """

    setup: ClassVar[ChannelSetup]

    def __init__(
        self, settings: Mapping[str, SetupValue], paired_user: ChannelUser | None
    ) -> None: ...

    async def send(self, notification: ChannelNotification) -> SentRef:
        """Send the notification, or raise why it was not sent."""
        ...


@runtime_checkable
class ReceivingKind(ChannelKind, Protocol):
    """A kind whose service calls the hub's public route when the user sends something."""

    async def receive(self, request: web.Request) -> Inbound | Reply | None:
        """What the user sent, or None for a call that is not authentic or carries neither. A
        kind whose service expects a call that is not authentic to be refused raises the
        refusal."""
        ...

    def answer(self, received: Inbound | Reply | None) -> web.Response:
        """The answer to the service's call, once the hub took what *received* says it
        carried."""
        ...

    async def edit(self, message: SentRef, notification: ChannelNotification, outcome: str) -> None:
        """Show on the message, which sent *notification*, that it was answered and what came of
        the reply. The notification stays on it, without its options."""
        ...


@runtime_checkable
class RegisteringKind(ChannelKind, Protocol):
    """A kind whose service the hub sets up for the channel, rather than the user: where the hub
    takes its calls, or what the user can send it."""

    async def register(self, route: str) -> None:
        """Set the service up to call *route*, the channel's public route, or raise why it would
        not."""
        ...


def installed_kinds() -> dict[ChannelKindName, type[ChannelKind]]:
    """The kinds installed packages expose. One that fails to load is left out."""
    kinds: dict[ChannelKindName, type[ChannelKind]] = {}
    for entry_point in entry_points(group=ENTRY_POINT_GROUP):
        try:
            kinds[entry_point.name] = entry_point.load()
        except Exception:
            _logger.exception('Channel kind "%s" did not load.', entry_point.name)
    return kinds


class Channels:
    """Set up, pair, and test the user's channels, and build the kind each one sends through."""

    def __init__(
        self,
        registry: HubRegistry,
        hub_secrets: HubSecrets,
        kinds: Mapping[ChannelKindName, type[ChannelKind]],
        public_url: str | None,
        clock: Callable[[], datetime],
    ) -> None:
        """*public_url* is where a channel's service reaches the hub's public routes."""
        self._registry = registry
        self._secrets = hub_secrets
        self._kinds = kinds
        self._public_url = public_url
        self._clock = clock

    async def kinds(self, command: ChannelKindsCommand) -> ChannelKindsResult:
        return ChannelKindsResult(
            kinds=[
                ChannelKindDescription(
                    kind=name, settings=list(kind.setup.settings), pairs=kind.setup.pairs
                )
                for name, kind in sorted(self._kinds.items())
            ]
        )

    async def list(self, command: ChannelListCommand) -> ChannelListResult:
        return ChannelListResult(channels=[_shown(record) for record in self._registry.channels()])

    async def create(self, command: ChannelCreateCommand) -> Channel:
        kind = self._kinds.get(command.kind)
        if kind is None:
            raise InvalidSetup({"kind": f'The hub has no channel kind "{command.kind}".'})
        channel_id = uuid4()
        settings = _checked(kind.setup, command.settings, command.secrets, {})
        await self._register(
            channel_id, command.kind, self._built(kind, settings, command.secrets, None)
        )
        settings |= self._kept(channel_id, kind.setup, command.secrets)
        pairing = (
            Pairing(
                f"{secrets.randbelow(10**6):06d}",
                self._clock() + timedelta(seconds=PAIRING_SECONDS),
            )
            if kind.setup.pairs
            else None
        )
        self._registry.create_channel(channel_id, command.name, command.kind, settings, pairing)
        return _shown(self._existing(channel_id))

    async def update(self, command: ChannelUpdateCommand) -> Channel:
        record = self._existing(command.channel_id)
        kind = self._kind(record)
        settings = _checked(kind.setup, command.settings, command.secrets, record.settings)
        if command.settings or command.secrets:
            await self._register(
                record.channel_id,
                record.kind,
                self._built(kind, settings, command.secrets, record.paired_user),
            )
        settings |= self._kept(record.channel_id, kind.setup, command.secrets)
        self._registry.update_channel(
            record.channel_id,
            name=command.name if command.name is not None else record.name,
            enabled=command.enabled if command.enabled is not None else record.enabled,
            settings=settings,
        )
        return _shown(self._existing(record.channel_id))

    async def delete(self, command: ChannelDeleteCommand) -> ChannelListResult:
        record = self._existing(command.channel_id)
        kept = self._secrets.values()
        for field in record.settings:
            if (name := _secret_name(record.channel_id, field)) in kept:
                self._secrets.delete(name)
        self._registry.delete_channel(record.channel_id)
        return await self.list(ChannelListCommand())

    async def test(self, command: ChannelTestCommand) -> Channel:
        """Send a test notification now, and record whether it went."""
        record = self._existing(command.channel_id)
        if record.pairing is not None:
            raise ChannelUnpaired(f'Channel "{record.name}" is not paired yet.')
        test = ChannelNotification(
            notification_id=uuid4(),
            header="kinby",
            title="Test",
            text=f'Notifications reach you through "{record.name}".',
            link=None,
            options=(),
        )
        try:
            await self.sender(record).send(test)
        except Exception as exc:
            self._registry.channel_failed(
                record.channel_id, str(exc) or type(exc).__name__, self._clock()
            )
        else:
            self._registry.channel_sent(record.channel_id, self._clock())
        return _shown(self._existing(record.channel_id))

    async def mute(self, command: ChannelMuteCommand) -> Channel:
        if self._registry.instance(command.instance_id) is None:
            raise ManagedInstanceNotFound(f'Instance "{command.instance_id}" was not found.')
        self._registry.mute(self._existing(command.channel_id).channel_id, command.instance_id)
        return _shown(self._existing(command.channel_id))

    async def unmute(self, command: ChannelMuteCommand) -> Channel:
        self._registry.unmute(self._existing(command.channel_id).channel_id, command.instance_id)
        return _shown(self._existing(command.channel_id))

    async def receive(
        self,
        channel_id: str,
        kind: str,
        request: web.Request,
        take: Callable[[ChannelRecord, Reply], None],
    ) -> web.Response:
        """Hand a call to the channel's public route to its kind, give *take* the reply it
        carries from the user the channel is paired with, and answer the call as the kind does.
        The first sender of a valid pairing code becomes that user."""
        parsed = _parsed(channel_id)
        record = self._registry.channel(parsed) if parsed is not None else None
        if record is None or record.kind != kind or record.kind not in self._kinds:
            raise web.HTTPNotFound()
        receiver = self.sender(record)
        if not isinstance(receiver, ReceivingKind):
            raise web.HTTPNotFound()
        received = await receiver.receive(request)
        match received:
            case Reply(sender=sender) as reply if sender == record.paired_user:
                take(record, reply)
            case Inbound(sender=sender, text=text) if (
                (pairing := record.pairing) is not None
                and self._clock() < pairing.expires_at
                and hmac.compare_digest(text.strip().encode(), pairing.code.encode())
            ):
                self._registry.pair_channel(record.channel_id, pairing.code, sender)
        return receiver.answer(received)

    def limit(self, record: ChannelRecord) -> int:
        """The most characters of a notification's text the channel's kind sends."""
        return self._kind(record).setup.message_limit

    def sender(self, record: ChannelRecord) -> ChannelKind:
        """The channel's kind, built with the values its secret settings name."""
        return self._built(self._kind(record), record.settings, {}, record.paired_user)

    def _built(
        self,
        kind: type[ChannelKind],
        settings: Mapping[str, SetupValue],
        typed: Mapping[str, SecretStr],
        paired_user: ChannelUser | None,
    ) -> ChannelKind:
        """*kind* built with the values its secret settings name, or the ones just *typed*."""
        kept = self._secrets.values()
        secret = {field.name for field in _fields(kind.setup, SetupFieldKind.SECRET)}
        values = {
            name: kept.get(str(value), "") if name in secret else value
            for name, value in settings.items()
        }
        values |= {name: value.get_secret_value() for name, value in typed.items()}
        return kind(values, paired_user)

    async def _register(self, channel_id: UUID, kind: ChannelKindName, built: ChannelKind) -> None:
        """Tell a kind that registers where its service reaches the channel. Raise when it cannot,
        so the channel is not set up to a service that never calls."""
        if not isinstance(built, RegisteringKind):
            return
        if self._public_url is None:
            raise InvalidSetup({"kind": f'A "{kind}" channel needs the hub\'s public URL.'})
        try:
            await built.register(f"{self._public_url}/channels/{channel_id}/{kind}")
        except Exception as exc:
            raise InvalidSetup(
                {"kind": f"The service refused the channel's route: {exc or type(exc).__name__}"}
            ) from exc

    def _kind(self, record: ChannelRecord) -> type[ChannelKind]:
        kind = self._kinds.get(record.kind)
        if kind is None:
            raise ChannelNotFound(f'The hub has no channel kind "{record.kind}" any more.')
        return kind

    def _existing(self, channel_id: UUID) -> ChannelRecord:
        record = self._registry.channel(channel_id)
        if record is None:
            raise ChannelNotFound(f'Channel "{channel_id}" was not found.')
        return record

    def _kept(
        self, channel_id: UUID, setup: ChannelSetup, values: Mapping[str, SecretStr]
    ) -> dict[str, HubSecretName]:
        """Keep each secret setting as a hub secret, and name the hub secret it went to."""
        kept: dict[str, HubSecretName] = {}
        for field in _fields(setup, SetupFieldKind.SECRET):
            if field.name in values:
                kept[field.name] = _secret_name(channel_id, field.name)
                self._secrets.save(kept[field.name], values[field.name].get_secret_value())
        return kept


def _checked(
    setup: ChannelSetup,
    settings: Mapping[str, SetupValue],
    secret_values: Mapping[str, SecretStr],
    held: Mapping[str, SetupValue],
) -> dict[str, SetupValue]:
    """The settings a channel holds once *settings* and *secret_values* update the ones it
    *held*: its configuration, defaults filling the gaps, and the hub secrets it held. Raise what
    is wrong with each, by field name."""
    errors: dict[str, str] = {}
    for name in settings:
        if not any(field.name == name for field in _fields(setup, SetupFieldKind.CONFIG)):
            errors[name] = "This channel kind has no configuration setting by this name."
    for name in secret_values:
        if not any(field.name == name for field in _fields(setup, SetupFieldKind.SECRET)):
            errors[name] = "This channel kind has no secret setting by this name."
    values = resolved_values(setup.settings, {**held, **settings})
    for field in setup.settings:
        value = values.get(field.name)
        if field.name in errors:
            continue
        if field.kind is SetupFieldKind.SECRET:
            typed = secret_values.get(field.name, SecretStr("")).get_secret_value().strip()
            if field.required and value is None and not typed:
                errors[field.name] = f"{field.label} is required."
        elif value is None:
            if field.required:
                errors[field.name] = f"{field.label} is required."
        elif (problem := value_problem(field, value)) is not None:
            errors[field.name] = f"{field.label} {problem}."
    if errors:
        raise InvalidSetup(errors)
    return values


def _fields(setup: ChannelSetup, kind: SetupFieldKind) -> list[SetupField]:
    return [field for field in setup.settings if field.kind is kind]


def _secret_name(channel_id: UUID, field: str) -> HubSecretName:
    """The hub secret that holds the value of one of a channel's secret settings."""
    return f"channel-{channel_id}-{field}"


def _parsed(channel_id: str) -> UUID | None:
    try:
        return UUID(channel_id)
    except ValueError:
        return None


def _shown(record: ChannelRecord) -> Channel:
    pairing = record.pairing
    return Channel(
        channel_id=record.channel_id,
        name=record.name,
        kind=record.kind,
        settings=record.settings,
        enabled=record.enabled,
        paired=record.paired_user is not None,
        pairing_code=pairing.code if pairing is not None else None,
        pairing_expires_at=pairing.expires_at if pairing is not None else None,
        muted=list(record.muted),
        last_sent_at=record.last_sent_at,
        last_failure=record.last_failure,
    )
