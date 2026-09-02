from pathlib import Path

import pytest

from kinby.cli import main

EXAMPLE_INSTANCES = Path(__file__).parents[1] / "examples" / "instances"


@pytest.mark.parametrize(
    ("instance_name", "expected_id", "lists_conventions"),
    [
        ("minimal", "minimal", False),
        ("coding-agent", "coding-agent", True),
    ],
)
def test_example_instances_load_through_instance_show(
    instance_name: str,
    expected_id: str,
    lists_conventions: bool,
    capsys: pytest.CaptureFixture[str],
) -> None:
    instance = EXAMPLE_INSTANCES / instance_name

    exit_code = main(["instance", "show", str(instance)])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert f"id: {expected_id}" in captured.out
    assert ("conventions:" in captured.out) is lists_conventions
    assert captured.err == ""


def test_coding_agent_example_has_an_instance_tool() -> None:
    tool_path = EXAMPLE_INSTANCES / "coding-agent" / "tools" / "workspace_name.py"

    assert tool_path.is_file()


def test_coding_agent_example_has_a_coding_recap_prompt() -> None:
    recap_path = EXAMPLE_INSTANCES / "coding-agent" / "RECAP.md"

    assert recap_path.read_text(encoding="utf-8") == (
        "Review the coding turn against the user's requested outcome.\n"
        "\n"
        'In "What should have gone differently":\n'
        "\n"
        "- Say which tests and checks ran, and which were skipped.\n"
        "- Name any code-reading, editing, or command path that could have been shorter.\n"
        "- State each assumption about the workspace, its conventions, or its state.\n"
    )
