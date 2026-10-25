"""Keep an instance's views, each one file in views/ that only this module writes (ADR 0108)."""

from __future__ import annotations

import asyncio
import os
import threading
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Annotated, Literal

import jsonpatch
from pydantic import Field, JsonValue, TypeAdapter, ValidationError

from kinby.contracts import (
    ContractModel,
    JsonPointer,
    View,
    ViewChange,
    ViewId,
    ViewSpec,
    ViewSummary,
)
from kinby.instance.dataclasses import Instance
from kinby.instance.layout import VIEWS_DIR

#: The most a view takes up serialized, so a whole snapshot stays cheap to send.
VIEW_CAP_BYTES = 1_000_000
_VIEW_ID = TypeAdapter(ViewId)

#: Who hears of each write, by the view's file, and hears None once it is deleted. Writes run in
#: worker threads, and a watcher only schedules the view on its event loop, so it is called under
#: the lock.
_watchers: dict[Path, set[Callable[[View | None], object]]] = {}
_watchers_lock = threading.Lock()


class ValueOperation(ContractModel):
    op: Literal["add", "replace", "test"]
    path: JsonPointer
    value: JsonValue


class RemoveOperation(ContractModel):
    op: Literal["remove"]
    path: JsonPointer


class FromOperation(ContractModel):
    op: Literal["move", "copy"]
    from_: JsonPointer = Field(alias="from")
    path: JsonPointer


#: One RFC 6902 JSON Patch operation.
type ViewPatchOperation = Annotated[
    ValueOperation | RemoveOperation | FromOperation, Field(discriminator="op")
]


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
    view = View(id=view_id, title=title, spec=spec, data=data, updated_at=datetime.now(UTC))
    return _store(instance, view)


def patch_view(instance: Instance, view_id: str, ops: Sequence[ViewPatchOperation]) -> View:
    """Apply *ops* in order to the view's title, spec and data, and write the result only if it
    is still a valid view."""
    document = read_view(instance, view_id).model_dump(
        mode="json", include={"title", "spec", "data"}
    )
    for number, op in enumerate(ops):
        try:
            operation = op.model_dump(mode="json", by_alias=True)
            document = jsonpatch.apply_patch(document, [operation], in_place=True)
        except jsonpatch.JsonPatchException, jsonpatch.JsonPointerException:
            # The library's message quotes the document, which may be up to a megabyte.
            raise ValueError(
                f'Operation {number} ({op.op} "{op.path}") does not apply to the view. '
                "Read it with view_read for its paths."
            ) from None
    patched = View.model_validate({**document, "id": view_id, "updated_at": datetime.now(UTC)})
    _store(instance, patched)
    return patched


def _store(instance: Instance, view: View) -> ViewChange:
    path = _view_path(instance, view.id)
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
    # Replaced and announced under one lock, so watchers hear of writes in the order they land.
    with _watchers_lock:
        os.replace(staged.name, path)
        for watcher in _watchers.get(path, ()):
            watcher(view)
    return change


def delete_view(instance: Instance, view_id: str) -> View:
    """Remove a view, and end every watch of it. Returns the view as it was."""
    path = _view_path(instance, view_id)
    with _watchers_lock:
        view = read_view(instance, view_id)
        path.unlink()
        for watcher in _watchers.pop(path, ()):
            watcher(None)
    return view


def watch_view(
    instance: Instance, view_id: str
) -> tuple[asyncio.Queue[View | None], Callable[[], None]]:
    """A queue that gets the view after each write from now on and None once it is deleted, and
    the call that stops it. Call it on the event loop that reads the queue."""
    loop = asyncio.get_running_loop()
    path = _view_path(instance, view_id)
    changes: asyncio.Queue[View | None] = asyncio.Queue()

    def watcher(view: View | None) -> None:
        loop.call_soon_threadsafe(changes.put_nowait, view)

    with _watchers_lock:
        _watchers.setdefault(path, set()).add(watcher)

    def stop() -> None:
        with _watchers_lock:
            watchers = _watchers.get(path, set())
            watchers.discard(watcher)
            if not watchers:
                _watchers.pop(path, None)

    return changes, stop


def _view_path(instance: Instance, view_id: str) -> Path:
    try:
        _VIEW_ID.validate_python(view_id)
    except ValidationError:
        raise ValueError(
            "A view id must contain only letters, digits, hyphens, and underscores, "
            "and start with a letter or digit."
        ) from None
    return instance.path / VIEWS_DIR / f"{view_id}.json"
