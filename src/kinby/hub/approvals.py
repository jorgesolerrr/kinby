"""The approvals the hub relays between the user and the home instance (ADR 0096)."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from pydantic import ValidationError

from kinby.contracts import (
    THREAD_APPROVAL_RESPOND,
    ApprovalDecision,
    ApprovalRequested,
    CallFrame,
    Event,
    ItemFrame,
    ThreadApprovalRespondCommand,
    is_turn_closing,
    parse_client_frame,
    parse_server_frame,
)
from kinby.core.errors import PermissionDenied


@dataclass
class _Witnessed:
    turn_id: UUID
    request: ApprovalRequested
    approved: bool = False


class HomeApprovals:
    """What the hub saw on the home instance's relay: each approval it raised, and whether the
    user approved it. One lasts until it is used or its turn ends."""

    def __init__(self) -> None:
        self._witnessed: dict[UUID, _Witnessed] = {}
        #: A client that subscribes again sees each request again, and a used one stays used.
        self._used: set[UUID] = set()

    def instance_sent(self, message: str) -> None:
        match parse_server_frame(message):
            case ItemFrame(item=item):
                try:
                    event = Event.model_validate(item)
                except ValidationError:
                    return
            case _:
                return
        if isinstance(event.payload, ApprovalRequested):
            if event.payload.approval_id in self._used:
                return
            self._witnessed[event.payload.approval_id] = _Witnessed(event.turn_id, event.payload)
        elif is_turn_closing(event.payload):
            self._witnessed = {
                approval_id: witnessed
                for approval_id, witnessed in self._witnessed.items()
                if witnessed.turn_id != event.turn_id
            }

    def client_sent(self, message: str) -> None:
        match parse_client_frame(message):
            case CallFrame(method=THREAD_APPROVAL_RESPOND.name, params=params):
                try:
                    answer = ThreadApprovalRespondCommand.model_validate(params)
                except ValidationError:
                    return
            case _:
                return
        witnessed = self._witnessed.get(answer.approval_id)
        if witnessed is not None and answer.decision is ApprovalDecision.APPROVE:
            witnessed.approved = True

    def approved(self, approval_id: UUID) -> ApprovalRequested:
        """The call the user approved under this id, not yet used."""
        witnessed = self._witnessed.get(approval_id)
        if witnessed is None or not witnessed.approved:
            raise PermissionDenied(f'The hub saw no approval "{approval_id}" it can use.')
        return witnessed.request

    def use(self, approval_id: UUID) -> None:
        del self._witnessed[approval_id]
        self._used.add(approval_id)
