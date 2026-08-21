from typing import Annotated

from langgraph.graph import MessagesState


def _extend_unique(existing: list[str] | None, new: list[str] | None) -> list[str]:
    left = existing or []
    right = new or []
    seen = set(left)
    out = list(left)
    for item in right:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


class KinbyState(MessagesState):
    files_read: Annotated[list[str], _extend_unique]
