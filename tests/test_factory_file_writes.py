import yaml

from kinby.factories.file_writes import with_step, with_step_off

FACTORY = """\
name: reading-brief
steps:
  - id: research
    kind: agent
    engine: claude
    prompt: prompts/research.md  # Reads the sources.
    results: {brief: str}
  - id: draft
    kind: agent
    engine: claude
    prompt: prompts/draft.md
    description: >-
      Drafts the reading brief
      from the research.
    results: { words: int, brief_sha: str }
    failed: { retry: 3 }
    outcomes:
      done: next
      # Too short goes back to research.
      short: { back: research, max: 2 }
    timeout: 20m
    # Review reads the draft.
  - id: review
    kind: approve
    summary: 'Publish the brief.'
done_requires: [brief]
"""


def declared(text: str, step_id: str) -> dict:
    return next(step for step in yaml.safe_load(text)["steps"] if step["id"] == step_id)


DRAFT = declared(FACTORY, "draft")


def test_a_step_saved_as_declared_leaves_the_file_as_it_was():
    assert with_step(FACTORY, DRAFT) == FACTORY


def test_saving_one_field_changes_only_its_line():
    assert with_step(FACTORY, DRAFT | {"timeout": "25m"}) == FACTORY.replace(
        "    timeout: 20m\n", "    timeout: 25m\n"
    )


def test_a_changed_flow_mapping_keeps_the_spacing_inside_its_braces():
    research = declared(FACTORY, "research")

    assert with_step(FACTORY, DRAFT | {"failed": {"retry": 4}}) == FACTORY.replace(
        "    failed: { retry: 3 }\n", "    failed: { retry: 4 }\n"
    )
    assert with_step(FACTORY, research | {"results": {"brief": "int"}}) == FACTORY.replace(
        "    results: {brief: str}\n", "    results: {brief: int}\n"
    )


def test_a_changed_key_in_a_block_mapping_changes_only_its_line():
    outcomes = {"done": "next", "short": {"back": "research", "max": 3}}

    assert with_step(FACTORY, DRAFT | {"outcomes": outcomes}) == FACTORY.replace(
        "      short: { back: research, max: 2 }\n", "      short: { back: research, max: 3 }\n"
    )


def test_a_key_written_again_lines_up_with_the_keys_beside_it():
    indented = (
        FACTORY.replace("      done: next\n", "          done: next\n")
        .replace(
            "      short: { back: research, max: 2 }\n",
            "          short: { back: research, max: 2 }\n",
        )
        .replace("      # Too short", "          # Too short")
    )
    changed = {"done": "next", "short": {"back": "research", "max": 3}}
    added = {"done": "next", "long": "next", "short": {"back": "research", "max": 2}}

    assert with_step(indented, DRAFT | {"outcomes": changed}) == indented.replace(
        "max: 2", "max: 3"
    )
    assert with_step(indented, DRAFT | {"outcomes": added}) == indented.replace(
        "          short:", "          long: next\n          short:"
    )


def test_a_removed_key_drops_only_its_lines():
    without = {key: value for key, value in DRAFT.items() if key not in {"description", "timeout"}}

    assert with_step(FACTORY, without) == FACTORY.replace(
        "    description: >-\n      Drafts the reading brief\n      from the research.\n", ""
    ).replace("    timeout: 20m\n", "")


def test_an_added_key_adds_only_its_lines():
    assert with_step(FACTORY, DRAFT | {"resume": "research"}) == FACTORY.replace(
        "    timeout: 20m\n", "    resume: research\n    timeout: 20m\n"
    )


def test_turning_a_step_off_and_on_changes_only_the_off_line():
    off = with_step_off(FACTORY, "draft", off=True)

    assert off == FACTORY.replace("    timeout: 20m\n", "    off: true\n    timeout: 20m\n")
    assert with_step_off(off, "draft", off=False) == FACTORY
