"""Adapt Inspect's active model to kinby's model protocols."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import suppress
from dataclasses import dataclass, replace

from inspect_ai.model import (
    ChatMessage,
    ChatMessageAssistant,
    ChatMessageSystem,
    ChatMessageTool,
    ChatMessageUser,
    Model,
    ModelOutput,
    StreamEvent,
    StreamTextEvent,
)
from inspect_ai.tool import ToolCall as InspectToolCall
from inspect_ai.tool import ToolCallError, ToolFunction, ToolInfo, ToolParams
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
    UsageMetadata,
)
from langchain_core.messages import ToolCall as LangChainToolCall
from langchain_core.tools import StructuredTool
from pydantic import ValidationError

from kinby.memory.recap import RecapDraft


@dataclass(frozen=True)
class InspectModelAdapter:
    """Present one Inspect model as both kinby model interfaces."""

    model: Model
    tools: tuple[ToolInfo, ...] = ()

    def bind_tools(self, tools: Sequence[StructuredTool]) -> InspectModelAdapter:
        return replace(self, tools=tuple(_tool_info(tool) for tool in tools))

    async def astream(self, messages: Sequence[BaseMessage]) -> AsyncIterator[AIMessageChunk]:
        stream: asyncio.Queue[StreamEvent | None] = asyncio.Queue()

        async def on_stream(event: StreamEvent) -> None:
            await stream.put(event)

        async def generate() -> ModelOutput:
            try:
                return await self.model.generate(
                    [_inspect_message(message) for message in messages],
                    tools=self.tools,
                    on_stream=on_stream,
                )
            finally:
                await stream.put(None)

        generation = asyncio.create_task(generate())
        streamed_text = False
        try:
            while (event := await stream.get()) is not None:
                if isinstance(event, StreamTextEvent):
                    streamed_text = True
                    yield AIMessageChunk(content=event.text)
            output = await generation
        finally:
            if not generation.done():
                generation.cancel()
                with suppress(asyncio.CancelledError):
                    await generation
        if output.empty:
            return
        chunk = _langchain_chunk(output)
        yield chunk.model_copy(update={"content": ""}) if streamed_text else chunk

    def with_structured_output(
        self,
        schema: type[RecapDraft],
        *,
        include_raw: bool,
    ) -> InspectStructuredRecap:
        return InspectStructuredRecap(self.model, schema, include_raw)


@dataclass(frozen=True)
class InspectStructuredRecap:
    model: Model
    schema: type[RecapDraft]
    include_raw: bool

    async def ainvoke(self, messages: Sequence[BaseMessage]) -> object:
        tool = ToolInfo(
            name=self.schema.__name__,
            description="Return the structured recap draft.",
            parameters=ToolParams.model_validate(self.schema.model_json_schema()),
        )
        output = await self.model.generate(
            [_inspect_message(message) for message in messages],
            tools=(tool,),
            tool_choice=ToolFunction(name=tool.name),
        )
        raw = AIMessage(
            content=output.completion,
            usage_metadata=_usage_metadata(output),
        )
        try:
            call = next(
                call for call in output.message.tool_calls or () if call.function == tool.name
            )
            parsed = self.schema.model_validate(call.arguments)
        except (StopIteration, ValidationError) as exc:
            if self.include_raw:
                return {"raw": raw, "parsed": None, "parsing_error": exc}
            raise
        if self.include_raw:
            return {"raw": raw, "parsed": parsed, "parsing_error": None}
        return parsed


type InspectModelFactory = Callable[[str], InspectModelAdapter]


def inspect_model_factory(model: Model) -> InspectModelFactory:
    """Route every kinby model name to the active Inspect model."""

    def build(model_name: str) -> InspectModelAdapter:
        del model_name
        return InspectModelAdapter(model)

    return build


def _tool_info(tool: StructuredTool) -> ToolInfo:
    return ToolInfo(
        name=tool.name,
        description=tool.description,
        parameters=ToolParams.model_validate(tool.get_input_jsonschema()),
    )


def _inspect_message(message: BaseMessage) -> ChatMessage:
    match message:
        case SystemMessage():
            return ChatMessageSystem(content=message.text)
        case HumanMessage():
            return ChatMessageUser(content=message.text)
        case ToolMessage():
            error = (
                ToolCallError(type="unknown", message=message.text)
                if message.status == "error"
                else None
            )
            return ChatMessageTool(
                content=message.text,
                tool_call_id=message.tool_call_id,
                function=message.name,
                error=error,
            )
        case AIMessage():
            return ChatMessageAssistant(
                content=message.text,
                tool_calls=[_inspect_tool_call(call) for call in message.tool_calls] or None,
            )
        case _:
            raise TypeError(f"Unsupported LangChain message: {type(message).__name__}")


def _inspect_tool_call(call: LangChainToolCall) -> InspectToolCall:
    call_id = call["id"]
    if call_id is None:
        raise ValueError("A LangChain tool call has no id.")
    return InspectToolCall(
        id=call_id,
        function=call["name"],
        arguments=call["args"],
    )


def _langchain_chunk(output: ModelOutput) -> AIMessageChunk:
    message = output.message
    return AIMessageChunk(
        content=message.text,
        tool_calls=[
            {
                "id": call.id,
                "name": call.function,
                "args": call.arguments,
                "type": "tool_call",
            }
            for call in message.tool_calls or ()
        ],
        usage_metadata=_usage_metadata(output),
    )


def _usage_metadata(output: ModelOutput) -> UsageMetadata | None:
    usage = output.usage
    if usage is None:
        return None
    return UsageMetadata(
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        total_tokens=usage.total_tokens,
    )
