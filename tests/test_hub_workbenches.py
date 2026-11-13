"""A workbench is created, started, stopped and deleted through the hub contract."""

import asyncio
from uuid import UUID, uuid4

from pydantic import SecretStr

from kinby.cli.client import ContractClient
from kinby.contracts import (
    INSTANCE_LIST,
    STATS_SUMMARY,
    WORKBENCH_CREATE,
    WORKBENCH_DELETE,
    WORKBENCH_GET,
    WORKBENCH_LIST,
    WORKBENCH_START,
    WORKBENCH_STOP,
    ControlToken,
    ErrorCode,
    ErrorEnvelope,
    InstanceListCommand,
    InstanceListResult,
    IntendedState,
    OperationGetResult,
    OperationState,
    ProcessState,
    StatsGetCommand,
    Workbench,
    WorkbenchCreateCommand,
    WorkbenchCreateResult,
    WorkbenchDeleteCommand,
    WorkbenchGetCommand,
    WorkbenchListCommand,
    WorkbenchListResult,
    WorkbenchOperationResult,
    WorkbenchStartCommand,
    WorkbenchStopCommand,
)
from kinby.core.contract_server import CONTROL_TOKEN_VARIABLE
from kinby.hub import HttpInstanceControl, ImageSelection
from kinby.instance import Serve
from kinby.workbench import WorkbenchServer
from tests.test_drain import ServedRuntime
from tests.test_hub import (
    FakeControl,
    FakeImages,
    FakeRuntime,
    created_instance,
    finished_operation,
    hub_at,
    hub_client,
)
from tests.test_hub_deletion import RefusingVolume

CEC_BACKEND = WorkbenchCreateCommand(
    name="cec-backend",
    recipe="coder",
    workspace_source="https://github.com/owner/cec-backend.git",
    values={"MOODLE_URL": "https://moodle.example.com"},
    secrets={"GH_TOKEN": SecretStr("ghp-secret")},
)


async def created(
    client: ContractClient, command: WorkbenchCreateCommand = CEC_BACKEND
) -> WorkbenchCreateResult:
    """Create a workbench and wait for the hub to finish its creation."""
    accepted = await client.call(WORKBENCH_CREATE, command)
    assert isinstance(accepted, WorkbenchCreateResult), accepted
    assert (await finished_operation(client, accepted)).state is OperationState.SUCCEEDED
    return accepted


async def finished(client: ContractClient, accepted: object) -> OperationGetResult:
    assert isinstance(accepted, WorkbenchOperationResult), accepted
    return await finished_operation(client, accepted)


async def got(client: ContractClient, workbench_id: UUID) -> Workbench:
    workbench = await client.call(WORKBENCH_GET, WorkbenchGetCommand(workbench_id=workbench_id))
    assert isinstance(workbench, Workbench), workbench
    return workbench


async def started(client: ContractClient, runtime: FakeRuntime) -> UUID:
    """Create and start a workbench, whose container then answers at an address."""
    workbench_id = (await created(client)).workbench_id
    start = await client.call(WORKBENCH_START, WorkbenchStartCommand(workbench_id=workbench_id))
    assert (await finished(client, start)).state is OperationState.SUCCEEDED
    runtime.addresses[str(workbench_id)] = f"http://kinby-{workbench_id}:8787"
    return workbench_id


def test_a_created_workbench_is_listed_and_got_with_its_values_and_never_a_secret(tmp_path):
    images = FakeImages()
    runtime = FakeRuntime()
    hub = hub_at(tmp_path / "hub", images=images, runtime=runtime)
    client = hub_client(hub)

    async def scenario() -> None:
        accepted = await created(client)
        listed = await client.call(WORKBENCH_LIST, WorkbenchListCommand())
        workbench = await got(client, accepted.workbench_id)

        assert accepted.workbench.name == "cec-backend"
        assert (workbench.name, workbench.recipe, workbench.workspace_source) == (
            "cec-backend",
            "coder",
            "https://github.com/owner/cec-backend.git",
        )
        assert workbench.values == {"MOODLE_URL": "https://moodle.example.com"}
        assert workbench.secrets == ["GH_TOKEN"]
        assert (workbench.intended_state, workbench.process) == (
            IntendedState.STOPPED,
            ProcessState.CREATED,
        )
        assert isinstance(listed, WorkbenchListResult)
        assert listed.workbenches == [workbench]
        for answer in (accepted, listed, workbench):
            assert "ghp-secret" not in answer.model_dump_json()
        assert images.selections == [ImageSelection("HEAD", recipe="coder")]
        [container] = runtime.created
        assert container.command == ("workbench", "--listen", "0.0.0.0:8787")
        assert container.env["GH_TOKEN"] == "ghp-secret"
        assert container.env["KINBY_CONTROL_TOKEN"]
        assert "MOODLE_URL" not in container.env

    asyncio.run(scenario())


def test_the_instance_list_never_includes_a_workbench(tmp_path):
    hub = hub_at(tmp_path / "hub")
    client = hub_client(hub)

    async def scenario() -> None:
        instance = await created_instance(client)
        workbench = await created(client)

        instances = await client.call(INSTANCE_LIST, InstanceListCommand())
        workbenches = await client.call(WORKBENCH_LIST, WorkbenchListCommand())

        assert isinstance(instances, InstanceListResult)
        assert [summary.instance_id for summary in instances.instances] == [instance.instance_id]
        assert isinstance(workbenches, WorkbenchListResult)
        assert [bench.workbench_id for bench in workbenches.workbenches] == [workbench.workbench_id]

    asyncio.run(scenario())


def test_a_workbench_starts_stops_and_is_deleted_reporting_each_operation(tmp_path):
    runtime = FakeRuntime()
    control = FakeControl()
    hub = hub_at(tmp_path / "hub", runtime=runtime, control=control)
    client = hub_client(hub)

    async def scenario() -> None:
        workbench_id = await started(client, runtime)
        running = await got(client, workbench_id)

        stop = await client.call(WORKBENCH_STOP, WorkbenchStopCommand(workbench_id=workbench_id))
        stopped = await finished(client, stop)
        idle = await got(client, workbench_id)

        delete = await client.call(
            WORKBENCH_DELETE, WorkbenchDeleteCommand(workbench_id=workbench_id)
        )
        deletion = await finished(client, delete)
        listed = await client.call(WORKBENCH_LIST, WorkbenchListCommand())
        gone = await client.call(WORKBENCH_GET, WorkbenchGetCommand(workbench_id=workbench_id))

        assert (running.intended_state, running.process) == (
            IntendedState.RUNNING,
            ProcessState.RUNNING,
        )
        assert stopped.state is OperationState.SUCCEEDED
        assert control.forces == [False]
        assert (idle.intended_state, idle.process) == (IntendedState.STOPPED, ProcessState.STOPPED)
        assert deletion.state is OperationState.SUCCEEDED, deletion.detail
        assert runtime.removed == [str(workbench_id)]
        assert not (hub.instances_directory / str(workbench_id)).exists()
        assert runtime.deleted_volumes == [f"kinby-{workbench_id}-workspace"]
        assert isinstance(listed, WorkbenchListResult)
        assert listed.workbenches == []
        assert isinstance(gone, ErrorEnvelope)
        assert gone.code is ErrorCode.NOT_FOUND

    asyncio.run(scenario())


def test_a_running_workbench_is_drained_before_it_is_deleted(tmp_path):
    runtime = FakeRuntime()
    control = FakeControl()
    hub = hub_at(tmp_path / "hub", runtime=runtime, control=control)
    client = hub_client(hub)

    async def scenario() -> None:
        workbench_id = await started(client, runtime)

        delete = await client.call(
            WORKBENCH_DELETE, WorkbenchDeleteCommand(workbench_id=workbench_id)
        )

        assert (await finished(client, delete)).state is OperationState.SUCCEEDED
        assert control.forces == [False]
        assert runtime.removed == [str(workbench_id)]

    asyncio.run(scenario())


def test_a_create_that_does_not_check_out_creates_nothing(tmp_path):
    images = FakeImages()
    runtime = FakeRuntime()
    hub = hub_at(tmp_path / "hub", images=images, runtime=runtime)
    client = hub_client(hub)

    async def scenario() -> None:
        await created(client)
        taken = await client.call(WORKBENCH_CREATE, CEC_BACKEND)
        unknown = await client.call(
            WORKBENCH_CREATE,
            WorkbenchCreateCommand(
                name="landing", recipe="painter", secrets={"not a name": SecretStr("x")}
            ),
        )

        assert isinstance(taken, ErrorEnvelope)
        assert taken.code is ErrorCode.INVALID_SETUP
        assert taken.fields == {"name": 'A workbench named "cec-backend" exists already.'}
        assert isinstance(unknown, ErrorEnvelope)
        assert unknown.fields == {
            "recipe": 'kinby does not ship image recipe "painter".',
            "secrets": 'Invalid environment variable name: "not a name".',
        }
        assert len(runtime.created) == 1
        assert len(images.selections) == 1

    asyncio.run(scenario())


def test_an_instance_may_share_a_workbenchs_name(tmp_path):
    hub = hub_at(tmp_path / "hub")
    client = hub_client(hub)

    async def scenario() -> None:
        await created(client)
        await created_instance(client, name="cec-backend")

    asyncio.run(scenario())


def test_lifecycle_methods_find_no_workbench_under_an_unknown_id(tmp_path):
    client = hub_client(hub_at(tmp_path / "hub"))

    async def scenario() -> None:
        unknown = uuid4()
        answers = [
            await client.call(WORKBENCH_GET, WorkbenchGetCommand(workbench_id=unknown)),
            await client.call(WORKBENCH_START, WorkbenchStartCommand(workbench_id=unknown)),
            await client.call(WORKBENCH_STOP, WorkbenchStopCommand(workbench_id=unknown)),
            await client.call(WORKBENCH_DELETE, WorkbenchDeleteCommand(workbench_id=unknown)),
        ]

        for answer in answers:
            assert isinstance(answer, ErrorEnvelope)
            assert (answer.code, answer.message) == (
                ErrorCode.NOT_FOUND,
                f'Workbench "{unknown}" was not found.',
            )

    asyncio.run(scenario())


def test_an_instance_is_no_workbench(tmp_path):
    client = hub_client(hub_at(tmp_path / "hub"))

    async def scenario() -> None:
        instance = await created_instance(client)

        answer = await client.call(
            WORKBENCH_GET, WorkbenchGetCommand(workbench_id=instance.instance_id)
        )

        assert isinstance(answer, ErrorEnvelope)
        assert answer.code is ErrorCode.NOT_FOUND

    asyncio.run(scenario())


def test_the_stats_summary_asks_no_workbench(tmp_path):
    runtime = FakeRuntime()
    control = FakeControl()
    hub = hub_at(tmp_path / "hub", runtime=runtime, control=control)
    client = hub_client(hub)

    async def scenario() -> None:
        await started(client, runtime)

        await client.call(STATS_SUMMARY, StatsGetCommand())

        assert control.stats_asked == []

    asyncio.run(scenario())


def test_the_hub_drains_and_stops_a_workbench_through_its_runtime(tmp_path):
    container = ServedRuntime()
    hub = hub_at(tmp_path / "hub", runtime=container, control=HttpInstanceControl())
    client = hub_client(hub)

    async def scenario() -> OperationGetResult:
        workbench_id = (await created(client)).workbench_id
        start = await client.call(WORKBENCH_START, WorkbenchStartCommand(workbench_id=workbench_id))
        assert (await finished(client, start)).state is OperationState.SUCCEEDED
        token = ControlToken(container.created[0].env[CONTROL_TOKEN_VARIABLE])
        server = WorkbenchServer(Serve("127.0.0.1", 0), token)
        container.serving = await server.start()
        try:
            stop = await client.call(
                WORKBENCH_STOP, WorkbenchStopCommand(workbench_id=workbench_id)
            )
            return await finished(client, stop)
        finally:
            await server.stop()

    stopped = asyncio.run(scenario())

    assert stopped.state is OperationState.SUCCEEDED, stopped.detail
    assert ("drain", "Draining accepted work.") in [
        (step.name, step.detail) for step in stopped.steps
    ]


def test_a_workbench_deletion_that_failed_part_way_is_retried_to_the_end(tmp_path):
    runtime = RefusingVolume("")
    hub = hub_at(tmp_path / "hub", runtime=runtime)
    client = hub_client(hub)

    async def scenario() -> None:
        workbench_id = (await created(client)).workbench_id
        runtime.refused = f"kinby-{workbench_id}-workspace"
        first = await client.call(
            WORKBENCH_DELETE, WorkbenchDeleteCommand(workbench_id=workbench_id)
        )
        failed = await finished(client, first)
        runtime.refused = ""

        retry = await client.call(
            WORKBENCH_DELETE, WorkbenchDeleteCommand(workbench_id=workbench_id)
        )

        assert failed.state is OperationState.FAILED
        assert (await finished(client, retry)).state is OperationState.SUCCEEDED
        assert runtime.deleted_volumes == [f"kinby-{workbench_id}-workspace"]

    asyncio.run(scenario())
