"""The hub's own secrets: set once, listed by name, never handed back."""

import asyncio
import os
from pathlib import Path

import pytest

from kinby.cli.client import ContractClient
from kinby.contracts import (
    CONTROL_SCOPES,
    HUB_SECRET_DELETE,
    HUB_SECRET_LIST,
    HUB_SECRET_SET,
    INSTANCE_SCOPES,
    INTAKE_SCOPES,
    UPDATE_SCOPES,
    ErrorCode,
    ErrorEnvelope,
    HubSecretDeleteCommand,
    HubSecretDeleteResult,
    HubSecretListCommand,
    HubSecretListResult,
    HubSecretSetCommand,
    HubSecretSetResult,
    OperationGetResult,
    OperationKind,
    OperationState,
    Scope,
)
from tests.test_hub import FakeImages, finished_operation, hub_at, hub_client

_SENTINEL = "sentinel-hub-secret-value"


async def set_hub_secret(client: ContractClient, name: str, value: str) -> OperationGetResult:
    accepted = await client.call(HUB_SECRET_SET, HubSecretSetCommand(name=name, value=value))
    assert isinstance(accepted, HubSecretSetResult)
    return await finished_operation(client, accepted)


async def _names(client: ContractClient) -> list[str]:
    listed = await client.call(HUB_SECRET_LIST, HubSecretListCommand())
    assert isinstance(listed, HubSecretListResult)
    return [secret.name for secret in listed.secrets]


def test_hub_secrets_are_set_listed_replaced_and_deleted_by_name(tmp_path):
    async def scenario() -> None:
        hub = hub_at(tmp_path / "hub", images=FakeImages())
        client = hub_client(hub)

        created = await set_hub_secret(client, "GITHUB_TOKEN", "first-value")
        await set_hub_secret(client, "ANTHROPIC_API_KEY", "sk-test")
        replaced = await set_hub_secret(client, "GITHUB_TOKEN", _SENTINEL)
        listed = await client.call(HUB_SECRET_LIST, HubSecretListCommand())

        assert created.kind is OperationKind.HUB_SECRET
        assert created.instance_id is None
        assert created.state is OperationState.SUCCEEDED
        assert replaced.state is OperationState.SUCCEEDED
        assert await _names(client) == ["ANTHROPIC_API_KEY", "GITHUB_TOKEN"]
        assert hub.secrets.values() == {"ANTHROPIC_API_KEY": "sk-test", "GITHUB_TOKEN": _SENTINEL}
        assert _SENTINEL not in listed.model_dump_json()

        deleted = await client.call(
            HUB_SECRET_DELETE, HubSecretDeleteCommand(name="ANTHROPIC_API_KEY")
        )

        assert isinstance(deleted, HubSecretDeleteResult)
        assert deleted.unlinked == []
        assert await _names(client) == ["GITHUB_TOKEN"]
        assert hub.secrets.values() == {"GITHUB_TOKEN": _SENTINEL}

    asyncio.run(scenario())


def test_deleting_a_hub_secret_the_hub_does_not_keep_is_not_found(tmp_path):
    async def scenario() -> None:
        hub = hub_at(tmp_path / "hub", images=FakeImages())
        client = hub_client(hub)
        await set_hub_secret(client, "GITHUB_TOKEN", "first-value")

        refused = await client.call(HUB_SECRET_DELETE, HubSecretDeleteCommand(name="OTHER"))

        assert isinstance(refused, ErrorEnvelope)
        assert refused.code is ErrorCode.NOT_FOUND
        assert await _names(client) == ["GITHUB_TOKEN"]

    asyncio.run(scenario())


def test_no_hub_secret_value_reaches_sqlite_the_operation_record_or_the_hub_environment(tmp_path):
    async def scenario() -> None:
        hub = hub_at(tmp_path / "hub", images=FakeImages())
        client = hub_client(hub)

        outcome = await set_hub_secret(client, "GITHUB_TOKEN", _SENTINEL)
        listed = await client.call(HUB_SECRET_LIST, HubSecretListCommand())

        assert _SENTINEL not in outcome.model_dump_json()
        assert _SENTINEL not in listed.model_dump_json()
        assert _SENTINEL not in (hub.directory / "registry.sqlite").read_bytes().decode(
            "utf-8", errors="ignore"
        )
        assert _SENTINEL not in os.environ.values()
        holding = [
            path
            for path in hub.directory.rglob("*")
            if path.is_file() and _SENTINEL.encode() in path.read_bytes()
        ]
        assert len(holding) == 1
        assert holding[0].stat().st_mode & 0o777 == 0o600

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "payload",
    [
        {"name": "GITHUB_TOKEN", "value": [_SENTINEL]},
        {"name": "GITHUB_TOKEN", "value": _SENTINEL, "unknown": _SENTINEL},
        {"name": "  ", "value": _SENTINEL},
        {"name": "GITHUB_TOKEN", "value": ""},
    ],
    ids=["malformed-value", "unknown-field", "blank-name", "empty-value"],
)
def test_malformed_input_is_refused_without_echoing_a_submitted_value(tmp_path, payload):
    async def scenario() -> None:
        hub = hub_at(tmp_path / "hub", images=FakeImages())

        refused = await hub.dispatcher.dispatch(HUB_SECRET_SET.name, payload, {Scope.HUB_ADMIN})

        assert isinstance(refused, ErrorEnvelope)
        assert refused.code is ErrorCode.INVALID_ARGUMENT
        assert _SENTINEL not in refused.message
        assert await _names(hub_client(hub)) == []

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "scopes",
    [UPDATE_SCOPES, INSTANCE_SCOPES, CONTROL_SCOPES, INTAKE_SCOPES],
    ids=["update", "instance", "control", "intake"],
)
@pytest.mark.parametrize(
    ("method", "payload"),
    [
        (HUB_SECRET_SET.name, {"name": "GITHUB_TOKEN", "value": _SENTINEL}),
        (HUB_SECRET_DELETE.name, {"name": "GITHUB_TOKEN"}),
    ],
    ids=["set", "delete"],
)
def test_the_update_token_and_instance_tokens_are_refused_on_every_write(
    tmp_path, scopes, method, payload
):
    async def scenario() -> None:
        hub = hub_at(tmp_path / "hub", images=FakeImages())
        await set_hub_secret(hub_client(hub), "GITHUB_TOKEN", "first-value")

        refused = await hub.dispatcher.dispatch(method, payload, scopes)

        assert isinstance(refused, ErrorEnvelope)
        assert refused.code is ErrorCode.PERMISSION_DENIED
        assert _SENTINEL not in refused.message
        assert hub.secrets.values() == {"GITHUB_TOKEN": "first-value"}

    asyncio.run(scenario())


def test_a_failed_write_keeps_the_previous_hub_secrets_whole(tmp_path, monkeypatch):
    async def scenario() -> None:
        hub = hub_at(tmp_path / "hub", images=FakeImages())
        client = hub_client(hub)
        await set_hub_secret(client, "GITHUB_TOKEN", "first-value")
        original = os.open

        def refuse_to_write(path: object, *arguments: object, **options: object) -> int:
            """Stand in for the disk failing under the write: nothing else can trigger it."""
            named = Path(path) if isinstance(path, str | Path) else None
            if named is not None and named.parent == hub.directory:
                raise OSError(f"no space left on device while writing {_SENTINEL}")
            return original(path, *arguments, **options)  # ty: ignore[invalid-argument-type]

        monkeypatch.setattr(os, "open", refuse_to_write)
        outcome = await set_hub_secret(client, "GITHUB_TOKEN", _SENTINEL)
        monkeypatch.undo()

        assert outcome.state is OperationState.FAILED
        assert _SENTINEL not in outcome.detail
        assert "no space left on device" in outcome.detail
        assert hub.secrets.values() == {"GITHUB_TOKEN": "first-value"}
        assert [path.name for path in hub.directory.iterdir() if "replacing" in path.name] == []

    asyncio.run(scenario())


def test_a_delete_asked_right_after_a_write_waits_for_it(tmp_path):
    async def scenario() -> None:
        hub = hub_at(tmp_path / "hub", images=FakeImages())
        client = hub_client(hub)
        await set_hub_secret(client, "GITHUB_TOKEN", "first-value")

        replaced = await hub.dispatcher.dispatch(
            HUB_SECRET_SET.name, {"name": "GITHUB_TOKEN", "value": _SENTINEL}, {Scope.HUB_ADMIN}
        )
        created = await hub.dispatcher.dispatch(
            HUB_SECRET_SET.name, {"name": "OTHER", "value": _SENTINEL}, {Scope.HUB_ADMIN}
        )
        deleted = [
            await hub.dispatcher.dispatch(HUB_SECRET_DELETE.name, {"name": name}, {Scope.HUB_ADMIN})
            for name in ("GITHUB_TOKEN", "OTHER")
        ]
        assert isinstance(replaced, HubSecretSetResult)
        assert isinstance(created, HubSecretSetResult)
        await finished_operation(client, replaced)
        await finished_operation(client, created)

        assert all(isinstance(result, HubSecretDeleteResult) for result in deleted)
        assert hub.secrets.values() == {}

    asyncio.run(scenario())


def test_the_hub_removes_a_replacement_its_last_run_left_unfinished(tmp_path):
    staging = tmp_path / "hub" / "hub-secrets.json.replacing"
    staging.parent.mkdir()
    staging.write_text(f'{{"GITHUB_TOKEN": "{_SENTINEL}"}}', encoding="utf-8")

    hub = hub_at(tmp_path / "hub", images=FakeImages())

    assert not staging.exists()
    assert hub.secrets.values() == {}
