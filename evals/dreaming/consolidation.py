"""Run consolidation over a fixture case and grade the graph it leaves."""

import shutil
import tempfile
import tomllib
from collections.abc import Sequence
from datetime import date
from pathlib import Path

from pydantic import ConfigDict, TypeAdapter
from pydantic.dataclasses import dataclass

from evals.stage import run_stage, write_stage
from kinby.contracts import NodeId, RoutineName
from kinby.core.turn_runner import ModelFactory
from kinby.instance.init import DEFAULT_CONSOLIDATION_LENS
from kinby.memory import Fact, GraphStore

CASES_DIR = Path(__file__).parent / "cases"
#: The longest body a replacement fact may have and still read in a tight turn.
MAX_BODY = 800
_STAGE = RoutineName("consolidation")


@dataclass(frozen=True, config=ConfigDict(extra="forbid"))
class Replacement:
    """A fact the case expects consolidation to write in place of older live facts."""

    supersedes: tuple[NodeId, ...]
    #: The episodes the frame's rules have it cite.
    sources: tuple[NodeId, ...]
    #: What the episodes teach that it must say, matched case-insensitively.
    mentions: tuple[str, ...]


@dataclass(frozen=True, config=ConfigDict(extra="forbid"))
class DreamingCase:
    name: str
    #: Consolidation dates the facts it writes on this day.
    today: date
    replacements: tuple[Replacement, ...]


_DREAMING_CASE = TypeAdapter(DreamingCase)


def load_case(case_path: Path) -> DreamingCase:
    return _DREAMING_CASE.validate_python(
        tomllib.loads((case_path / "case.toml").read_text(encoding="utf-8"))
    )


def live_facts(instance_path: Path) -> tuple[Fact, ...]:
    return tuple(node for node in GraphStore(instance_path).nodes() if isinstance(node, Fact))


async def consolidate(case_path: Path, model_factory: ModelFactory) -> tuple[Fact, ...]:
    """Run consolidation once over a copy of the case's fixture and return the live facts after."""
    case = load_case(case_path)
    with tempfile.TemporaryDirectory(prefix="kinby-dreaming-eval-") as temporary:
        instance_path = Path(temporary) / "instance"
        shutil.copytree(case_path / "instance", instance_path)
        # The stage as a new instance ships it, so the eval checks the shipped lens.
        write_stage(
            instance_path,
            _STAGE,
            description="Consolidate the episodes no consolidation has covered into cited facts.",
            run="consolidate",
            lens=DEFAULT_CONSOLIDATION_LENS,
        )
        await run_stage(instance_path, _STAGE, today=case.today, model_factory=model_factory)
        return live_facts(instance_path)


def grade(case: DreamingCase, before: Sequence[Fact], after: Sequence[Fact]) -> tuple[str, ...]:
    """What the live facts *after* consolidation miss of *case*, from the fixture's *before*."""
    live = {fact.node for fact in after}
    fixture = {fact.node for fact in before}
    written = [fact for fact in after if fact.node not in fixture]
    found: list[str] = []
    for replacement in case.replacements:
        found.extend(f'"{node}" is still live.' for node in replacement.supersedes if node in live)
        fact = next(
            (fact for fact in written if set(replacement.supersedes) <= set(fact.supersedes)),
            None,
        )
        if fact is None:
            found.append(f"No written fact supersedes all of {list(replacement.supersedes)}.")
            continue
        found.extend(
            f'"{fact.description}" does not cite "{source}".'
            for source in replacement.sources
            if source not in fact.sources
        )
        if len(fact.body) > MAX_BODY:
            found.append(f'"{fact.description}" has a {len(fact.body)}-character body.')
        text = f"{fact.description}\n{fact.body}".casefold()
        found.extend(
            f'"{fact.description}" does not mention "{mention}".'
            for mention in replacement.mentions
            if mention.casefold() not in text
        )
    return tuple(found)
