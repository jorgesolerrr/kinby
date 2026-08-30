"""Run a model turn with LangGraph working state beneath the event stream."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import date
from typing import Annotated, Protocol, cast
from uuid import uuid4

from langchain.chat_models import init_chat_model
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    AnyMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.tools import StructuredTool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph, add_messages
from langgraph.runtime import Runtime
from pydantic import JsonValue, TypeAdapter

from kinby.contracts import (
    ApprovalRequested,
    MessageDelta,
    ToolCall,
    ToolResult,
)
from kinby.core.errors import ModelNoResponse
from kinby.core.prompt import assemble_system_prompt, render_system_prompt
from kinby.core.turns import Emit, ParkedTurn, TurnOutcome, TurnRequest, TurnResult
from kinby.instance import Instance, reload_manifest
from kinby.plugins.errors import exception_message
from kinby.plugins.registry import ToolRegistry, ToolSnapshot
from kinby.plugins.tools import ToolContext

ApprovalHook = Callable[[TurnRequest], Awaitable[str]]
_TOOL_ARGUMENTS = TypeAdapter(dict[str, JsonValue])


class ChatModel(Protocol):
    def astream(self, messages: Sequence[BaseMessage]) -> AsyncIterator[AIMessageChunk]: ...
    def bind_tools(self, tools: Sequence[StructuredTool]) -> ChatModel: ...


ModelFactory = Callable[[str], ChatModel]


@dataclass
class ModelState:
    messages: Annotated[list[AnyMessage], add_messages] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass(frozen=True)
class ModelContext:
    emit: Emit
    model: ChatModel
    system_message: SystemMessage
    tools: ToolSnapshot
    tool_context: ToolContext
    user_message: HumanMessage


def _init_model(model: str) -> ChatModel:
    return cast(ChatModel, init_chat_model(model))


class LangGraphRunner:
    def __init__(
        self,
        instance: Instance,
        *,
        model_factory: ModelFactory = _init_model,
        model_override: str | None = None,
        approval_hook: ApprovalHook | None = None,
    ) -> None:
        self._instance = instance
        self._model_factory = model_factory
        self._model_override = model_override
        self._approval_hook = approval_hook
        self._tools = ToolRegistry(
            instance.path,
            defaults=instance.manifest.tools.defaults,
        )
        self._checkpointer = InMemorySaver()
        graph_builder = StateGraph(ModelState, context_schema=ModelContext)
        graph_builder.add_node("model", self._call_model)
        graph_builder.add_node("tools", self._call_tools)
        graph_builder.add_edge(START, "model")
        graph_builder.add_conditional_edges("model", _after_model)
        graph_builder.add_edge("tools", "model")
        self._graph = graph_builder.compile(checkpointer=self._checkpointer)

    def prepare_for_turn(self) -> str:
        manifest = reload_manifest(self._instance, model_override=self._model_override)
        self._instance = replace(self._instance, manifest=manifest)
        return manifest.models.main

    async def run(self, turn: TurnRequest, emit: Emit) -> TurnResult:
        if self._approval_hook is not None:
            request = await self._approval_hook(turn)
            await emit(ApprovalRequested(approval_id=uuid4(), request=request))
            return ParkedTurn()
        return await self._invoke(turn, emit)

    async def resume(self, turn: TurnRequest, answer: str, emit: Emit) -> TurnOutcome:
        # Gate semantics belong to #7. Ticket #34's placeholder answer only resumes.
        return await self._invoke(turn, emit)

    async def _invoke(self, turn: TurnRequest, emit: Emit) -> TurnOutcome:
        sections = assemble_system_prompt(self._instance, date.today())
        tools, warnings = self._tools.refresh()
        for warning in warnings:
            await emit(warning)
        model = self._model_factory(turn.model)
        runnables = [tool.runnable for tool in tools.tools]
        bound_model = model.bind_tools(runnables) if runnables else model
        result = ModelState(
            **await self._graph.ainvoke(
                ModelState(),
                {"configurable": {"thread_id": str(turn.thread_id)}},
                context=ModelContext(
                    emit=emit,
                    model=bound_model,
                    system_message=SystemMessage(content=render_system_prompt(sections)),
                    tools=tools,
                    tool_context=ToolContext(
                        instance=self._instance,
                        thread_id=turn.thread_id,
                    ),
                    user_message=HumanMessage(content=turn.message),
                ),
                # ADR 0011 keeps interrupted tool calls out of checkpoint history.
                durability="exit",
            )
        )
        return TurnOutcome(
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
        )

    async def _call_model(
        self,
        state: ModelState,
        runtime: Runtime[ModelContext],
    ) -> ModelState:
        # Keep the user message atomic with the call so failures cannot checkpoint it.
        following_tool_call = bool(state.messages) and isinstance(state.messages[-1], ToolMessage)
        messages = (
            state.messages
            if following_tool_call
            else [*state.messages, runtime.context.user_message]
        )
        response: AIMessageChunk | None = None
        async for chunk in runtime.context.model.astream(
            [runtime.context.system_message, *messages]
        ):
            response = chunk if response is None else response + chunk
            if chunk.text:
                await runtime.context.emit(MessageDelta(text=chunk.text))
        if response is None:
            raise ModelNoResponse("The model returned no response.")
        usage = response.usage_metadata
        returned_messages: list[AnyMessage] = [response]
        if not following_tool_call:
            returned_messages.insert(0, runtime.context.user_message)
        return ModelState(
            messages=returned_messages,
            input_tokens=state.input_tokens + (usage["input_tokens"] if usage is not None else 0),
            output_tokens=state.output_tokens
            + (usage["output_tokens"] if usage is not None else 0),
        )

    async def _call_tools(
        self,
        state: ModelState,
        runtime: Runtime[ModelContext],
    ) -> ModelState:
        response = state.messages[-1]
        if not isinstance(response, AIMessage):
            raise ModelNoResponse("The model returned an invalid tool call response.")
        messages: list[AnyMessage] = []
        for model_call in response.tool_calls:
            name = model_call["name"]
            call_id = model_call["id"] or str(uuid4())
            arguments = _TOOL_ARGUMENTS.validate_python(model_call["args"])
            await runtime.context.emit(ToolCall(call_id=call_id, name=name, arguments=arguments))
            result = await self._run_tool(
                call_id,
                name,
                arguments,
                runtime,
            )
            await runtime.context.emit(result)
            messages.append(
                ToolMessage(
                    content=result.output,
                    name=result.name,
                    tool_call_id=result.call_id,
                    status="error" if result.error else "success",
                )
            )
        return ModelState(
            messages=messages,
            input_tokens=state.input_tokens,
            output_tokens=state.output_tokens,
        )

    async def _run_tool(
        self,
        call_id: str,
        name: str,
        arguments: dict[str, JsonValue],
        runtime: Runtime[ModelContext],
    ) -> ToolResult:
        selected = runtime.context.tools.get(name)
        if selected is None:
            return ToolResult(
                call_id=call_id,
                name=name,
                output=f'Tool "{name}" is not available in this turn.',
                error=True,
            )
        try:
            # A tool is user code. Turn its failures into model-visible tool errors.
            output = await selected.ainvoke(arguments, runtime.context.tool_context)
        except Exception as exc:
            return ToolResult(
                call_id=call_id,
                name=name,
                output=exception_message(exc),
                error=True,
            )
        return ToolResult(call_id=call_id, name=name, output=output, error=False)


def _after_model(state: ModelState) -> str:
    response = state.messages[-1]
    if isinstance(response, AIMessage) and response.tool_calls:
        return "tools"
    return END
