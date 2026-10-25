"""The view tools: a turn keeps the instance's views (ADR 0108)."""

from __future__ import annotations

import asyncio
import json

from pydantic import JsonValue

from kinby.contracts import ViewChange, ViewChanged, ViewId, ViewSpec
from kinby.instance.views import (
    ViewPatchOperation,
    list_views,
    patch_view,
    read_view,
    write_view,
)
from kinby.plugins.tools import Tool, ToolContext, tool


def view_tools() -> tuple[Tool, ...]:
    return (view_list, view_read, view_write, view_patch)


@tool(write=False)
def view_list(context: ToolContext) -> str:
    """List the instance's views: each one's id, title and when it last changed."""
    return json.dumps(
        [summary.model_dump(mode="json") for summary in list_views(context.instance)],
        ensure_ascii=False,
    )


@tool(write=False)
def view_read(id: ViewId, context: ToolContext) -> str:
    """Read one view whole: its title, spec and data."""
    return read_view(context.instance, id).model_dump_json()


@tool(write=True, view=True)
async def view_write(
    id: ViewId, title: str, spec: ViewSpec, data: dict[str, JsonValue], context: ToolContext
) -> str:
    """Create or replace a view: a page of UI the user opens in the web app, which you keep
    current. Write one on your own when it shows something more precisely than a message, such
    as a board a routine updates every morning.

    id is a short slug. The spec lists components from the view catalogue, each with a unique id;
    the one with id "root" is rendered, and a Card names its children, and a Table column its
    cell, by id. A prop is a literal or {"path": "<JSON Pointer>"} into data. Card children may
    instead be {"path": "/items", "template": "<component id>"}, which renders the template once
    per item of the array, with the template's paths read in the item. Table rows, Kanban columns,
    Chart points and Checklist items repeat over an array the same way; each component says which
    of its props are read in the item. A view is capped at 1 MB.
    """
    change = await asyncio.to_thread(write_view, context.instance, id, title, spec, data)
    await context.report_view_changed(ViewChanged(view=id, title=title, change=change))
    return f"{change.value.capitalize()} view {id}."


@tool(write=True, view=True)
async def view_patch(id: ViewId, ops: list[ViewPatchOperation], context: ToolContext) -> str:
    """Change part of a view with RFC 6902 JSON Patch operations, such as adding one item to a
    list, instead of writing the whole view again. Call view_read first.

    The operations apply in order to {"title", "spec", "data"} as view_read returns them: the
    path "/data/postings/-" appends to the postings, and "/spec/components/2/columns/-" adds a
    column to the third component. The result must still be a valid view under 1 MB, or nothing
    is written.
    """
    patched = await asyncio.to_thread(patch_view, context.instance, id, ops)
    await context.report_view_changed(
        ViewChanged(view=id, title=patched.title, change=ViewChange.CHANGED)
    )
    return f"Changed view {id}."
