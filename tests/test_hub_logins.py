import asyncio
from uuid import UUID

from kinby.contracts import (
    IMAGE_PREPARE,
    INSTANCE_CREATE,
    INSTANCE_DELETE,
    INSTANCE_DELETE_PREVIEW,
    INSTANCE_LIST,
    INSTANCE_LOGIN_START,
    INSTANCE_REMOVE,
    INSTANCE_START,
    INSTANCE_STATUS,
    INSTANCE_STOP,
    OPERATION_GET,
    ErrorCode,
    ErrorEnvelope,
    ImagePrepareCommand,
    ImagePrepareResult,
    InstanceCreateCommand,
    InstanceDeleteCommand,
    InstanceDeletePreviewCommand,
    InstanceDeletePreviewResult,
    InstanceListCommand,
    InstanceLoginStartCommand,
    InstanceRemoveCommand,
    InstanceStartCommand,
    InstanceStatusCommand,
    InstanceStatusResult,
    InstanceStopCommand,
    LifecycleOperationResult,
    LoginPrompt,
    OperationGetCommand,
    OperationGetResult,
    OperationKind,
    OperationState,
    PackageSelection,
    Scope,
    StorageItem,
    StorageKind,
    SubscriptionLogin,
)
from kinby.hub import Hub, SetupSpec
from kinby.hub.curated import curated_list
from kinby.packages import InstalledPackage, PackageDescriptor
from tests.test_hub import (
    FakeControl,
    FakeImages,
    FakeRuntime,
    finished_operation,
    hub_at,
    hub_client,
)

WRITER = PackageSelection(id="writer", distribution="kinby-writer", version="1.4.2")
EDITOR = SubscriptionLogin(
    id="editor",
    label="Editor account",
    description="Signs the editor in with your subscription.",
    command=["editor", "login", "--device"],
    volume="/root/.editor",
    prompt_pattern=r"Open (?P<url>https://\S+) and enter (?P<code>[A-Z0-9-]+)",
)
CODEX = SubscriptionLogin(
    id="codex",
    label="Codex",
    description="Signs Codex in with your ChatGPT plan.",
    command=["codex", "login", "--device-auth"],
    volume="/root/.codex",
    prompt_pattern=r"(?s)(?P<url>https://\S+).*?(?P<code>[A-Z0-9]{4}-[A-Z0-9]{5})",
)


def coder_package(*logins: SubscriptionLogin) -> InstalledPackage:
    return InstalledPackage(
        descriptor=PackageDescriptor(
            id="coder",
            display_name="Software factory",
            description="Implements issues.",
            icon="code",
            distribution="kinby-code-factory",
            version="1.0.0",
            logins=logins,
        ),
        files={"SYSTEM.md": "Write clearly.\n"},
    )


def coder_selection() -> PackageSelection:
    [coder] = [entry.package for entry in curated_list() if entry.package.id == "coder"]
    return coder.selection


def writer_package(*logins: SubscriptionLogin) -> InstalledPackage:
    return InstalledPackage(
        descriptor=PackageDescriptor(
            id="writer",
            display_name="Writing teammate",
            description="Drafts articles.",
            icon="pen",
            distribution="kinby-writer",
            version="1.4.2",
            logins=logins,
        ),
        files={"SYSTEM.md": "Write clearly.\n"},
    )


def writer_hub(
    tmp_path, *logins: SubscriptionLogin, runtime: FakeRuntime | None = None
) -> tuple[Hub, FakeRuntime]:
    runtime = runtime or FakeRuntime()
    images = FakeImages(package=writer_package(*(logins or (EDITOR,))))
    hub = hub_at(tmp_path / "hub", runtime=runtime, images=images, control=FakeControl())
    return hub, runtime


async def created_writer(
    hub: Hub, secrets: dict[str, str] | None = None
) -> LifecycleOperationResult:
    """Prepare the writer, create one instance from it, and wait until it is published."""
    client = hub_client(hub)
    accepted = await client.call(IMAGE_PREPARE, ImagePrepareCommand(package=WRITER))
    assert isinstance(accepted, ImagePrepareResult)
    assert (await finished_operation(client, accepted)).state is OperationState.SUCCEEDED
    created = await client.call(
        INSTANCE_CREATE,
        InstanceCreateCommand(
            manifest_id="writer",
            model="openai:gpt-5",
            package=WRITER,
            secrets={"api_key": "sk-private", **(secrets or {})},
        ),
    )
    assert isinstance(created, LifecycleOperationResult)
    assert (await finished_operation(client, created)).state is OperationState.SUCCEEDED
    return created


def volumes(storage: list[StorageItem] | tuple[StorageItem, ...]) -> set[tuple[str, str]]:
    return {(item.source, item.destination) for item in storage if item.kind is StorageKind.VOLUME}


def test_each_declared_login_gets_a_named_volume_the_instance_owns_and_mounts(tmp_path):
    async def scenario() -> None:
        hub, runtime = writer_hub(tmp_path, EDITOR, CODEX)

        instance_id = (await created_writer(hub)).instance_id

        listed = await hub_client(hub).call(INSTANCE_LIST, InstanceListCommand())
        assert not isinstance(listed, ErrorEnvelope)
        owned = {
            (f"kinby-{instance_id}-workspace", "/instance/workspace"),
            (f"kinby-{instance_id}-editor", "/root/.editor"),
            # The name the hub gave the Codex volume before packages declared their logins.
            (f"kinby-{instance_id}-codex", "/root/.codex"),
        }
        assert volumes(listed.instances[0].storage) == owned
        assert volumes(runtime.created[0].storage) == owned

    asyncio.run(scenario())


async def created_coder(hub: Hub) -> LifecycleOperationResult:
    """Prepare the pinned coder and create one instance from it."""
    client = hub_client(hub)
    selection = coder_selection()
    accepted = await client.call(IMAGE_PREPARE, ImagePrepareCommand(package=selection))
    assert isinstance(accepted, ImagePrepareResult)
    assert (await finished_operation(client, accepted)).state is OperationState.SUCCEEDED
    created = await client.call(
        INSTANCE_CREATE,
        InstanceCreateCommand(
            manifest_id="coder",
            model="openai:gpt-5",
            package=selection,
            secrets={"api_key": "sk-private"},
        ),
    )
    assert isinstance(created, LifecycleOperationResult)
    assert (await finished_operation(client, created)).state is OperationState.SUCCEEDED
    return created


def test_a_new_coder_keeps_a_codex_volume_until_its_package_declares_one(tmp_path):
    async def scenario() -> None:
        runtime = FakeRuntime()
        hub = hub_at(
            tmp_path / "hub",
            runtime=runtime,
            images=FakeImages(package=coder_package()),
            control=FakeControl(),
        )

        instance_id = (await created_coder(hub)).instance_id

        owned = {
            (f"kinby-{instance_id}-workspace", "/instance/workspace"),
            (f"kinby-{instance_id}-codex", "/root/.codex"),
        }
        assert volumes(runtime.created[0].storage) == owned

    asyncio.run(scenario())


def test_a_coder_that_declares_its_codex_login_gets_that_volume_once(tmp_path):
    async def scenario() -> None:
        runtime = FakeRuntime()
        hub = hub_at(
            tmp_path / "hub",
            runtime=runtime,
            images=FakeImages(package=coder_package(CODEX)),
            control=FakeControl(),
        )

        instance_id = (await created_coder(hub)).instance_id

        assert volumes(runtime.created[0].storage) == {
            (f"kinby-{instance_id}-workspace", "/instance/workspace"),
            (f"kinby-{instance_id}-codex", "/root/.codex"),
        }

    asyncio.run(scenario())


async def login_started(hub: Hub, instance_id: UUID, login_id: str) -> LifecycleOperationResult:
    accepted = await hub_client(hub).call(
        INSTANCE_LOGIN_START,
        InstanceLoginStartCommand(instance_id=instance_id, login_id=login_id),
    )
    assert isinstance(accepted, LifecycleOperationResult)
    return accepted


async def prompted(hub: Hub, accepted: LifecycleOperationResult) -> OperationGetResult:
    """Poll the login until its running step shows the URL and the code."""
    for _ in range(100):
        operation = await hub_client(hub).call(
            OPERATION_GET, OperationGetCommand(operation_id=accepted.operation_id)
        )
        assert isinstance(operation, OperationGetResult)
        if any(step.prompt is not None for step in operation.steps):
            return operation
        await asyncio.sleep(0.01)
    raise AssertionError("the login never showed a prompt")


def test_a_login_shows_the_url_and_code_its_setup_container_prints(tmp_path):
    async def scenario() -> None:
        runtime = FakeRuntime()
        runtime.setup_lines = [
            "Welcome to the editor.",
            "Open \x1b[94mhttps://auth.example/device\x1b[0m and enter \x1b[1mAB12-C3D\x1b[0m",
            "Open https://auth.example/other and enter ZZZZ-ZZZ",
        ]
        hub, _ = writer_hub(tmp_path, runtime=runtime)
        created = await created_writer(hub)

        accepted = await login_started(hub, created.instance_id, "editor")
        operation = await prompted(hub, accepted)

        assert accepted.instance_id == created.instance_id
        assert (operation.instance_id, operation.kind, operation.state) == (
            created.instance_id,
            OperationKind.LOGIN,
            OperationState.RUNNING,
        )
        assert [(step.name, step.state, step.prompt) for step in operation.steps] == [
            ("container", OperationState.SUCCEEDED, None),
            (
                "sign-in",
                OperationState.RUNNING,
                LoginPrompt(url="https://auth.example/device", code="AB12-C3D"),
            ),
        ]
        assert runtime.setups == [
            SetupSpec(
                instance_id=str(created.instance_id),
                image="sha256:selected-image",
                command=["editor", "login", "--device"],
                volume=StorageItem(
                    kind=StorageKind.VOLUME,
                    source=f"kinby-{created.instance_id}-editor",
                    destination="/root/.editor",
                    writable=True,
                ),
            )
        ]

    asyncio.run(scenario())


def test_a_login_whose_command_exits_zero_succeeds_and_its_container_goes(tmp_path):
    async def scenario() -> None:
        runtime = FakeRuntime()
        runtime.setup_lines = ["Open https://auth.example/device and enter AB12-C3D"]
        hub, _ = writer_hub(tmp_path, runtime=runtime)
        created = await created_writer(hub)
        accepted = await login_started(hub, created.instance_id, "editor")
        await prompted(hub, accepted)

        runtime.setup_exits.set()
        outcome = await finished_operation(hub_client(hub), accepted)

        assert (outcome.state, outcome.detail) == (OperationState.SUCCEEDED, "Signed in.")
        assert [(step.name, step.state) for step in outcome.steps] == [
            ("container", OperationState.SUCCEEDED),
            ("sign-in", OperationState.SUCCEEDED),
            ("verify", OperationState.SUCCEEDED),
        ]
        assert runtime.setups_running == 0

    asyncio.run(scenario())


def test_a_login_whose_command_fails_fails_with_its_exit_code_and_last_words(tmp_path):
    async def scenario() -> None:
        runtime = FakeRuntime()
        runtime.setup_lines = ["Open https://auth.example/device and enter AB12-C3D", "Denied.", ""]
        runtime.setup_exit_code = 1
        runtime.setup_exits.set()
        hub, _ = writer_hub(tmp_path, runtime=runtime)
        created = await created_writer(hub)

        outcome = await finished_operation(
            hub_client(hub), await login_started(hub, created.instance_id, "editor")
        )

        assert outcome.state is OperationState.FAILED
        assert outcome.detail == "The sign-in exited with code 1. Denied."
        assert [(step.name, step.state) for step in outcome.steps][-1] == (
            "verify",
            OperationState.FAILED,
        )

    asyncio.run(scenario())


def test_a_login_nobody_finishes_expires_and_its_container_goes(tmp_path, monkeypatch):
    monkeypatch.setattr("kinby.hub.service.LOGIN_SECONDS", 0.05)

    async def scenario() -> None:
        runtime = FakeRuntime()
        runtime.setup_lines = ["Open https://auth.example/device and enter AB12-C3D"]
        hub, _ = writer_hub(tmp_path, runtime=runtime)
        created = await created_writer(hub)

        outcome = await finished_operation(
            hub_client(hub), await login_started(hub, created.instance_id, "editor")
        )

        assert outcome.state is OperationState.FAILED
        assert "code expired" in outcome.detail
        assert [(step.name, step.state) for step in outcome.steps][-1] == (
            "sign-in",
            OperationState.FAILED,
        )
        assert runtime.setups_running == 0

    asyncio.run(scenario())


def test_a_url_and_code_on_separate_lines_are_found_together(tmp_path):
    async def scenario() -> None:
        runtime = FakeRuntime()
        runtime.setup_lines = [
            "1. Open this link in your browser and sign in to your account",
            "   https://auth.openai.com/codex/device",
            "2. Enter this one-time code (expires in 15 minutes)",
            "   ABCD-12345",
        ]
        hub, _ = writer_hub(tmp_path, CODEX, runtime=runtime)
        created = await created_writer(hub)

        operation = await prompted(hub, await login_started(hub, created.instance_id, "codex"))

        assert operation.steps[-1].prompt == LoginPrompt(
            url="https://auth.openai.com/codex/device", code="ABCD-12345"
        )

    asyncio.run(scenario())


def test_starting_a_login_that_runs_returns_it_with_the_same_code(tmp_path):
    async def scenario() -> None:
        runtime = FakeRuntime()
        runtime.setup_lines = ["Open https://auth.example/device and enter AB12-C3D", "ABCD-12345"]
        hub, _ = writer_hub(tmp_path, EDITOR, CODEX, runtime=runtime)
        created = await created_writer(hub)
        first = await login_started(hub, created.instance_id, "editor")
        shown = await prompted(hub, first)

        again = await login_started(hub, created.instance_id, "editor")
        other = await login_started(hub, created.instance_id, "codex")

        assert again == first
        assert await prompted(hub, again) == shown
        assert other.operation_id != first.operation_id
        await prompted(hub, other)
        assert [spec.command[0] for spec in runtime.setups] == ["editor", "codex"]

    asyncio.run(scenario())


def test_a_login_the_package_does_not_declare_is_not_found_and_runs_nothing(tmp_path):
    async def scenario() -> None:
        runtime = FakeRuntime()
        hub, _ = writer_hub(tmp_path, runtime=runtime)
        created = await created_writer(hub)

        refused = await hub_client(hub).call(
            INSTANCE_LOGIN_START,
            InstanceLoginStartCommand(instance_id=created.instance_id, login_id="mailer"),
        )

        assert isinstance(refused, ErrorEnvelope)
        assert refused.code is ErrorCode.NOT_FOUND
        assert runtime.setups == []

    asyncio.run(scenario())


def test_a_login_leaves_the_instance_free_to_start_and_stop(tmp_path):
    async def scenario() -> None:
        runtime = FakeRuntime()
        runtime.setup_lines = ["Open https://auth.example/device and enter AB12-C3D"]
        hub, _ = writer_hub(tmp_path, runtime=runtime)
        client = hub_client(hub)
        created = await created_writer(hub)
        instance_id = created.instance_id
        login = await login_started(hub, instance_id, "editor")
        await prompted(hub, login)

        started = await client.call(INSTANCE_START, InstanceStartCommand(instance_id=instance_id))
        assert isinstance(started, LifecycleOperationResult)
        assert (await finished_operation(client, started)).state is OperationState.SUCCEEDED
        status = await client.call(INSTANCE_STATUS, InstanceStatusCommand(instance_id=instance_id))
        runtime.addresses[str(instance_id)] = f"http://{instance_id}:8787"
        stopped = await client.call(INSTANCE_STOP, InstanceStopCommand(instance_id=instance_id))
        assert isinstance(stopped, LifecycleOperationResult)
        assert (await finished_operation(client, stopped)).state is OperationState.SUCCEEDED
        runtime.setup_exits.set()

        assert isinstance(status, InstanceStatusResult)
        # A client that lost a start's answer finds the start here, never the login.
        assert status.active_operation_id is None
        assert (await finished_operation(client, login)).state is OperationState.SUCCEEDED

    asyncio.run(scenario())


def test_removing_an_instance_is_refused_while_one_of_its_logins_runs(tmp_path):
    async def scenario() -> None:
        runtime = FakeRuntime()
        hub, _ = writer_hub(tmp_path, runtime=runtime)
        client = hub_client(hub)
        created = await created_writer(hub)
        login = await login_started(hub, created.instance_id, "editor")

        refused = await client.call(
            INSTANCE_REMOVE, InstanceRemoveCommand(instance_id=created.instance_id)
        )
        runtime.setup_exits.set()
        await finished_operation(client, login)
        removed = await client.call(
            INSTANCE_REMOVE, InstanceRemoveCommand(instance_id=created.instance_id)
        )

        assert isinstance(refused, ErrorEnvelope)
        assert refused.code is ErrorCode.INSTANCE_BUSY
        assert str(login.operation_id) in refused.message
        assert isinstance(removed, LifecycleOperationResult)
        assert (await finished_operation(client, removed)).state is OperationState.SUCCEEDED

    asyncio.run(scenario())


class HeldRemoval(FakeRuntime):
    """Hold the container's removal until the test releases it, so a removal stays in flight."""

    def __init__(self) -> None:
        super().__init__()
        self.removing = asyncio.Event()
        self.released = asyncio.Event()

    async def remove(self, instance_id: str, *, delete_data: bool = False) -> None:
        self.removing.set()
        await self.released.wait()
        await super().remove(instance_id, delete_data=delete_data)


def test_deleting_an_instance_is_refused_while_one_of_its_logins_runs(tmp_path):
    async def scenario() -> None:
        runtime = HeldRemoval()
        hub, _ = writer_hub(tmp_path, runtime=runtime)
        client = hub_client(hub)
        created = await created_writer(hub)
        instance_id = created.instance_id
        removal = await client.call(INSTANCE_REMOVE, InstanceRemoveCommand(instance_id=instance_id))
        assert isinstance(removal, LifecycleOperationResult)
        await runtime.removing.wait()
        # The instance is still listed until its container is gone, so a login can start.
        login = await login_started(hub, instance_id, "editor")
        runtime.released.set()
        assert (await finished_operation(client, removal)).state is OperationState.SUCCEEDED
        preview = await client.call(
            INSTANCE_DELETE_PREVIEW, InstanceDeletePreviewCommand(instance_id=instance_id)
        )
        assert isinstance(preview, InstanceDeletePreviewResult)

        refused = await client.call(
            INSTANCE_DELETE,
            InstanceDeleteCommand(
                instance_id=instance_id,
                directories=preview.directories,
                volumes=preview.volumes,
            ),
        )

        assert isinstance(refused, ErrorEnvelope)
        assert refused.code is ErrorCode.INSTANCE_BUSY
        assert str(login.operation_id) in refused.message
        assert runtime.deleted_volumes == []

    asyncio.run(scenario())


def test_only_an_admin_session_starts_a_login(tmp_path):
    async def scenario() -> None:
        runtime = FakeRuntime()
        hub, _ = writer_hub(tmp_path, runtime=runtime)
        created = await created_writer(hub)

        refused = await hub_client(hub, {Scope.HUB_READ}).call(
            INSTANCE_LOGIN_START,
            InstanceLoginStartCommand(instance_id=created.instance_id, login_id="editor"),
        )

        assert isinstance(refused, ErrorEnvelope)
        assert refused.code is ErrorCode.PERMISSION_DENIED
        assert runtime.setups == []

    asyncio.run(scenario())
