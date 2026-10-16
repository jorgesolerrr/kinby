"""A hub write asks the user, and the hub runs it only for the approval it saw the user give."""

import asyncio
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import aiohttp
import pytest
from langchain_core.messages import AIMessageChunk

from kinby.contracts import (
    ApprovalRequested,
    CallFrame,
    ControlToken,
    ErrorCode,
    Event,
    FrameId,
    FrameType,
    GateRule,
    ItemFrame,
    LifecycleOperationResult,
    OperationKind,
    OperationState,
    Payload,
    PermissionMode,
    RoutineListResult,
    ToolGated,
    ToolResult,
    is_turn_closing,
)
from kinby.core import LangGraphRunner
from kinby.core.dispatcher import TurnConfig, build_dispatcher
from kinby.core.events import EventLog
from kinby.core.turns import ParkedTurn
from kinby.hub import Hub
from kinby.hub.approvals import HomeApprovals
from kinby.instance import Serve
from kinby.instance.config_changes import file_hash
from kinby.instance.permissions import GatePolicy
from kinby.plugins.hub import HUB_SCOPES_VARIABLE, HUB_URL_VARIABLE, hub_tools
from tests.test_factory_runs import factory_hub, started, started_coder
from tests.test_gate import ScriptedModel, _instance, _run_turn
from tests.test_hub import (
    FakeControl,
    FakeRuntime,
    finished_operation,
    hub_at,
    hub_client,
    instance_environment,
    started_instance,
)
from tests.test_hub_home_reads import answers, as_the_home_instance, error_code
from tests.test_hub_routing import control_token, instance_served
from tests.test_hub_server import frame, served, url
from tests.test_routines import instance_at
from tests.test_scheduler import FakeClock
from tests.test_scheduler import runtime as instance_runtime

Script = Callable[[UUID], Sequence[AIMessageChunk]]


@dataclass(frozen=True)
class KinbyHub:
    """A served hub whose home instance runs in this process, beside alice, which runs in this
    process too."""

    hub: Hub
    runtime: FakeRuntime
    address: Serve
    access_token: str
    home: UUID
    home_token: ControlToken
    alice: UUID

    def route(self, instance_id: UUID) -> str:
        return url(self.address, f"/instances/{instance_id}/hub")


@asynccontextmanager
async def kinby_hub(
    directory: Path, monkeypatch: pytest.MonkeyPatch, script: Script
) -> AsyncIterator[KinbyHub]:
    """Serve a hub and its home instance behind the relay, whose model answers with *script*,
    given alice's id. The home instance calls the hub on its own hub route. Alice lives in
    *directory*/alice."""
    runtime = FakeRuntime()
    hub = hub_at(directory / "hub", runtime=runtime)
    recovery = await hub.recover()
    assert recovery.home is not None
    home = await started(hub_client(hub), runtime, recovery.home)
    alice = (await started_instance(hub_client(hub), hub, name="alice")).instance_id
    access_token = hub.access.issue()
    assert access_token is not None
    home_token = control_token(hub, str(home))
    instance = _instance(directory)
    model = ScriptedModel(script(alice))
    log = EventLog(instance.manifest.state_dir)
    runner = LangGraphRunner(instance, event_log=log, model_factory=lambda _: model)
    dispatcher = build_dispatcher(
        instance.manifest.state_dir,
        event_log=log,
        turns=TurnConfig(runner.prepare_for_turn, runner.permission_ceiling, runner),
    )
    (directory / "alice").mkdir()
    alice_dispatcher = instance_runtime(
        instance_at(directory / "alice"), FakeClock(datetime(2026, 10, 16, tzinfo=UTC))
    )
    async with (
        served(hub) as address,
        instance_served(dispatcher, home_token) as private,
        instance_served(alice_dispatcher, control_token(hub, str(alice))) as alices,
    ):
        runtime.addresses[str(home)] = f"http://{private.host}:{private.port}"
        runtime.addresses[str(alice)] = f"http://{alices.host}:{alices.port}"
        kinby = KinbyHub(hub, runtime, address, access_token, home, home_token, alice)
        as_the_home_instance(monkeypatch, kinby.route(home))
        monkeypatch.setenv("KINBY_CONTROL_TOKEN", home_token)
        yield kinby
    hub.close()


def calling(call_id: str, tool: str, arguments: dict[str, object]) -> AIMessageChunk:
    return AIMessageChunk(
        content="",
        tool_calls=[{"name": tool, "args": arguments, "id": call_id, "type": "tool_call"}],
    )


async def relayed_turn(kinby: KinbyHub, approvals: int = 1) -> list[Payload]:
    """Start a turn in the home instance through the relay, as the user does, and approve its
    first *approvals* approvals. Return what it emitted once it ends or parks again."""
    headers = {"Authorization": f"Bearer {kinby.access_token}"}
    async with (
        aiohttp.ClientSession(headers=headers) as session,
        session.ws_connect(url(kinby.address, f"/instances/{kinby.home}/ws")) as socket,
    ):
        await socket.send_json({"type": "call", "id": "create", "method": "thread.create"})
        created = await frame(socket)
        assert isinstance(created["result"], dict), created
        thread_id = created["result"]["id"]
        await socket.send_json(
            {
                "type": "subscribe",
                "id": "events",
                "method": "thread.subscribe",
                "params": {"thread_id": thread_id},
            }
        )
        await socket.send_json(
            {
                "type": "call",
                "id": "start",
                "method": "thread.turn.start",
                "params": {"thread_id": thread_id, "message": "Set things up."},
            }
        )
        payloads: list[Payload] = []
        while True:
            received = await frame(socket)
            if received["type"] != FrameType.ITEM.value:
                continue
            payload = Event.model_validate(received["item"]).payload
            payloads.append(payload)
            if is_turn_closing(payload):
                return payloads
            if isinstance(payload, ApprovalRequested):
                if approvals == 0:
                    return payloads
                approvals -= 1
                await socket.send_json(
                    {
                        "type": "call",
                        "id": "respond",
                        "method": "thread.approval.respond",
                        "params": {
                            "thread_id": thread_id,
                            "approval_id": str(payload.approval_id),
                            "decision": "approve",
                        },
                    }
                )


def written(
    approval_id: UUID, tool: str, arguments: Mapping[str, object]
) -> tuple[str, dict[str, object]]:
    return (
        "hub.write",
        {"approval_id": str(approval_id), "tool": tool, "arguments": arguments},
    )


def test_kinby_creates_an_instance_once_the_user_approves_it_through_the_relay(
    tmp_path, monkeypatch
):
    def script(_: UUID) -> list[AIMessageChunk]:
        return [
            calling("create-1", "instance_create", {"manifest_id": "bob", "model": "openai:gpt-5"}),
            AIMessageChunk(content="Bob is being created."),
        ]

    async def scenario() -> None:
        async with kinby_hub(tmp_path, monkeypatch, script) as kinby:
            payloads = await relayed_turn(kinby)
            [approval] = [payload for payload in payloads if isinstance(payload, ApprovalRequested)]
            [result] = [payload for payload in payloads if isinstance(payload, ToolResult)]
            assert not result.error, result.output
            created = LifecycleOperationResult.model_validate_json(result.output)
            operation = await finished_operation(hub_client(kinby.hub), created)

        assert approval.name == "instance_create"
        assert operation.state is OperationState.SUCCEEDED
        assert operation.requester == kinby.home
        assert str(created.instance_id) in [spec.instance_id for spec in kinby.runtime.created]

    asyncio.run(scenario())


def test_the_hub_runs_a_home_write_once_for_the_approval_it_saw_and_its_arguments(
    tmp_path, monkeypatch
):
    def script(alice: UUID) -> list[AIMessageChunk]:
        stop = {"instance_id": str(alice), "action": "stop"}
        return [
            calling("stop-1", "instance_lifecycle", stop),
            calling("stop-2", "instance_lifecycle", stop),
        ]

    async def scenario() -> None:
        async with kinby_hub(tmp_path, monkeypatch, script) as kinby:
            # The tool cannot reach the hub, so the approval waits for this test, which holds
            # Kinby's control token as a script in its container would.
            monkeypatch.setenv(HUB_URL_VARIABLE, url(kinby.address, "/nowhere"))
            payloads = await relayed_turn(kinby)
            approval = next(
                payload for payload in payloads if isinstance(payload, ApprovalRequested)
            )
            stop = {"instance_id": str(kinby.alice), "action": "stop"}
            forged, mismatched, accepted, reused = await answers(
                kinby.route(kinby.home),
                kinby.home_token,
                written(uuid4(), "instance_lifecycle", stop),
                written(approval.approval_id, "instance_lifecycle", {**stop, "action": "recreate"}),
                written(approval.approval_id, "instance_lifecycle", stop),
                written(approval.approval_id, "instance_lifecycle", stop),
            )
            assert isinstance(accepted["result"], dict), accepted
            stopped = LifecycleOperationResult.model_validate(accepted["result"]["result"])
            operation = await finished_operation(hub_client(kinby.hub), stopped)

        assert error_code(forged) == ErrorCode.PERMISSION_DENIED.value
        assert error_code(mismatched) == ErrorCode.PERMISSION_DENIED.value
        assert error_code(reused) == ErrorCode.PERMISSION_DENIED.value
        assert operation.kind is OperationKind.STOP
        assert operation.instance_id == kinby.alice
        assert operation.requester == kinby.home

    asyncio.run(scenario())


@pytest.mark.parametrize("field", ["secrets", "save_to_hub"])
def test_the_hub_refuses_an_approved_creation_that_carries_a_secret(
    tmp_path, monkeypatch, field: str
):
    def script(_: UUID) -> list[AIMessageChunk]:
        bob = {"manifest_id": "bob", "model": "openai:gpt-5", field: {"api_key": "sk-bob"}}
        return [
            calling("create-1", "instance_create", bob),
            calling("create-2", "instance_create", bob),
        ]

    async def scenario() -> None:
        async with kinby_hub(tmp_path, monkeypatch, script) as kinby:
            monkeypatch.setenv(HUB_URL_VARIABLE, url(kinby.address, "/nowhere"))
            payloads = await relayed_turn(kinby)
            approval = next(
                payload for payload in payloads if isinstance(payload, ApprovalRequested)
            )
            refused, listed = await answers(
                kinby.route(kinby.home),
                kinby.home_token,
                written(approval.approval_id, "instance_create", approval.arguments),
                ("instance.list", {}),
            )

        assert error_code(refused) == ErrorCode.PERMISSION_DENIED.value
        assert isinstance(listed["result"], dict), listed
        assert "bob" not in [instance["manifest_id"] for instance in listed["result"]["instances"]]

    asyncio.run(scenario())


def tool_results(payloads: Sequence[Payload]) -> dict[str, ToolResult]:
    results = {payload.call_id: payload for payload in payloads if isinstance(payload, ToolResult)}
    for result in results.values():
        assert not result.error, result.output
    return results


def relayed_read(instance_id: UUID, method: str, **params: object) -> tuple[str, dict[str, object]]:
    return (
        "instance.relay.read",
        {"instance_id": str(instance_id), "method": method, "params": params},
    )


def relayed(answer: dict[str, object]) -> dict[str, object]:
    assert isinstance(answer["result"], dict), answer
    result = answer["result"]["result"]
    assert isinstance(result, dict)
    return result


JOBS = "---\ndescription: Job applications\n---\nCheck my job applications.\n"


def test_kinby_writes_a_routine_into_another_instance_and_runs_it_once(tmp_path, monkeypatch):
    def script(alice: UUID) -> list[AIMessageChunk]:
        jobs = {"instance_id": str(alice), "name": "jobs"}
        return [
            calling("write-1", "instance_routine_write", {**jobs, "content": JOBS, "hash": None}),
            calling("run-1", "instance_routine_run", jobs),
            AIMessageChunk(content="The routine ran once."),
        ]

    async def scenario() -> None:
        async with kinby_hub(tmp_path, monkeypatch, script) as kinby:
            payloads = await relayed_turn(kinby, approvals=2)
            tool_results(payloads)
            async with asyncio.timeout(5):
                while True:
                    [listed] = await answers(
                        kinby.route(kinby.home),
                        kinby.home_token,
                        relayed_read(kinby.alice, "routine.list"),
                    )
                    [routine] = RoutineListResult.model_validate(relayed(listed)).routines
                    if routine.last_run is not None:
                        break
                    await asyncio.sleep(0.01)

        assert [payload.name for payload in payloads if isinstance(payload, ApprovalRequested)] == [
            "instance_routine_write",
            "instance_routine_run",
        ]
        written = tmp_path / "alice" / "routines" / "jobs" / "ROUTINE.md"
        assert written.read_text(encoding="utf-8") == JOBS

    asyncio.run(scenario())


def test_kinby_sets_another_instances_picks_and_prompt_once_the_user_approves(
    tmp_path, monkeypatch
):
    def script(alice: UUID) -> list[AIMessageChunk]:
        prompt = {"name": "behavior", "content": "Be brief.", "hash": file_hash(b"")}
        return [
            calling(
                "picks-1",
                "instance_picks_set",
                {"instance_id": str(alice), "skills": ["tdd"], "hash": None},
            ),
            calling("prompt-1", "instance_prompt_set", {"instance_id": str(alice), **prompt}),
            AIMessageChunk(content="Alice is set up."),
        ]

    async def scenario() -> None:
        async with kinby_hub(tmp_path, monkeypatch, script) as kinby:
            tool_results(await relayed_turn(kinby, approvals=2))
            picks, prompt = await answers(
                kinby.route(kinby.home),
                kinby.home_token,
                ("instance.picks.get", {"instance_id": str(kinby.alice)}),
                relayed_read(kinby.alice, "prompt.get", name="behavior"),
            )

        assert isinstance(picks["result"], dict), picks
        assert [pick["name"] for pick in picks["result"]["picks"]] == ["tdd"]
        assert relayed(prompt)["content"] == "Be brief."

    asyncio.run(scenario())


def test_the_hub_refuses_picks_and_a_prompt_without_an_approval(tmp_path, monkeypatch):
    async def scenario() -> None:
        async with kinby_hub(tmp_path, monkeypatch, lambda _: []) as kinby:
            alice = str(kinby.alice)
            picked, prompted, picks, prompt = await answers(
                kinby.route(kinby.home),
                kinby.home_token,
                written(
                    uuid4(),
                    "instance_picks_set",
                    {"instance_id": alice, "skills": ["tdd"], "hash": None},
                ),
                written(
                    uuid4(),
                    "instance_prompt_set",
                    {
                        "instance_id": alice,
                        "name": "behavior",
                        "content": "Be brief.",
                        "hash": file_hash(b""),
                    },
                ),
                ("instance.picks.get", {"instance_id": alice}),
                relayed_read(kinby.alice, "prompt.get", name="behavior"),
            )

        assert error_code(picked) == ErrorCode.PERMISSION_DENIED.value
        assert error_code(prompted) == ErrorCode.PERMISSION_DENIED.value
        assert isinstance(picks["result"], dict), picks
        assert picks["result"]["picks"] == []
        assert relayed(prompt)["content"] != "Be brief."

    asyncio.run(scenario())


@pytest.mark.parametrize("method", ["permissions.set", "manifest.set"])
def test_the_home_instance_never_sets_another_instances_permissions_or_manifest(
    tmp_path, monkeypatch, method: str
):
    async def scenario() -> None:
        async with kinby_hub(tmp_path, monkeypatch, lambda _: []) as kinby:
            relayed_write, hub_write = await answers(
                kinby.route(kinby.home),
                kinby.home_token,
                relayed_read(kinby.alice, method),
                written(uuid4(), method, {"instance_id": str(kinby.alice)}),
            )

        assert error_code(relayed_write) == ErrorCode.PERMISSION_DENIED.value
        assert error_code(hub_write) == ErrorCode.PERMISSION_DENIED.value

    asyncio.run(scenario())


def lifecycle_runner(tmp_path: Path, policy: GatePolicy | None = None) -> LangGraphRunner:
    model = ScriptedModel(
        [
            calling(
                "stop-1", "instance_lifecycle", {"instance_id": str(uuid4()), "action": "stop"}
            ),
            AIMessageChunk(content="Done."),
        ]
    )
    return LangGraphRunner(_instance(tmp_path), model_factory=lambda _: model, gate_policy=policy)


def test_a_hub_write_parks_in_full_access(tmp_path, monkeypatch):
    as_the_home_instance(monkeypatch, "http://hub:8080/instances/x/hub")
    runner = lifecycle_runner(tmp_path, GatePolicy(mode=PermissionMode.FULL_ACCESS))

    result, payloads = asyncio.run(_run_turn(runner))

    assert isinstance(result, ParkedTurn)
    assert [payload.name for payload in payloads if isinstance(payload, ApprovalRequested)] == [
        "instance_lifecycle"
    ]


def test_an_allow_rule_in_permissions_does_not_lower_a_hub_writes_ask(tmp_path, monkeypatch):
    as_the_home_instance(monkeypatch, "http://hub:8080/instances/x/hub")
    runner = lifecycle_runner(tmp_path)
    (tmp_path / "instance" / "permissions.toml").write_text(
        'mode = "full-access"\n\n[tools]\ninstance_lifecycle = "allow"\n', encoding="utf-8"
    )

    result, payloads = asyncio.run(_run_turn(runner))

    assert isinstance(result, ParkedTurn)
    assert any(isinstance(payload, ApprovalRequested) for payload in payloads)


def test_read_only_denies_a_hub_write(tmp_path, monkeypatch):
    as_the_home_instance(monkeypatch, "http://hub:8080/instances/x/hub")
    runner = lifecycle_runner(tmp_path, GatePolicy(mode=PermissionMode.READ_ONLY))

    result, payloads = asyncio.run(_run_turn(runner))

    assert not isinstance(result, ParkedTurn)
    assert not any(isinstance(payload, ApprovalRequested) for payload in payloads)
    [gated] = [payload for payload in payloads if isinstance(payload, ToolGated)]
    assert gated.action.value == "deny"


def test_the_hub_writes_each_instance_the_scopes_its_hub_route_grants(tmp_path):
    runtime = FakeRuntime()
    hub = factory_hub(tmp_path / "hub", FakeControl(), runtime)

    async def scenario() -> tuple[UUID, UUID]:
        recovery = await hub.recover()
        assert recovery.home is not None
        return await started(hub_client(hub), runtime, recovery.home), await started_coder(
            hub, runtime
        )

    home, coder = asyncio.run(scenario())

    assert instance_environment(hub, home)[HUB_SCOPES_VARIABLE] == "hub:home,hub:read"
    assert instance_environment(hub, coder)[HUB_SCOPES_VARIABLE] == "factory:intake"


@pytest.mark.parametrize(
    ("scopes", "present"),
    [("hub:home,hub:read", True), ("factory:intake", False)],
    ids=["home", "factory"],
)
def test_only_an_instance_its_hub_grants_hub_home_has_hub_tools(monkeypatch, scopes, present):
    monkeypatch.setenv(HUB_URL_VARIABLE, "http://hub:8080/instances/x/hub")
    monkeypatch.setenv(HUB_SCOPES_VARIABLE, scopes)

    names = {tool.name for tool in hub_tools()}
    if present:
        assert {"hub_read", "instance_create", "instance_lifecycle", "instance_prompt_set"} <= names
    else:
        assert not names


def test_a_replayed_request_keeps_the_approval_the_user_gave():
    approvals = HomeApprovals()
    thread_id = uuid4()
    request = ApprovalRequested(
        approval_id=uuid4(),
        name="instance_lifecycle",
        arguments={"instance_id": str(uuid4()), "action": "stop"},
        rule=GateRule("hub.write"),
    )
    event = Event(
        sequence=3,
        thread_id=thread_id,
        turn_id=uuid4(),
        payload=request,
        timestamp=datetime.now(UTC),
    )
    requested = ItemFrame(id=FrameId("events"), item=event.model_dump(mode="json"))
    approved = CallFrame(
        id=FrameId("respond"),
        method="thread.approval.respond",
        params={
            "thread_id": str(thread_id),
            "approval_id": str(request.approval_id),
            "decision": "approve",
        },
    )

    approvals.instance_sent(requested.model_dump_json())
    approvals.client_sent(approved.model_dump_json())
    # Another tab subscribes from the start of the thread and sees the request again.
    approvals.instance_sent(requested.model_dump_json())

    assert approvals.approved(request.approval_id) == request
