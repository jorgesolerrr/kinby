"""Answer a client's reads of the instance's views."""

from __future__ import annotations

from kinby.contracts import View, ViewGetCommand, ViewListCommand, ViewListResult
from kinby.core.errors import ViewNotFound
from kinby.instance import Instance
from kinby.instance.views import list_views, read_view


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
