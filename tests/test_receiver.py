import asyncio
import json
import logging
import os
import signal
import socket
from datetime import UTC, datetime
from http.client import HTTPConnection
from importlib import import_module
from threading import Event as ThreadEvent
from threading import Thread
from uuid import UUID

import pytest

from kinby.cli import main
from kinby.contracts import RoutineOrigin, RoutineTrigger, SignalReceived, TurnStarted
from kinby.core.dispatcher import TurnConfig
from kinby.core.events import EventLog
from kinby.core.receiver import Receiver
from kinby.instance import Serve
from tests.helpers import fixed_permission_ceiling, fixed_turn_preparation
from tests.test_routines import instance_at, routine_file
from tests.test_scheduler import FakeClock, ScriptedRunner, runtime


async def request(
    address: Serve,
    method: str,
    path: str,
    *,
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, bytes]:
    def send() -> tuple[int, bytes]:
        connection = HTTPConnection(address.host, address.port)
        try:
            connection.request(method, path, body=body, headers=headers or {})
            response = connection.getresponse()
            return response.status, response.read()
        finally:
            connection.close()

    return await asyncio.to_thread(send)


def test_health_names_the_served_instance(tmp_path) -> None:
    async def scenario() -> None:
        instance = instance_at(tmp_path)
        dispatcher = runtime(instance, FakeClock(datetime(2026, 9, 7, tzinfo=UTC)))
        receiver = Receiver(Serve("127.0.0.1", 0), dispatcher.scheduler, instance)
        address = await receiver.start()
        try:
            status, body = await request(address, "GET", "/health")
            head_status, _ = await request(address, "HEAD", "/health")
            unknown_status, _ = await request(address, "GET", "/missing")
        finally:
            await receiver.stop()

        assert status == 200
        assert json.loads(body) == {"id": "test"}
        assert head_status == 404
        assert unknown_status == 404

    asyncio.run(scenario())


def test_hmac_signal_records_a_sanitized_delivery_and_rejects_a_bad_signature(
    tmp_path, monkeypatch
) -> None:
    async def scenario() -> None:
        monkeypatch.setenv("SIGNAL_SECRET", "secret")
        instance = instance_at(tmp_path)
        routine_file(
            instance,
            """description: Issues
signal:
  auth: hmac-sha256
  secret: SIGNAL_SECRET
  signature_header: X-Hub-Signature-256
  delivery_header: X-GitHub-Delivery""",
        )
        clock = FakeClock(datetime(2026, 9, 7, 12, tzinfo=UTC))
        dispatcher = runtime(instance, clock)
        receiver = Receiver(Serve("127.0.0.1", 0), dispatcher.scheduler, instance)
        address = await receiver.start()
        body = b'{"action":"opened"}'
        try:
            status, response_body = await request(
                address,
                "POST",
                "/signals/news",
                body=body,
                headers={
                    "Authorization": "discard me",
                    "Content-Type": "application/json; charset=utf-8",
                    "X-GitHub-Delivery": "delivery-1",
                    "X-Hub-Signature-256": (
                        "sha256=d42142b53efbc7cf5cd20b6e074eb33707e0de3b368f698e6d6f6c824ffb8d37"
                    ),
                    "X-Source": "GitHub",
                },
            )
            rejected_status, rejected_body = await request(
                address,
                "POST",
                "/signals/news",
                body=body,
                headers={"X-Hub-Signature-256": "sha256=wrong"},
            )
        finally:
            await receiver.stop()

        await dispatcher.scheduler.tick()
        await dispatcher.scheduler.drain()
        response = json.loads(response_body)
        assert status == 202
        assert set(response) == {"thread_id", "sequence"}
        assert UUID(response["thread_id"])
        assert response["sequence"] == 1
        assert rejected_status == 401
        assert rejected_body == b""

        events = list(EventLog(instance.manifest.state_dir).all_events())
        received = next(event for event in events if isinstance(event.payload, SignalReceived))
        started = next(event for event in events if isinstance(event.payload, TurnStarted))
        assert isinstance(received.payload, SignalReceived)
        assert received.payload.delivery.headers == {
            "accept-encoding": "identity",
            "content-length": "19",
            "content-type": "application/json; charset=utf-8",
            "host": f"127.0.0.1:{address.port}",
            "x-github-delivery": "delivery-1",
            "x-source": "GitHub",
        }
        assert received.payload.delivery.content_type == "application/json; charset=utf-8"
        assert received.payload.delivery.body == '{"action":"opened"}'
        assert received.payload.delivery.delivery_id == "delivery-1"
        assert received.payload.delivery.received_at.tzinfo is not None
        assert started.thread_id == received.thread_id
        assert started.turn_id == received.turn_id
        assert isinstance(started.payload, TurnStarted)
        assert started.payload.origin == RoutineOrigin(
            name="news",
            trigger=RoutineTrigger.SIGNAL,
            delivery_id="delivery-1",
        )

    asyncio.run(scenario())


def test_bearer_signal_accepts_only_the_configured_token(tmp_path, monkeypatch) -> None:
    async def scenario() -> None:
        monkeypatch.setenv("SIGNAL_SECRET", "right-token")
        instance = instance_at(tmp_path)
        routine_file(instance, "description: Issues\nsignal:\n  secret: SIGNAL_SECRET")
        dispatcher = runtime(instance, FakeClock(datetime(2026, 9, 7, tzinfo=UTC)))
        receiver = Receiver(Serve("127.0.0.1", 0), dispatcher.scheduler, instance)
        address = await receiver.start()
        try:
            accepted, _ = await request(
                address,
                "POST",
                "/signals/news",
                headers={"Authorization": "Bearer right-token"},
            )
            rejected, rejected_body = await request(
                address,
                "POST",
                "/signals/news",
                headers={"Authorization": "Bearer wrong-token"},
            )
        finally:
            await receiver.stop()

        assert accepted == 202
        assert rejected == 401
        assert rejected_body == b""
        assert len(list(EventLog(instance.manifest.state_dir).all_events())) == 1

    asyncio.run(scenario())


def test_rejected_calls_are_logged_and_never_recorded(tmp_path, monkeypatch, caplog) -> None:
    async def scenario() -> None:
        monkeypatch.setenv("SIGNAL_SECRET", "secret")
        instance = instance_at(tmp_path)
        routine_file(instance, "description: Plain", name="plain")
        disabled_path = routine_file(
            instance,
            "description: Disabled\nsignal:\n  secret: SIGNAL_SECRET",
            name="disabled",
        )
        routine_file(
            instance,
            "description: Invalid\nsignal:\n  secret: MISSING_SIGNAL_SECRET",
            name="invalid",
        )
        dispatcher = runtime(instance, FakeClock(datetime(2026, 9, 7, tzinfo=UTC)))
        receiver = Receiver(Serve("127.0.0.1", 0), dispatcher.scheduler, instance)
        address = await receiver.start()
        disabled_path.write_text(
            disabled_path.read_text().replace(
                "description: Disabled", "description: Disabled\nenabled: false"
            )
        )
        try:
            statuses = [
                (await request(address, "POST", "/signals/missing"))[0],
                (await request(address, "POST", "/signals/plain"))[0],
                (await request(address, "POST", "/signals/invalid"))[0],
                (await request(address, "GET", "/signals/disabled"))[0],
                (
                    await request(
                        address,
                        "POST",
                        "/signals/disabled",
                        headers={"Authorization": "Bearer wrong"},
                    )
                )[0],
                (
                    await request(
                        address,
                        "POST",
                        "/signals/disabled",
                        headers={"Authorization": "Bearer secret"},
                    )
                )[0],
                (
                    await request(
                        address,
                        "POST",
                        "/signals/disabled",
                        body=b"x" * (1024**2 + 1),
                        headers={"Authorization": "Bearer wrong"},
                    )
                )[0],
            ]
        finally:
            await receiver.stop()

        assert statuses == [404, 404, 404, 405, 401, 410, 413]
        assert list(EventLog(instance.manifest.state_dir).all_events()) == []

    with caplog.at_level(logging.INFO, logger="kinby.core.receiver"):
        asyncio.run(scenario())

    messages = [
        record.getMessage() for record in caplog.records if record.name == "kinby.core.receiver"
    ]
    assert len(messages) == 7
    for path, reason in (
        ("/signals/missing", "404"),
        ("/signals/plain", "404"),
        ("/signals/invalid", "404"),
        ("/signals/disabled", "405"),
        ("/signals/disabled", "401"),
        ("/signals/disabled", "410"),
        ("/signals/disabled", "413"),
    ):
        assert any(path in message and reason in message for message in messages)


def test_unknown_and_non_signal_paths_do_not_read_a_secret(tmp_path) -> None:
    async def scenario() -> None:
        instance = instance_at(tmp_path)
        routine_file(instance, "description: Plain", name="plain")
        routine_file(
            instance,
            "description: Protected\nsignal:\n  secret: MISSING_SIGNAL_SECRET",
            name="protected",
        )
        dispatcher = runtime(instance, FakeClock(datetime(2026, 9, 7, tzinfo=UTC)))
        receiver = Receiver(Serve("127.0.0.1", 0), dispatcher.scheduler, instance)
        address = await receiver.start()
        try:
            unknown, _ = await request(address, "POST", "/signals/missing")
            no_signal, _ = await request(address, "POST", "/signals/plain")
        finally:
            await receiver.stop()

        assert unknown == 404
        assert no_signal == 404

    asyncio.run(scenario())


def test_repeated_delivery_id_returns_the_existing_thread(tmp_path, monkeypatch) -> None:
    async def scenario() -> None:
        monkeypatch.setenv("SIGNAL_SECRET", "secret")
        instance = instance_at(tmp_path)
        routine_file(
            instance,
            """description: Issues
signal:
  secret: SIGNAL_SECRET
  delivery_header: X-Delivery""",
        )
        dispatcher = runtime(instance, FakeClock(datetime(2026, 9, 7, tzinfo=UTC)))
        receiver = Receiver(Serve("127.0.0.1", 0), dispatcher.scheduler, instance)
        address = await receiver.start()
        headers = {
            "Authorization": "Bearer secret",
            "X-Delivery": "delivery-1",
        }
        try:
            first_status, first_body = await request(
                address, "POST", "/signals/news", headers=headers
            )
            repeated_status, repeated_body = await request(
                address, "POST", "/signals/news", headers=headers
            )
        finally:
            await receiver.stop()

        assert first_status == 202
        assert repeated_status == 200
        assert json.loads(repeated_body) == json.loads(first_body)
        assert len(list(EventLog(instance.manifest.state_dir).all_events())) == 1

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("shutdown_signal", "has_signal"),
    [(signal.SIGINT, True), (signal.SIGTERM, False)],
)
def test_serve_listens_reports_paths_and_stops_on_process_signal(
    tmp_path, monkeypatch, capsys, shutdown_signal, has_signal
) -> None:
    monkeypatch.setenv("SIGNAL_SECRET", "secret")
    instance = instance_at(tmp_path)
    if has_signal:
        routine_file(instance, "description: Issues\nsignal:\n  secret: SIGNAL_SECRET")
    with socket.socket() as available:
        available.bind(("127.0.0.1", 0))
        port = available.getsockname()[1]
    with (tmp_path / "kinby.toml").open("a") as manifest:
        manifest.write(f'[serve]\nlisten = "127.0.0.1:{port}"\n')
    monkeypatch.setattr(
        "kinby.core.runtime.turn_config",
        lambda *args, **kwargs: TurnConfig(
            fixed_turn_preparation,
            fixed_permission_ceiling,
            ScriptedRunner(),
        ),
    )
    started = ThreadEvent()
    original_start = Receiver.start

    async def start_and_report(receiver):
        address = await original_start(receiver)
        started.set()
        return address

    monkeypatch.setattr(Receiver, "start", start_and_report)
    health: list[tuple[int, bytes]] = []

    def probe_and_stop() -> None:
        if started.wait(3):
            connection = HTTPConnection("127.0.0.1", port)
            try:
                connection.request("GET", "/health")
                response = connection.getresponse()
                health.append((response.status, response.read()))
            finally:
                connection.close()
        os.kill(os.getpid(), shutdown_signal)

    stopper = Thread(target=probe_and_stop, daemon=True)
    stopper.start()
    exit_code = main(["serve", "--instance", str(tmp_path)])
    stopper.join(timeout=5)

    output = capsys.readouterr()
    assert exit_code == 0, (output.out, output.err)
    assert started.is_set()
    assert not stopper.is_alive()
    assert health == [(200, b'{"id": "test"}')]
    assert f"listen: 127.0.0.1:{port}" in output.out
    assert ("/signals/news" in output.out) is has_signal


def test_serve_logs_a_rejected_signal_to_stderr_at_info(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("SIGNAL_SECRET", "secret")
    instance = instance_at(tmp_path)
    routine_file(instance, "description: Issues\nsignal:\n  secret: SIGNAL_SECRET")
    with socket.socket() as available:
        available.bind(("127.0.0.1", 0))
        port = available.getsockname()[1]
    with (tmp_path / "kinby.toml").open("a") as manifest:
        manifest.write(f'[serve]\nlisten = "127.0.0.1:{port}"\n')
    monkeypatch.setattr(
        "kinby.core.runtime.turn_config",
        lambda *args, **kwargs: TurnConfig(
            fixed_turn_preparation,
            fixed_permission_ceiling,
            ScriptedRunner(),
        ),
    )
    started = ThreadEvent()
    original_start = Receiver.start

    async def start_and_report(receiver):
        address = await original_start(receiver)
        started.set()
        return address

    monkeypatch.setattr(Receiver, "start", start_and_report)
    statuses: list[int] = []

    def reject_and_stop() -> None:
        if started.wait(3):
            connection = HTTPConnection("127.0.0.1", port)
            try:
                connection.request(
                    "POST",
                    "/signals/news",
                    headers={"Authorization": "Bearer wrong-token"},
                )
                response = connection.getresponse()
                statuses.append(response.status)
                response.read()
            finally:
                connection.close()
        os.kill(os.getpid(), signal.SIGTERM)

    stopper = Thread(target=reject_and_stop, daemon=True)
    stopper.start()
    exit_code = main(["serve", "--instance", str(tmp_path)])
    stopper.join(timeout=5)

    output = capsys.readouterr()
    assert exit_code == 0, (output.out, output.err)
    assert statuses == [401]
    assert not stopper.is_alive()
    assert (
        "INFO kinby.core.receiver Signal request rejected at "
        "/signals/news: 401 authentication failed"
    ) in output.err.splitlines()


def test_serve_without_listen_does_not_start_a_receiver(tmp_path, monkeypatch, capsys) -> None:
    instance_at(tmp_path)
    monkeypatch.setattr(
        "kinby.core.runtime.turn_config",
        lambda *args, **kwargs: TurnConfig(
            fixed_turn_preparation,
            fixed_permission_ceiling,
            ScriptedRunner(),
        ),
    )
    cli = import_module("kinby.cli.main")
    original_boot = cli.boot_instance
    booted = ThreadEvent()

    async def boot_and_report(*args, **kwargs):
        instance_runtime = await original_boot(*args, **kwargs)
        booted.set()
        return instance_runtime

    monkeypatch.setattr(cli, "boot_instance", boot_and_report)

    def stop_process() -> None:
        if booted.wait(3):
            os.kill(os.getpid(), signal.SIGTERM)

    stopper = Thread(target=stop_process, daemon=True)
    stopper.start()
    exit_code = main(["serve", "--instance", str(tmp_path)])
    stopper.join(timeout=5)

    output = capsys.readouterr()
    assert exit_code == 0, (output.out, output.err)
    assert booted.is_set()
    assert not stopper.is_alive()
    assert "listen:" not in output.out
