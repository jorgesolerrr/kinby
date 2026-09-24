import asyncio
import json

import pytest

from kinby.cli import main
from kinby.contracts import (
    RoutineName,
    RoutineOrigin,
    RoutineTrigger,
    ToolResult,
    TurnFailed,
    is_turn_closing,
)
from kinby.core.events import EventLog
from kinby.core.threads import ThreadStore
from kinby.core.turn_runner import LangGraphRunner
from kinby.core.turns import Turns
from kinby.instance import load_instance
from tests.fake_package import install_fake_package


@pytest.fixture
def writer(tmp_path, monkeypatch):
    package = install_fake_package(tmp_path / "site")
    monkeypatch.syspath_prepend(str(package.site))
    return package


def _packaged_instance(tmp_path, capsys):
    path = tmp_path / "instance"
    assert main(["init", str(path), "--package", "writer", "--model", "openai:gpt-5"]) == 0
    capsys.readouterr()
    return path


def _run_draft(path, capsys):
    status = main(["routine", "run", "draft", "--instance", str(path)])
    return status, capsys.readouterr()


def _seen(path):
    return json.loads((path / "workspace" / "seen.json").read_text(encoding="utf-8"))


def test_a_code_step_receives_the_validated_config_and_an_edit_applies_at_the_next_run(
    writer, tmp_path, capsys
):
    path = _packaged_instance(tmp_path, capsys)

    status, _ = _run_draft(path, capsys)

    assert status == 0
    assert (path / "package.yaml").read_text(
        encoding="utf-8"
    ) == "tone: plain\ntoken: EDITOR_TOKEN\n"
    assert _seen(path) == {"tone": "plain", "token": "EDITOR_TOKEN"}

    (path / "package.yaml").write_text("tone: formal\ntoken: EDITOR_TOKEN\n", encoding="utf-8")
    status, _ = _run_draft(path, capsys)

    assert status == 0
    assert _seen(path) == {"tone": "formal", "token": "EDITOR_TOKEN"}


@pytest.mark.parametrize(
    ("body", "message"),
    [
        (
            "tone: plain\ntoken: EDITOR_TOKEN\ntonne: formal\n",
            "tonne: Extra inputs are not permitted",
        ),
        (
            "tone: plain\ntoken: GITHUB_TOKEN\n",
            'token: "GITHUB_TOKEN" is not a required secret this package declares',
        ),
        ("tone: [plain\n", "while parsing a flow sequence"),
    ],
    ids=["unknown key", "undeclared secret", "not yaml"],
)
def test_an_invalid_config_stops_the_boot_naming_the_file(writer, tmp_path, capsys, body, message):
    path = _packaged_instance(tmp_path, capsys)
    (path / "package.yaml").write_text(body, encoding="utf-8")

    status, output = _run_draft(path, capsys)

    assert status == 1
    assert f"{path / 'package.yaml'}: " in output.err
    assert message in output.err
    assert not (path / "workspace" / "seen.json").exists()


def test_a_missing_config_is_an_error_and_never_falls_back_to_defaults(writer, tmp_path, capsys):
    path = _packaged_instance(tmp_path, capsys)
    (path / "package.yaml").unlink()

    status, output = _run_draft(path, capsys)

    assert status == 1
    assert f"{path / 'package.yaml'}: " in output.err
    assert "requires this file" in output.err


def test_an_invalid_value_fails_that_run_with_the_validator_message_and_path(
    writer, tmp_path, capsys
):
    path = _packaged_instance(tmp_path, capsys)
    instance = load_instance(path)
    (path / "package.yaml").write_text("tone: shouty\ntoken: EDITOR_TOKEN\n", encoding="utf-8")

    events = asyncio.run(_fire_draft(instance))

    result = events[-2].payload
    failed = events[-1].payload
    assert isinstance(result, ToolResult) and result.error
    assert isinstance(failed, TurnFailed)
    assert f"{path / 'package.yaml'}: tone: Input should be 'plain' or 'formal'" in failed.message
    assert not (path / "workspace" / "seen.json").exists()


async def _fire_draft(instance):
    log = EventLog(instance.manifest.state_dir)
    store = ThreadStore(instance.manifest.state_dir)
    runner = LangGraphRunner(instance, event_log=log, model_factory=_no_model)
    turns = Turns(store, log, runner, runner.prepare_for_turn, runner.permission_ceiling, _no_recap)
    thread = store.create(None)
    await turns.wake(
        thread.id,
        "Draft it.",
        RoutineOrigin(name=RoutineName("draft"), trigger=RoutineTrigger.MANUAL),
    )
    subscription = (await log.subscribe(thread.id)).items
    events = []
    async with asyncio.timeout(5):
        async for event in subscription:
            events.append(event)
            if is_turn_closing(event.payload):
                break
    await subscription.aclose()
    return events


def _no_model(name):
    raise AssertionError("a failed code step calls no model")


def _no_recap(thread_id, turn_id):
    pass


def test_initialization_refuses_a_template_whose_config_fails_validation(
    tmp_path, monkeypatch, capsys
):
    package = install_fake_package(tmp_path / "site", config="tone: plain\n")
    monkeypatch.syspath_prepend(str(package.site))
    path = tmp_path / "instance"

    status = main(["init", str(path), "--package", "writer"])

    assert status == 1
    error = capsys.readouterr().err
    assert f"{package.template / 'package.yaml'}: token: Field required" in error
    assert not path.exists()


def test_a_vanilla_instance_has_no_config_and_its_code_steps_receive_none(tmp_path, capsys):
    path = tmp_path / "instance"
    assert main(["init", str(path), "--model", "openai:gpt-5"]) == 0
    routine = path / "routines" / "draft"
    routine.mkdir()
    (routine / "ROUTINE.md").write_text(
        "---\ndescription: Draft.\nenabled: false\nmode: full-access\n---\nDraft it.\n",
        encoding="utf-8",
    )
    (routine / "run.py").write_text(
        "import json\nfrom kinby.plugins import ToolContext, tool\n@tool(write=False)\n"
        "def draft(context: ToolContext) -> None:\n"
        '    """Record the configuration."""\n'
        "    (context.workspace / 'seen.json').write_text(json.dumps(context.package_config))\n",
        encoding="utf-8",
    )
    capsys.readouterr()

    status, _ = _run_draft(path, capsys)

    assert status == 0
    assert not (path / "package.yaml").exists()
    assert _seen(path) is None
