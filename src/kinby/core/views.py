"""Answer a client's reads of the instance's views."""

from __future__ import annotations

from collections.abc import AsyncGenerator

from kinby.contracts import (
    Stream,
    View,
    ViewDeleteCommand,
    ViewGetCommand,
    ViewListCommand,
    ViewListResult,
    ViewSubscribeCommand,
)
from kinby.core.errors import ViewNotFound
from kinby.instance import Instance
from kinby.instance.views import delete_view, list_views, read_view, watch_view


class InstanceViews:
    """The instance's views as clients read them."""

    def __init__(self, instance: Instance) -> None:
        self._instance = instance

    async def list(self, command: ViewListCommand) -> ViewListResult:
        return ViewListResult(views=list_views(self._instance))

    async def get(self, command: ViewGetCommand) -> View:
        try:
            return read_view(self._instance, command.id)
        except LookupError as exc:
            raise ViewNotFound(str(exc)) from exc

    async def subscribe(self, command: ViewSubscribeCommand) -> Stream[View]:
        """The view, then the view again after each write, until it is deleted. Nothing is
        replayed."""
        # Watched before the read, so a write between the two is never missed.
        changes, stop = watch_view(self._instance, command.id)
        try:
            current = await self.get(ViewGetCommand(id=command.id))
        except ViewNotFound:
            stop()
            raise

        async def items() -> AsyncGenerator[View]:
            yield current
            while (view := await changes.get()) is not None:
                yield view

        return Stream(0, items(), _close=stop)

    async def delete(self, command: ViewDeleteCommand) -> ViewListResult:
        """Delete a view for a client. No turn made it, so no thread records it."""
        try:
            delete_view(self._instance, command.id)
        except LookupError as exc:
            raise ViewNotFound(str(exc)) from exc
        return await self.list(ViewListCommand())
