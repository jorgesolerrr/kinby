import asyncio
import sqlite3
from uuid import UUID

from kinby.contracts import (
    INSTANCE_LIST,
    INSTANCE_SECRETS_SET,
    INSTANCE_START,
    INSTANCE_STATUS,
    ErrorEnvelope,
    InstanceListCommand,
    InstanceSecretsSetCommand,
    InstanceSetup,
    InstanceStartCommand,
    InstanceStatusCommand,
    LifecycleOperationResult,
    LoginSetup,
    LoginState,
    OperationState,
    SecretSetup,
    SetupField,
    SetupFieldKind,
    SetupFieldType,
    SubscriptionLogin,
)
from kinby.hub import Hub
from kinby.packages import InstalledPackage, PackageDescriptor
from tests.test_hub import (
    FakeControl,
    FakeImages,
    FakeRuntime,
    finished_operation,
    hub_at,
    hub_client,
    prepared,
)
from tests.test_hub_logins import (
    CODEX,
    EDITOR,
    WRITER,
    created_writer,
    login_started,
    prompted,
)

TOKEN = SetupField(
    name="WRITER_TOKEN",
    label="Writer token",
    description="Publishes drafts.",
    kind=SetupFieldKind.SECRET,
    type=SetupFieldType.TEXT,
    required=True,
)
WEBHOOK = SetupField(
    name="WRITER_WEBHOOK",
    label="Webhook secret",
    description="Checks deliveries.",
    kind=SetupFieldKind.SECRET,
    type=SetupFieldType.TEXT,
    required=False,
)


def writer(*logins: SubscriptionLogin, fields: tuple[SetupField, ...] = ()) -> InstalledPackage:
    return InstalledPackage(
        descriptor=PackageDescriptor(
            id="writer",
            display_name="Writing teammate",
            description="Drafts articles.",
            icon="pen",
            distribution="kinby-writer",
            version="1.4.2",
            setup_fields=fields,
            logins=logins,
        ),
        files={"SYSTEM.md": "Write clearly.\n"},
    )


def setup_hub(tmp_path, images: FakeImages, runtime: FakeRuntime | None = None) -> Hub:
    return hub_at(
        tmp_path / "hub", runtime=runtime or FakeRuntime(), images=images, control=FakeControl()
    )


async def setup_of(hub: Hub, instance_id: UUID) -> InstanceSetup:
    status = await hub_client(hub).call(
        INSTANCE_STATUS, InstanceStatusCommand(instance_id=instance_id)
    )
    assert not isinstance(status, ErrorEnvelope)
    return status.setup


def test_a_created_instance_reads_its_logins_pending_and_which_secrets_are_set(tmp_path):
    async def scenario() -> None:
        package = writer(EDITOR, CODEX, fields=(TOKEN, WEBHOOK))
        hub = setup_hub(tmp_path, FakeImages(package=package))

        created = await created_writer(hub, secrets={"WRITER_TOKEN": "tok-private"})
        setup = await setup_of(hub, created.instance_id)

        assert "tok-private" not in setup.model_dump_json()
        assert setup == InstanceSetup(
            logins=[
                LoginSetup(
                    id="editor",
                    label="Editor account",
                    description="Signs the editor in with your subscription.",
                    state=LoginState.PENDING,
                ),
                LoginSetup(
                    id="codex",
                    label="Codex",
                    description="Signs Codex in with your ChatGPT plan.",
                    state=LoginState.PENDING,
                ),
            ],
            secrets=[
                SecretSetup(name="api_key", label="API key", required=True, is_set=True),
                SecretSetup(name="WRITER_TOKEN", label="Writer token", required=True, is_set=True),
                SecretSetup(
                    name="WRITER_WEBHOOK", label="Webhook secret", required=False, is_set=False
                ),
            ],
        )

    asyncio.run(scenario())


def login_states(setup: InstanceSetup) -> dict[str, LoginState]:
    return {login.id: login.state for login in setup.logins}


def running_logins(setup: InstanceSetup) -> dict[str, UUID | None]:
    return {login.id: login.operation_id for login in setup.logins}


def test_a_login_being_signed_in_names_its_operation_until_it_ends(tmp_path):
    async def scenario() -> None:
        runtime = FakeRuntime()
        runtime.setup_lines = ["Open https://auth.example/device and enter AB12-C3D"]
        hub = setup_hub(tmp_path, FakeImages(package=writer(EDITOR, CODEX)), runtime)
        instance_id = (await created_writer(hub)).instance_id
        signing_in = await login_started(hub, instance_id, "editor")
        await prompted(hub, signing_in)

        running = running_logins(await setup_of(hub, instance_id))
        runtime.setup_exits.set()
        await finished_operation(hub_client(hub), signing_in)

        assert running == {"editor": signing_in.operation_id, "codex": None}
        assert running_logins(await setup_of(hub, instance_id)) == {"editor": None, "codex": None}

    asyncio.run(scenario())


def test_a_login_that_signs_in_reads_signed_in_and_one_that_fails_reads_failed(tmp_path):
    async def scenario() -> None:
        runtime = FakeRuntime()
        runtime.setup_lines = ["Open https://auth.example/device and enter AB12-C3D"]
        runtime.setup_exits.set()
        hub = setup_hub(tmp_path, FakeImages(package=writer(EDITOR, CODEX)), runtime)
        instance_id = (await created_writer(hub)).instance_id

        signed_in = await login_started(hub, instance_id, "editor")
        await finished_operation(hub_client(hub), signed_in)
        runtime.setup_exit_code = 1
        failed = await login_started(hub, instance_id, "codex")
        await finished_operation(hub_client(hub), failed)

        setup = await setup_of(hub, instance_id)
        assert login_states(setup) == {
            "editor": LoginState.SIGNED_IN,
            "codex": LoginState.FAILED,
        }
        assert running_logins(setup) == {"editor": None, "codex": None}

    asyncio.run(scenario())


def test_signing_in_again_after_a_success_that_fails_reads_failed(tmp_path):
    async def scenario() -> None:
        runtime = FakeRuntime()
        runtime.setup_exits.set()
        hub = setup_hub(tmp_path, FakeImages(package=writer(EDITOR)), runtime)
        instance_id = (await created_writer(hub)).instance_id
        await finished_operation(hub_client(hub), await login_started(hub, instance_id, "editor"))

        runtime.setup_exit_code = 1
        await finished_operation(hub_client(hub), await login_started(hub, instance_id, "editor"))

        assert login_states(await setup_of(hub, instance_id)) == {"editor": LoginState.FAILED}

    asyncio.run(scenario())


def test_a_login_whose_code_expires_reads_failed(tmp_path, monkeypatch):
    monkeypatch.setattr("kinby.hub.service.LOGIN_SECONDS", 0.05)

    async def scenario() -> None:
        hub = setup_hub(tmp_path, FakeImages(package=writer(EDITOR)))
        instance_id = (await created_writer(hub)).instance_id

        expired = await finished_operation(
            hub_client(hub), await login_started(hub, instance_id, "editor")
        )

        assert "code expired" in expired.detail
        setup = await setup_of(hub, instance_id)
        assert login_states(setup) == {"editor": LoginState.FAILED}
        assert running_logins(setup) == {"editor": None}

    asyncio.run(scenario())


def test_a_login_a_restart_interrupted_reads_failed(tmp_path):
    async def scenario() -> None:
        hub = setup_hub(tmp_path, FakeImages(package=writer(EDITOR)))
        instance_id = (await created_writer(hub)).instance_id
        await login_started(hub, instance_id, "editor")
        hub.close()

        reopened = setup_hub(tmp_path, FakeImages(package=writer(EDITOR)))

        assert login_states(await setup_of(reopened, instance_id)) == {"editor": LoginState.FAILED}

    asyncio.run(scenario())


async def pending_by_instance(hub: Hub) -> dict[UUID, bool]:
    listed = await hub_client(hub).call(INSTANCE_LIST, InstanceListCommand())
    assert not isinstance(listed, ErrorEnvelope)
    return {summary.instance_id: summary.setup_pending for summary in listed.instances}


def test_setup_is_pending_until_every_login_signs_in(tmp_path):
    async def scenario() -> None:
        runtime = FakeRuntime()
        runtime.setup_exits.set()
        hub = setup_hub(tmp_path, FakeImages(package=writer(EDITOR, CODEX)), runtime)
        instance_id = (await created_writer(hub)).instance_id
        seeded = await pending_by_instance(hub)

        await finished_operation(hub_client(hub), await login_started(hub, instance_id, "editor"))
        one_left = await pending_by_instance(hub)
        await finished_operation(hub_client(hub), await login_started(hub, instance_id, "codex"))

        assert seeded == {instance_id: True}
        assert one_left == {instance_id: True}
        assert await pending_by_instance(hub) == {instance_id: False}

    asyncio.run(scenario())


def test_setup_is_pending_while_a_required_secret_is_not_set(tmp_path):
    async def scenario() -> None:
        images = FakeImages(package=writer())
        hub = setup_hub(tmp_path, images)
        instance_id = (await created_writer(hub)).instance_id
        complete = await pending_by_instance(hub)

        # A rebuilt image asks for a secret the instance was created without.
        images.package = writer(fields=(TOKEN, WEBHOOK))
        await prepared(hub_client(hub), WRITER)
        asked = await pending_by_instance(hub)
        replaced = await hub_client(hub).call(
            INSTANCE_SECRETS_SET,
            InstanceSecretsSetCommand(instance_id=instance_id, secrets={"WRITER_TOKEN": "tok"}),
        )
        assert isinstance(replaced, LifecycleOperationResult)
        await finished_operation(hub_client(hub), replaced)

        assert complete == {instance_id: False}
        assert asked == {instance_id: True}
        # The optional webhook secret is still not set, and that leaves nothing pending.
        assert await pending_by_instance(hub) == {instance_id: False}

    asyncio.run(scenario())


def test_an_instance_created_before_logins_were_tracked_reads_complete(tmp_path):
    async def scenario() -> None:
        hub = setup_hub(tmp_path, FakeImages(package=writer(EDITOR)))
        instance_id = (await created_writer(hub)).instance_id
        hub.close()
        with sqlite3.connect(tmp_path / "hub" / "registry.sqlite") as connection:
            connection.execute("DROP TABLE logins")

        reopened = setup_hub(tmp_path, FakeImages(package=writer(EDITOR)))

        assert login_states(await setup_of(reopened, instance_id)) == {
            "editor": LoginState.SIGNED_IN
        }
        assert await pending_by_instance(reopened) == {instance_id: False}

    asyncio.run(scenario())


def test_an_instance_starts_while_its_setup_is_pending(tmp_path):
    async def scenario() -> None:
        runtime = FakeRuntime()
        hub = setup_hub(tmp_path, FakeImages(package=writer(EDITOR)), runtime)
        instance_id = (await created_writer(hub)).instance_id

        started = await hub_client(hub).call(
            INSTANCE_START, InstanceStartCommand(instance_id=instance_id)
        )

        assert isinstance(started, LifecycleOperationResult)
        outcome = await finished_operation(hub_client(hub), started)
        assert outcome.state is OperationState.SUCCEEDED
        assert runtime.started == [str(instance_id)]
        assert await pending_by_instance(hub) == {instance_id: True}

    asyncio.run(scenario())
