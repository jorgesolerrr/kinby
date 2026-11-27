import asyncio
from collections.abc import Callable
from pathlib import Path

import pytest

from evals.dreaming.consolidation import (
    CASES_DIR,
    MAX_BODY,
    Replacement,
    consolidate,
    grade,
    live_facts,
    load_case,
)
from kinby.instance.init import DEFAULT_CONSOLIDATION_LENS
from tests.test_consolidation import ConsolidatingModel

_CASES = sorted(path for path in CASES_DIR.iterdir() if path.is_dir())
_MERGE = CASES_DIR / "duplicate-merge"


def written(replacement: Replacement, **changes: object) -> dict[str, object]:
    """The fact a model following the case writes, with *changes* to break it."""
    return {
        "description": "The replacement fact",
        "subjects": ["replacement"],
        "body": " ".join(f"`{mention}`." for mention in replacement.mentions),
        "sources": list(replacement.sources),
        "supersedes": list(replacement.supersedes),
    } | changes


def failures(case_path: Path, facts: list[dict[str, object]]) -> tuple[str, ...]:
    model = ConsolidatingModel(facts)
    after = asyncio.run(asyncio.wait_for(consolidate(case_path, lambda _: model), 10))
    assert DEFAULT_CONSOLIDATION_LENS in model.wake
    return grade(load_case(case_path), live_facts(case_path / "instance"), after)


@pytest.mark.parametrize("case_path", _CASES, ids=[path.name for path in _CASES])
def test_a_consolidation_writing_each_expected_fact_passes_the_case(case_path: Path) -> None:
    case = load_case(case_path)

    assert failures(case_path, [written(each) for each in case.replacements]) == ()


@pytest.mark.parametrize("case_path", _CASES, ids=[path.name for path in _CASES])
def test_a_consolidation_writing_nothing_fails_the_case(case_path: Path) -> None:
    case = load_case(case_path)
    superseded = [node for each in case.replacements for node in each.supersedes]

    found = failures(case_path, [])

    assert len(found) == len(superseded) + len(case.replacements)
    assert all(any(node in failure for failure in found) for node in superseded)


def _split(replacement: Replacement) -> list[dict[str, object]]:
    first, *rest = replacement.supersedes
    return [written(replacement, supersedes=[first]), written(replacement, supersedes=rest)]


def _long(replacement: Replacement) -> list[dict[str, object]]:
    padded = f"{written(replacement)['body']} {'x' * MAX_BODY}"
    return [written(replacement, body=padded)]


@pytest.mark.parametrize(
    ("facts", "failure"),
    [
        pytest.param(
            lambda each: [written(each, supersedes=list(each.supersedes[1:]))],
            "is still live",
            id="partial",
        ),
        pytest.param(_split, "No written fact supersedes", id="split"),
        pytest.param(_long, "-character body", id="long"),
        pytest.param(
            lambda each: [written(each, body=f"`{each.mentions[0]}`.")],
            "does not mention",
            id="missing mention",
        ),
        pytest.param(
            lambda each: [written(each, sources=list(each.sources[1:]))],
            "does not cite",
            id="missing source",
        ),
    ],
)
def test_a_replacement_short_of_the_case_fails_it(
    facts: Callable[[Replacement], list[dict[str, object]]], failure: str
) -> None:
    [replacement] = load_case(_MERGE).replacements

    assert [found for found in failures(_MERGE, facts(replacement)) if failure in found]
