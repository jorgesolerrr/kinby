"""The owner hands its lines work from chat, through the gate, and lists them with their unfinished
runs, on its hub route."""

import asyncio
import json
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from langchain_core.messages import AIMessageChunk

from kinby.contracts import (
    FACTORY_RUN_CANCEL,
    LINE_OWNED_HAND_IN,
    LINE_OWNED_LIST,
    ApprovalRequested,
    ErrorCode,
    ErrorEnvelope,
    Event,
    FactoryRun,
    FactoryRunCancelCommand,
    FactoryRunStatus,
    LineHandInCommand,
    LineOwnedListCommand,
    LineOwnedListResult,
    PermissionMode,
    ToolResult,
)
from kinby.core.dispatcher import Dispatcher
from kinby.core.turn_runner import LangGraphRunner
from kinby.core.turns import ParkedTurn
from kinby.instance.permissions import GatePolicy
from tests.test_browser_tools import _parked
from tests.test_factory_runs import intake_client
from tests.test_gate import ScriptedModel, _instance, _run_turn
from tests.test_hub import created_instance
from tests.test_hub_home_writes import calling
from tests.test_hub_line_intake import LANDING, Owned, _line, _runs, owned
from tests.test_routine_grants import turn_events
from tests.test_scheduler import call

DONE = AIMessageChunk(content="Done.")


async def _approved(dispatcher: Dispatcher, parked: Event) -> list[Event]:
    """Approve the call *parked* asks about, and return the events up to the turn's close."""
    assert isinstance(parked.payload, ApprovalRequested)
    async with asyncio.timeout(5):
        # The turn may still be parking, raising its notification.
        while isinstance(
            answer := await call(
                dispatcher,
                "thread.approval.respond",
                thread_id=parked.thread_id,
                approval_id=parked.payload.approval_id,
                decision="approve",
            ),
            ErrorEnvelope,
        ):
            assert answer.code is ErrorCode.THREAD_BUSY, answer
            await asyncio.sleep(0.01)
    return await turn_events(dispatcher, parked.thread_id)


async def _handed_in(owned: Owned, instance_id: UUID, line_id: UUID, issue: int) -> object:
    return await intake_client(owned.hub, instance_id).call(
        LINE_OWNED_HAND_IN, LineHandInCommand(line_id=line_id, work_item={"issue": issue})
    )


def _output(events: list[Event]) -> str:
    [result] = [event.payload for event in events if isinstance(event.payload, ToolResult)]
    assert not result.error, result.output
    return result.output


def test_the_owner_hands_its_line_a_work_item_once_the_user_approves_it(tmp_path, monkeypatch):
    replies: list[AIMessageChunk] = []

    async def scenario() -> None:
        async with owned(tmp_path, monkeypatch, ScriptedModel(replies)) as hub:
            line = await _line(hub)
            arguments: dict[str, object] = {
                "line_id": str(line.line_id),
                "work_item": {"issue": 12},
            }
            replies.extend([calling("hand-in", "hand_to_line", arguments), DONE])

            parked = await _parked(hub.dispatcher, "Hand in issue 12.", ApprovalRequested)
            before = await _runs(hub, line)
            events = await _approved(hub.dispatcher, parked)
            [run] = await _runs(hub, line)

            assert isinstance(parked.payload, ApprovalRequested)
            assert parked.payload.name == "hand_to_line"
            assert before == []
            assert run.work_item == {"issue": 12}
            assert str(run.run_id) in _output(events)

    asyncio.run(scenario())


def test_the_hub_refuses_a_hand_in_to_a_line_the_instance_does_not_own(tmp_path, monkeypatch):
    async def scenario() -> None:
        async with owned(tmp_path, monkeypatch) as hub:
            line = await _line(hub)
            other = (await created_instance(hub.client)).instance_id

            refused = await _handed_in(hub, other, line.line_id, 12)

            assert isinstance(refused, ErrorEnvelope), refused
            assert refused.code is ErrorCode.PERMISSION_DENIED
            assert await _runs(hub, line) == []

    asyncio.run(scenario())


def test_a_hand_in_that_matches_an_unfinished_run_returns_that_run(tmp_path, monkeypatch):
    async def scenario() -> None:
        async with owned(tmp_path, monkeypatch) as hub:
            line = await _line(hub)

            first = await _handed_in(hub, hub.home, line.line_id, 12)
            again = await _handed_in(hub, hub.home, line.line_id, 12)

            assert isinstance(first, FactoryRun), first
            assert isinstance(again, FactoryRun), again
            assert again.run_id == first.run_id
            assert len(await _runs(hub, line)) == 1

    asyncio.run(scenario())


def test_the_owner_lists_its_lines_each_with_its_unfinished_runs_and_their_status(
    tmp_path, monkeypatch
):
    replies = [calling("list", "list_lines", {}), DONE]

    async def scenario() -> None:
        async with owned(tmp_path, monkeypatch, ScriptedModel(replies)) as hub:
            backend = await _line(hub)
            landing = await _line(hub, LANDING)
            open_run = await _handed_in(hub, hub.home, backend.line_id, 12)
            cancelled = await _handed_in(hub, hub.home, backend.line_id, 13)
            assert isinstance(open_run, FactoryRun), open_run
            assert isinstance(cancelled, FactoryRun), cancelled
            await hub.client.call(
                FACTORY_RUN_CANCEL, FactoryRunCancelCommand(run_id=cancelled.run_id)
            )
            async with asyncio.timeout(5):
                while (await _runs(hub, backend))[0].status is not FactoryRunStatus.PARKED:
                    await asyncio.sleep(0.01)

            created = await call(hub.dispatcher, "thread.create")
            await call(hub.dispatcher, "thread.turn.start", thread_id=created.id, message="Lines?")
            listed = LineOwnedListResult.model_validate(
                json.loads(_output(await turn_events(hub.dispatcher, created.id)))
            )

            assert [(owned.line, owned.workbench) for owned in listed.lines] == [
                (backend, "cec-backend"),
                (landing, "landing"),
            ]
            assert [[(run.run_id, run.status) for run in owned.runs] for owned in listed.lines] == [
                [(open_run.run_id, FactoryRunStatus.PARKED)],
                [],
            ]

    asyncio.run(scenario())


def test_an_instance_lists_none_of_the_lines_another_owns(tmp_path, monkeypatch):
    async def scenario() -> None:
        async with owned(tmp_path, monkeypatch) as hub:
            await _line(hub)
            other = (await created_instance(hub.client)).instance_id

            listed = await intake_client(hub.hub, other).call(
                LINE_OWNED_LIST, LineOwnedListCommand()
            )

            assert listed == LineOwnedListResult(lines=[])

    asyncio.run(scenario())


def _hand_in_runner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, policy: GatePolicy | None = None
) -> LangGraphRunner:
    """A turn that hands a line issue 12, in an instance its hub grants line:owner."""
    monkeypatch.setenv("KINBY_HUB_URL", "http://hub:8080/instances/x/hub")
    monkeypatch.setenv("KINBY_HUB_SCOPES", "line:owner")
    arguments: dict[str, object] = {"line_id": str(uuid4()), "work_item": {"issue": 12}}
    model = ScriptedModel([calling("hand-in", "hand_to_line", arguments), DONE])
    return LangGraphRunner(_instance(tmp_path), model_factory=lambda _: model, gate_policy=policy)


@pytest.mark.parametrize(
    "mode", [PermissionMode.ASK, PermissionMode.AUTO, PermissionMode.FULL_ACCESS]
)
def test_a_hand_in_asks_the_user_in_every_mode_but_read_only(tmp_path, monkeypatch, mode):
    runner = _hand_in_runner(tmp_path, monkeypatch, GatePolicy(mode=mode))

    result, payloads = asyncio.run(_run_turn(runner))

    assert isinstance(result, ParkedTurn)
    assert [p.name for p in payloads if isinstance(p, ApprovalRequested)] == ["hand_to_line"]


def test_an_allow_rule_in_permissions_does_not_lower_a_hand_ins_ask(tmp_path, monkeypatch):
    runner = _hand_in_runner(tmp_path, monkeypatch)
    (tmp_path / "instance" / "permissions.toml").write_text(
        'mode = "full-access"\n\n[tools]\nhand_to_line = "allow"\n', encoding="utf-8"
    )

    result, payloads = asyncio.run(_run_turn(runner))

    assert isinstance(result, ParkedTurn)
    assert [p.name for p in payloads if isinstance(p, ApprovalRequested)] == ["hand_to_line"]
