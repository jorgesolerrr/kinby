"""Keep an instance's views, each one file in views/ that only this module writes (ADR 0108)."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from tempfile import NamedTemporaryFile

from pydantic import JsonValue, TypeAdapter, ValidationError

from kinby.contracts import View, ViewChange, ViewId, ViewSpec, ViewSummary
from kinby.instance.dataclasses import Instance
from kinby.instance.layout import VIEWS_DIR

#: The most a view takes up serialized, so a whole snapshot stays cheap to send.
VIEW_CAP_BYTES = 1_000_000
_VIEW_ID = TypeAdapter(ViewId)


def list_views(instance: Instance) -> list[ViewSummary]:
    """Every view of the instance, by id."""
    views = (
        View.model_validate_json(path.read_bytes())
        for path in sorted((instance.path / VIEWS_DIR).glob("*.json"))
    )
    return [ViewSummary(id=view.id, title=view.title, updated_at=view.updated_at) for view in views]


def read_view(instance: Instance, view_id: str) -> View:
    path = _view_path(instance, view_id)
    try:
        return View.model_validate_json(path.read_bytes())
    except FileNotFoundError:
        raise LookupError(f'View "{view_id}" was not found.') from None


def write_view(
    instance: Instance, view_id: str, title: str, spec: ViewSpec, data: dict[str, JsonValue]
) -> ViewChange:
    """Create or replace a view, whole. A reader sees the old view or the new one, never half."""
    path = _view_path(instance, view_id)
    view = View(id=view_id, title=title, spec=spec, data=data, updated_at=datetime.now(UTC))
    serialized = view.model_dump_json().encode()
    if len(serialized) > VIEW_CAP_BYTES:
        megabytes = len(serialized) / VIEW_CAP_BYTES
        raise ValueError(
            f"A view is capped at 1 MB serialized, and this one is {megabytes:.1f} MB. "
            "Keep less data in it."
        )
    change = ViewChange.CHANGED if path.exists() else ViewChange.CREATED
    path.parent.mkdir(exist_ok=True)
    with NamedTemporaryFile(dir=path.parent, prefix=".", suffix=".tmp", delete=False) as staged:
        staged.write(serialized)
    os.replace(staged.name, path)
    return change


def _view_path(instance: Instance, view_id: str) -> Path:
    try:
        _VIEW_ID.validate_python(view_id)
    except ValidationError:
        raise ValueError(
            "A view id must contain only letters, digits, hyphens, and underscores, "
            "and start with a letter or digit."
        ) from None
    return instance.path / VIEWS_DIR / f"{view_id}.json"
