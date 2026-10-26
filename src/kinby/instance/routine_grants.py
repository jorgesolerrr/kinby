"""Keep the routine grants of an instance: the tools the user always allows in a routine."""

from __future__ import annotations

from pathlib import Path
from threading import Lock

from pydantic import TypeAdapter

from kinby.contracts import RoutineGrant, RoutineName, ToolName

ROUTINE_GRANTS_NAME = "routine-grants.json"
_GRANTS = TypeAdapter(list[RoutineGrant])
#: A change reads the whole file and writes it back, so changes from any thread go one at a time.
_changing = Lock()


class RoutineGrants:
    """One grant per routine and tool, in the instance's state directory."""

    def __init__(self, state_dir: Path) -> None:
        self._path = state_dir / ROUTINE_GRANTS_NAME

    def of(self, routine: RoutineName) -> list[RoutineGrant]:
        return [grant for grant in self._read() if grant.routine == routine]

    def grant(self, grant: RoutineGrant) -> None:
        """Keep *grant*, in place of any earlier grant of its tool to its routine."""
        with _changing:
            self._write([*self._others(grant.routine, grant.tool), grant])

    def revoke(self, routine: RoutineName, tool: ToolName) -> None:
        with _changing:
            self._write(self._others(routine, tool))

    def clear(self, routine: RoutineName) -> None:
        """Revoke every grant of *routine*, as any edit to it does."""
        with _changing:
            grants = self._read()
            if any(each.routine == routine for each in grants):
                self._write([each for each in grants if each.routine != routine])

    def _others(self, routine: RoutineName, tool: ToolName) -> list[RoutineGrant]:
        return [each for each in self._read() if (each.routine, each.tool) != (routine, tool)]

    def _read(self) -> list[RoutineGrant]:
        try:
            return _GRANTS.validate_json(self._path.read_bytes())
        except FileNotFoundError:
            return []

    def _write(self, grants: list[RoutineGrant]) -> None:
        staging = self._path.with_name(f".{self._path.name}.staging")
        staging.write_bytes(_GRANTS.dump_json(grants, indent=2))
        staging.replace(self._path)
