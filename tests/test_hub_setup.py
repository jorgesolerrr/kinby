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
    InstanceStatusResult,
    LifecycleOperationResult,
    LoginSetup,
    LoginState,
    OperationState,
    RepositoryCommandList,
    SecretSetup,
    SetupField,
    SetupFieldKind,
    SetupFieldType,
)
from kinby.hub import Hub
from tests.test_factory_runs import FILES, LISTS_FACTORY, factory_hub, started_coder
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
    created_writer,
    login_started,
    prompted,
    writer,
)
from tests.test_steps import hold_commands

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


def setup_hub(tmp_path, images: FakeImages, runtime: FakeRuntime | None = None) -> Hub:
    return hub_at(
        tmp_path / "hub", runtime=runtime or FakeRuntime(), images=images, control=FakeControl()
    )


async def status_of(hub: Hub, instance_id: UUID) -> InstanceStatusResult:
    status = await hub_client(hub).call(
        INSTANCE_STATUS, InstanceStatusCommand(instance_id=instance_id)
    )
    assert not isinstance(status, ErrorEnvelope)
    return status


async def setup_of(hub: Hub, instance_id: UUID) -> InstanceSetup:
    return (await status_of(hub, instance_id)).setup


def test_a_created_instance_reads_its_logins_pending_and_which_secrets_are_set(tmp_path):
    async def scenario() -> None:
        declared = writer(EDITOR, CODEX, fields=(TOKEN, WEBHOOK))
        hub = setup_hub(tmp_path, FakeImages(declared=declared))

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
                SecretSetup(
                    name="api_key",
                    variable="OPENAI_API_KEY",
                    label="API key",
                    required=True,
                    is_set=True,
                ),
                SecretSetup(
                    name="WRITER_TOKEN",
                    variable="WRITER_TOKEN",
                    label="Writer token",
                    required=True,
                    is_set=True,
                ),
                SecretSetup(
                    name="WRITER_WEBHOOK",
                    variable="WRITER_WEBHOOK",
                    label="Webhook secret",
                    required=False,
                    is_set=False,
                ),
            ],
            missing_commands=[],
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
        hub = setup_hub(tmp_path, FakeImages(declared=writer(EDITOR, CODEX)), runtime)
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
        hub = setup_hub(tmp_path, FakeImages(declared=writer(EDITOR, CODEX)), runtime)
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


def test_signing_in_again_after_a_success_that_fails_stays_signed_in(tmp_path):
    async def scenario() -> None:
        runtime = FakeRuntime()
        runtime.setup_exits.set()
        hub = setup_hub(tmp_path, FakeImages(declared=writer(EDITOR)), runtime)
        instance_id = (await created_writer(hub)).instance_id
        await finished_operation(hub_client(hub), await login_started(hub, instance_id, "editor"))

        runtime.setup_exit_code = 1
        retried = await finished_operation(
            hub_client(hub), await login_started(hub, instance_id, "editor")
        )

        assert retried.state is OperationState.FAILED
        assert login_states(await setup_of(hub, instance_id)) == {"editor": LoginState.SIGNED_IN}
        assert await pending_by_instance(hub) == {instance_id: False}

    asyncio.run(scenario())


def test_signing_in_again_after_a_success_whose_code_expires_stays_signed_in(tmp_path, monkeypatch):
    async def scenario() -> None:
        runtime = FakeRuntime()
        runtime.setup_exits.set()
        hub = setup_hub(tmp_path, FakeImages(declared=writer(EDITOR)), runtime)
        instance_id = (await created_writer(hub)).instance_id
        await finished_operation(hub_client(hub), await login_started(hub, instance_id, "editor"))

        runtime.setup_exits.clear()
        monkeypatch.setattr("kinby.hub.service.LOGIN_SECONDS", 0.05)
        expired = await finished_operation(
            hub_client(hub), await login_started(hub, instance_id, "editor")
        )

        assert expired.state is OperationState.FAILED
        assert expired.detail == "The code expired. Sign in again for a new one."
        assert login_states(await setup_of(hub, instance_id)) == {"editor": LoginState.SIGNED_IN}

    asyncio.run(scenario())


def test_a_login_whose_code_expires_reads_failed(tmp_path, monkeypatch):
    monkeypatch.setattr("kinby.hub.service.LOGIN_SECONDS", 0.05)

    async def scenario() -> None:
        hub = setup_hub(tmp_path, FakeImages(declared=writer(EDITOR)))
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
        hub = setup_hub(tmp_path, FakeImages(declared=writer(EDITOR)))
        instance_id = (await created_writer(hub)).instance_id
        await login_started(hub, instance_id, "editor")
        hub.close()

        reopened = setup_hub(tmp_path, FakeImages(declared=writer(EDITOR)))

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
        hub = setup_hub(tmp_path, FakeImages(declared=writer(EDITOR, CODEX)), runtime)
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
        images = FakeImages(declared=writer())
        hub = setup_hub(tmp_path, images)
        instance_id = (await created_writer(hub)).instance_id
        complete = await pending_by_instance(hub)

        # A rebuilt image asks for a secret the instance was created without.
        images.declared = writer(fields=(TOKEN, WEBHOOK))
        await prepared(hub_client(hub))
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


def test_an_api_key_set_from_the_setup_card_reads_set(tmp_path):
    async def scenario() -> None:
        hub = setup_hub(tmp_path, FakeImages(declared=writer()))
        instance_id = (await created_writer(hub)).instance_id
        # An instance whose secrets lost the key, which only a hand edit can bring about.
        (hub.instances_directory / str(instance_id) / ".env").write_text("")
        missing = await setup_of(hub, instance_id)

        replaced = await hub_client(hub).call(
            INSTANCE_SECRETS_SET,
            InstanceSecretsSetCommand(instance_id=instance_id, secrets={"api_key": "sk-new"}),
        )
        assert isinstance(replaced, LifecycleOperationResult)
        await finished_operation(hub_client(hub), replaced)

        assert missing.secrets == [
            SecretSetup(
                name="api_key",
                variable="OPENAI_API_KEY",
                label="API key",
                required=True,
                is_set=False,
            )
        ]
        assert (await setup_of(hub, instance_id)).secrets == [
            SecretSetup(
                name="api_key",
                variable="OPENAI_API_KEY",
                label="API key",
                required=True,
                is_set=True,
            )
        ]
        assert await pending_by_instance(hub) == {instance_id: False}

    asyncio.run(scenario())


def test_a_secret_the_instance_code_declares_is_pending_until_it_is_set(tmp_path):
    async def scenario() -> None:
        hub = setup_hub(tmp_path, FakeImages(declared=writer()))
        instance_id = (await created_writer(hub)).instance_id
        record = hub.registry.instance(instance_id)
        assert record is not None
        project = record.path / "code" / "pyproject.toml"
        project.parent.mkdir()
        project.write_text('[tool.kinby]\nsecrets = ["SEARCH_TOKEN"]\n', encoding="utf-8")
        declared = await setup_of(hub, instance_id)
        asked = await pending_by_instance(hub)
        replaced = await hub_client(hub).call(
            INSTANCE_SECRETS_SET,
            InstanceSecretsSetCommand(instance_id=instance_id, secrets={"SEARCH_TOKEN": "tok"}),
        )
        assert isinstance(replaced, LifecycleOperationResult)
        await finished_operation(hub_client(hub), replaced)

        assert declared.secrets[-1] == SecretSetup(
            name="SEARCH_TOKEN",
            variable="SEARCH_TOKEN",
            label="SEARCH_TOKEN",
            required=True,
            is_set=False,
        )
        assert asked == {instance_id: True}
        assert await pending_by_instance(hub) == {instance_id: False}

    asyncio.run(scenario())


def test_an_optional_secret_the_instance_code_declares_becomes_required(tmp_path):
    async def scenario() -> None:
        hub = setup_hub(tmp_path, FakeImages(declared=writer(fields=(WEBHOOK,))))
        instance_id = (await created_writer(hub)).instance_id
        record = hub.registry.instance(instance_id)
        assert record is not None
        project = record.path / "code" / "pyproject.toml"
        project.parent.mkdir()
        project.write_text('[tool.kinby]\nsecrets = ["WRITER_WEBHOOK"]\n', encoding="utf-8")

        setup = await setup_of(hub, instance_id)

        assert [s for s in setup.secrets if s.variable == "WRITER_WEBHOOK"] == [
            SecretSetup(
                name="WRITER_WEBHOOK",
                variable="WRITER_WEBHOOK",
                label="Webhook secret",
                required=True,
                is_set=False,
            )
        ]
        assert await pending_by_instance(hub) == {instance_id: True}

    asyncio.run(scenario())


def test_an_instance_created_before_logins_were_tracked_reads_complete(tmp_path):
    async def scenario() -> None:
        hub = setup_hub(tmp_path, FakeImages(declared=writer(EDITOR)))
        instance_id = (await created_writer(hub)).instance_id
        hub.close()
        with sqlite3.connect(tmp_path / "hub" / "registry.sqlite") as connection:
            connection.execute("DROP TABLE logins")

        reopened = setup_hub(tmp_path, FakeImages(declared=writer(EDITOR)))

        assert login_states(await setup_of(reopened, instance_id)) == {
            "editor": LoginState.SIGNED_IN
        }
        assert await pending_by_instance(reopened) == {instance_id: False}

    asyncio.run(scenario())


def test_an_instance_starts_while_its_setup_is_pending(tmp_path):
    async def scenario() -> None:
        runtime = FakeRuntime()
        hub = setup_hub(tmp_path, FakeImages(declared=writer(EDITOR)), runtime)
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


async def secret_variables(hub: Hub, instance_id: UUID) -> dict[str, str | None]:
    return {secret.name: secret.variable for secret in (await setup_of(hub, instance_id)).secrets}


def test_each_secret_names_the_variable_its_value_lands_in(tmp_path):
    async def scenario() -> None:
        hub = setup_hub(tmp_path, FakeImages(declared=writer(fields=(TOKEN, WEBHOOK))))
        instance_id = (
            await created_writer(
                hub, secrets={"WRITER_TOKEN": "tok"}, model="anthropic:claude-opus-5-5"
            )
        ).instance_id

        assert await secret_variables(hub, instance_id) == {
            "api_key": "ANTHROPIC_API_KEY",
            "WRITER_TOKEN": "WRITER_TOKEN",
            "WRITER_WEBHOOK": "WRITER_WEBHOOK",
        }

    asyncio.run(scenario())


def test_the_api_key_names_no_variable_when_the_model_cannot_be_read(tmp_path):
    async def scenario() -> None:
        hub = setup_hub(tmp_path, FakeImages(declared=writer(fields=(TOKEN, WEBHOOK))))
        instance_id = (await created_writer(hub, secrets={"WRITER_TOKEN": "tok"})).instance_id
        (hub.instances_directory / str(instance_id) / "kinby.toml").write_text("not = [toml")

        assert await secret_variables(hub, instance_id) == {
            "api_key": None,
            "WRITER_TOKEN": "WRITER_TOKEN",
            "WRITER_WEBHOOK": "WRITER_WEBHOOK",
        }

    asyncio.run(scenario())


def test_setup_names_the_lists_its_factory_role_needs_until_each_is_recorded(tmp_path):
    async def scenario() -> None:
        runtime = FakeRuntime()
        files = FILES | {"factory.yaml": LISTS_FACTORY}
        hub = factory_hub(tmp_path / "hub", FakeControl(), runtime, files)
        # Pending setup never blocks the start, so the coder can run the survey.
        coder = await started_coder(hub, runtime)
        none_held = await setup_of(hub, coder)
        none_pending = await pending_by_instance(hub)
        hold_commands(hub.instances_directory / str(coder), "checks = []\n")
        one_held = await setup_of(hub, coder)
        hold_commands(hub.instances_directory / str(coder), 'checks = []\nsetup = ["uv sync"]\n')

        assert none_held.missing_commands == ["setup", "checks"]
        assert none_pending == {coder: True}
        assert one_held.missing_commands == ["setup"]
        assert (await setup_of(hub, coder)).missing_commands == []
        assert await pending_by_instance(hub) == {coder: False}

    asyncio.run(scenario())


def test_an_instance_whose_factory_names_no_list_has_no_commands_pending(tmp_path):
    async def scenario() -> None:
        runtime = FakeRuntime()
        hub = factory_hub(tmp_path / "hub", FakeControl(), runtime)
        coder = await started_coder(hub, runtime)

        assert (await setup_of(hub, coder)).missing_commands == []
        assert await pending_by_instance(hub) == {coder: False}

    asyncio.run(scenario())


def test_status_carries_each_list_an_instance_holds_when_its_factory_role_needs_any(tmp_path):
    async def scenario() -> None:
        runtime = FakeRuntime()
        files = FILES | {"factory.yaml": LISTS_FACTORY}
        hub = factory_hub(tmp_path / "hub", FakeControl(), runtime, files)
        coder = await started_coder(hub, runtime)
        none_held = await status_of(hub, coder)
        hold_commands(
            hub.instances_directory / str(coder),
            'checks = ["uv run ruff check .", "uv run pytest"]\nsetup = []\n',
        )

        assert none_held.repository_commands == []
        assert (await status_of(hub, coder)).repository_commands == [
            RepositoryCommandList(name="checks", commands=["uv run ruff check .", "uv run pytest"]),
            RepositoryCommandList(name="setup", commands=[]),
        ]

    asyncio.run(scenario())


def test_status_carries_no_list_when_the_instances_factory_role_needs_none(tmp_path):
    async def scenario() -> None:
        runtime = FakeRuntime()
        hub = factory_hub(tmp_path / "hub", FakeControl(), runtime)
        coder = await started_coder(hub, runtime)
        hold_commands(hub.instances_directory / str(coder), 'setup = ["uv sync"]\n')

        assert (await status_of(hub, coder)).repository_commands == []

    asyncio.run(scenario())
