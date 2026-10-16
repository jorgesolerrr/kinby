"""The home instance: the hub creates and marks one on start, and never removes it."""

import asyncio

from kinby.contracts import (
    INSTANCE_LIST,
    INSTANCE_REMOVE,
    INSTANCE_STATUS,
    ErrorCode,
    ErrorEnvelope,
    InstanceListCommand,
    InstanceListResult,
    InstanceRemoveCommand,
    InstanceStatusCommand,
    InstanceStatusResult,
    IntendedState,
    LifecycleOperationResult,
    OperationState,
)
from kinby.hub import Hub, RuntimeStatus
from tests.test_hub import (
    FakeImages,
    FakeRuntime,
    UnavailableRuntime,
    created_instance,
    finished_operation,
    hub_at,
    hub_client,
)


async def _listed(hub: Hub) -> InstanceListResult:
    listed = await hub_client(hub).call(INSTANCE_LIST, InstanceListCommand())
    assert isinstance(listed, InstanceListResult)
    return listed


async def _home_created(hub: Hub) -> LifecycleOperationResult:
    """Start the hub, and wait for the home instance it creates."""
    recovery = await hub.recover()
    assert recovery.home is not None
    assert (await finished_operation(hub_client(hub), recovery.home)).state is (
        OperationState.SUCCEEDED
    )
    return recovery.home


def test_starting_a_hub_with_no_home_instance_creates_kinby_and_marks_it(tmp_path):
    async def scenario() -> None:
        runtime = FakeRuntime()
        hub = hub_at(tmp_path / "hub", runtime=runtime)

        home = await _home_created(hub)
        listed = await _listed(hub)
        status = await hub_client(hub).call(
            INSTANCE_STATUS, InstanceStatusCommand(instance_id=home.instance_id)
        )

        [kinby] = listed.instances
        assert kinby.instance_id == home.instance_id
        assert kinby.persona_name == "Kinby"
        assert kinby.home
        assert kinby.intended_state is IntendedState.STOPPED
        assert kinby.factory_role is None
        # No API key was given, so the instance waits for one.
        assert kinby.setup_pending
        assert [spec.instance_id for spec in runtime.created] == [str(home.instance_id)]
        assert isinstance(status, InstanceStatusResult)
        assert status.home
        hub.close()

    asyncio.run(scenario())


def test_starting_a_hub_that_has_a_home_instance_creates_nothing(tmp_path):
    async def scenario() -> None:
        runtime = FakeRuntime()
        hub = hub_at(tmp_path / "hub", runtime=runtime)
        home = await _home_created(hub)
        hub.close()

        reopened = hub_at(tmp_path / "hub", runtime=runtime)
        recovery = await reopened.recover()

        assert recovery.home is None
        assert [instance.instance_id for instance in (await _listed(reopened)).instances] == [
            home.instance_id
        ]
        assert len(runtime.created) == 1
        reopened.close()

    asyncio.run(scenario())


def test_only_the_home_instance_is_marked_home(tmp_path):
    async def scenario() -> None:
        hub = hub_at(tmp_path / "hub")
        home = await _home_created(hub)
        other = await created_instance(hub_client(hub))

        listed = await _listed(hub)
        status = await hub_client(hub).call(
            INSTANCE_STATUS, InstanceStatusCommand(instance_id=other.instance_id)
        )

        assert {instance.instance_id: instance.home for instance in listed.instances} == {
            home.instance_id: True,
            other.instance_id: False,
        }
        assert isinstance(status, InstanceStatusResult)
        assert not status.home
        hub.close()

    asyncio.run(scenario())


def test_a_home_instance_whose_creation_failed_is_created_again_on_the_next_start(tmp_path):
    async def scenario() -> None:
        runtime = FakeRuntime()
        hub = hub_at(tmp_path / "hub", runtime=runtime, images=FakeImages(failure="no network"))
        recovery = await hub.recover()
        assert recovery.home is not None
        failed = await finished_operation(hub_client(hub), recovery.home)
        assert failed.state is OperationState.FAILED
        hub.close()

        reopened = hub_at(tmp_path / "hub", runtime=runtime)
        home = await _home_created(reopened)

        [kinby] = (await _listed(reopened)).instances
        assert kinby.instance_id == home.instance_id
        assert kinby.home
        reopened.close()

    asyncio.run(scenario())


def test_a_home_creation_interrupted_after_its_container_is_kept_while_the_runtime_is_away(
    tmp_path,
):
    async def scenario() -> None:
        runtime = FakeRuntime()
        runtime.create_failure = asyncio.CancelledError()
        hub = hub_at(tmp_path / "hub", runtime=runtime)
        interrupted = (await hub.recover()).home
        assert interrupted is not None
        while not runtime.created:
            await asyncio.sleep(0.01)
        hub.close()
        # Docker made the container before the hub stopped.
        runtime.states[str(interrupted.instance_id)] = RuntimeStatus("created", None)
        runtime.create_failure = None

        unavailable = hub_at(tmp_path / "hub", runtime=UnavailableRuntime())
        while_away = await unavailable.recover()
        unavailable.close()
        reopened = hub_at(tmp_path / "hub", runtime=runtime)
        back = await reopened.recover()

        assert while_away.home is None
        assert back.home is None
        [kinby] = (await _listed(reopened)).instances
        assert kinby.instance_id == interrupted.instance_id
        assert kinby.home
        reopened.close()

    asyncio.run(scenario())


def test_the_hub_refuses_to_remove_the_home_instance(tmp_path):
    async def scenario() -> None:
        runtime = FakeRuntime()
        hub = hub_at(tmp_path / "hub", runtime=runtime)
        home = await _home_created(hub)

        refused = await hub_client(hub).call(
            INSTANCE_REMOVE, InstanceRemoveCommand(instance_id=home.instance_id)
        )

        assert isinstance(refused, ErrorEnvelope)
        assert refused.code is ErrorCode.INVALID_ARGUMENT
        assert "Kinby" in refused.message
        assert "home instance" in refused.message
        assert [instance.instance_id for instance in (await _listed(hub)).instances] == [
            home.instance_id
        ]
        assert runtime.removed == []
        hub.close()

    asyncio.run(scenario())
