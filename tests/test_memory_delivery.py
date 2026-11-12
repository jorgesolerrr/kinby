"""How the runtime brings memory to a turn without the agent choosing to search."""

import asyncio
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from langchain_core.messages import AIMessageChunk, HumanMessage, SystemMessage

from kinby.contracts import Event, MemoryRecalled, Payload, PermissionMode
from kinby.core.turn_runner import LangGraphRunner
from kinby.core.turns import PreparedTurnRequest, TurnContext
from kinby.instance import Instance, init_instance, load_instance
from kinby.memory import Fact, GraphStore, MemoryDelivery, NodeId, memory_index
from tests.test_memory_tools import ScriptedModel

#: About 1k tokens, at four characters a token.
_INDEX_CAP = 4_000


def _fact(day: date, description: str, body: str = "") -> Fact:
    return Fact(
        node=NodeId(f"{day.isoformat()}-{description.casefold().replace(' ', '-')}"),
        date=day,
        description=description,
        subjects=(),
        body=body or f"{description}.",
        thread=None,
    )


def _instance(tmp_path: Path, *facts: Fact) -> Instance:
    path = tmp_path / "instance"
    init_instance(path, model="openai:gpt-5")
    graph = GraphStore(path)
    for fact in facts:
        graph.remember(fact)
    return load_instance(path)


def _turn(
    instance: Instance, message: str, delivery: MemoryDelivery = MemoryDelivery.NONE
) -> tuple[list[Payload], SystemMessage, HumanMessage]:
    """Run one turn of *message* that answers at once. Return its events and the system prompt
    and the message the model was sent."""
    model = ScriptedModel([AIMessageChunk(content="Done.")])
    runner = LangGraphRunner(instance, model_factory=lambda _: model, memory_delivery=delivery)
    preparation = runner.prepare_for_turn()
    payloads: list[Payload] = []

    async def emit(payload: Payload) -> Event:
        payloads.append(payload)
        return Event(
            sequence=len(payloads),
            thread_id=uuid4(),
            turn_id=uuid4(),
            payload=payload,
            timestamp=datetime.now(UTC),
        )

    asyncio.run(
        runner.run(
            PreparedTurnRequest(
                thread_id=uuid4(),
                turn_id=uuid4(),
                message=message,
                model=preparation.model,
                permission_mode=PermissionMode.ASK,
                system_prompt=preparation.system_prompt,
            ),
            TurnContext(preparation.budgets, emit),
        )
    )
    system, *_, human = model.messages[0]
    assert isinstance(system, SystemMessage)
    assert isinstance(human, HumanMessage)
    return payloads, system, human


def _recalls(payloads: list[Payload]) -> list[MemoryRecalled]:
    return [payload for payload in payloads if isinstance(payload, MemoryRecalled)]


def test_the_index_stays_under_its_cap_at_500_facts_and_shows_the_newest(tmp_path: Path) -> None:
    first = date(2025, 1, 1)
    facts = [_fact(first + timedelta(days=day), f"Errand {day}") for day in range(500)]
    instance = _instance(tmp_path, *facts)

    index = memory_index(GraphStore(instance.path))

    assert index is not None
    assert len(index) <= _INDEX_CAP
    listed = [line for line in index.splitlines() if line.startswith("- ")]
    assert listed[:2] == [
        f"- {facts[-1].node}: {facts[-1].description}",
        f"- {facts[-2].node}: {facts[-2].description}",
    ]
    assert facts[0].node not in index


def test_the_index_leaves_out_forgotten_facts_and_is_none_without_facts(tmp_path: Path) -> None:
    kept = _fact(date(2026, 9, 1), "Deploys run from the hub")
    forgotten = _fact(date(2026, 9, 2), "Deploys run from a laptop")
    instance = _instance(tmp_path, kept, forgotten)
    graph = GraphStore(instance.path)
    graph.forget(forgotten.node, date(2026, 9, 3))

    index = memory_index(graph)

    assert index is not None
    assert kept.node in index
    assert forgotten.node not in index
    assert memory_index(GraphStore(tmp_path / "empty")) is None


def test_a_turn_with_the_index_reads_it_in_its_system_prompt_and_writes_no_recall(
    tmp_path: Path,
) -> None:
    fact = _fact(date(2026, 9, 1), "Release branches are cut from main")
    instance = _instance(tmp_path, fact)

    payloads, system, human = _turn(instance, "Cut the 2.5 release branch.", MemoryDelivery.INDEX)

    assert f"- {fact.node}: {fact.description}" in system.text
    assert "Open one before you act on it." in system.text
    assert human.text == "Cut the 2.5 release branch."
    assert _recalls(payloads) == []


def test_a_new_fact_changes_the_index_but_not_the_prompt_version(tmp_path: Path) -> None:
    instance = _instance(tmp_path, _fact(date(2026, 9, 1), "Release branches are cut from main"))
    runner = LangGraphRunner(instance, memory_delivery=MemoryDelivery.INDEX)
    before = runner.prepare_for_turn()

    GraphStore(instance.path).remember(_fact(date(2026, 9, 2), "Staging keys live in the vault"))
    after = runner.prepare_for_turn()

    assert "Staging keys live in the vault" in after.system_prompt
    assert after.prompt_version == before.prompt_version


def test_a_pre_search_turn_reads_the_top_three_facts_and_writes_one_recall_naming_them(
    tmp_path: Path,
) -> None:
    best = _fact(date(2026, 9, 1), "Release branches are cut from main", "Never from develop.")
    newer = [
        _fact(date(2026, 9, day), f"Release {day} shipped late", f"Release {day} slipped.")
        for day in (2, 3, 4)
    ]
    unrelated = _fact(date(2026, 9, 5), "Maya walks her dog")
    instance = _instance(tmp_path, best, *newer, unrelated)

    payloads, _, human = _turn(instance, "Cut the release branch", MemoryDelivery.PRE_SEARCH)

    added = (best, newer[2], newer[1])
    assert human.text.startswith("Cut the release branch\n\n")
    assert all(fact.body in human.text for fact in added)
    assert newer[0].body not in human.text
    assert unrelated.body not in human.text
    assert _recalls(payloads) == [MemoryRecalled(nodes=tuple(fact.node for fact in added))]


def test_a_pre_search_that_finds_no_fact_adds_nothing_and_writes_no_recall(
    tmp_path: Path,
) -> None:
    instance = _instance(tmp_path, _fact(date(2026, 9, 1), "Maya walks her dog"))

    payloads, _, human = _turn(instance, "Deploy staging", MemoryDelivery.PRE_SEARCH)

    assert human.text == "Deploy staging"
    assert _recalls(payloads) == []


def test_without_a_delivery_a_turn_gets_no_memory_it_did_not_ask_for(tmp_path: Path) -> None:
    fact = _fact(date(2026, 9, 1), "Release branches are cut from main")
    instance = _instance(tmp_path, fact)

    payloads, system, human = _turn(instance, "Cut the release branch")

    assert fact.node not in system.text
    assert human.text == "Cut the release branch"
    assert _recalls(payloads) == []
