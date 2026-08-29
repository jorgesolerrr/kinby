"""Run a model turn with LangGraph working state beneath the event stream."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import date
from typing import Annotated, Protocol, cast
from uuid import uuid4

from langchain.chat_models import init_chat_model
from langchain_core.messages import (
    AIMessageChunk,
    AnyMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
)
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import START, StateGraph, add_messages
from langgraph.runtime import Runtime

from kinby.contracts import ApprovalRequested, MessageDelta
from kinby.core.errors import ModelNoResponse
from kinby.core.prompt import assemble_system_prompt, render_system_prompt
from kinby.core.turns import Emit, ParkedTurn, TurnOutcome, TurnRequest, TurnResult
from kinby.instance import Instance, reload_manifest

ApprovalHook = Callable[[TurnRequest], Awaitable[str]]


class ChatModel(Protocol):
    def astream(self, messages: Sequence[BaseMessage]) -> AsyncIterator[AIMessageChunk]: ...


ModelFactory = Callable[[str], ChatModel]


@dataclass
class ModelState:
    messages: Annotated[list[AnyMessage], add_messages] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass(frozen=True)
class ModelContext:
    message: str
    emit: Emit
    model: ChatModel
    system_message: SystemMessage


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
        self._checkpointer = InMemorySaver()
        graph_builder = StateGraph(ModelState, context_schema=ModelContext)
        graph_builder.add_node("model", self._call_model)
        graph_builder.add_edge(START, "model")
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
        result = ModelState(
            **await self._graph.ainvoke(
                ModelState(),
                {"configurable": {"thread_id": str(turn.thread_id)}},
                context=ModelContext(
                    message=turn.message,
                    emit=emit,
                    model=self._model_factory(turn.model),
                    system_message=SystemMessage(content=render_system_prompt(sections)),
                ),
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
        user_message = HumanMessage(content=runtime.context.message)
        response: AIMessageChunk | None = None
        async for chunk in runtime.context.model.astream(
            [runtime.context.system_message, *state.messages, user_message]
        ):
            response = chunk if response is None else response + chunk
            if chunk.text:
                await runtime.context.emit(MessageDelta(text=chunk.text))
        if response is None:
            raise ModelNoResponse("The model returned no response.")
        usage = response.usage_metadata
        return ModelState(
            messages=[user_message, response],
            input_tokens=usage["input_tokens"] if usage is not None else 0,
            output_tokens=usage["output_tokens"] if usage is not None else 0,
        )
