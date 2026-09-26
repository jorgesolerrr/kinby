import json

import pytest

from kinby.contracts import (
    RESULT_MODELS,
    CallFrame,
    CancelFrame,
    ErrorCode,
    ErrorEnvelope,
    ErrorFrame,
    FrameId,
    FrameType,
    SubscribeFrame,
    methods,
    parse_client_frame,
)


@pytest.mark.parametrize(
    ("message", "frame"),
    [
        (
            '{"type": "call", "id": "1", "method": "thread.list", "params": {}}',
            CallFrame(id=FrameId("1"), method="thread.list"),
        ),
        (
            '{"type": "subscribe", "id": "2", "method": "thread.subscribe"}',
            SubscribeFrame(id=FrameId("2"), method="thread.subscribe"),
        ),
        ('{"type": "cancel", "id": "3"}', CancelFrame(id=FrameId("3"))),
    ],
)
def test_a_client_frame_is_read_by_its_type(message: str, frame: CallFrame) -> None:
    assert parse_client_frame(message) == frame


@pytest.mark.parametrize(
    "message",
    [
        "not json",
        '{"id": "1", "method": "thread.list"}',
        '{"type": "result", "id": "1", "result": {}}',
        '{"type": "call", "id": "1", "method": "thread.list", "extra": true}',
    ],
)
def test_an_unreadable_message_names_the_argument_as_invalid(message: str) -> None:
    parsed = parse_client_frame(message)

    assert isinstance(parsed, ErrorEnvelope)
    assert parsed.code is ErrorCode.INVALID_ARGUMENT
    assert parsed.retryable is False


def test_an_error_frame_without_an_id_answers_a_frame_that_could_not_be_read() -> None:
    frame = ErrorFrame(
        error=ErrorEnvelope(
            code=ErrorCode.INVALID_ARGUMENT,
            message="The frame could not be read.",
            retryable=False,
        )
    )

    assert json.loads(frame.model_dump_json()) == {
        "type": FrameType.ERROR.value,
        "id": None,
        "error": {
            "code": ErrorCode.INVALID_ARGUMENT.value,
            "message": "The frame could not be read.",
            "retryable": False,
            "fields": {},
        },
    }


def test_every_declared_method_says_what_its_answer_arrives_as() -> None:
    declared = [
        value
        for value in vars(methods).values()
        if isinstance(value, methods.Method | methods.Subscription)
    ]

    assert {value.name for value in declared} == set(RESULT_MODELS)
