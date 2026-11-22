"""Uploads: a file the user gives a turn lands in the workspace, and the turn names it
(ADR 0132)."""

import asyncio
import base64
import io
import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest
from langchain_core.messages import BaseMessage, HumanMessage
from PIL import Image

from kinby.contracts import (
    UPLOAD_BYTES,
    AcceptedResult,
    ErrorCode,
    ErrorEnvelope,
    Payload,
    ThreadCreateResult,
    ThreadSummary,
    TurnStarted,
    UploadPutResult,
    WorkspaceReverted,
)
from kinby.core.dispatcher import ScheduledDispatcher
from kinby.core.events import EventLog
from kinby.core.snapshots import WorkspaceSnapshots
from kinby.core.turn_runner import ChatModel
from kinby.instance import Instance
from tests.test_cleared_results import RecordingModel, closed
from tests.test_compaction import LONG, CompactingModel, compact
from tests.test_routines import signal_runtime
from tests.test_saved_outputs import instance_at, until
from tests.test_scheduler import call

TODAY = "2026-11-22"


def runtime(
    instance: Instance, model: ChatModel, snapshots: WorkspaceSnapshots | None = None
) -> ScheduledDispatcher:
    dispatcher, _ = signal_runtime(
        instance,
        model,
        snapshots=snapshots,
        clock=lambda: datetime(2026, 11, 22, 23, 30, tzinfo=UTC),
    )
    return dispatcher


def payloads(instance: Instance, thread_id: UUID) -> list[Payload]:
    return [
        event.payload for event in EventLog(instance.manifest.state_dir).thread_events(thread_id)
    ]


async def put(dispatcher: ScheduledDispatcher, name: str, content: bytes) -> str:
    uploaded = await call(
        dispatcher, "upload.put", name=name, content=base64.b64encode(content).decode()
    )
    assert isinstance(uploaded, UploadPutResult), uploaded
    return uploaded.path


async def new_thread(dispatcher: ScheduledDispatcher) -> UUID:
    thread = await call(dispatcher, "thread.create")
    assert isinstance(thread, ThreadCreateResult)
    return thread.id


async def run_turn(
    dispatcher: ScheduledDispatcher, thread_id: UUID, message: str, uploads: list[str]
) -> AcceptedResult:
    accepted = await call(
        dispatcher, "thread.turn.start", thread_id=thread_id, message=message, uploads=uploads
    )
    assert isinstance(accepted, AcceptedResult), accepted
    await closed(dispatcher, thread_id, accepted.turn_id)
    return accepted


def sent_message(model: RecordingModel) -> str:
    [*_, user] = [message for message in model.requests[-1] if isinstance(message, HumanMessage)]
    return user.text


def test_an_upload_lands_under_the_days_folder_and_returns_its_path(tmp_path: Path) -> None:
    instance = instance_at(tmp_path)
    dispatcher = runtime(instance, RecordingModel())

    path = asyncio.run(put(dispatcher, "statement.csv", b"date,amount\n"))

    assert path == f"uploads/{TODAY}/statement.csv"
    assert (instance.manifest.workspace.path / path).read_bytes() == b"date,amount\n"


def test_an_upload_never_overwrites_a_file_of_the_same_name(tmp_path: Path) -> None:
    instance = instance_at(tmp_path)
    dispatcher = runtime(instance, RecordingModel())

    async def scenario() -> list[str]:
        return [await put(dispatcher, "statement.pdf", bytes([each])) for each in range(3)]

    paths = asyncio.run(scenario())

    assert paths == [
        f"uploads/{TODAY}/statement.pdf",
        f"uploads/{TODAY}/statement (2).pdf",
        f"uploads/{TODAY}/statement (3).pdf",
    ]
    workspace = instance.manifest.workspace.path
    assert [(workspace / path).read_bytes() for path in paths] == [b"\x00", b"\x01", b"\x02"]


@pytest.mark.parametrize(
    ("name", "landed"),
    [
        ("../../kinby.toml", "kinby.toml"),
        ("C:\\Users\\ada\\March: savings*?.csv", "March_ savings__.csv"),
        ("..", "upload"),
    ],
)
def test_an_unsafe_name_lands_safely_in_the_days_folder(
    tmp_path: Path, name: str, landed: str
) -> None:
    instance = instance_at(tmp_path)
    dispatcher = runtime(instance, RecordingModel())

    path = asyncio.run(put(dispatcher, name, b"x"))

    assert path == f"uploads/{TODAY}/{landed}"
    assert (instance.manifest.workspace.path / path).read_bytes() == b"x"


def test_an_upload_over_ten_megabytes_is_refused(tmp_path: Path) -> None:
    instance = instance_at(tmp_path)
    dispatcher = runtime(instance, RecordingModel())
    content = base64.b64encode(bytes(UPLOAD_BYTES + 1)).decode()

    refused = asyncio.run(call(dispatcher, "upload.put", name="big.bin", content=content))

    assert isinstance(refused, ErrorEnvelope)
    assert refused.code is ErrorCode.INVALID_ARGUMENT
    assert not (instance.manifest.workspace.path / "uploads").exists()


def test_a_turn_records_its_uploads_and_the_model_reads_them_after_the_message(
    tmp_path: Path,
) -> None:
    instance = instance_at(tmp_path)
    model = RecordingModel([])
    dispatcher = runtime(instance, model)

    async def scenario() -> tuple[list[str], TurnStarted]:
        paths = [await put(dispatcher, "march.csv", b"1"), await put(dispatcher, "april.pdf", b"2")]
        thread_id = await new_thread(dispatcher)
        accepted = await run_turn(dispatcher, thread_id, "Total this month's spending.", paths)
        started = await until(
            dispatcher, thread_id, lambda event: event.turn_id == accepted.turn_id
        )
        assert isinstance(started.payload, TurnStarted)
        return paths, started.payload

    paths, started = asyncio.run(scenario())

    assert started.message == "Total this month's spending."
    assert started.uploads == paths
    assert sent_message(model) == (
        "Total this month's spending.\n\n"
        f"Uploaded: uploads/{TODAY}/march.csv, uploads/{TODAY}/april.pdf"
    )


def test_files_with_an_empty_message_start_a_turn(tmp_path: Path) -> None:
    instance = instance_at(tmp_path)
    model = RecordingModel([])
    dispatcher = runtime(instance, model)

    async def scenario() -> None:
        path = await put(dispatcher, "statement.pdf", b"%PDF")
        await run_turn(dispatcher, await new_thread(dispatcher), "", [path])

    asyncio.run(scenario())

    assert sent_message(model) == f"Uploaded: uploads/{TODAY}/statement.pdf"


@pytest.mark.parametrize(
    "path",
    [
        "kinby.toml",
        "uploads/../kinby.toml",
        f"uploads/{TODAY}/missing.csv",
        f"uploads/{TODAY}",
        "/etc/passwd",
    ],
)
def test_a_turn_naming_no_upload_is_refused(tmp_path: Path, path: str) -> None:
    instance = instance_at(tmp_path)
    (instance.manifest.workspace.path / "kinby.toml").write_text("id = 'other'\n")
    dispatcher = runtime(instance, RecordingModel())

    async def scenario() -> tuple[object, list[Payload]]:
        await put(dispatcher, "statement.csv", b"1")
        thread_id = await new_thread(dispatcher)
        refused = await call(
            dispatcher, "thread.turn.start", thread_id=thread_id, message="Go.", uploads=[path]
        )
        return refused, payloads(instance, thread_id)

    refused, recorded = asyncio.run(scenario())

    assert isinstance(refused, ErrorEnvelope)
    assert refused.code is ErrorCode.INVALID_ARGUMENT
    assert path in refused.message
    assert recorded == []


def test_the_title_reads_the_message_and_not_the_uploads(tmp_path: Path) -> None:
    instance = instance_at(tmp_path)
    dispatcher = runtime(instance, RecordingModel([]))

    async def scenario() -> ThreadSummary:
        path = await put(dispatcher, "march.csv", b"1")
        thread_id = await new_thread(dispatcher)
        await run_turn(dispatcher, thread_id, "Total this month's spending", [path])
        [thread] = (await call(dispatcher, "thread.list")).threads
        return thread

    thread = asyncio.run(scenario())

    assert thread.title == "Total this month's spending"


def test_reverting_the_uploads_turn_keeps_the_upload(tmp_path: Path) -> None:
    instance = instance_at(tmp_path)

    async def scenario() -> tuple[str, list[Payload]]:
        snapshots = await WorkspaceSnapshots.open(
            instance.manifest.state_dir, instance.manifest.workspace.path, enabled=True
        )
        assert snapshots is not None
        dispatcher = runtime(instance, RecordingModel([]), snapshots)
        path = await put(dispatcher, "march.csv", b"date,amount\n")
        thread_id = await new_thread(dispatcher)
        accepted = await run_turn(dispatcher, thread_id, "Total it.", [path])
        reverted = await call(
            dispatcher, "thread.turn.revert", thread_id=thread_id, turn_id=accepted.turn_id
        )
        assert isinstance(reverted, AcceptedResult), reverted
        return path, payloads(instance, thread_id)

    path, recorded = asyncio.run(scenario())

    assert any(isinstance(payload, WorkspaceReverted) for payload in recorded)
    assert (instance.manifest.workspace.path / path).read_bytes() == b"date,amount\n"


def png(width: int, height: int, *, noise: bool = False) -> bytes:
    image = (
        Image.frombytes("RGB", (width, height), os.urandom(width * height * 3))
        if noise
        else Image.new("RGB", (width, height), "white")
    )
    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


def user_messages(request: list[BaseMessage]) -> list[HumanMessage]:
    return [message for message in request if isinstance(message, HumanMessage)]


def images(message: HumanMessage) -> list[dict[str, object]]:
    return [
        block
        for block in message.content
        if isinstance(block, dict) and block.get("type") == "image"
    ]


def test_an_image_upload_arrives_as_an_image_block_on_its_message(tmp_path: Path) -> None:
    instance = instance_at(tmp_path)
    model = RecordingModel([])
    dispatcher = runtime(instance, model)
    screenshot = png(40, 30)

    async def scenario() -> str:
        path = await put(dispatcher, "positions.png", screenshot)
        await run_turn(dispatcher, await new_thread(dispatcher), "List my positions.", [path])
        return path

    path = asyncio.run(scenario())

    [user] = user_messages(model.requests[-1])
    assert user.text == f"List my positions.\n\nUploaded: {path}"
    assert images(user) == [
        {
            "type": "image",
            "base64": base64.b64encode(screenshot).decode(),
            "mime_type": "image/png",
        }
    ]


def test_a_pdf_and_a_csv_arrive_as_paths_only(tmp_path: Path) -> None:
    instance = instance_at(tmp_path)
    model = RecordingModel([])
    dispatcher = runtime(instance, model)

    async def scenario() -> list[str]:
        paths = [await put(dispatcher, "march.csv", b"1"), await put(dispatcher, "s.pdf", b"%PDF")]
        await run_turn(dispatcher, await new_thread(dispatcher), "Total it.", paths)
        return paths

    paths = asyncio.run(scenario())

    [user] = user_messages(model.requests[-1])
    assert user.text == f"Total it.\n\nUploaded: {', '.join(paths)}"
    assert images(user) == []


def test_an_image_block_stays_in_the_next_turns_request(tmp_path: Path) -> None:
    instance = instance_at(tmp_path)
    model = RecordingModel([], [])
    dispatcher = runtime(instance, model)
    screenshot = png(40, 30)

    async def scenario() -> None:
        path = await put(dispatcher, "positions.png", screenshot)
        thread_id = await new_thread(dispatcher)
        await run_turn(dispatcher, thread_id, "List my positions.", [path])
        await run_turn(dispatcher, thread_id, "Which one gained most?", [])

    asyncio.run(scenario())

    first, second = user_messages(model.requests[-1])
    assert [block["base64"] for block in images(first)] == [base64.b64encode(screenshot).decode()]
    assert second.text == "Which one gained most?"


def test_an_upload_gone_from_the_workspace_is_named_as_gone(tmp_path: Path) -> None:
    instance = instance_at(tmp_path)
    model = RecordingModel([], [])
    dispatcher = runtime(instance, model)

    async def scenario() -> str:
        path = await put(dispatcher, "positions.png", png(40, 30))
        thread_id = await new_thread(dispatcher)
        await run_turn(dispatcher, thread_id, "List my positions.", [path])
        (instance.manifest.workspace.path / path).unlink()
        await run_turn(dispatcher, thread_id, "And now?", [])
        return path

    path = asyncio.run(scenario())

    first, _ = user_messages(model.requests[-1])
    assert first.text == f"List my positions.\n\nUploaded: {path} (gone from the workspace)"
    assert images(first) == []


@pytest.mark.parametrize(
    ("width", "height", "noise"), [(3000, 200, False), (1400, 1400, True)], ids=["wide", "heavy"]
)
def test_an_image_over_the_limit_arrives_scaled_and_stays_whole_on_disk(
    tmp_path: Path, width: int, height: int, noise: bool
) -> None:
    instance = instance_at(tmp_path)
    screenshot = png(width, height, noise=noise)
    model = RecordingModel([])
    dispatcher = runtime(instance, model)

    async def scenario() -> str:
        path = await put(dispatcher, "positions.png", screenshot)
        await run_turn(dispatcher, await new_thread(dispatcher), "Read it.", [path])
        return path

    path = asyncio.run(scenario())

    [user] = user_messages(model.requests[-1])
    [block] = images(user)
    sent = block["base64"]
    assert isinstance(sent, str)
    assert len(sent) <= 5 * 1024 * 1024
    with Image.open(io.BytesIO(base64.b64decode(sent))) as image:
        assert image.format == "PNG"
        assert max(image.size) <= 2000
        assert image.width / image.height == pytest.approx(width / height, rel=0.05)
    assert (instance.manifest.workspace.path / path).read_bytes() == screenshot


def test_a_compactions_summary_request_names_the_uploads(tmp_path: Path) -> None:
    instance = instance_at(tmp_path)
    model = CompactingModel([], [], "Summary one.")
    dispatcher = runtime(instance, model)

    async def scenario() -> str:
        path = await put(dispatcher, "positions.png", png(40, 30))
        thread_id = await new_thread(dispatcher)
        await run_turn(dispatcher, thread_id, "a" * LONG, [path])
        await run_turn(dispatcher, thread_id, "b" * LONG, [])
        await compact(dispatcher, thread_id, "the positions")
        return path

    path = asyncio.run(scenario())

    [summary_call] = model.requests[-1]
    assert f"User: {'a' * LONG}\n\nUploaded: {path}" in summary_call.text
