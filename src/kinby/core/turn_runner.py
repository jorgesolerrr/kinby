"""Run a model turn with LangGraph working state beneath the event stream."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import date
from typing import Annotated, Protocol, cast
from uuid import UUID, uuid4

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
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import StructuredTool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph, add_messages
from langgraph.runtime import Runtime
from langgraph.types import Command, Interrupt, interrupt
from pydantic import JsonValue, TypeAdapter

from kinby.contracts import (
    ApprovalRequested,
    MessageDelta,
    ToolCall,
    ToolResult,
)
from kinby.core.errors import (
    InvalidApprovalRequest,
    ModelNoResponse,
    ThreadBusy,
)
from kinby.core.prompt import assemble_system_prompt, render_system_prompt
from kinby.core.turns import (
    ApprovalDecision,
    Emit,
    ParkedTurn,
    TurnOutcome,
    TurnRequest,
    TurnResult,
)
from kinby.instance import Instance, reload_manifest
from kinby.plugins.errors import exception_message
from kinby.plugins.registry import ToolRegistry, ToolSnapshot
from kinby.plugins.tools import ToolContext

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


_MODEL_STATE = TypeAdapter(ModelState)
_INTERRUPTS = TypeAdapter(tuple[Interrupt, ...])


@dataclass(frozen=True)
class ModelContext:
    emit: Emit
    model: ChatModel
    system_message: SystemMessage
    tools: ToolSnapshot
    tool_context: ToolContext
    user_message: HumanMessage


@dataclass(frozen=True)
class ToolCallDecision:
    call: ToolCall
    decision: ApprovalDecision


def _init_model(model: str) -> ChatModel:
    return cast(ChatModel, init_chat_model(model))


class LangGraphRunner:
    def __init__(
        self,
        instance: Instance,
        *,
        model_factory: ModelFactory = _init_model,
        model_override: str | None = None,
    ) -> None:
        self._instance = instance
        self._model_factory = model_factory
        self._model_override = model_override
        self._tools = ToolRegistry(
            instance.path,
            defaults=instance.manifest.tools.defaults,
        )
        self._completed: dict[UUID, RunnableConfig] = {}
        self._parked: dict[UUID, UUID] = {}
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
        if turn.thread_id in self._parked:
            raise ThreadBusy(f'Thread "{turn.thread_id}" already has a parked turn.')
        config = self._completed.get(turn.thread_id)
        if config is None:
            await self._checkpointer.adelete_thread(str(turn.thread_id))
            config = _graph_config(turn)
        return await self._invoke(turn, emit, ModelState(), config)

    def can_resume(self, turn: TurnRequest) -> bool:
        return self._parked.get(turn.thread_id) == turn.turn_id

    async def discard(self, turn: TurnRequest) -> None:
        if self.can_resume(turn):
            del self._parked[turn.thread_id]

    async def resume(
        self,
        turn: TurnRequest,
        decision: ApprovalDecision,
        emit: Emit,
    ) -> TurnResult:
        return await self._invoke(turn, emit, Command(resume=decision), _graph_config(turn))

    async def _invoke(
        self,
        turn: TurnRequest,
        emit: Emit,
        graph_input: ModelState | Command,
        config: RunnableConfig,
    ) -> TurnResult:
        sections = assemble_system_prompt(self._instance, date.today())
        tools, warnings = self._tools.refresh()
        for warning in warnings:
            await emit(warning)
        model = self._model_factory(turn.model)
        runnables = [tool.runnable for tool in tools.tools]
        bound_model = model.bind_tools(runnables) if runnables else model
        graph_result = await self._graph.ainvoke(
            graph_input,
            config,
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
            # ADR 0011 keeps interrupted tool calls out of completed history.
            durability="exit",
        )
        interrupts = _INTERRUPTS.validate_python(graph_result.get("__interrupt__", ()))
        if interrupts:
            approval = interrupts[0].value
            if not isinstance(approval, ApprovalRequested):
                raise InvalidApprovalRequest("The graph returned an invalid approval request.")
            self._parked[turn.thread_id] = turn.turn_id
            await emit(approval)
            return ParkedTurn()
        result = _MODEL_STATE.validate_python(graph_result)
        completed = await self._graph.aget_state(_graph_config(turn))
        self._completed[turn.thread_id] = completed.config
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
        calls: list[ToolCallDecision] = []
        for model_call in response.tool_calls:
            name = model_call["name"]
            call_id = model_call["id"] or str(uuid4())
            arguments = _TOOL_ARGUMENTS.validate_python(model_call["args"])
            call = ToolCall(call_id=call_id, name=name, arguments=arguments)
            selected = runtime.context.tools.get(name)
            if selected is None or not selected.write:
                decision = ApprovalDecision.APPROVE
            else:
                decision = ApprovalDecision(
                    interrupt(
                        ApprovalRequested(
                            approval_id=uuid4(),
                            request=f"{name}: {json.dumps(arguments, sort_keys=True)}",
                        )
                    )
                )
            calls.append(ToolCallDecision(call, decision))

        messages: list[AnyMessage] = []
        for call_decision in calls:
            call = call_decision.call
            if call_decision.decision is ApprovalDecision.DENY:
                result = ToolResult(
                    call_id=call.call_id,
                    name=call.name,
                    output=f'Tool "{call.name}" was denied by the user.',
                    error=True,
                )
                await runtime.context.emit(result)
                messages.append(
                    ToolMessage(
                        content=result.output,
                        name=result.name,
                        tool_call_id=result.call_id,
                        status="error",
                    )
                )
                continue
            await runtime.context.emit(call)
            result = await self._run_tool(
                call.call_id,
                call.name,
                call.arguments,
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


def _graph_config(turn: TurnRequest) -> RunnableConfig:
    return {"configurable": {"thread_id": str(turn.thread_id)}}
