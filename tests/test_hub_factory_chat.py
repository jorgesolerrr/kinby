"""The owner of a line changes a step of the line's factory from chat: an approval that shows the
diff and every line on the factory, which the hub checks it saw the user give before it makes the
change as one chat version."""

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

import aiohttp
import pytest
from langchain_core.messages import AIMessageChunk
from pydantic import JsonValue

from kinby.contracts import (
    FACTORY_OWNED_CHANGE,
    FACTORY_OWNED_CHANGE_PREVIEW,
    FACTORY_OWNED_GET,
    FACTORY_STEP_OFF_SET,
    LINE_SETUP,
    WORKBENCH_START,
    ApprovalRequested,
    ErrorCode,
    ErrorEnvelope,
    Event,
    FactoryChangeCommand,
    FactoryChangeOperation,
    FactoryChangePreviewCommand,
    FactoryGetCommand,
    FactoryResult,
    FactoryStepOffSetCommand,
    FactoryVersionSource,
    FrameType,
    GateDecider,
    GateOutcome,
    LineSetupCommand,
    LineSetupResult,
    OperationState,
    Payload,
    ToolGated,
    ToolResult,
    WorkbenchStartCommand,
    is_turn_closing,
)
from kinby.core.dispatcher import TurnConfig, build_dispatcher
from kinby.core.events import EventLog
from kinby.core.turn_runner import LangGraphRunner
from kinby.hub import Hub
from kinby.instance import Serve
from tests.test_factory_runs import intake_client, started
from tests.test_gate import ScriptedModel
from tests.test_hub import FakeControl, FakeRuntime, hub_at, hub_client, started_instance
from tests.test_hub_factories import write_factory
from tests.test_hub_factory_versions import read, sources, versions
from tests.test_hub_home_writes import calling
from tests.test_hub_line_intake import LANDING
from tests.test_hub_routing import control_token, instance_served
from tests.test_hub_server import frame, served, url
from tests.test_hub_workbenches import CEC_BACKEND, created, finished
from tests.test_routines import instance_at

FACTORY = """\
name: tickets
requires:
  image: coder
work_item: { issue: int }
steps:
  - { id: review, kind: approve, summary: Review the issue. }
  - { id: land, kind: approve, summary: Land the change. }
"""
OTHER = FACTORY.replace("name: tickets", "name: other")
DONE = AIMessageChunk(content="Done.")


@dataclass(frozen=True)
class Owner:
    """A served hub whose tickets factory has two lines: one alice owns, and alice runs in this
    process, and one the home instance owns. Its other factory has no line."""

    hub: Hub
    address: Serve
    access_token: str
    alice: UUID


@asynccontextmanager
async def owner_hub(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, replies: list[AIMessageChunk]
) -> AsyncIterator[Owner]:
    """The served hub, whose alice answers with *replies*."""
    runtime = FakeRuntime()
    for name, factory in (("tickets", FACTORY), ("other", OTHER)):
        write_factory(tmp_path / "hub" / "factories" / name, {"factory.yaml": factory})
    hub = hub_at(
        tmp_path / "hub",
        runtime=runtime,
        control=FakeControl(),
        shipped_factories=tmp_path / "shipped",
    )
    recovery = await hub.recover()
    assert recovery.home is not None
    client = hub_client(hub)
    await started(client, runtime, recovery.home)
    alice = (await started_instance(client, hub)).instance_id
    for workbench, owner in ((CEC_BACKEND, alice), (LANDING, None)):
        workbench_id = (await created(client, workbench)).workbench_id
        start = await client.call(WORKBENCH_START, WorkbenchStartCommand(workbench_id=workbench_id))
        assert (await finished(client, start)).state is OperationState.SUCCEEDED
        set_up = await client.call(
            LINE_SETUP,
            LineSetupCommand(factory="tickets", workbench_id=workbench_id, owner_id=owner),
        )
        assert isinstance(set_up, LineSetupResult), set_up
    (tmp_path / "alice").mkdir()
    instance = instance_at(tmp_path / "alice")
    log = EventLog(instance.manifest.state_dir)
    model = ScriptedModel(replies)
    runner = LangGraphRunner(instance, event_log=log, model_factory=lambda _: model)
    dispatcher = build_dispatcher(
        instance.manifest.state_dir,
        event_log=log,
        turns=TurnConfig(runner.prepare_for_turn, runner.permission_ceiling, runner),
    )
    token = control_token(hub, str(alice))
    monkeypatch.setenv("KINBY_CONTROL_TOKEN", token)
    monkeypatch.setenv("KINBY_HUB_SCOPES", ",".join(hub.instance_scopes(alice)))
    access_token = hub.access.issue()
    assert access_token is not None
    async with served(hub) as address, instance_served(dispatcher, token) as own:
        monkeypatch.setenv("KINBY_HUB_URL", url(address, f"/instances/{alice}/hub"))
        runtime.addresses[str(alice)] = f"http://{own.host}:{own.port}"
        yield Owner(hub, address, access_token, alice)
    hub.close()


async def _nothing() -> None:
    return None


async def relayed_turn(
    owner: Owner, decision: str, before_answer: Callable[[], Awaitable[None]] = _nothing
) -> tuple[ApprovalRequested, list[Payload]]:
    """Start a turn in alice through the relay, as the user does, and answer its one approval
    with *decision* once *before_answer* ran. Return the approval and what the turn emitted."""
    payloads = await _relayed(owner, decision, before_answer)
    [approval] = [payload for payload in payloads if isinstance(payload, ApprovalRequested)]
    return approval, payloads


async def _relayed(
    owner: Owner, decision: str, before_answer: Callable[[], Awaitable[None]] = _nothing
) -> list[Payload]:
    """What a turn started in alice through the relay emitted, each approval answered with
    *decision* once *before_answer* ran."""
    headers = {"Authorization": f"Bearer {owner.access_token}"}
    async with (
        aiohttp.ClientSession(headers=headers) as session,
        session.ws_connect(url(owner.address, f"/instances/{owner.alice}/ws")) as socket,
    ):
        await socket.send_json({"type": "call", "id": "create", "method": "thread.create"})
        created_thread = await frame(socket)
        assert isinstance(created_thread["result"], dict), created_thread
        thread_id = created_thread["result"]["id"]
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
                "params": {"thread_id": thread_id, "message": "Change the factory."},
            }
        )
        approval = None
        payloads: list[Payload] = []
        while True:
            received = await frame(socket)
            if received["type"] == FrameType.ERROR.value and received["id"] == "respond":
                # The turn may still be parking, raising its notification.
                assert ErrorEnvelope.model_validate(received["error"]).code is ErrorCode.THREAD_BUSY
                await asyncio.sleep(0.01)
            elif received["type"] != FrameType.ITEM.value:
                continue
            else:
                payload = Event.model_validate(received["item"]).payload
                payloads.append(payload)
                if is_turn_closing(payload):
                    return payloads
                if not isinstance(payload, ApprovalRequested):
                    continue
                approval = payload
                await before_answer()
            assert approval is not None
            await socket.send_json(
                {
                    "type": "call",
                    "id": "respond",
                    "method": "thread.approval.respond",
                    "params": {
                        "thread_id": thread_id,
                        "approval_id": str(approval.approval_id),
                        "decision": decision,
                    },
                }
            )


def _result(payloads: list[Payload]) -> ToolResult:
    [result] = [payload for payload in payloads if isinstance(payload, ToolResult)]
    return result


def _change(operation: str, arguments: dict[str, object]) -> AIMessageChunk:
    return calling("change", "change_factory", {"operation": operation, "arguments": arguments})


@pytest.mark.parametrize(
    ("operation", "arguments", "step", "file"),
    [
        (
            "step_save",
            {
                "step": {
                    "id": "review",
                    "kind": "approve",
                    "summary": "Review the issue and its comments.",
                }
            },
            "review",
            FACTORY.replace(
                "  - { id: review, kind: approve, summary: Review the issue. }\n",
                "  - {id: review, kind: approve, summary: Review the issue and its comments.}\n",
            ),
        ),
        (
            "step_add",
            {"after": "review", "step": {"id": "merge", "kind": "approve", "summary": "Merge."}},
            "merge",
            FACTORY.replace(
                "  - { id: land,",
                "  - id: merge\n    kind: approve\n    summary: Merge.\n  - { id: land,",
            ),
        ),
        (
            "step_off_set",
            {"step": "land", "off": True},
            "land",
            FACTORY.replace(
                "  - { id: land, kind: approve, summary: Land the change. }\n",
                "  - {id: land, kind: approve, off: true, summary: Land the change.}\n",
            ),
        ),
    ],
    ids=["save", "add", "off"],
)
def test_the_owner_of_a_line_changes_a_step_from_chat_as_one_chat_version_once_approved(
    tmp_path, monkeypatch, operation, arguments, step, file
):
    replies: list[AIMessageChunk] = []

    async def scenario() -> None:
        async with owner_hub(tmp_path, monkeypatch, replies) as owner:
            client = hub_client(owner.hub)
            before = await read(client)
            replies.extend(
                [_change(operation, {"name": "tickets", "hash": before.hash, **arguments}), DONE]
            )

            approval, payloads = await relayed_turn(owner, "approve")
            after = await read(client)
            listed = await versions(client)

        assert approval.name == "change_factory"
        assert approval.preview is not None
        assert "cec-backend, owned by alice" in approval.preview
        assert "landing, owned by home" in approval.preview
        assert "--- a/factory.yaml\n+++ b/factory.yaml\n" in approval.preview
        result = _result(payloads)
        assert not result.error, result.output
        assert after.files["factory.yaml"] == file
        assert sources(listed[:1]) == [(FactoryVersionSource.CHAT, [step])]
        assert after.version == listed[0].version
        assert listed[0].version in result.output

    asyncio.run(scenario())


def test_a_declined_change_changes_nothing(tmp_path, monkeypatch):
    replies: list[AIMessageChunk] = []

    async def scenario() -> None:
        async with owner_hub(tmp_path, monkeypatch, replies) as owner:
            client = hub_client(owner.hub)
            before = await read(client)
            before_versions = await versions(client)
            replies.extend(
                [
                    _change(
                        "step_off_set",
                        {"name": "tickets", "hash": before.hash, "step": "land", "off": True},
                    ),
                    DONE,
                ]
            )

            _, payloads = await relayed_turn(owner, "deny")

            assert _result(payloads).error
            assert await read(client) == before
            assert await versions(client) == before_versions

    asyncio.run(scenario())


def test_a_change_the_factory_check_refuses_reaches_the_owner_as_the_tools_result(
    tmp_path, monkeypatch
):
    replies: list[AIMessageChunk] = []

    async def scenario() -> None:
        async with owner_hub(tmp_path, monkeypatch, replies) as owner:
            client = hub_client(owner.hub)
            before = await read(client)
            twin = {"id": "review", "kind": "approve", "summary": "Review it again."}
            replies.extend(
                [
                    _change(
                        "step_add",
                        {"name": "tickets", "hash": before.hash, "after": "land", "step": twin},
                    ),
                    DONE,
                ]
            )

            _, payloads = await relayed_turn(owner, "approve")

            result = _result(payloads)
            assert result.error
            assert 'Step id "review" is used by more than one step.' in result.output
            assert await read(client) == before

    asyncio.run(scenario())


def test_a_change_made_from_a_stale_read_reaches_the_owner_as_the_tools_result(
    tmp_path, monkeypatch
):
    replies: list[AIMessageChunk] = []

    async def scenario() -> None:
        async with owner_hub(tmp_path, monkeypatch, replies) as owner:
            client = hub_client(owner.hub)
            before = await read(client)
            replies.extend(
                [
                    _change(
                        "step_off_set",
                        {"name": "tickets", "hash": before.hash, "step": "land", "off": True},
                    ),
                    DONE,
                ]
            )

            async def saved_from_the_web() -> None:
                turned = FactoryStepOffSetCommand(
                    name="tickets", step="review", off=True, hash=before.hash
                )
                assert isinstance(await client.call(FACTORY_STEP_OFF_SET, turned), FactoryResult)

            _, payloads = await relayed_turn(owner, "approve", saved_from_the_web)

            result = _result(payloads)
            assert result.error
            assert "changed since it was read" in result.output
            assert sources((await versions(client))[:1]) == [(FactoryVersionSource.WEB, ["review"])]

    asyncio.run(scenario())


def _refused(payloads: list[Payload]) -> ToolResult:
    """The result of the turn's one call, which the gate denied on its refused preview."""
    assert not [payload for payload in payloads if isinstance(payload, ApprovalRequested)]
    [gated] = [payload for payload in payloads if isinstance(payload, ToolGated)]
    assert (gated.action, gated.rule, gated.decided_by) == (
        GateOutcome.DENY,
        "preview.refused",
        GateDecider.POLICY,
    )
    result = _result(payloads)
    assert result.error
    return result


def test_a_change_to_a_factory_with_no_line_of_the_instance_is_refused_without_asking(
    tmp_path, monkeypatch
):
    replies: list[AIMessageChunk] = []

    async def scenario() -> None:
        async with owner_hub(tmp_path, monkeypatch, replies) as owner:
            client = hub_client(owner.hub)
            before = await read(client, "other")
            before_versions = await versions(client, "other")
            replies.extend(
                [
                    _change(
                        "step_off_set",
                        {"name": "other", "hash": before.hash, "step": "land", "off": True},
                    ),
                    DONE,
                ]
            )

            payloads = await _relayed(owner, "approve")

            assert 'Factory "other" has no line of this instance' in _refused(payloads).output
            assert await read(client, "other") == before
            assert await versions(client, "other") == before_versions

    asyncio.run(scenario())


def test_a_change_from_a_stale_read_is_refused_without_asking(tmp_path, monkeypatch):
    replies: list[AIMessageChunk] = []

    async def scenario() -> None:
        async with owner_hub(tmp_path, monkeypatch, replies) as owner:
            client = hub_client(owner.hub)
            before = await read(client)
            turned = FactoryStepOffSetCommand(
                name="tickets", step="review", off=True, hash=before.hash
            )
            assert isinstance(await client.call(FACTORY_STEP_OFF_SET, turned), FactoryResult)
            replies.extend(
                [
                    _change(
                        "step_off_set",
                        {"name": "tickets", "hash": before.hash, "step": "land", "off": True},
                    ),
                    DONE,
                ]
            )

            payloads = await _relayed(owner, "approve")

            assert "changed since it was read" in _refused(payloads).output
            assert sources((await versions(client))[:1]) == [(FactoryVersionSource.WEB, ["review"])]

    asyncio.run(scenario())


def test_a_change_whose_preview_the_hub_does_not_answer_is_refused_without_asking(
    tmp_path, monkeypatch
):
    replies: list[AIMessageChunk] = []

    async def scenario() -> None:
        async with owner_hub(tmp_path, monkeypatch, replies) as owner:
            client = hub_client(owner.hub)
            before = await read(client)
            before_versions = await versions(client)
            replies.extend(
                [
                    _change(
                        "step_off_set",
                        {"name": "tickets", "hash": before.hash, "step": "land", "off": True},
                    ),
                    DONE,
                ]
            )
            monkeypatch.setenv("KINBY_HUB_URL", url(owner.address, "/nowhere"))

            payloads = await _relayed(owner, "approve")

            assert "could not answer the preview" in _refused(payloads).output
            assert await read(client) == before
            assert await versions(client) == before_versions

    asyncio.run(scenario())


def test_the_owner_reads_the_factory_of_its_line_and_previews_a_change_to_it(tmp_path, monkeypatch):
    async def scenario() -> None:
        async with owner_hub(tmp_path, monkeypatch, []) as owner:
            alice = intake_client(owner.hub, owner.alice)
            got = await alice.call(FACTORY_OWNED_GET, FactoryGetCommand(name="tickets"))
            assert isinstance(got, FactoryResult), got

            previewed = await alice.call(
                FACTORY_OWNED_CHANGE_PREVIEW,
                FactoryChangePreviewCommand(
                    operation=FactoryChangeOperation.STEP_OFF_SET,
                    arguments={"name": "tickets", "hash": got.hash, "step": "land", "off": True},
                ),
            )

            assert got == await read(hub_client(owner.hub))
            assert not isinstance(previewed, ErrorEnvelope), previewed
            assert previewed.lines == ["cec-backend, owned by alice", "landing, owned by home"]
            assert "+  - {id: land, kind: approve, off: true, summary: Land the change.}\n" in (
                previewed.diff
            )

    asyncio.run(scenario())


def test_an_instance_that_owns_no_line_on_the_factory_can_neither_read_nor_change_it(
    tmp_path, monkeypatch
):
    async def scenario() -> None:
        async with owner_hub(tmp_path, monkeypatch, []) as owner:
            client = hub_client(owner.hub)
            alice = intake_client(owner.hub, owner.alice)
            before = await read(client, "other")
            arguments: dict[str, JsonValue] = {
                "name": "other",
                "hash": before.hash,
                "step": "land",
                "off": True,
            }

            answers = [
                await alice.call(FACTORY_OWNED_GET, FactoryGetCommand(name="other")),
                await alice.call(
                    FACTORY_OWNED_CHANGE_PREVIEW,
                    FactoryChangePreviewCommand(
                        operation=FactoryChangeOperation.STEP_OFF_SET, arguments=arguments
                    ),
                ),
                await alice.call(
                    FACTORY_OWNED_CHANGE,
                    FactoryChangeCommand(
                        approval_id=uuid4(),
                        operation=FactoryChangeOperation.STEP_OFF_SET,
                        arguments=arguments,
                    ),
                ),
            ]

            for answer in answers:
                assert isinstance(answer, ErrorEnvelope), answer
                assert answer.code is ErrorCode.PERMISSION_DENIED
                assert "no line" in answer.message
            assert await read(client, "other") == before

    asyncio.run(scenario())


def test_the_hub_refuses_a_change_it_never_saw_the_user_approve(tmp_path, monkeypatch):
    async def scenario() -> None:
        async with owner_hub(tmp_path, monkeypatch, []) as owner:
            client = hub_client(owner.hub)
            before = await read(client)

            refused = await intake_client(owner.hub, owner.alice).call(
                FACTORY_OWNED_CHANGE,
                FactoryChangeCommand(
                    approval_id=uuid4(),
                    operation=FactoryChangeOperation.STEP_OFF_SET,
                    arguments={"name": "tickets", "hash": before.hash, "step": "land", "off": True},
                ),
            )

            assert isinstance(refused, ErrorEnvelope), refused
            assert refused.code is ErrorCode.PERMISSION_DENIED
            assert await read(client) == before

    asyncio.run(scenario())
