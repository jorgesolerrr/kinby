"""Run a model turn with LangGraph working state beneath the event stream."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime
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
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.errors import GraphRecursionError
from langgraph.graph import END, START, StateGraph, add_messages
from langgraph.graph.state import CompiledStateGraph
from langgraph.runtime import Runtime
from langgraph.types import Command, Interrupt, interrupt
from pydantic import JsonValue, TypeAdapter

from kinby.contracts import (
    ApprovalRequested,
    CompletionOutcome,
    Delivery,
    EventType,
    GateDecider,
    GateOutcome,
    MessageDelta,
    PermissionMode,
    RoutineOrigin,
    RoutineTrigger,
    SignalReceived,
    ToolCall,
    ToolGated,
    ToolResult,
    UserOrigin,
    Warning,
    gate_denial_source,
)
from kinby.core.budgets import DailyBudget, daily_cost
from kinby.core.errors import (
    BudgetExceeded,
    CodeStepFailed,
    CodeStepNotFound,
    InvalidApprovalRequest,
    ModelNoResponse,
    PermissionDenied,
    RoutineNotFound,
)
from kinby.core.events import EventLog
from kinby.core.gate import evaluate
from kinby.core.pricing import price_map
from kinby.core.prompt import assemble_system_prompt, render_system_prompt, render_wake
from kinby.core.turn_metrics import UnpricedModel
from kinby.core.turns import (
    ApprovalDecision,
    Emit,
    ParkedTurn,
    TurnContext,
    TurnOutcome,
    TurnPreparation,
    TurnRequest,
    TurnResult,
)
from kinby.instance import Budgets, Instance, reload_manifest
from kinby.instance.permissions import (
    SHIPPED_POLICY,
    GateAction,
    GatePolicy,
    load_permissions,
    validate_bash_regexes,
)
from kinby.plugins.core import core_tools
from kinby.plugins.errors import exception_message
from kinby.plugins.registry import ToolRegistry, ToolSnapshot
from kinby.plugins.routines import (
    Routine,
    load_routines,
    resolve_code_step,
    strip_signal_credentials,
)
from kinby.plugins.skills import load_skills
from kinby.plugins.tools import Tool, ToolContext

_TOOL_ARGUMENTS = TypeAdapter(dict[str, JsonValue])
_CHECKPOINTS_NAME = "checkpoints.sqlite"
_BUDGET_VERSION = "kinby_budget_version"
_BUDGET_START_STEP = "kinby_budget_start_step"
_BUDGET_RESUMES = "kinby_budget_resumes"
_BUDGET_SECONDS_USED = "kinby_budget_seconds_used"
_BUDGET_SEGMENT_STARTED_AT = "kinby_budget_segment_started_at"
_BUDGET_STEPS = "kinby_budget_steps"
_BUDGET_TOKENS = "kinby_budget_tokens"
_BUDGET_SECONDS = "kinby_budget_seconds"
_BUDGET_USD_PER_DAY = "kinby_budget_usd_per_day"
_CHECKPOINT_SERIALIZER = JsonPlusSerializer(
    allowed_msgpack_modules=(
        ApprovalDecision,
        ApprovalRequested,
        EventType,
        PermissionMode,
        TurnRequest,
        UserOrigin,
        RoutineOrigin,
        RoutineTrigger,
    )
)


class ChatModel(Protocol):
    def astream(self, messages: Sequence[BaseMessage]) -> AsyncIterator[AIMessageChunk]: ...
    def bind_tools(self, tools: Sequence[StructuredTool]) -> ChatModel: ...


ModelFactory = Callable[[str], ChatModel]


@dataclass
class ModelState:
    turn: TurnRequest
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
    gate_policy: GatePolicy
    permission_mode: PermissionMode
    tools: ToolSnapshot
    tool_context: ToolContext
    user_message: HumanMessage
    budgets: Budgets


@dataclass(frozen=True)
class ToolCallResolution:
    call: ToolCall
    gate: ToolGated


@dataclass(frozen=True)
class _BudgetProgress:
    budgets: Budgets
    start_step: int
    resumes: int = 0
    steps_used: int = 0
    seconds_used: float = 0

    @property
    def steps_remaining(self) -> int | None:
        if self.budgets.steps is None:
            return None
        return self.budgets.steps - self.steps_used

    @property
    def seconds_remaining(self) -> float | None:
        if self.budgets.seconds is None:
            return None
        return self.budgets.seconds - self.seconds_used


@dataclass(frozen=True)
class _PreparedTurn:
    tools: ToolSnapshot
    progress: _BudgetProgress
    permission_mode: PermissionMode
    message: str
    payload: str | None


def _init_model(model: str) -> ChatModel:
    return cast(ChatModel, init_chat_model(model))


class LangGraphRunner:
    def __init__(
        self,
        instance: Instance,
        *,
        event_log: EventLog | None = None,
        model_factory: ModelFactory = _init_model,
        model_override: str | None = None,
        gate_policy: GatePolicy | None = None,
    ) -> None:
        self._instance = instance
        self._event_log = (
            event_log if event_log is not None else EventLog(instance.manifest.state_dir)
        )
        self._model_factory = model_factory
        self._model_override = model_override
        self._gate_policy_override = gate_policy
        self._gate_policy = gate_policy if gate_policy is not None else SHIPPED_POLICY
        instance.manifest.state_dir.mkdir(parents=True, exist_ok=True)
        self._tools = ToolRegistry(
            instance.path,
            defaults=instance.manifest.tools.defaults,
        )
        self._emitted_tool_calls: set[tuple[UUID, UUID, str]] = set()
        self._checkpoint_path = instance.manifest.state_dir / _CHECKPOINTS_NAME
        graph_builder = StateGraph(ModelState, context_schema=ModelContext)
        graph_builder.add_node("model", self._call_model)
        graph_builder.add_node("tools", self._call_tools)
        graph_builder.add_edge(START, "model")
        graph_builder.add_conditional_edges("model", _after_model)
        graph_builder.add_edge("tools", "model")
        self._graph_builder = graph_builder

    def prepare_for_turn(self) -> TurnPreparation:
        manifest = reload_manifest(self._instance, model_override=self._model_override)
        self._instance = replace(self._instance, manifest=manifest)
        self._gate_policy = self._load_gate_policy()
        limit = manifest.budgets.usd_per_day
        daily_budget = None
        if limit is not None:
            prices = price_map(manifest.prices)
            daily_budget = DailyBudget(
                limit=limit,
                cost=daily_cost(
                    self._event_log.all_events(),
                    prices,
                    datetime.now(UTC).date(),
                ),
                unpriced_model=(
                    UnpricedModel(manifest.models.main)
                    if manifest.models.main not in prices
                    else None
                ),
            )
        return TurnPreparation(
            model=manifest.models.main,
            default_mode=self._gate_policy.mode,
            ceiling=self._gate_policy.ceiling,
            daily_budget=daily_budget,
            budgets=manifest.budgets,
        )

    def permission_ceiling(self) -> PermissionMode:
        return self._load_gate_policy().ceiling

    def _load_gate_policy(self) -> GatePolicy:
        if self._gate_policy_override is None:
            return load_permissions(self._instance)
        validate_bash_regexes(
            self._gate_policy_override,
            source="gate policy override",
        )
        return self._gate_policy_override

    async def run(self, turn: TurnRequest, context: TurnContext) -> TurnResult:
        config, start_step = await self._start_config(turn)
        progress = _BudgetProgress(context.budgets, start_step)
        return await self._invoke(
            turn,
            context,
            ModelState(turn=turn),
            config,
            progress,
        )

    async def restore(self, thread_id: UUID, turn_id: UUID) -> TurnRequest | None:
        config: RunnableConfig = {"configurable": {"thread_id": str(thread_id)}}
        async with self._graph() as (graph, _):
            state = await graph.aget_state(config)
        if not any(task.interrupts for task in state.tasks):
            return None
        restored = _MODEL_STATE.validate_python(state.values).turn
        if restored.thread_id != thread_id or restored.turn_id != turn_id:
            return None
        return restored

    async def resume(
        self,
        turn: TurnRequest,
        decision: ApprovalDecision,
        context: TurnContext,
    ) -> TurnResult:
        progress = await self._resume_budget_progress(
            turn,
            context.budgets,
        )
        steps_budget = progress.budgets.steps
        if steps_budget is not None and progress.steps_used >= steps_budget:
            raise BudgetExceeded("steps", steps_budget)
        seconds_budget = progress.budgets.seconds
        if seconds_budget is not None and progress.seconds_used >= seconds_budget:
            raise BudgetExceeded("seconds", seconds_budget)
        self._restore_emitted_tool_calls(turn)
        return await self._invoke(
            turn,
            context,
            Command(resume=decision),
            _graph_config(turn),
            replace(progress, resumes=progress.resumes + 1),
        )

    async def _invoke(
        self,
        turn: TurnRequest,
        context: TurnContext,
        graph_input: ModelState | Command,
        config: RunnableConfig,
        progress: _BudgetProgress,
    ) -> TurnResult:
        budgets = progress.budgets
        emit = context.emit
        skills, skill_warnings = load_skills(self._instance)
        sections = assemble_system_prompt(self._instance, skills, date.today())
        discovered_tools, tool_warnings = self._tools.refresh()
        tools, core_tool_warnings = discovered_tools.with_core(*core_tools(self._instance, skills))
        for warning in (*tool_warnings, *core_tool_warnings, *skill_warnings):
            await emit(warning)
        prepared = await self._prepare_turn(turn, graph_input, tools, progress, emit)
        if isinstance(prepared, TurnOutcome):
            return prepared
        tools = prepared.tools
        progress = prepared.progress
        budgets = progress.budgets
        model = self._model_factory(turn.model)
        runnables = [tool.runnable for tool in tools.tools]
        bound_model = model.bind_tools(runnables) if runnables else model
        if progress.steps_remaining is not None:
            config["recursion_limit"] = progress.steps_remaining
        seconds_timeout = asyncio.timeout(progress.seconds_remaining)
        try:
            async with self._graph() as (graph, _):
                started_at = datetime.now(UTC).timestamp()
                config["metadata"] = _budget_metadata(progress, started_at)
                async with seconds_timeout:
                    graph_result = await graph.ainvoke(
                        graph_input,
                        config,
                        context=ModelContext(
                            emit=emit,
                            model=bound_model,
                            system_message=SystemMessage(content=render_system_prompt(sections)),
                            gate_policy=self._gate_policy,
                            permission_mode=prepared.permission_mode,
                            tools=tools,
                            tool_context=ToolContext(
                                instance=self._instance,
                                thread_id=turn.thread_id,
                            ),
                            user_message=HumanMessage(
                                content=render_wake(turn.origin, prepared.message, prepared.payload)
                            ),
                            budgets=budgets,
                        ),
                        # ADR 0011 keeps interrupted tool calls out of completed history.
                        durability="exit",
                    )
        except TimeoutError as exc:
            if not seconds_timeout.expired() or budgets.seconds is None:
                raise
            raise BudgetExceeded("seconds", budgets.seconds) from exc
        except GraphRecursionError as exc:
            if budgets.steps is None:
                raise
            raise BudgetExceeded("steps", budgets.steps) from exc
        interrupts = _INTERRUPTS.validate_python(graph_result.get("__interrupt__", ()))
        if interrupts:
            approval = interrupts[0].value
            if not isinstance(approval, ApprovalRequested):
                raise InvalidApprovalRequest("The graph returned an invalid approval request.")
            await emit(approval)
            return ParkedTurn()
        result = _MODEL_STATE.validate_python(graph_result)
        return TurnOutcome(
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
        )

    async def _prepare_turn(
        self,
        turn: TurnRequest,
        graph_input: ModelState | Command,
        tools: ToolSnapshot,
        progress: _BudgetProgress,
        emit: Emit,
    ) -> _PreparedTurn | TurnOutcome:
        budgets = progress.budgets
        permission_mode = turn.permission_mode
        message = turn.message
        payload = None
        if isinstance(turn.origin, RoutineOrigin):
            routines, warnings = load_routines(self._instance, policy=self._gate_policy)
            for warning in warnings:
                await emit(warning)
            routine = next(
                (routine for routine in routines if routine.name == turn.origin.name), None
            )
            if routine is None:
                raise RoutineNotFound(f'Routine "{turn.origin.name}" was not found.')
            budgets, budget_warnings = _routine_budgets(budgets, routine)
            progress = replace(progress, budgets=budgets)
            for warning in budget_warnings:
                await emit(warning)
            permission_mode = routine.mode
            message = routine.prompt
            delivery = self._delivery_for_turn(turn)
            declared = routine.code_step
            if declared is None:
                code_step = None
            else:
                try:
                    code_step = resolve_code_step(declared, tools)
                except ValueError as exc:
                    raise CodeStepNotFound(
                        f'Code step tool "{declared.name}" is not available in this turn.'
                    ) from exc
            if code_step is not None:
                tools = ToolSnapshot(
                    tuple(tool for tool in tools.tools if tool.name != code_step.name)
                )
            if code_step is not None and isinstance(graph_input, ModelState):
                code_started = asyncio.get_running_loop().time()
                output = await self._run_code_step(
                    routine,
                    code_step,
                    turn.origin,
                    turn.thread_id,
                    delivery,
                    emit,
                    budgets,
                )
                progress = replace(
                    progress,
                    seconds_used=progress.seconds_used
                    + asyncio.get_running_loop().time()
                    - code_started,
                )
                if output is None:
                    return TurnOutcome(outcome=CompletionOutcome.NO_WORK)
                payload = output
            elif delivery is not None:
                payload = delivery.body
        return _PreparedTurn(tools, progress, permission_mode, message, payload)

    def _delivery_for_turn(self, turn: TurnRequest) -> Delivery | None:
        return next(
            (
                event.payload.delivery
                for event in self._event_log.stored(turn.thread_id)
                if event.turn_id == turn.turn_id and isinstance(event.payload, SignalReceived)
            ),
            None,
        )

    async def _run_code_step(
        self,
        routine: Routine,
        code_step: Tool,
        origin: RoutineOrigin,
        thread_id: UUID,
        delivery: Delivery | None,
        emit: Emit,
        budgets: Budgets,
    ) -> str | None:
        arguments = dict(routine.arguments)
        if delivery is not None:
            raw_signal = delivery.model_dump(mode="json")
            raw_signal["headers"] = strip_signal_credentials(routine.signal, delivery.headers)
            media_type = delivery.content_type.partition(";")[0].strip().lower()
            if media_type == "application/json":
                try:
                    raw_signal["body"] = json.loads(delivery.body)
                except json.JSONDecodeError as exc:
                    raise CodeStepFailed(f"The signal body is not valid JSON: {exc}") from exc
            arguments["signal"] = _TOOL_ARGUMENTS.validate_python(raw_signal)
        elif routine.signal is not None and origin.trigger is not RoutineTrigger.SIGNAL:
            arguments["signal"] = {}
        call = ToolCall(
            call_id=str(uuid4()),
            name=code_step.name,
            arguments=arguments,
            write=code_step.write,
        )
        await emit(call)
        decision = evaluate(
            self._gate_policy,
            routine.mode,
            call,
            code_step,
            self._instance.manifest.workspace.path,
        )
        await emit(
            ToolGated(
                call_id=call.call_id,
                name=call.name,
                action=_gate_outcome(decision.action),
                rule=decision.rule,
                decided_by=GateDecider.POLICY,
            )
        )
        if decision.action is not GateAction.ALLOW:
            message = (
                f'Code step "{call.name}" requires allow; gate rule '
                f'"{decision.rule}" returned {decision.action.value}.'
            )
            await emit(ToolResult(call_id=call.call_id, name=call.name, output=message, error=True))
            raise PermissionDenied(message)
        timeout = asyncio.timeout(budgets.seconds)
        started_at = asyncio.get_running_loop().time()
        try:
            async with timeout:
                output = await code_step.ainvoke_raw(
                    call.arguments, ToolContext(instance=self._instance, thread_id=thread_id)
                )
        except Exception as exc:
            failure = (
                BudgetExceeded("seconds", budgets.seconds)
                if timeout.expired() and budgets.seconds is not None
                else CodeStepFailed(exception_message(exc))
            )
            await emit(
                ToolResult(
                    call_id=call.call_id,
                    name=call.name,
                    output=str(failure),
                    error=True,
                    duration_ms=_elapsed_ms(started_at),
                )
            )
            raise failure from exc
        await emit(
            ToolResult(
                call_id=call.call_id,
                name=call.name,
                output=str(output),
                error=False,
                duration_ms=_elapsed_ms(started_at),
            )
        )
        return output

    async def _start_config(self, turn: TurnRequest) -> tuple[RunnableConfig, int]:
        async with self._graph() as (graph, checkpointer):
            async for state in graph.aget_state_history(_graph_config(turn)):
                interrupted = any(task.interrupts for task in state.tasks)
                if not state.next and not interrupted:
                    return state.config, _checkpoint_step(state.metadata) + 1
            await checkpointer.adelete_thread(str(turn.thread_id))
        return _graph_config(turn), -1

    async def _resume_budget_progress(
        self,
        turn: TurnRequest,
        fallback: Budgets,
    ) -> _BudgetProgress:
        async with self._graph() as (graph, _):
            state = await graph.aget_state(_graph_config(turn))
        metadata = state.metadata or {}
        if _metadata_int(metadata, _BUDGET_VERSION) != 1:
            return _BudgetProgress(fallback, _checkpoint_step(metadata))

        budgets = _budgets_from_metadata(metadata)
        start_step = _metadata_int(metadata, _BUDGET_START_STEP)
        resumes = _metadata_int(metadata, _BUDGET_RESUMES)
        used_steps = max(0, _checkpoint_step(metadata) - start_step - resumes)
        seconds_used = _metadata_float(metadata, _BUDGET_SECONDS_USED)
        segment_started_at = _metadata_float(metadata, _BUDGET_SEGMENT_STARTED_AT)
        checkpoint_at = _timestamp(state.created_at)
        if checkpoint_at is not None:
            seconds_used += max(0, checkpoint_at - segment_started_at)
        return _BudgetProgress(
            budgets=budgets,
            start_step=start_step,
            resumes=resumes,
            steps_used=used_steps,
            seconds_used=seconds_used,
        )

    @asynccontextmanager
    async def _graph(
        self,
    ) -> AsyncIterator[
        tuple[
            CompiledStateGraph[ModelState, ModelContext, ModelState, ModelState],
            AsyncSqliteSaver,
        ]
    ]:
        async with AsyncSqliteSaver.from_conn_string(str(self._checkpoint_path)) as checkpointer:
            checkpointer.serde = _CHECKPOINT_SERIALIZER
            yield self._graph_builder.compile(checkpointer=checkpointer), checkpointer

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
        input_tokens = state.input_tokens + (usage["input_tokens"] if usage is not None else 0)
        output_tokens = state.output_tokens + (usage["output_tokens"] if usage is not None else 0)
        token_budget = runtime.context.budgets.tokens
        if token_budget is not None and input_tokens + output_tokens > token_budget:
            raise BudgetExceeded("tokens", token_budget)
        returned_messages: list[AnyMessage] = [response]
        if not following_tool_call:
            returned_messages.insert(0, runtime.context.user_message)
        return ModelState(
            turn=state.turn,
            messages=returned_messages,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

    async def _call_tools(
        self,
        state: ModelState,
        runtime: Runtime[ModelContext],
    ) -> ModelState:
        response = state.messages[-1]
        if not isinstance(response, AIMessage):
            raise ModelNoResponse("The model returned an invalid tool call response.")
        calls: list[ToolCallResolution] = []
        for model_call in response.tool_calls:
            name = model_call["name"]
            call_id = model_call["id"] or str(uuid4())
            arguments = _TOOL_ARGUMENTS.validate_python(model_call["args"])
            selected = runtime.context.tools.get(name)
            call = ToolCall(
                call_id=call_id,
                name=name,
                arguments=arguments,
                write=selected.write if selected is not None else True,
            )
            await self._emit_tool_call_once(state.turn, call, runtime.context.emit)
            gate_decision = evaluate(
                runtime.context.gate_policy,
                runtime.context.permission_mode,
                call,
                selected,
                runtime.context.tool_context.workspace,
            )
            if gate_decision.action is GateAction.ASK:
                approval_decision = ApprovalDecision(
                    interrupt(
                        ApprovalRequested(
                            approval_id=uuid4(),
                            name=name,
                            arguments=arguments,
                            rule=gate_decision.rule,
                        )
                    )
                )
                action = (
                    GateOutcome.ALLOW
                    if approval_decision is ApprovalDecision.APPROVE
                    else GateOutcome.DENY
                )
                decided_by = GateDecider.USER
            else:
                action = _gate_outcome(gate_decision.action)
                decided_by = GateDecider.POLICY
            calls.append(
                ToolCallResolution(
                    call,
                    ToolGated(
                        call_id=call.call_id,
                        name=call.name,
                        action=action,
                        rule=gate_decision.rule,
                        decided_by=decided_by,
                    ),
                )
            )

        messages: list[AnyMessage] = []
        for resolution in calls:
            call = resolution.call
            await runtime.context.emit(resolution.gate)
            if resolution.gate.action is GateOutcome.DENY:
                result = ToolResult(
                    call_id=call.call_id,
                    name=call.name,
                    output=(
                        f'Tool "{call.name}" was denied by {gate_denial_source(resolution.gate)}.'
                    ),
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
                self._forget_tool_call(state.turn, call)
                continue
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
            self._forget_tool_call(state.turn, call)
        return ModelState(
            turn=state.turn,
            messages=messages,
            input_tokens=state.input_tokens,
            output_tokens=state.output_tokens,
        )

    async def _emit_tool_call_once(
        self,
        turn: TurnRequest,
        call: ToolCall,
        emit: Emit,
    ) -> None:
        key = (turn.thread_id, turn.turn_id, call.call_id)
        if key in self._emitted_tool_calls:
            return
        await emit(call)
        self._emitted_tool_calls.add(key)

    def _restore_emitted_tool_calls(self, turn: TurnRequest) -> None:
        if any(
            thread_id == turn.thread_id and turn_id == turn.turn_id
            for thread_id, turn_id, _ in self._emitted_tool_calls
        ):
            return
        self._emitted_tool_calls.update(
            (turn.thread_id, turn.turn_id, payload.call_id)
            for event in self._event_log.stored(turn.thread_id)
            if event.turn_id == turn.turn_id and isinstance(payload := event.payload, ToolCall)
        )

    def _forget_tool_call(self, turn: TurnRequest, call: ToolCall) -> None:
        self._emitted_tool_calls.discard((turn.thread_id, turn.turn_id, call.call_id))

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
        started_at = asyncio.get_running_loop().time()
        try:
            # A tool is user code. Turn its failures into model-visible tool errors.
            output = await selected.ainvoke(arguments, runtime.context.tool_context)
        except Exception as exc:
            return ToolResult(
                call_id=call_id,
                name=name,
                output=exception_message(exc),
                error=True,
                duration_ms=_elapsed_ms(started_at),
            )
        return ToolResult(
            call_id=call_id,
            name=name,
            output=output,
            error=False,
            duration_ms=_elapsed_ms(started_at),
        )


def _elapsed_ms(started_at: float) -> int:
    return round((asyncio.get_running_loop().time() - started_at) * 1000)


def _gate_outcome(action: GateAction) -> GateOutcome:
    match action:
        case GateAction.ALLOW:
            return GateOutcome.ALLOW
        case GateAction.ASK | GateAction.DENY:
            return GateOutcome.DENY


def _after_model(state: ModelState) -> str:
    response = state.messages[-1]
    if isinstance(response, AIMessage) and response.tool_calls:
        return "tools"
    return END


def _graph_config(turn: TurnRequest) -> RunnableConfig:
    return {"configurable": {"thread_id": str(turn.thread_id)}}


def _budget_metadata(progress: _BudgetProgress, started_at: float) -> dict[str, int | float]:
    metadata: dict[str, int | float] = {
        _BUDGET_VERSION: 1,
        _BUDGET_START_STEP: progress.start_step,
        _BUDGET_RESUMES: progress.resumes,
        _BUDGET_SECONDS_USED: progress.seconds_used,
        _BUDGET_SEGMENT_STARTED_AT: started_at,
    }
    for key, value in (
        (_BUDGET_STEPS, progress.budgets.steps),
        (_BUDGET_TOKENS, progress.budgets.tokens),
        (_BUDGET_SECONDS, progress.budgets.seconds),
        (_BUDGET_USD_PER_DAY, progress.budgets.usd_per_day),
    ):
        if value is not None:
            metadata[key] = value
    return metadata


def _budgets_from_metadata(metadata: Mapping[str, object]) -> Budgets:
    return Budgets(
        steps=_metadata_optional_int(metadata, _BUDGET_STEPS),
        tokens=_metadata_optional_int(metadata, _BUDGET_TOKENS),
        seconds=_metadata_optional_float(metadata, _BUDGET_SECONDS),
        usd_per_day=_metadata_optional_float(metadata, _BUDGET_USD_PER_DAY),
    )


def _checkpoint_step(metadata: Mapping[str, object] | None) -> int:
    return _metadata_int(metadata or {}, "step", default=-1)


def _metadata_int(
    metadata: Mapping[str, object],
    key: str,
    *,
    default: int = 0,
) -> int:
    value = _metadata_optional_int(metadata, key)
    return default if value is None else value


def _metadata_optional_int(metadata: Mapping[str, object], key: str) -> int | None:
    value = metadata.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _metadata_float(metadata: Mapping[str, object], key: str) -> float:
    value = _metadata_optional_float(metadata, key)
    return 0 if value is None else value


def _metadata_optional_float(metadata: Mapping[str, object], key: str) -> float | None:
    value = metadata.get(key)
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _timestamp(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return datetime.fromisoformat(value).timestamp()
    except ValueError:
        return None


def _routine_budgets(manifest: Budgets, routine: Routine) -> tuple[Budgets, tuple[Warning, ...]]:
    warnings = tuple(
        Warning(
            sources=(str(routine.source),),
            message=(
                f"Routine {name} budget {value} exceeds the manifest budget. {ceiling} applies."
            ),
        )
        for name, value, ceiling in (
            ("steps", routine.budgets.steps, manifest.steps),
            ("tokens", routine.budgets.tokens, manifest.tokens),
            ("seconds", routine.budgets.seconds, manifest.seconds),
        )
        if value is not None and ceiling is not None and value > ceiling
    )
    return replace(
        manifest,
        steps=_lower_limit(manifest.steps, routine.budgets.steps),
        tokens=_lower_limit(manifest.tokens, routine.budgets.tokens),
        seconds=_lower_limit(manifest.seconds, routine.budgets.seconds),
    ), warnings


def _lower_limit[Limit: (int, float)](ceiling: Limit | None, value: Limit | None) -> Limit | None:
    if ceiling is None:
        return value
    return ceiling if value is None else min(ceiling, value)
