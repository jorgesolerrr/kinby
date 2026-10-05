from pathlib import Path
from uuid import uuid4

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


def _create(instance: Path, title: str, capsys: pytest.CaptureFixture[str]) -> str:
    assert main(["thread", "create", str(instance), "--title", title]) == 0
    return capsys.readouterr().out.splitlines()[0].removeprefix("id: ")


def _titles(instance: Path, capsys: pytest.CaptureFixture[str], *options: str) -> list[str]:
    assert main(["thread", "list", str(instance), *options]) == 0
    return [line.split("\t")[2] for line in capsys.readouterr().out.splitlines()]


def test_cli_archives_a_thread_out_of_the_default_list_and_unarchives_it(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    instance = tmp_path / "alice"
    init_instance(instance)
    _create(instance, "Launch notes", capsys)
    thread_id = _create(instance, "Groceries", capsys)

    archive_exit_code = main(["thread", "archive", thread_id, str(instance)])
    archived = capsys.readouterr()
    default = _titles(instance, capsys)
    archived_only = _titles(instance, capsys, "--filter", "archived")
    every = _titles(instance, capsys, "--filter", "all")
    unarchive_exit_code = main(["thread", "unarchive", thread_id, str(instance)])
    unarchived = capsys.readouterr()

    assert (archive_exit_code, archived.err) == (0, "")
    assert archived.out == f"archived {thread_id}\n"
    assert default == ["Launch notes"]
    assert archived_only == ["Groceries"]
    assert every == ["Groceries", "Launch notes"]
    assert (unarchive_exit_code, unarchived.err) == (0, "")
    assert unarchived.out == f"unarchived {thread_id}\n"
    assert _titles(instance, capsys, "--filter", "sidebar") == ["Groceries", "Launch notes"]


def test_cli_refuses_to_archive_an_unknown_thread(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    instance = tmp_path / "alice"
    init_instance(instance)
    thread_id = uuid4()

    exit_code = main(["thread", "archive", str(thread_id), str(instance)])

    assert exit_code == 1
    assert f'NOT_FOUND: Thread "{thread_id}" was not found.' in capsys.readouterr().err
