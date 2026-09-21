"""Replacing one managed instance's stored secrets, without ever handing a value back."""

import asyncio
import os
from pathlib import Path

import pytest

from kinby.contracts import (
    INSTANCE_LIST,
    INSTANCE_SECRETS_SET,
    INSTANCE_STATUS,
    INSTANCE_STOP,
    ErrorCode,
    ErrorEnvelope,
    InstanceListCommand,
    InstanceSecretsSetCommand,
    InstanceStatusCommand,
    InstanceStopCommand,
    LifecycleOperationResult,
    OperationKind,
    OperationState,
    Scope,
)
from kinby.core.contract_server import CONTROL_TOKEN_VARIABLE
from tests.test_hub import (
    FakeControl,
    FakeImages,
    FakeRuntime,
    created_instance,
    finished_operation,
    hub_at,
    hub_client,
    instance_environment,
    started_instance,
)

_SENTINEL = "sentinel-replacement-secret"


def test_replacing_a_secret_writes_the_file_and_asks_for_a_recreation(tmp_path):
    async def scenario() -> None:
        runtime = FakeRuntime()
        hub = hub_at(tmp_path / "hub", runtime=runtime, images=FakeImages())
        client = hub_client(hub)
        created = await created_instance(client, secrets={"PROVIDER_TOKEN": "first-value"})

        accepted = await client.call(
            INSTANCE_SECRETS_SET,
            InstanceSecretsSetCommand(
                instance_id=created.instance_id,
                secrets={"PROVIDER_TOKEN": _SENTINEL},
            ),
        )
        assert isinstance(accepted, LifecycleOperationResult)
        outcome = await finished_operation(client, accepted)

        assert outcome.kind is OperationKind.SECRETS
        assert outcome.state is OperationState.SUCCEEDED
        assert outcome.detail == "Secrets replaced. Recreate the container to apply them."
        environment = instance_environment(hub, created.instance_id)
        assert environment["PROVIDER_TOKEN"] == _SENTINEL
        assert environment[CONTROL_TOKEN_VARIABLE] != ""
        assert runtime.created[0].env["PROVIDER_TOKEN"] == "first-value"

    asyncio.run(scenario())


def test_an_unauthorized_replacement_parses_nothing_and_writes_nothing(tmp_path):
    async def scenario() -> None:
        hub = hub_at(tmp_path / "hub", images=FakeImages())
        client = hub_client(hub)
        created = await created_instance(client, secrets={"PROVIDER_TOKEN": "first-value"})

        refused = await hub.dispatcher.dispatch(
            INSTANCE_SECRETS_SET.name,
            {
                "instance_id": str(created.instance_id),
                "secrets": {"BAD KEY": _SENTINEL},
            },
            {Scope.HUB_READ},
        )

        assert isinstance(refused, ErrorEnvelope)
        assert refused.code is ErrorCode.PERMISSION_DENIED
        assert _SENTINEL not in refused.message
        assert instance_environment(hub, created.instance_id)["PROVIDER_TOKEN"] == "first-value"

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "payload",
    [
        {"secrets": {"PROVIDER_TOKEN": [_SENTINEL]}},
        {"secrets": {"PROVIDER_TOKEN": _SENTINEL}, "apply": _SENTINEL},
        {"secrets": {}},
    ],
    ids=["malformed-value", "unknown-field", "nothing-to-replace"],
)
def test_malformed_input_is_refused_without_echoing_a_submitted_value(tmp_path, payload):
    async def scenario() -> None:
        hub = hub_at(tmp_path / "hub", images=FakeImages())
        client = hub_client(hub)
        created = await created_instance(client, secrets={"PROVIDER_TOKEN": "first-value"})

        refused = await hub.dispatcher.dispatch(
            INSTANCE_SECRETS_SET.name,
            {"instance_id": str(created.instance_id), **payload},
            {Scope.HUB_ADMIN},
        )

        assert isinstance(refused, ErrorEnvelope)
        assert refused.code is ErrorCode.INVALID_ARGUMENT
        assert _SENTINEL not in refused.message
        assert instance_environment(hub, created.instance_id)["PROVIDER_TOKEN"] == "first-value"

    asyncio.run(scenario())


def test_an_invalid_variable_name_fails_the_operation_and_replaces_nothing(tmp_path):
    async def scenario() -> None:
        hub = hub_at(tmp_path / "hub", images=FakeImages())
        client = hub_client(hub)
        created = await created_instance(client, secrets={"PROVIDER_TOKEN": "first-value"})

        accepted = await client.call(
            INSTANCE_SECRETS_SET,
            InstanceSecretsSetCommand(
                instance_id=created.instance_id,
                secrets={"PROVIDER_TOKEN": _SENTINEL, "BAD KEY": _SENTINEL},
            ),
        )
        assert isinstance(accepted, LifecycleOperationResult)
        outcome = await finished_operation(client, accepted)

        assert outcome.state is OperationState.FAILED
        assert outcome.detail == 'Invalid environment variable name: "BAD KEY".'
        environment = instance_environment(hub, created.instance_id)
        assert environment["PROVIDER_TOKEN"] == "first-value"
        assert not (hub.instances_directory / str(created.instance_id) / ".env.replacing").exists()

    asyncio.run(scenario())


def test_no_stored_secret_reaches_sqlite_the_operation_record_or_the_hub_environment(tmp_path):
    async def scenario() -> None:
        hub = hub_at(tmp_path / "hub", images=FakeImages())
        client = hub_client(hub)
        created = await created_instance(client, secrets={"PROVIDER_TOKEN": "first-value"})

        accepted = await client.call(
            INSTANCE_SECRETS_SET,
            InstanceSecretsSetCommand(
                instance_id=created.instance_id,
                secrets={"PROVIDER_TOKEN": _SENTINEL},
            ),
        )
        assert isinstance(accepted, LifecycleOperationResult)
        outcome = await finished_operation(client, accepted)
        listed = await client.call(INSTANCE_LIST, InstanceListCommand())
        status = await client.call(
            INSTANCE_STATUS,
            InstanceStatusCommand(instance_id=created.instance_id),
        )

        assert _SENTINEL not in outcome.model_dump_json()
        assert not isinstance(listed, ErrorEnvelope)
        assert _SENTINEL not in listed.model_dump_json()
        assert not isinstance(status, ErrorEnvelope)
        assert _SENTINEL not in status.model_dump_json()
        assert _SENTINEL not in (hub.directory / "registry.sqlite").read_bytes().decode(
            "utf-8", errors="ignore"
        )
        assert _SENTINEL not in os.environ.values()

    asyncio.run(scenario())


def test_concurrent_replacements_serialize_and_keep_each_instance_to_its_own_secrets(tmp_path):
    async def scenario() -> None:
        hub = hub_at(tmp_path / "hub", images=FakeImages())
        client = hub_client(hub)
        first = await created_instance(client, secrets={"PROVIDER_TOKEN": "first-value"})
        second = await created_instance(client, secrets={"OTHER_TOKEN": "second-value"})

        accepted: list[LifecycleOperationResult] = []
        for instance_id, secrets in (
            (first.instance_id, {"PROVIDER_TOKEN": "replaced-once"}),
            (second.instance_id, {"OTHER_TOKEN": _SENTINEL}),
            (first.instance_id, {"PROVIDER_TOKEN": _SENTINEL}),
        ):
            result = await client.call(
                INSTANCE_SECRETS_SET,
                InstanceSecretsSetCommand(instance_id=instance_id, secrets=secrets),
            )
            assert isinstance(result, LifecycleOperationResult)
            accepted.append(result)
        outcomes = [await finished_operation(client, result) for result in accepted]

        assert all(outcome.state is OperationState.SUCCEEDED for outcome in outcomes)
        assert len({result.operation_id for result in accepted}) == 3
        replaced = instance_environment(hub, first.instance_id)
        assert replaced["PROVIDER_TOKEN"] == _SENTINEL
        assert "OTHER_TOKEN" not in replaced
        other = instance_environment(hub, second.instance_id)
        assert other["OTHER_TOKEN"] == _SENTINEL
        assert "PROVIDER_TOKEN" not in other
        assert replaced[CONTROL_TOKEN_VARIABLE] != other[CONTROL_TOKEN_VARIABLE]

    asyncio.run(scenario())


def test_a_replacement_waits_for_the_stop_that_holds_the_instance(tmp_path):
    async def scenario() -> None:
        control = FakeControl(holds=True)
        hub = hub_at(tmp_path / "hub", images=FakeImages(), control=control)
        client = hub_client(hub)
        created = await started_instance(client, hub, secrets={"PROVIDER_TOKEN": "first-value"})

        stopping = await client.call(
            INSTANCE_STOP,
            InstanceStopCommand(instance_id=created.instance_id),
        )
        await asyncio.wait_for(control.asked.wait(), timeout=5)
        accepted = await client.call(
            INSTANCE_SECRETS_SET,
            InstanceSecretsSetCommand(
                instance_id=created.instance_id,
                secrets={"PROVIDER_TOKEN": _SENTINEL},
            ),
        )
        await asyncio.sleep(0.05)
        during = instance_environment(hub, created.instance_id)
        control.release.set()
        assert isinstance(stopping, LifecycleOperationResult)
        assert (await finished_operation(client, stopping)).state is OperationState.SUCCEEDED
        assert isinstance(accepted, LifecycleOperationResult)
        after = await finished_operation(client, accepted)

        assert during["PROVIDER_TOKEN"] == "first-value"
        assert after.state is OperationState.SUCCEEDED
        assert instance_environment(hub, created.instance_id)["PROVIDER_TOKEN"] == _SENTINEL

    asyncio.run(scenario())


def test_a_failed_write_keeps_the_previous_secrets_whole_and_stays_inspectable(
    tmp_path,
    monkeypatch,
):
    async def scenario() -> None:
        hub = hub_at(tmp_path / "hub", images=FakeImages())
        client = hub_client(hub)
        created = await created_instance(client, secrets={"PROVIDER_TOKEN": "first-value"})
        instance_path = hub.instances_directory / str(created.instance_id)
        original = os.open

        def refuse_to_write(path: object, *arguments: object, **options: object) -> int:
            """Stand in for the disk failing under the write: nothing else can trigger it."""
            named = Path(path) if isinstance(path, str | Path) else None
            if named is not None and named.parent == instance_path:
                raise OSError(f"no space left on device while writing {_SENTINEL}")
            return original(path, *arguments, **options)  # ty: ignore[invalid-argument-type]

        monkeypatch.setattr(os, "open", refuse_to_write)
        accepted = await client.call(
            INSTANCE_SECRETS_SET,
            InstanceSecretsSetCommand(
                instance_id=created.instance_id,
                secrets={"PROVIDER_TOKEN": _SENTINEL},
            ),
        )
        assert isinstance(accepted, LifecycleOperationResult)
        outcome = await finished_operation(client, accepted)
        monkeypatch.undo()

        assert outcome.state is OperationState.FAILED
        assert _SENTINEL not in outcome.detail
        assert "no space left on device" in outcome.detail
        assert [step.name for step in outcome.steps] == ["secrets"]
        assert instance_environment(hub, created.instance_id)["PROVIDER_TOKEN"] == "first-value"
        assert sorted(path.name for path in instance_path.glob(".env*")) == [".env"]

    asyncio.run(scenario())
