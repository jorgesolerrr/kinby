from pathlib import Path

import pytest

from kinby.contracts import PackageCommit
from kinby.hub.curated import curated_list

CODER = """\
id = "coder"
display_name = "Software factory"
description = "Implements GitHub issues."
icon = "code"
distribution = "kinby-code-factory"

[source]
url = "https://github.com/jorgesolerrr/kinby-code-factory"
sha = "{sha}"
"""


def write_entry(directory: Path, *, sha: str = "a" * 40, recipe: str | None = "RUN true\n") -> None:
    (directory / "coder.toml").write_text(CODER.format(sha=sha), encoding="utf-8")
    if recipe is not None:
        (directory / "coder.Dockerfile").write_text(recipe, encoding="utf-8")


def test_every_shipped_entry_pins_a_full_commit_and_carries_its_recipe():
    entries = curated_list()

    assert entries
    for entry in entries:
        assert isinstance(entry.package.selection.version, PackageCommit)
        assert entry.recipe.strip()


def test_an_entry_reads_as_a_card_with_the_selection_that_prepares_it(tmp_path):
    write_entry(tmp_path, recipe="RUN install-coding-clients\n")

    [entry] = curated_list(tmp_path, tmp_path)

    assert entry.package.model_dump(mode="json") == {
        "id": "coder",
        "display_name": "Software factory",
        "description": "Implements GitHub issues.",
        "icon": "code",
        "selection": {
            "id": "coder",
            "distribution": "kinby-code-factory",
            "version": {
                "url": "https://github.com/jorgesolerrr/kinby-code-factory",
                "sha": "a" * 40,
            },
            "image_recipe": "",
        },
    }
    assert entry.recipe == "RUN install-coding-clients\n"


def test_an_entry_pinned_to_a_branch_is_refused(tmp_path):
    write_entry(tmp_path, sha="main")

    with pytest.raises(ValueError, match=r"coder\.toml"):
        curated_list(tmp_path, tmp_path)


def test_an_entry_without_its_recipe_is_refused(tmp_path):
    write_entry(tmp_path, recipe=None)

    with pytest.raises(FileNotFoundError, match=r"coder\.Dockerfile"):
        curated_list(tmp_path, tmp_path)
