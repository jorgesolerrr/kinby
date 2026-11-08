"""The home instance reads the hub and its instances on its hub route, and nothing more."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import aiohttp
import pytest
from aiohttp import web
from langchain_core.messages import AIMessageChunk

from kinby.contracts import (
    ApprovalRequested,
    ControlToken,
    ErrorCode,
    FactoryRunOrigin,
    FrameType,
    HomeInstanceOrigin,
    Origin,
    RoutineName,
    RoutineOrigin,
    RoutineTrigger,
    ToolResult,
    UserOrigin,
    is_turn_closing,
)
from kinby.core.dispatcher import ScheduledDispatcher
from kinby.core.errors import InstanceUnavailable
from kinby.core.events import EventLog
from kinby.core.threads import ThreadStore
from kinby.core.turn_runner import LangGraphRunner
from kinby.core.turns import Turns
from kinby.hub import Hub, relay
from kinby.hub.models import InstanceEndpoint
from kinby.instance import Serve
from tests.test_factory_runs import started
from tests.test_gate import ScriptedModel, _instance, _run
from tests.test_hub import FakeRuntime, hub_at, hub_client, started_instance
from tests.test_hub_routing import control_token, instance_served
from tests.test_hub_server import frame, served, url
from tests.test_routines import RoutineModel, instance_at, no_recap, routine_file
from tests.test_scheduler import FakeClock, call, events_for
from tests.test_scheduler import runtime as instance_runtime


@dataclass(frozen=True)
class HomeHub:
    """A served hub, its home instance, and a running instance the home instance reads."""

    hub: Hub
    runtime: FakeRuntime
    address: Serve
    home: UUID
    home_token: ControlToken
    alice: UUID
    alice_token: ControlToken
    alice_dispatcher: ScheduledDispatcher

    def route(self, instance_id: UUID) -> str:
        return url(self.address, f"/instances/{instance_id}/hub")


@asynccontextmanager
async def home_hub(directory: Path) -> AsyncIterator[HomeHub]:
    """Serve a hub whose home instance runs, beside alice, an in-process instance with a routine."""
    runtime = FakeRuntime()
    hub = hub_at(directory / "hub", runtime=runtime)
    recovery = await hub.recover()
    assert recovery.home is not None
    home = await started(hub_client(hub), runtime, recovery.home)
    alice = (await started_instance(hub_client(hub), hub, name="alice")).instance_id
    (directory / "alice").mkdir()
    instance = instance_at(directory / "alice")
    routine_file(instance, "description: News")
    alice_token = control_token(hub, str(alice))
    dispatcher = instance_runtime(instance, FakeClock(datetime(2026, 10, 15, tzinfo=UTC)))
    async with served(hub) as address, instance_served(dispatcher, alice_token) as private:
        runtime.addresses[str(alice)] = f"http://{private.host}:{private.port}"
        yield HomeHub(
            hub,
            runtime,
            address,
            home,
            control_token(hub, str(home)),
            alice,
            alice_token,
            dispatcher,
        )
    hub.close()


async def answers(
    route: str, token: ControlToken, *calls: tuple[str, dict[str, object]]
) -> list[dict[str, object]]:
    """Call each method on one hub route, in order, and collect the frames it answers."""
    async with (
        aiohttp.ClientSession(headers={"Authorization": f"Bearer {token}"}) as session,
        session.ws_connect(route) as socket,
    ):
        received = []
        for method, params in calls:
            await socket.send_json({"type": "call", "id": "1", "method": method, "params": params})
            received.append(await frame(socket))
        return received


def as_the_home_instance(monkeypatch: pytest.MonkeyPatch, hub_url: str) -> None:
    """Hold what the hub writes into the home instance: its hub route and the scopes it grants."""
    monkeypatch.setenv("KINBY_HUB_URL", hub_url)
    monkeypatch.setenv("KINBY_HUB_SCOPES", "hub:home,hub:read")


def error_code(answer: dict[str, object]) -> object:
    assert answer["type"] == FrameType.ERROR.value, answer
    assert isinstance(answer["error"], dict)
    return answer["error"]["code"]


def test_the_home_instance_reads_the_hub_and_another_instances_routines(tmp_path):
    async def scenario() -> None:
        async with home_hub(tmp_path) as hub:
            listed, routines = await answers(
                hub.route(hub.home),
                hub.home_token,
                ("instance.list", {}),
                (
                    "instance.relay.read",
                    {"instance_id": str(hub.alice), "method": "routine.list", "params": {}},
                ),
            )

        assert isinstance(listed["result"], dict), listed
        assert {instance["manifest_id"] for instance in listed["result"]["instances"]} == {
            "home",
            "alice",
        }
        assert isinstance(routines["result"], dict), routines
        relayed = routines["result"]["result"]
        assert isinstance(relayed, dict)
        assert [routine["name"] for routine in relayed["routines"]] == ["news"]

    asyncio.run(scenario())


def test_the_home_instance_is_refused_what_is_not_a_read(tmp_path):
    async def scenario() -> None:
        async with home_hub(tmp_path) as hub:
            remove, secret, relayed_write = await answers(
                hub.route(hub.home),
                hub.home_token,
                ("instance.remove", {"instance_id": str(hub.alice)}),
                ("hub.secret.set", {"name": "GITHUB_TOKEN", "value": "ghp"}),
                (
                    "instance.relay.read",
                    {
                        "instance_id": str(hub.alice),
                        "method": "routine.delete",
                        "params": {"name": "news"},
                    },
                ),
            )

        assert error_code(remove) == ErrorCode.PERMISSION_DENIED.value
        assert error_code(secret) == ErrorCode.PERMISSION_DENIED.value
        assert error_code(relayed_write) == ErrorCode.PERMISSION_DENIED.value
        assert (tmp_path / "alice" / "routines" / "news" / "ROUTINE.md").exists()

    asyncio.run(scenario())


def test_another_instances_control_token_gets_nothing_on_the_hub(tmp_path):
    async def scenario() -> None:
        async with home_hub(tmp_path) as hub:
            listed, relayed, written = await answers(
                hub.route(hub.alice),
                hub.alice_token,
                ("instance.list", {}),
                (
                    "instance.relay.read",
                    {"instance_id": str(hub.home), "method": "routine.list", "params": {}},
                ),
                (
                    "hub.write",
                    {
                        "approval_id": "00000000-0000-0000-0000-000000000001",
                        "tool": "instance_lifecycle",
                        "arguments": {"instance_id": str(hub.home), "action": "stop"},
                    },
                ),
            )

        assert error_code(listed) == ErrorCode.NOT_FOUND.value
        assert error_code(relayed) == ErrorCode.NOT_FOUND.value
        assert error_code(written) == ErrorCode.NOT_FOUND.value

    asyncio.run(scenario())


def result_of(answer: dict[str, object]) -> dict[str, object]:
    assert answer["type"] == FrameType.RESULT.value, answer
    assert isinstance(answer["result"], dict)
    return answer["result"]


def relayed(
    instance_id: UUID, method: str, params: dict[str, object]
) -> tuple[str, dict[str, object]]:
    return (
        "instance.relay.read",
        {"instance_id": str(instance_id), "method": method, "params": params},
    )


def relayed_result(answer: dict[str, object]) -> dict[str, object]:
    relayed_answer = result_of(answer)["result"]
    assert isinstance(relayed_answer, dict)
    return relayed_answer


def test_stats_get_leaves_out_the_per_turn_records_unless_the_read_asks_for_them(tmp_path):
    async def scenario() -> None:
        async with home_hub(tmp_path) as hub:
            thread = await call(hub.alice_dispatcher, "thread.create")
            await call(hub.alice_dispatcher, "thread.turn.start", thread_id=thread.id, message="Hi")
            await events_for(hub.alice_dispatcher, thread.id)
            bounded, whole = await answers(
                hub.route(hub.home),
                hub.home_token,
                relayed(hub.alice, "stats.get", {}),
                relayed(hub.alice, "stats.get", {"records": True}),
            )

        assert relayed_result(bounded)["records"] == []
        total = relayed_result(bounded)["total"]
        assert isinstance(total, dict)
        assert total["completed"] == 1
        records = relayed_result(whole)["records"]
        assert isinstance(records, list)
        assert len(records) == 1

    asyncio.run(scenario())


def test_instance_logs_hold_the_last_200_lines_unless_the_read_sets_tail(tmp_path):
    async def scenario() -> None:
        async with home_hub(tmp_path) as hub:
            hub.runtime.log_output = "".join(f"line {index}\n" for index in range(300)).encode()
            bounded, longer = await answers(
                hub.route(hub.home),
                hub.home_token,
                ("instance.logs", {"instance_id": str(hub.alice)}),
                ("instance.logs", {"instance_id": str(hub.alice), "tail": 250}),
            )

        bounded_lines = str(result_of(bounded)["text"]).splitlines()
        assert len(bounded_lines) == 200
        assert bounded_lines[0] == "line 100"
        assert len(str(result_of(longer)["text"]).splitlines()) == 250

    asyncio.run(scenario())


def test_a_relayed_thread_list_holds_50_threads_and_a_cursor_to_the_rest(tmp_path):
    async def scenario() -> None:
        async with home_hub(tmp_path) as hub:
            for _ in range(51):
                await call(hub.alice_dispatcher, "thread.create")
            route, token = hub.route(hub.home), hub.home_token
            first, everything = await answers(
                route,
                token,
                relayed(hub.alice, "thread.list", {"filter": "all"}),
                relayed(hub.alice, "thread.list", {"filter": "all", "limit": None}),
            )
            cursor = relayed_result(first)["cursor"]
            (rest,) = await answers(
                route,
                token,
                relayed(hub.alice, "thread.list", {"filter": "all", "cursor": cursor}),
            )

        first_page = relayed_result(first)["threads"]
        assert isinstance(first_page, list)
        assert len(first_page) == 50
        assert cursor == {
            "last_activity_at": first_page[-1]["last_activity_at"],
            "id": first_page[-1]["id"],
        }
        rest_page = relayed_result(rest)["threads"]
        assert isinstance(rest_page, list)
        assert len(rest_page) == 1
        assert relayed_result(rest)["cursor"] is None
        whole = relayed_result(everything)["threads"]
        assert isinstance(whole, list)
        assert len(whole) == 51
        assert relayed_result(everything)["cursor"] is None

    asyncio.run(scenario())


def test_factory_run_list_holds_50_runs_and_a_cursor_to_the_rest(tmp_path):
    async def scenario() -> None:
        async with home_hub(tmp_path) as hub:
            for issue in range(51):
                hub.hub.registry.open_run(hub.alice, "checks", {"issue": issue}, "code")
            route, token = hub.route(hub.home), hub.home_token
            first, everything = await answers(
                route,
                token,
                ("factory.run.list", {"factory": "checks"}),
                ("factory.run.list", {"factory": "checks", "limit": None}),
            )
            cursor = result_of(first)["cursor"]
            (rest,) = await answers(
                route, token, ("factory.run.list", {"factory": "checks", "cursor": cursor})
            )

        first_page = result_of(first)["runs"]
        assert isinstance(first_page, list)
        assert [run["work_item"]["issue"] for run in first_page] == list(range(50))
        assert cursor == first_page[-1]["run_id"]
        rest_page = result_of(rest)["runs"]
        assert isinstance(rest_page, list)
        assert [run["work_item"]["issue"] for run in rest_page] == [50]
        assert result_of(rest)["cursor"] is None
        whole = result_of(everything)["runs"]
        assert isinstance(whole, list)
        assert len(whole) == 51
        assert result_of(everything)["cursor"] is None

    asyncio.run(scenario())


def hub_read_call(call_id: str, method: str, params: dict[str, object]) -> dict[str, object]:
    return {
        "name": "hub_read",
        "args": {"method": method, "params": params},
        "id": call_id,
        "type": "tool_call",
    }


def test_kinby_reads_the_hub_and_another_instance_without_asking(tmp_path, monkeypatch):
    async def scenario() -> None:
        async with home_hub(tmp_path) as hub:
            as_the_home_instance(monkeypatch, hub.route(hub.home))
            monkeypatch.setenv("KINBY_CONTROL_TOKEN", hub.home_token)
            model = ScriptedModel(
                [
                    AIMessageChunk(
                        content="",
                        tool_calls=[
                            hub_read_call("list-1", "instance.list", {}),
                            hub_read_call(
                                "routines-1", "routine.list", {"instance_id": str(hub.alice)}
                            ),
                            hub_read_call(
                                "file-1",
                                "library.skill.file.read",
                                {"name": "tdd", "path": "mocking.md"},
                            ),
                        ],
                    ),
                    AIMessageChunk(content="Alice runs one routine."),
                ]
            )
            _, payloads = await _run(_instance(tmp_path), model)

        assert not any(isinstance(payload, ApprovalRequested) for payload in payloads)
        results = {
            payload.call_id: payload for payload in payloads if isinstance(payload, ToolResult)
        }
        assert not results["list-1"].error, results["list-1"].output
        assert '"manifest_id": "alice"' in results["list-1"].output
        assert not results["routines-1"].error, results["routines-1"].output
        assert '"name": "news"' in results["routines-1"].output
        assert not results["file-1"].error, results["file-1"].output
        assert '"path": "mocking.md"' in results["file-1"].output

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("origin", "present"),
    [
        (UserOrigin(), True),
        (RoutineOrigin(name=RoutineName("news"), trigger=RoutineTrigger.MANUAL), False),
        (
            FactoryRunOrigin(
                factory="checks",
                run_id=UUID("00000000-0000-0000-0000-000000000001"),
                step="code",
            ),
            False,
        ),
        (HomeInstanceOrigin(), False),
    ],
    ids=["user", "routine", "factory-step", "home-instance"],
)
def test_hub_tools_are_on_turns_the_user_starts_only(
    tmp_path, monkeypatch, origin: Origin, present
):
    as_the_home_instance(monkeypatch, "http://hub:8080/instances/x/hub")
    instance = instance_at(tmp_path)
    routine_file(instance, "description: News")
    model = RoutineModel()

    async def scenario() -> None:
        log = EventLog(instance.manifest.state_dir)
        store = ThreadStore(instance.manifest.state_dir)
        runner = LangGraphRunner(instance, event_log=log, model_factory=lambda _: model)
        turns = Turns(
            store, log, runner, runner.prepare_for_turn, runner.permission_ceiling, no_recap
        )
        thread = store.create(None)
        await turns.wake(thread.id, "How is the hub going?", origin)
        subscription = (await log.subscribe(thread.id)).items
        async with asyncio.timeout(5):
            async for event in subscription:
                if is_turn_closing(event.payload):
                    break
        await subscription.aclose()

    asyncio.run(scenario())

    hub_tools = {"hub_read", "hub_write"}
    if present:
        assert hub_tools <= set(model.tools)
    else:
        assert hub_tools.isdisjoint(model.tools)


@asynccontextmanager
async def silent_instance() -> AsyncIterator[Serve]:
    """An instance that takes calls on its socket and answers none, until the caller leaves."""

    async def socket(request: web.Request) -> web.WebSocketResponse:
        response = web.WebSocketResponse()
        await response.prepare(request)
        async for _ in response:
            pass
        return response

    application = web.Application()
    application.router.add_get("/ws", socket)
    runner = web.AppRunner(application)
    await runner.setup()
    try:
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        yield Serve("127.0.0.1", site.port)
    finally:
        await runner.cleanup()


def test_a_relayed_read_the_instance_never_answers_ends_unavailable(monkeypatch):
    monkeypatch.setattr(relay, "RELAYED_CALL_SECONDS", 0.1)

    async def scenario() -> None:
        async with silent_instance() as address:
            endpoint = InstanceEndpoint(
                instance_id=UUID(int=1),
                url=f"http://{address.host}:{address.port}",
                control_token=ControlToken("token"),
            )
            with pytest.raises(InstanceUnavailable):
                await asyncio.wait_for(relay.relayed_call(endpoint, "routine.list", {}), 5)

    asyncio.run(scenario())
