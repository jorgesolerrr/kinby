"""`kinby hub read`, `methods`, `help` and `wait`: the home instance reads its hub from bash
(ADR 0120).

They reach the hub through the route, scopes and control token the hub tools read from the
environment, so the token never appears in a command.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from enum import Enum
from types import NoneType, UnionType
from typing import Annotated, Literal, get_args, get_origin
from uuid import UUID

from pydantic import BaseModel, JsonValue  # noqa: TID251 - a hub write's tool schema
from pydantic.fields import FieldInfo
from pydantic_core import to_jsonable_python

from kinby.cli.client import format_error
from kinby.contracts import (
    HUB_READS,
    OPERATION_GET,
    RELAYED_READS,
    OperationGetCommand,
    OperationGetResult,
    OperationState,
)
from kinby.contracts.methods import METHODS
from kinby.plugins.hub import HUB_WRITES, HubRefused, holds_hub_home, read_hub
from kinby.plugins.tools import Tool

#: How often wait reads the operation.
POLL_SECONDS = 1.0
#: How long wait waits unless told: under the 120 seconds bash gives a command by default.
WAIT_SECONDS = 100.0
#: The widest a table cell prints.
CELL_WIDTH = 60


def _arguments(write: Tool) -> dict[str, FieldInfo]:
    """The arguments a hub write takes, as the model sees them."""
    schema = write.runnable.tool_call_schema
    if not (isinstance(schema, type) and issubclass(schema, BaseModel)):
        raise TypeError(f'The hub write "{write.name}" has no argument model.')
    return schema.model_fields


_READS = HUB_READS | RELAYED_READS
_PARAMS = {method.name: method.command.model_fields for method in METHODS if method.name in _READS}
_WRITES = {write.name: _arguments(write) for write in HUB_WRITES}
_NOT_HOME = "kinby hub read and wait run only in the home instance, which its hub grants hub:home."
_LISTED = "kinby hub methods lists them."


def read_on_hub(argv: list[str]) -> int:
    """Run the arguments after ``kinby hub``: read, methods, help or wait."""
    command, *rest = argv
    match command:
        case "read":
            return _read(rest)
        case "methods":
            argparse.ArgumentParser(
                prog="kinby hub methods",
                description="List every hub read, relayed read and hub write.",
            ).parse_args(rest)
            return _methods()
        case "help":
            parser = argparse.ArgumentParser(
                prog="kinby hub help",
                description="Print a read's params or a write's arguments.",
            )
            parser.add_argument("name", help="a read or a write, as kinby hub methods lists it")
            return _help(parser.parse_args(rest).name)
        case _:
            return _wait(rest)


def _read(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="kinby hub read",
        description=(
            "Make one hub read and print its answer as compact JSON. A relayed read names its "
            f"instance as instance_id. {_LISTED}"
        ),
        add_help=False,
    )
    parser.add_argument("method", nargs="?", help="the read")
    parser.add_argument(
        "params",
        nargs="*",
        type=_param,
        metavar="name=value",
        help="one param: a value that parses as JSON is JSON, anything else a string",
    )
    parser.add_argument(
        "--params",
        dest="json_params",
        type=_json_object,
        default={},
        metavar="JSON",
        help="params as one JSON object, which name=value adds to",
    )
    parser.add_argument("--table", action="store_true", help="print a table for a person")
    parser.add_argument("-h", "--help", action="store_true", help="print the read's params")
    args = parser.parse_intermixed_args(argv)
    if args.help:
        if args.method is None:
            parser.print_help()
            return 0
        return _help(args.method)
    if args.method not in _READS:
        print(f'"{args.method}" is not a hub read. {_LISTED}', file=sys.stderr)
        return 1
    if not holds_hub_home():
        print(_NOT_HOME, file=sys.stderr)
        return 1
    try:
        answer = asyncio.run(read_hub(args.method, {**args.json_params, **dict(args.params)}))
    except (HubRefused, ConnectionError) as exc:
        print(format_error(exc.error) if isinstance(exc, HubRefused) else exc, file=sys.stderr)
        return 1
    print(_table(answer) if args.table else _compact(answer))
    return 0


def _param(text: str) -> tuple[str, JsonValue]:
    name, separator, value = text.partition("=")
    if not name or not separator:
        raise argparse.ArgumentTypeError(f'"{text}" is not name=value.')
    try:
        return name, json.loads(value)
    except json.JSONDecodeError:
        return name, value


def _json_object(text: str) -> dict[str, JsonValue]:
    try:
        params = json.loads(text)
    except json.JSONDecodeError as exc:
        raise argparse.ArgumentTypeError(f"--params is not JSON: {exc}") from exc
    if not isinstance(params, dict):
        raise argparse.ArgumentTypeError("--params is not a JSON object.")
    return params


def _compact(answer: JsonValue) -> str:
    return json.dumps(answer, ensure_ascii=False, separators=(",", ":"))


def _table(answer: dict[str, JsonValue]) -> str:
    """The rows of the answer's one list, or the answer as one row."""
    lists = [value for value in answer.values() if isinstance(value, list)]
    rows = [
        row if isinstance(row, dict) else {"value": row}
        for row in (lists[0] if len(lists) == 1 else [answer])
    ]
    columns = list(dict.fromkeys(name for row in rows for name in row))
    lines = [columns, *([_cell(row.get(column)) for column in columns] for row in rows)]
    widths = [max(len(line[index]) for line in lines) for index in range(len(columns))]
    return "\n".join(
        "  ".join(cell.ljust(width) for cell, width in zip(line, widths, strict=True)).rstrip()
        for line in lines
    )


def _cell(value: JsonValue) -> str:
    text = value if isinstance(value, str) else _compact(value)
    return text if len(text) <= CELL_WIDTH else f"{text[: CELL_WIDTH - 1]}…"


def _methods() -> int:
    listed = [
        *((name, "hub read") for name in sorted(HUB_READS)),
        *((name, "relayed read") for name in sorted(RELAYED_READS)),
        *((name, "hub write") for name in _WRITES),
    ]
    width = max(len(name) for name, _ in listed)
    for name, kind in listed:
        print(f"{name:<{width}}  {kind}")
    return 0


def _help(name: str) -> int:
    if name in _WRITES:
        print(f"{name}: a hub write. Arguments:{_fields(_WRITES[name])}")
    elif name in RELAYED_READS:
        instance = "\n  instance_id: UUID, required"
        print(f"{name}: a relayed read. Params:{instance}{_fields(_PARAMS[name])}")
    elif name in HUB_READS:
        print(f"{name}: a hub read. Params:{_fields(_PARAMS[name]) or ' none'}")
    else:
        print(f'"{name}" is not a hub read or a hub write. {_LISTED}', file=sys.stderr)
        return 1
    return 0


def _fields(fields: dict[str, FieldInfo]) -> str:
    return "".join(
        f"\n  {name}: {_type(field.annotation)}{_default(field)}" for name, field in fields.items()
    )


def _default(field: FieldInfo) -> str:
    if field.is_required():
        return ", required"
    return f" = {_compact(to_jsonable_python(field.get_default(call_default_factory=True)))}"


def _type(annotation: object) -> str:
    """*annotation* as a reader writes it: domain names kept, constraints and modules dropped."""
    origin, arguments = get_origin(annotation), get_args(annotation)
    if origin is Annotated:
        return _type(arguments[0])
    if origin is UnionType:
        return " | ".join(_type(each) for each in arguments)
    if origin is Literal:
        return " | ".join(_compact(each) for each in arguments)
    if origin is not None:
        return f"{_type(origin)}[{', '.join(_type(each) for each in arguments)}]"
    if annotation is NoneType:
        return "null"
    if isinstance(annotation, type) and issubclass(annotation, Enum):
        return " | ".join(str(member.value) for member in annotation)
    return getattr(annotation, "__name__", str(annotation))


def _wait(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="kinby hub wait",
        description=(
            "Read an operation until it ends, then print it as compact JSON. Only a succeeded "
            "operation exits zero."
        ),
    )
    parser.add_argument("operation_id", type=UUID)
    parser.add_argument(
        "--timeout",
        type=float,
        default=WAIT_SECONDS,
        metavar="SECONDS",
        help=f"how long to wait (default: {WAIT_SECONDS:g})",
    )
    args = parser.parse_args(argv)
    if not holds_hub_home():
        print(_NOT_HOME, file=sys.stderr)
        return 1
    try:
        operation = asyncio.run(_ended(args.operation_id, args.timeout))
    except TimeoutError:
        print(
            f"The operation had not ended after {args.timeout:g} seconds. "
            "Run kinby hub wait again to keep waiting.",
            file=sys.stderr,
        )
        return 1
    except (HubRefused, ConnectionError) as exc:
        print(format_error(exc.error) if isinstance(exc, HubRefused) else exc, file=sys.stderr)
        return 1
    print(operation.model_dump_json())
    return 0 if operation.state is OperationState.SUCCEEDED else 1


async def _ended(operation_id: UUID, seconds: float) -> OperationGetResult:
    command = OperationGetCommand(operation_id=operation_id).model_dump(mode="json")
    async with asyncio.timeout(seconds):
        while True:
            operation = OperationGetResult.model_validate(
                await read_hub(OPERATION_GET.name, command)
            )
            if operation.state in {OperationState.SUCCEEDED, OperationState.FAILED}:
                return operation
            await asyncio.sleep(POLL_SECONDS)
