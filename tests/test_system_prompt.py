import asyncio
from collections.abc import AsyncIterator, Sequence
from datetime import date
from pathlib import Path
from typing import Self

from langchain_core.messages import AIMessageChunk, BaseMessage, SystemMessage
from langchain_core.tools import StructuredTool

from kinby.contracts import AcceptedResult, Event, Scope, ThreadCreateResult
from kinby.core import LangGraphRunner, TurnConfig, assemble_system_prompt, build_dispatcher
from kinby.core.dispatcher import Dispatcher
from kinby.instance import load_instance


class ScriptedModel:
    def __init__(self) -> None:
        self.calls: list[tuple[BaseMessage, ...]] = []

    def bind_tools(self, tools: Sequence[StructuredTool]) -> Self:
        return self

    async def astream(
        self,
        messages: Sequence[BaseMessage],
    ) -> AsyncIterator[AIMessageChunk]:
        self.calls.append(tuple(messages))
        yield AIMessageChunk(content="Done")


def _instance_with_prompt_files(tmp_path: Path) -> Path:
    instance_path = tmp_path / "ada"
    workspace_path = instance_path / "workspace"
    memory_path = instance_path / "memory"
    workspace_path.mkdir(parents=True)
    memory_path.mkdir()
    (instance_path / "kinby.toml").write_text(
        'id = "ada"\n'
        'persona_name = "Ada"\n\n'
        '[models]\nmain = "openai:gpt-5"\n\n'
        "[workspace.conventions]\nenabled = true\n"
        'instructions = ["AGENTS.md", "TEAM.md"]\n',
        encoding="utf-8",
    )
    (instance_path / "SYSTEM.md").write_text("INSTANCE BEHAVIOR", encoding="utf-8")
    (workspace_path / "AGENTS.md").write_text("FIRST WORKSPACE RULES", encoding="utf-8")
    (workspace_path / "TEAM.md").write_text("SECOND WORKSPACE RULES", encoding="utf-8")
    (memory_path / "profile.md").write_text("USER PROFILE", encoding="utf-8")
    return instance_path


async def _start_turn(dispatcher: Dispatcher, message: str) -> None:
    created = await dispatcher.dispatch(
        "thread.create",
        {},
        {Scope.THREAD_OPERATE},
    )
    assert isinstance(created, ThreadCreateResult)
    accepted = await dispatcher.dispatch(
        "thread.turn.start",
        {"thread_id": created.id, "message": message},
        {Scope.THREAD_OPERATE},
    )
    assert isinstance(accepted, AcceptedResult)
    subscription = dispatcher.subscribe(
        "thread.subscribe",
        {"thread_id": created.id, "after_sequence": 0},
        {Scope.THREAD_READ},
    )
    for _ in range(3):
        await asyncio.wait_for(anext(subscription), timeout=1)
    await subscription.aclose()


def test_model_receives_prompt_sections_in_order_with_environment_last(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        instance = load_instance(_instance_with_prompt_files(tmp_path))
        model = ScriptedModel()
        runner = LangGraphRunner(instance, model_factory=lambda _: model)
        dispatcher = build_dispatcher(
            instance.manifest.state_dir,
            turns=TurnConfig(runner.prepare_for_turn, runner),
        )

        await _start_turn(dispatcher, "Hello")

        assert len(model.calls) == 1
        system_messages = [
            message for message in model.calls[0] if isinstance(message, SystemMessage)
        ]
        assert len(system_messages) == 1
        assert model.calls[0][0] is system_messages[0]
        prompt = system_messages[0].text
        ordered_text = (
            "You are a personal AI teammate running on kinby.",
            "INSTANCE BEHAVIOR",
            "FIRST WORKSPACE RULES",
            "SECOND WORKSPACE RULES",
            "# Skills",
            "USER PROFILE",
            "# Environment",
        )
        positions = [prompt.index(text) for text in ordered_text]
        assert positions == sorted(positions)
        assert prompt.endswith(
            "# Environment\n"
            "instance id: ada\n"
            "persona name: Ada\n"
            f"workspace path: {instance.manifest.workspace.path}\n"
            "main model: openai:gpt-5\n"
            f"date: {date.today().isoformat()}"
        )

    asyncio.run(scenario())


def test_prompt_files_and_manifest_are_reloaded_between_turns(tmp_path: Path) -> None:
    async def scenario() -> None:
        instance_path = _instance_with_prompt_files(tmp_path)
        instance = load_instance(instance_path)
        model = ScriptedModel()
        runner = LangGraphRunner(instance, model_factory=lambda _: model)
        dispatcher = build_dispatcher(
            instance.manifest.state_dir,
            turns=TurnConfig(runner.prepare_for_turn, runner),
        )
        created = await dispatcher.dispatch(
            "thread.create",
            {},
            {Scope.THREAD_OPERATE},
        )
        assert isinstance(created, ThreadCreateResult)

        after_sequence = 0
        for behavior, persona_name in (
            ("FIRST BEHAVIOR", "Ada"),
            ("SECOND BEHAVIOR", "Grace"),
        ):
            (instance_path / "SYSTEM.md").write_text(behavior, encoding="utf-8")
            manifest_path = instance_path / "kinby.toml"
            manifest_text = manifest_path.read_text(encoding="utf-8")
            manifest_path.write_text(
                manifest_text.replace('persona_name = "Ada"', f'persona_name = "{persona_name}"'),
                encoding="utf-8",
            )
            accepted = await dispatcher.dispatch(
                "thread.turn.start",
                {"thread_id": created.id, "message": "Hello"},
                {Scope.THREAD_OPERATE},
            )
            assert isinstance(accepted, AcceptedResult)
            subscription = dispatcher.subscribe(
                "thread.subscribe",
                {"thread_id": created.id, "after_sequence": after_sequence},
                {Scope.THREAD_READ},
            )
            events = [await asyncio.wait_for(anext(subscription), timeout=1) for _ in range(3)]
            await subscription.aclose()
            last = events[-1]
            assert isinstance(last, Event)
            after_sequence = last.sequence

        system_prompts = [
            next(message.text for message in call if isinstance(message, SystemMessage))
            for call in model.calls
        ]
        assert "FIRST BEHAVIOR" in system_prompts[0]
        assert "SECOND BEHAVIOR" not in system_prompts[0]
        assert "SECOND BEHAVIOR" in system_prompts[1]
        assert "FIRST BEHAVIOR" not in system_prompts[1]
        assert "persona name: Ada" in system_prompts[0]
        assert "persona name: Grace" in system_prompts[1]
        assert all(
            sum(isinstance(message, SystemMessage) for message in call) == 1 for call in model.calls
        )

    asyncio.run(scenario())


def test_missing_optional_files_leave_core_sections(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        instance_path = tmp_path / "bare"
        instance_path.mkdir()
        (instance_path / "kinby.toml").write_text(
            'id = "bare"\n\n[models]\nmain = "openai:gpt-5"\n',
            encoding="utf-8",
        )
        instance = load_instance(instance_path)
        model = ScriptedModel()
        runner = LangGraphRunner(instance, model_factory=lambda _: model)
        dispatcher = build_dispatcher(
            instance.manifest.state_dir,
            turns=TurnConfig(runner.prepare_for_turn, runner),
        )

        await _start_turn(dispatcher, "Hello")

        system_message = next(
            message for message in model.calls[0] if isinstance(message, SystemMessage)
        )
        assert system_message.text == (
            "You are a personal AI teammate running on kinby.\n\n"
            "# Skills\n"
            "Use the `skill` tool to read a skill's full instructions.\n\n"
            "# Environment\n"
            "instance id: bare\n"
            f"workspace path: {instance.manifest.workspace.path}\n"
            "main model: openai:gpt-5\n"
            f"date: {date.today().isoformat()}"
        )

    asyncio.run(scenario())


def test_prompt_sections_name_their_sources(tmp_path: Path) -> None:
    instance = load_instance(_instance_with_prompt_files(tmp_path))

    sections = assemble_system_prompt(instance, (), date(2026, 8, 28))

    assert [(section.name, str(section.source)) for section in sections] == [
        ("preamble", "kinby"),
        ("behavior prompt", str(instance.path / "SYSTEM.md")),
        (
            "workspace conventions",
            str(instance.manifest.workspace.path / "AGENTS.md"),
        ),
        (
            "workspace conventions",
            str(instance.manifest.workspace.path / "TEAM.md"),
        ),
        ("skills catalogue", "runtime"),
        ("profile", str(instance.path / "memory" / "profile.md")),
        ("environment", "runtime"),
    ]
