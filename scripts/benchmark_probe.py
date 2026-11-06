"""Measure one cost of kinby in this fresh process and print it as JSON, for scripts/benchmark.py.

Each probe imports what it measures inside its own function, so the memory it reports holds no
other probe's imports.
"""

import asyncio
import json
import statistics
import sys
import time
from collections.abc import Awaitable, Callable
from contextlib import aclosing
from pathlib import Path
from uuid import UUID

_STATUS = Path("/proc/self/status")
_TIMED_RUNS = 3

type Measures = dict[str, float]


def _kilobytes(field: str) -> int:
    for line in _STATUS.read_text().splitlines():
        if line.startswith(f"{field}:"):
            return int(line.split()[1])
    raise LookupError(f"{_STATUS} has no {field}")


async def _peak(read: Awaitable[object]) -> Measures:
    """The resident memory before *read*, and the most the process held while it ran."""
    # Writing 5 resets the high-water mark to the current resident memory.
    Path("/proc/self/clear_refs").write_text("5")
    before = _kilobytes("VmRSS")
    await read
    return {"before_kb": before, "peak_kb": _kilobytes("VmHWM")}


async def _median_ms(run: Callable[[], Awaitable[object]]) -> float:
    durations = []
    for _ in range(_TIMED_RUNS):
        started = time.perf_counter()
        await run()
        durations.append((time.perf_counter() - started) * 1000)
    return statistics.median(durations)


async def hub(directory: Path) -> Measures:
    """The hub built and listening, as `kinby hub` starts it, before recovery asks Docker for
    its containers."""
    import kinby.cli.main  # noqa: F401 - `kinby hub` loads the CLI before the hub
    from kinby.hub import HubContractServer, build_docker_hub
    from kinby.instance import Serve

    built = await asyncio.to_thread(
        build_docker_hub,
        directory,
        Path(__file__).resolve().parents[1],
        directory,
        network="kinby_private",
        private_url="http://hub:8080",
    )
    server = HubContractServer(built.dispatcher, built.access, built, built.replies)
    await server.start(Serve("127.0.0.1", 0))
    try:
        return {"rss_kb": _kilobytes("VmRSS")}
    finally:
        await server.stop()


async def recap_catch_up(directory: Path) -> Measures:
    from kinby.core.events import EventLog
    from kinby.instance import load_instance
    from kinby.memory import GraphStore, RecapWriter

    instance = load_instance(directory)
    recap = RecapWriter(EventLog(instance.manifest.state_dir), GraphStore(instance.path), instance)
    return await _peak(recap.catch_up())


async def stats(directory: Path) -> Measures:
    from functools import partial

    from kinby.cli.client import ContractClient
    from kinby.contracts import INSTANCE_SCOPES, STATS_GET, ErrorEnvelope, StatsGetCommand
    from kinby.core.dispatcher import build_dispatcher
    from kinby.core.pricing import instance_prices
    from kinby.instance import load_instance

    instance = load_instance(directory)
    dispatcher = build_dispatcher(
        instance.manifest.state_dir, prices=partial(instance_prices, instance)
    )
    client = ContractClient(dispatcher.dispatch, dispatcher.subscribe, INSTANCE_SCOPES)

    async def read() -> None:
        result = await client.call(STATS_GET, StatsGetCommand())
        if isinstance(result, ErrorEnvelope):
            raise RuntimeError(result.message)

    return await _peak(read())


async def reflection(directory: Path) -> Measures:
    from kinby.contracts import RoutineName
    from kinby.core.events import EventLog
    from kinby.instance import load_instance
    from kinby.memory import GraphStore
    from kinby.memory.reflection import reflection_step

    instance = load_instance(directory)
    step = reflection_step(
        GraphStore(instance.path),
        EventLog(instance.manifest.state_dir),
        RoutineName("reflection"),
    )
    return await _peak(step.runnable.ainvoke({}))


async def reads(directory: Path, thread_id: UUID) -> Measures:
    """How long reading one thread and one scheduler tick take on a booted instance."""
    from kinby.cli.client import ContractClient
    from kinby.contracts import (
        INSTANCE_SCOPES,
        THREAD_SUBSCRIBE,
        ErrorEnvelope,
        ThreadSubscribeCommand,
    )
    from kinby.core.runtime import boot_instance
    from kinby.instance import load_instance

    runtime = await boot_instance(load_instance(directory))
    # The tick is timed alone, not next to the one the scheduler starts at boot.
    await runtime.scheduler.stop()
    client = ContractClient(
        runtime.dispatcher.dispatch, runtime.dispatcher.subscribe, INSTANCE_SCOPES
    )

    async def read_thread() -> None:
        command = ThreadSubscribeCommand(thread_id=thread_id)
        stream = await client.subscribe(THREAD_SUBSCRIBE, command)
        if isinstance(stream, ErrorEnvelope):
            raise RuntimeError(stream.message)
        async with aclosing(stream):
            read = 0
            async for _ in stream.items:
                read += 1
                if read == stream.head_sequence:
                    return

    try:
        return {
            "thread_read_ms": await _median_ms(read_thread),
            "tick_ms": await _median_ms(runtime.scheduler.tick),
        }
    finally:
        await runtime.stop_after_running_routine()


def main() -> None:
    match sys.argv[1:]:
        case ["hub", directory]:
            measures = hub(Path(directory))
        case ["recap-catch-up", directory]:
            measures = recap_catch_up(Path(directory))
        case ["stats", directory]:
            measures = stats(Path(directory))
        case ["reflection", directory]:
            measures = reflection(Path(directory))
        case ["reads", directory, thread_id]:
            measures = reads(Path(directory), UUID(thread_id))
        case _:
            raise SystemExit(f"unknown probe: {' '.join(sys.argv[1:])}")
    print(json.dumps(asyncio.run(measures)))


if __name__ == "__main__":
    main()
