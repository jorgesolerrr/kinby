"""The view tools: a turn keeps the instance's views (ADR 0108, ADR 0131)."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping

from pydantic import JsonValue, TypeAdapter

from kinby.contracts import PageWrite, ViewChange, ViewChanged, ViewId
from kinby.instance import Instance
from kinby.instance.views import (
    ViewPatchOperation,
    delete_view,
    edit_view,
    edited_page,
    list_views,
    patch_view,
    read_view,
    write_view,
    written_page,
)
from kinby.plugins.tools import Tool, ToolContext, tool

_OPERATIONS = TypeAdapter(list[ViewPatchOperation])


def view_tools() -> tuple[Tool, ...]:
    return (view_list, view_read, view_write, view_patch, view_edit, view_delete)


@tool(write=False)
def view_list(context: ToolContext) -> str:
    """List the instance's views: each one's id, title and when it last changed."""
    return json.dumps(
        [summary.model_dump(mode="json") for summary in list_views(context.instance)],
        ensure_ascii=False,
    )


@tool(write=False)
def view_read(id: ViewId, context: ToolContext) -> str:
    """Read one view whole: its title, page and data."""
    return read_view(context.instance, id).model_dump_json()


@tool(write=True, page=True)
async def view_write(
    id: ViewId,
    title: str,
    page: str,
    data: dict[str, JsonValue],
    context: ToolContext,
) -> str:
    """Create or replace a view: a page of HTML the user opens in the web app, and the data it
    shows, which you keep current. Write one on your own when it shows something more precisely
    than a message, such as a board a routine updates every morning. Read the write-view skill
    before writing a page. id is a short slug, and a view is capped at 1 MB.
    """
    change = await asyncio.to_thread(write_view, context.instance, id, title, page, data)
    await context.report_view_changed(ViewChanged(view=id, title=title, change=change))
    return f"{change.value.capitalize()} view {id}."


@tool(write=True, view=True)
async def view_patch(id: ViewId, ops: list[dict[str, JsonValue]], context: ToolContext) -> str:
    """Change a view's title or data with RFC 6902 JSON Patch operations, as the write-view skill
    shows. Change the page with view_edit."""
    operations = _OPERATIONS.validate_python(ops)
    patched = await asyncio.to_thread(patch_view, context.instance, id, operations)
    await context.report_view_changed(
        ViewChanged(view=id, title=patched.title, change=ViewChange.CHANGED)
    )
    return f"Changed view {id}."


@tool(write=True, page=True)
async def view_edit(id: ViewId, old: str, new: str, context: ToolContext) -> str:
    """Change a view's page: replace old, an exact string that appears once in it, with new."""
    edited = await asyncio.to_thread(edit_view, context.instance, id, old, new)
    await context.report_view_changed(
        ViewChanged(view=id, title=edited.title, change=ViewChange.CHANGED)
    )
    return f"Changed the page of view {id}."


@tool(write=True)
async def view_delete(id: ViewId, context: ToolContext) -> str:
    """Delete a view for good. The user may rely on it, so delete one only when it no longer
    serves."""
    deleted = await asyncio.to_thread(delete_view, context.instance, id)
    await context.report_view_changed(
        ViewChanged(view=id, title=deleted.title, change=ViewChange.DELETED)
    )
    return f"Deleted view {id}."


def page_write(
    name: str, arguments: Mapping[str, JsonValue], instance: Instance
) -> PageWrite | None:
    """What a call of view_write or view_edit would draw, for its approval to show. None for a
    call of another tool, and for one the tool would refuse."""
    try:
        match name, arguments:
            case (
                view_write.name,
                {"id": str(id), "title": str(title), "page": str(page), "data": dict(data)},
            ):
                return written_page(instance, id, title, page, data)
            case view_edit.name, {"id": str(id), "old": str(old), "new": str(new)}:
                return edited_page(instance, id, old, new)
    except LookupError, ValueError:
        pass
    return None
