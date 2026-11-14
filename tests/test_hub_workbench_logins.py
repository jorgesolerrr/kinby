"""A workbench holds the subscription logins it was created with: each keeps its credentials in a
volume of the workbench's own, which a recreation keeps."""

import asyncio
from uuid import UUID

from kinby.contracts import (
    WORKBENCH_CREATE,
    WORKBENCH_LOGIN_START,
    WORKBENCH_RECREATE,
    ErrorEnvelope,
    LoginSetup,
    LoginState,
    OperationState,
    StorageItem,
    StorageKind,
    WorkbenchLoginStartCommand,
    WorkbenchRecreateCommand,
)
from kinby.hub import Hub
from tests.test_hub import FakeRuntime, hub_at, hub_client
from tests.test_hub_logins import EDITOR
from tests.test_hub_workbenches import CEC_BACKEND, created, finished, got

SIGNED = CEC_BACKEND.model_copy(update={"logins": [EDITOR]})


async def signed_in(hub: Hub, runtime: FakeRuntime) -> tuple[UUID, str]:
    """Create a workbench with the editor login and sign it in. Its id and the login's volume."""
    client = hub_client(hub)
    workbench_id = (await created(client, SIGNED)).workbench_id
    runtime.setup_lines = ["Open https://auth.example/device and enter AB12-C3D"]
    runtime.setup_exits.set()
    login = await client.call(
        WORKBENCH_LOGIN_START,
        WorkbenchLoginStartCommand(workbench_id=workbench_id, login_id="editor"),
    )
    assert (await finished(client, login)).state is OperationState.SUCCEEDED
    return workbench_id, f"kinby-{workbench_id}-editor"


def test_a_workbench_mounts_a_volume_for_each_login_it_holds_none_signed_in_yet(tmp_path):
    runtime = FakeRuntime()
    hub = hub_at(tmp_path / "hub", runtime=runtime)

    async def scenario() -> None:
        workbench_id = (await created(hub_client(hub), SIGNED)).workbench_id
        workbench = await got(hub_client(hub), workbench_id)

        assert workbench.logins == [
            LoginSetup(
                id="editor",
                label=EDITOR.label,
                description=EDITOR.description,
                state=LoginState.PENDING,
            )
        ]
        assert (
            StorageItem(
                kind=StorageKind.VOLUME,
                source=f"kinby-{workbench_id}-editor",
                destination="/root/.editor",
                writable=True,
            )
            in runtime.created[0].storage
        )

    asyncio.run(scenario())


def test_a_login_signed_in_for_a_workbench_is_held_through_a_recreation(tmp_path):
    runtime = FakeRuntime()
    hub = hub_at(tmp_path / "hub", runtime=runtime)

    async def scenario() -> None:
        client = hub_client(hub)
        workbench_id, volume = await signed_in(hub, runtime)
        [setup] = runtime.setups
        recreated = await client.call(
            WORKBENCH_RECREATE, WorkbenchRecreateCommand(workbench_id=workbench_id)
        )
        assert (await finished(client, recreated)).state is OperationState.SUCCEEDED
        workbench = await got(client, workbench_id)

        assert setup.volume.source == volume
        assert [(login.id, login.state) for login in workbench.logins] == [
            ("editor", LoginState.SIGNED_IN)
        ]
        assert len(runtime.created) == 2
        assert volume in {item.source for item in runtime.created[-1].storage}

    asyncio.run(scenario())


def test_a_workbench_signs_in_no_login_it_does_not_hold(tmp_path):
    runtime = FakeRuntime()
    hub = hub_at(tmp_path / "hub", runtime=runtime)

    async def scenario() -> None:
        client = hub_client(hub)
        workbench_id = (await created(client)).workbench_id

        refused = await client.call(
            WORKBENCH_LOGIN_START,
            WorkbenchLoginStartCommand(workbench_id=workbench_id, login_id="editor"),
        )

        assert isinstance(refused, ErrorEnvelope)
        assert 'no login "editor"' in refused.message
        assert runtime.setups == []

    asyncio.run(scenario())


def test_a_workbench_with_two_logins_of_one_id_is_refused(tmp_path):
    hub = hub_at(tmp_path / "hub")

    async def scenario() -> None:
        twice = SIGNED.model_copy(update={"logins": [EDITOR, EDITOR]})

        refused = await hub_client(hub).call(WORKBENCH_CREATE, twice)

        assert isinstance(refused, ErrorEnvelope)
        assert refused.fields == {"logins": 'Login "editor" is declared more than once.'}

    asyncio.run(scenario())
