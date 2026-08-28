# Types: before and after

Worked cases for the [Types](../CODING-STANDARD.md#types) rules, taken from kinby as it stood on 2026-08-28. Each case is a signature that hid something, and the signature that says it. The first, fourth, and sixth landed in the same PR as this doc; the others are open.

## Generic types leaking upward

`cli/client.py` and `core/dispatcher.py`. The dispatcher routes any command to any handler, so `BaseModel` is honest there. The CLI asked for one specific command and got the generic type back anyway.

Before:

```python
async def call(self, method: str, payload: Mapping[str, object]) -> BaseModel: ...


# every caller, cli/main.py
created = await client.call("thread.create", {"title": title})
if isinstance(created, ErrorEnvelope):
    ...
if not isinstance(created, ThreadCreateResult):
    print(UNEXPECTED_RESULT_ERROR, file=sys.stderr)
```

Three callers repeat the `isinstance` ladder, and each ends in an "unexpected result" branch that can only be reached by a bug. The CLI knew the answer type when it sent the command; the signature threw it away.

After:

```python
# contracts/methods.py
class Method[Command: ContractModel, Result: ContractModel]:
    name: str
    scope: Scope
    command: type[Command]
    result: type[Result]


THREAD_CREATE = Method(
    "thread.create", Scope.THREAD_OPERATE, ThreadCreateCommand, ThreadCreateResult
)


# cli/client.py
async def call[Command: ContractModel, Result: ContractModel](
    self, method: Method[Command, Result], command: Command
) -> Result | ErrorEnvelope: ...


created = await client.call(THREAD_CREATE, ThreadCreateCommand(title=title))
if isinstance(created, ErrorEnvelope):
    ...
print(created.id)  # ThreadCreateResult, no second check
```

A `Method` value ties a wire name to its command and result, so `register` takes typed handlers (`-> ThreadCreateResult`, not `-> BaseModel`) and `call` returns what the method promises. The wire underneath still moves `ContractModel`; the client checks the result against `method.result` once, and that is the only place an "unexpected result" can appear. The f-string method name in the CLI went with it: a `Method` constant is the closed set, so the separate `StrEnum` in the next case is no longer needed here.

## Primitive where a closed set exists

The same shape as `method: str` above, in miniature. Whenever a `str` parameter only ever receives a handful of literals, the set is the type.

Before:

```python
def register(self, method: str, ...) -> None: ...
await client.call(f"thread.{args.command}", payload)
```

After:

```python
class Method(StrEnum):
    THREAD_CREATE = "thread.create"
    THREAD_LIST = "thread.list"
    THREAD_SUBSCRIBE = "thread.subscribe"
    THREAD_TURN_START = "thread.turn.start"

def register(self, method: Method, ...) -> None: ...
```

The wire still carries the string; the enum parses it once at the edge. Inside the program a wrong method is a type error, and the set of methods is readable in one place.

## Untyped bag for a per-variant shape

`contracts/models.py`: `Event.payload: dict[str, JsonValue]`. Each `EventType` carries a different payload (`text` for a delta, `input_tokens`/`output_tokens` for completion, `code`/`message` for failure), but nothing links the type to its shape, so the REPL decodes by string key and hopes.

Before:

```python
class Event(ContractModel):
    type: EventType
    payload: dict[str, JsonValue]


# cli/repl.py
if event.type is EventType.MESSAGE_DELTA:
    print(event.payload["text"], end="")
```

After:

```python
class MessageDelta(ContractModel):
    text: str


class TurnCompleted(ContractModel):
    input_tokens: int
    output_tokens: int


class TurnFailed(ContractModel):
    code: ErrorCode
    message: str


Payload = MessageDelta | TurnCompleted | TurnFailed | ...


class Event(ContractModel):
    payload: Payload = Field(discriminator="kind")


match event.payload:
    case MessageDelta(text=text):
        print(text, end="")
    case TurnFailed(message=message):
        ...
```

A union of small models is a sum type. `match` on it is exhaustive, the type checker knows which fields exist in each arm, and a new event kind is a new class, not a new string key to remember.

## Optionals encoding a mode

`core/dispatcher.py`, `build_dispatcher`. Three `| None` parameters together encode "with a turn runner or without", and the body raises when the caller picks a combination that has no meaning.

Before:

```python
def build_dispatcher(
    state_dir: Path,
    *,
    event_log: EventLog | None = None,
    model: str | None = None,
    runner: TurnRunner | None = None,
) -> Dispatcher:
    ...
    if runner is not None and model is None:
        raise ValueError("model is required when a turn runner is configured")
```

After:

```python
@dataclass(frozen=True)
class TurnConfig:
    model: str
    runner: TurnRunner


def build_dispatcher(
    state_dir: Path,
    *,
    event_log: EventLog | None = None,
    turns: TurnConfig | None = None,
) -> Dispatcher: ...
```

The one optional left means exactly one thing: turns are on or off. "Runner without a model" cannot be written, so the `ValueError` goes away. `turn_config(model)` builds the default pair for the CLI.

## `Any` past the boundary

`instance/manifest.py`. Eight helpers take `Mapping[str, Any]`. The file is TOML, so `Any` is honest at the point of reading, but it travels through every helper instead of stopping at the first.

Before:

```python
def _required_string(values: Mapping[str, Any], key: str, prefix: str = "") -> str: ...
def _optional_bool(values: Mapping[str, Any], key: str, prefix: str) -> bool: ...
def _parse_conventions(workspace_path: Path, values: Mapping[str, Any]) -> Conventions: ...
```

After:

```python
class RawManifest(BaseModel):
    """Shape of manifest.toml, validated once at load."""

    model_config = ConfigDict(extra="forbid")
    id: str
    model: str = PLACEHOLDER_MODEL
    conventions: RawConventions = RawConventions()


def load_manifest(path: Path) -> Manifest:
    raw = RawManifest.model_validate(tomllib.loads(path.read_text()))
    return _to_manifest(path.parent, raw)
```

`Any` now appears once, inside `model_validate`. Unknown keys, missing strings, and wrong types are rejected by one declaration instead of eight hand-written helpers, and everything after `load_manifest` reads typed fields.

## `None`: product state or sentinel

Two `str | None` fields that look alike and are not.

Honest, keep it:

```python
class ThreadSummary(ContractModel):
    title: str | None  # a thread can have no title; the CLI prints "(untitled)"
```

Sentinel, replace it:

```python
class LangGraphRunner:
    def __init__(self, model: str) -> None:
        self._model: ChatModel | None = None      # "not built yet"

    async def run(self, ...) -> TurnOutcome:
        if self._model is None:
            self._model = self._factory(self._model_name)
```

`None` here means "lazy init pending", a fact about the object's lifecycle, and every method re-checks it. Build the model in `__init__` and the field becomes `self._model: ChatModel`. Side effect worth knowing: a provider that fails to initialise now fails when `kinby run` starts, not on the first message.

Same test for parameters: `init_instance(directory, model: str | None = None)` uses `None` as a stand-in for `PLACEHOLDER_MODEL`. The default can be the placeholder itself.
