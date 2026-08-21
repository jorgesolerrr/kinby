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


def _add_usage(
    existing: dict[str, int] | None, new: dict[str, int] | None
) -> dict[str, int]:
    left = existing or {}
    right = new or {}
    keys = ("input_tokens", "output_tokens", "total_tokens", "calls")
    return {k: int(left.get(k) or 0) + int(right.get(k) or 0) for k in keys}


class KinbyState(MessagesState):
    files_read: Annotated[list[str], _extend_unique]
    token_usage: Annotated[dict[str, int], _add_usage]
    last_token_usage: dict[str, int]
    summary: str
