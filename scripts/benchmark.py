"""Report what an instance and the hub cost, so a change to either can show its before and after.

    uv run scripts/benchmark.py [--events 100000]

It prints the idle memory of a fresh instance before its first model call and after a few turns,
the idle memory of the hub, the peak memory of the readers that cover long ranges over a generated
history, and how long reading one thread and one scheduler tick take on that history. A local
stand-in answers for the model provider and for Docker, so it needs no key and no daemon. Each
number comes from a process of its own. It reads /proc, so it runs on Linux. It is not part of
the gate.
"""

import argparse
import asyncio
import json
import os
import socket
import sys
from collections.abc import AsyncIterator, Mapping
from contextlib import aclosing, asynccontextmanager
from datetime import datetime, timedelta
from itertools import count
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import UUID, uuid4

from aiohttp import web

from kinby.cli.client import ContractClient
from kinby.cli.contract_socket import contract_client
from kinby.contracts import (
    THREAD_CREATE,
    THREAD_SUBSCRIBE,
    THREAD_TURN_START,
    ControlToken,
    ErrorEnvelope,
    MemoryRecapped,
    MessageDelta,
    ModelCompleted,
    Payload,
    ThreadCreateCommand,
    ThreadSubscribeCommand,
    ThreadTurnStartCommand,
    ToolCall,
    ToolResult,
    TurnCompleted,
    TurnFailed,
    TurnStarted,
    Warning,
)
from kinby.core.clock import utc_now
from kinby.core.contract_server import CONTROL_TOKEN_VARIABLE
from kinby.core.events import EventLog
from kinby.core.threads import ThreadStore
from kinby.instance import load_instance
from kinby.instance.init import init_instance

_PROBE = Path(__file__).with_name("benchmark_probe.py")
_MODEL = "anthropic:claude-sonnet-4-5"
_TOKEN = ControlToken("benchmark")
_TURNS = 3
_TURNS_PER_THREAD = 10
_EVENT_INTERVAL = timedelta(minutes=5)
_TOOL_OUTPUT = "A line of a file the turn read, as long as a real one.\n" * 70
_INPUT_TOKENS = 1200
_OUTPUT_TOKENS = 150
_RECAP_DRAFT = {
    "keep": True,
    "description": "A benchmark turn",
    "subjects": ["benchmark"],
    "happened": "The turn answered.",
    "decided": "Nothing.",
    "retrospective": "Nothing.",
}


async def _docker_version(request: web.Request) -> web.Response:
    return web.json_response({"ApiVersion": "1.47"})


async def _count_tokens(request: web.Request) -> web.Response:
    return web.json_response({"input_tokens": _INPUT_TOKENS})


async def _messages(request: web.Request) -> web.StreamResponse:
    """Answer a turn's streamed model call with a short text, and the recap's with its draft."""
    body = await request.json()
    message = {
        "id": f"msg_{uuid4().hex}",
        "type": "message",
        "role": "assistant",
        "model": body["model"],
        "content": [],
        "stop_reason": None,
        "stop_sequence": None,
        "usage": {"input_tokens": _INPUT_TOKENS, "output_tokens": _OUTPUT_TOKENS},
    }
    if not body.get("stream"):
        text = {"type": "text", "text": json.dumps(_RECAP_DRAFT)}
        return web.json_response({**message, "content": [text], "stop_reason": "end_turn"})
    response = web.StreamResponse(headers={"content-type": "text/event-stream"})
    await response.prepare(request)
    for event in (
        {"type": "message_start", "message": message},
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Ok."}},
        {"type": "content_block_stop", "index": 0},
        {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn", "stop_sequence": None},
            "usage": {"output_tokens": _OUTPUT_TOKENS},
        },
        {"type": "message_stop"},
    ):
        await response.write(f"event: {event['type']}\ndata: {json.dumps(event)}\n\n".encode())
    await response.write_eof()
    return response


@asynccontextmanager
async def _stand_in() -> AsyncIterator[dict[str, str]]:
    """Serve the Anthropic and Docker endpoints kinby calls, and yield the environment that
    points kinby at them."""
    application = web.Application()
    application.router.add_get("/version", _docker_version)
    application.router.add_post("/v1/messages", _messages)
    application.router.add_post("/v1/messages/count_tokens", _count_tokens)
    runner = web.AppRunner(application)
    await runner.setup()
    port = _free_port()
    await web.TCPSite(runner, "127.0.0.1", port).start()
    try:
        yield {
            **os.environ,
            "ANTHROPIC_API_KEY": "benchmark",
            "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{port}",
            "DOCKER_HOST": f"tcp://127.0.0.1:{port}",
            CONTROL_TOKEN_VARIABLE: _TOKEN,
        }
    finally:
        await runner.cleanup()


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _resident_kb(pid: int) -> int:
    for line in Path(f"/proc/{pid}/status").read_text().splitlines():
        if line.startswith("VmRSS:"):
            return int(line.split()[1])
    raise LookupError(f"process {pid} reports no VmRSS")


async def _serve_rss(directory: Path, env: Mapping[str, str]) -> tuple[int, int]:
    """The resident memory of `kinby serve` on a fresh instance, before its first model call and
    after it ran a few turns."""
    port = _free_port()
    with (directory / "kinby.toml").open("a", encoding="utf-8") as manifest:
        manifest.write(f'\n[serve]\nlisten = "127.0.0.1:{port}"\n')
    log = directory.with_suffix(".log")
    with log.open("w", encoding="utf-8") as output:
        process = await asyncio.create_subprocess_exec(
            Path(sys.executable).with_name("kinby"),
            "serve",
            directory,
            env=env,
            stdout=output,
            stderr=output,
            cwd=directory.parent,
        )
    try:
        while "listen:" not in log.read_text(encoding="utf-8"):
            if process.returncode is not None:
                raise RuntimeError(f"kinby serve stopped:\n{log.read_text(encoding='utf-8')}")
            await asyncio.sleep(0.1)
        async with contract_client(f"ws://127.0.0.1:{port}/control", _TOKEN) as client:
            thread = await client.call(THREAD_CREATE, ThreadCreateCommand())
            if isinstance(thread, ErrorEnvelope):
                raise RuntimeError(thread.message)
            before = _resident_kb(process.pid)
            for turn in range(_TURNS):
                accepted = await client.call(
                    THREAD_TURN_START,
                    ThreadTurnStartCommand(thread_id=thread.id, message=f"Turn {turn}"),
                )
                if isinstance(accepted, ErrorEnvelope):
                    raise RuntimeError(accepted.message)
                await _recapped(client, accepted.thread_id, accepted.turn_id, accepted.sequence)
            return before, _resident_kb(process.pid)
    finally:
        process.terminate()
        await process.wait()


async def _recapped(client: ContractClient, thread_id: UUID, turn_id: UUID, after: int) -> None:
    """Wait until the turn is closed and recapped, so its model calls are behind it."""
    stream = await client.subscribe(
        THREAD_SUBSCRIBE, ThreadSubscribeCommand(thread_id=thread_id, after_sequence=after)
    )
    if isinstance(stream, ErrorEnvelope):
        raise RuntimeError(stream.message)
    async with aclosing(stream):
        async for event in stream.items:
            if isinstance(event, ErrorEnvelope):
                raise RuntimeError(event.message)
            if event.turn_id == turn_id and isinstance(event.payload, MemoryRecapped):
                return
            if isinstance(event.payload, TurnFailed | Warning):
                raise RuntimeError(f"the benchmark turn did not finish: {event.payload}")


def _turn(call_id: str) -> tuple[Payload, ...]:
    """The events of one recapped turn with a tool round, as a real one records them."""
    return (
        TurnStarted(message="Read the plan and tell me what is left for this week.", model=_MODEL),
        MessageDelta(text="Reading the plan."),
        ToolCall(call_id=call_id, name="read_file", arguments={"path": "notes/plan.md"}),
        ToolResult(call_id=call_id, name="read_file", output=_TOOL_OUTPUT, error=False),
        _model_completed(duration_ms=900),
        MessageDelta(text="Two items are left: the release notes and the migration check."),
        _model_completed(duration_ms=700),
        TurnCompleted(input_tokens=_INPUT_TOKENS, output_tokens=_OUTPUT_TOKENS),
        _model_completed(duration_ms=1100),
        MemoryRecapped(
            node=None, model=_MODEL, input_tokens=_INPUT_TOKENS, output_tokens=_OUTPUT_TOKENS
        ),
    )


def _model_completed(duration_ms: int) -> ModelCompleted:
    return ModelCompleted(
        model=_MODEL,
        duration_ms=duration_ms,
        input_tokens=_INPUT_TOKENS,
        output_tokens=_OUTPUT_TOKENS,
    )


_THREAD_EVENTS = _TURNS_PER_THREAD * len(_turn(""))


async def _generate_history(state_dir: Path, events: int) -> tuple[UUID, int]:
    """Write about *events* events through the transcript store, in threads of ten turns, and
    return one thread's id and the number written."""
    threads = max(1, events // _THREAD_EVENTS)
    total = threads * _THREAD_EVENTS
    start = utc_now() - _EVENT_INTERVAL * total
    ticks = count()

    def clock() -> datetime:
        return start + _EVENT_INTERVAL * next(ticks)

    store = ThreadStore(state_dir)
    log = EventLog(state_dir, clock=clock)
    thread_ids = [store.create(f"Thread {number}").id for number in range(threads)]
    for thread_id in thread_ids:
        for turn in range(_TURNS_PER_THREAD):
            turn_id = uuid4()
            for payload in _turn(f"call-{turn}"):
                await log.append(thread_id, turn_id, payload)
    return thread_ids[0], total


async def _probe(env: Mapping[str, str], name: str, *arguments: object) -> dict[str, float]:
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        _PROBE,
        name,
        *map(str, arguments),
        env=env,
        stdout=asyncio.subprocess.PIPE,
    )
    output, _ = await process.communicate()
    if process.returncode:
        raise RuntimeError(f"the {name} probe failed")
    return json.loads(output.splitlines()[-1])


def _mb(kilobytes: float) -> str:
    return f"{kilobytes / 1024:,.1f} MB"


async def _run(events: int) -> None:
    with TemporaryDirectory(prefix="kinby-benchmark-") as temporary:
        root = Path(temporary)
        async with _stand_in() as env:
            fresh = init_instance(root / "fresh", _MODEL)
            before_call, after_turns = await _serve_rss(fresh, env)
            print(f"instance idle, before its first model call: {_mb(before_call)}")
            print(f"instance idle, after {_TURNS} turns on anthropic: {_mb(after_turns)}")
            hub = await _probe(env, "hub", root / "hub")
            print(f"hub idle: {_mb(hub['rss_kb'])}")

            history = init_instance(root / "history", _MODEL)
            state_dir = load_instance(history).manifest.state_dir
            thread_id, written = await _generate_history(state_dir, events)
            size = sum(path.stat().st_size for path in state_dir.rglob("*") if path.is_file())
            print(f"history: {written:,} events, {_mb(size / 1024)} on disk")
            for reader in ("recap-catch-up", "stats", "reflection"):
                peak = await _probe(env, reader, history)
                print(
                    f"{reader} peak: {_mb(peak['peak_kb'])}, "
                    f"{_mb(peak['peak_kb'] - peak['before_kb'])} over "
                    f"the {_mb(peak['before_kb'])} before it read"
                )
            reads = await _probe(env, "reads", history, thread_id)
            print(f"read one thread of {_THREAD_EVENTS} events: {reads['thread_read_ms']:,.1f} ms")
            print(f"one scheduler tick's reads: {reads['tick_ms']:,.1f} ms")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--events",
        type=int,
        default=100_000,
        help="how many events the generated history holds (default: 100000)",
    )
    asyncio.run(_run(parser.parse_args().events))


if __name__ == "__main__":
    main()
