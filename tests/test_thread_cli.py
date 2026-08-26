from pathlib import Path

import pytest

from kinby.cli import main
from kinby.instance import init_instance


def test_cli_creates_and_lists_a_thread_through_separate_runs(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    instance = tmp_path / "alice"
    init_instance(instance)

    create_exit_code = main(["thread", "create", str(instance), "--title", "Launch notes"])
    create_output = capsys.readouterr()

    assert create_exit_code == 0
    assert create_output.err == ""
    thread_id = create_output.out.splitlines()[0].removeprefix("id: ")

    list_exit_code = main(["thread", "list", str(instance)])
    list_output = capsys.readouterr()

    assert list_exit_code == 0
    assert list_output.err == ""
    assert thread_id in list_output.out
    assert "Launch notes" in list_output.out
