"""Carry one contract call, or one subscription item, as a single JSON text message."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal, NewType

from pydantic import Field, JsonValue, TypeAdapter, ValidationError

from kinby.contracts.models import ContractModel, ErrorCode, ErrorEnvelope

FrameId = NewType("FrameId", str)


class FrameType(StrEnum):
    CALL = "call"
    SUBSCRIBE = "subscribe"
    CANCEL = "cancel"
    RESULT = "result"
    ERROR = "error"
    SUBSCRIBED = "subscribed"
    ITEM = "item"
    END = "end"


class CallFrame(ContractModel):
    type: Literal[FrameType.CALL] = FrameType.CALL
    id: FrameId
    method: str
    params: dict[str, JsonValue] = Field(default_factory=dict)


class SubscribeFrame(ContractModel):
    type: Literal[FrameType.SUBSCRIBE] = FrameType.SUBSCRIBE
    id: FrameId
    method: str
    params: dict[str, JsonValue] = Field(default_factory=dict)


class CancelFrame(ContractModel):
    type: Literal[FrameType.CANCEL] = FrameType.CANCEL
    id: FrameId


class ResultFrame(ContractModel):
    type: Literal[FrameType.RESULT] = FrameType.RESULT
    id: FrameId
    result: dict[str, JsonValue]


class ErrorFrame(ContractModel):
    """A frame the server could not read carries no id."""

    type: Literal[FrameType.ERROR] = FrameType.ERROR
    id: FrameId | None = None
    error: ErrorEnvelope


class SubscribedFrame(ContractModel):
    type: Literal[FrameType.SUBSCRIBED] = FrameType.SUBSCRIBED
    id: FrameId
    head_sequence: Annotated[int, Field(ge=0)]


class ItemFrame(ContractModel):
    type: Literal[FrameType.ITEM] = FrameType.ITEM
    id: FrameId
    item: dict[str, JsonValue]


class EndFrame(ContractModel):
    type: Literal[FrameType.END] = FrameType.END
    id: FrameId


ClientFrame = Annotated[CallFrame | SubscribeFrame | CancelFrame, Field(discriminator="type")]
ServerFrame = Annotated[
    ResultFrame | ErrorFrame | SubscribedFrame | ItemFrame | EndFrame,
    Field(discriminator="type"),
]
Frame = ClientFrame | ServerFrame

_CLIENT_FRAMES: TypeAdapter[ClientFrame] = TypeAdapter(ClientFrame)
_SERVER_FRAMES: TypeAdapter[ServerFrame] = TypeAdapter(ServerFrame)


def parse_client_frame(message: str) -> ClientFrame | ErrorEnvelope:
    """Turn one received message into a frame, or the error the sender gets back."""
    try:
        return _CLIENT_FRAMES.validate_json(message)
    except ValidationError as exc:
        return _unreadable(exc)


def parse_server_frame(message: str) -> ServerFrame | ErrorEnvelope:
    """Turn one received message into a frame, or the error the client reports instead."""
    try:
        return _SERVER_FRAMES.validate_json(message)
    except ValidationError as exc:
        return _unreadable(exc)


def _unreadable(exc: ValidationError) -> ErrorEnvelope:
    return ErrorEnvelope(
        code=ErrorCode.INVALID_ARGUMENT,
        message=f"The frame could not be read: {exc}",
        retryable=False,
    )
