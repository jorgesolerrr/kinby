"""Saved outputs: the whole output of a tool call too long to give the model, kept as a file in
a folder for its turn under the state directory until the turn closes (ADR 0121)."""

from __future__ import annotations

import shutil
from collections.abc import Collection
from pathlib import Path
from uuid import UUID, uuid4

from kinby.contracts import ToolResult
from kinby.instance.layout import SAVED_OUTPUTS_DIR
from kinby.plugins.defaults.shell import OUTPUT_CAP


def turn_outputs(state_dir: Path, turn_id: UUID) -> Path:
    """The folder that holds the saved outputs of the turn *turn_id*."""
    return state_dir / SAVED_OUTPUTS_DIR / str(turn_id)


def cut_to_cap(result: ToolResult, outputs: Path) -> ToolResult:
    """*result* as the model gets it: whole within the cap, or else its start and where its whole
    output is saved in *outputs*."""
    size = len(result.output)
    if size <= OUTPUT_CAP:
        return result
    outputs.mkdir(parents=True, exist_ok=True)
    saved = outputs / f"{uuid4()}.txt"
    saved.write_text(result.output, encoding="utf-8")
    note = (
        f"\n[Cut at {OUTPUT_CAP:,} of {size:,} characters. The whole output is saved at {saved} "
        "for the rest of this turn: read it in parts with grep, jq, head or tail.]"
    )
    return result.model_copy(update={"output": result.output[:OUTPUT_CAP] + note, "size": size})


def discard_outputs(state_dir: Path, turn_id: UUID) -> None:
    shutil.rmtree(turn_outputs(state_dir, turn_id), ignore_errors=True)


def sweep_outputs(state_dir: Path, keep: Collection[UUID]) -> None:
    """Remove the saved outputs of every turn but those in *keep*."""
    folder = state_dir / SAVED_OUTPUTS_DIR
    if not folder.is_dir():
        return
    kept = {str(turn_id) for turn_id in keep}
    for outputs in folder.iterdir():
        if outputs.name not in kept:
            shutil.rmtree(outputs, ignore_errors=True)
