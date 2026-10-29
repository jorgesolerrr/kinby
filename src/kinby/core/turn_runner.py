"""Run a model turn with LangGraph working state beneath the event stream."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime
from functools import partial
from typing import Annotated, Protocol, cast
from uuid import UUID, uuid4, uuid5

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
    ApprovalDecision,
    ApprovalRequested,
    CompletionOutcome,
    Delivery,
    EventType,
    FactoryRunOrigin,
    FileHash,
    GateDecider,
    GateOutcome,
    MemoryRecalled,
    MessageDelta,
    PermissionMode,
    ProfileCounted,
    ProfileVersion,
    RoutineGrant,
    RoutineName,
    RoutineOrigin,
    RoutineTrigger,
    RunDelegated,
    SignalReceived,
    TakeoverRequested,
    ToolCall,
    ToolGated,
    ToolResult,
    UserOrigin,
    Warning,
    gate_denial_source,
)
from kinby.core.budgets import DailyBudget, daily_cost
from kinby.core.clock import utc_today
from kinby.core.errors import (
    BudgetExceeded,
    CodeStepFailed,
    CodeStepNotFound,
    GrantOutsideRoutine,
    InvalidApprovalRequest,
    InvalidParkedTurn,
    ModelNoResponse,
    PermissionDenied,
    ReadOnlyMarkRefused,
    RoutineNotFound,
)
from kinby.core.events import EventLog
from kinby.core.gate import AUTHORED_RULE, URL_READS, evaluate
from kinby.core.model_calls import completed_model_call
from kinby.core.models import Tokenizer, init_model, is_anthropic_model
from kinby.core.pricing import price_map
from kinby.core.prompt import (
    PromptSection,
    PromptSectionName,
    assemble_system_prompt,
    profile_version,
    prompt_version,
    render_system_prompt,
    render_wake,
)
from kinby.core.threads import ThreadStore, pending_approval
from kinby.core.turn_metrics import UnpricedModel
from kinby.core.turns import (
    ApprovalAnswer,
    Emit,
    ParkedTurn,
    PreparedTurnRequest,
    TurnContext,
    TurnOutcome,
    TurnPreparation,
    TurnRequest,
    TurnResult,
)
from kinby.instance import Budgets, Instance, ModelName, reload_manifest
from kinby.instance.config_changes import directory_hash
from kinby.instance.permissions import (
    SHIPPED_POLICY,
    GateAction,
    GatePolicy,
    load_permissions,
    validate_bash_regexes,
)
from kinby.instance.read_only_tools import read_only_tools, set_read_only
from kinby.instance.routine_grants import RoutineGrants
from kinby.memory import GraphStore
from kinby.memory.consolidation import consolidate_tool, consolidation_step
from kinby.memory.learning_review import learning_review_step
from kinby.memory.reflection import reflection_step
from kinby.plugins.browser import BrowserService
from kinby.plugins.browser_tools import browser_takeover
from kinby.plugins.core import core_tools
from kinby.plugins.errors import exception_message
from kinby.plugins.hub_browser import HubBrowser
from kinby.plugins.instance_tools import routine_config_file
from kinby.plugins.mcp import mcp_tools
from kinby.plugins.notify import (
    UNTITLED,
    approval_notification,
    raise_notification,
    raises_notifications,
    takeover_notification,
)
from kinby.plugins.registry import ToolRegistry, ToolSnapshot
from kinby.plugins.routines import (
    CONSOLIDATION_STEP,
    DREAMING_STEPS,
    LEARNING_REVIEW_STEP,
    REFLECTION_STEP,
    Routine,
    SharedCodeStep,
    load_routines,
    resolve_code_step,
    strip_signal_credentials,
)
from kinby.plugins.skills import load_skills
from kinby.plugins.tools import RecallReporter, RunReporter, Tool, ToolContext

logger = logging.getLogger(__name__)

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
_URL = re.compile(r"https?://[^\s<>\"'`()\[\]{}]+")
#: The tools whose results bring URLs in from the web.
_URL_SOURCES = frozenset({"web_search", "browser_snapshot", "browser_text", *URL_READS})
_CHECKPOINT_SERIALIZER = JsonPlusSerializer(
    allowed_msgpack_modules=(
        ApprovalAnswer,
        ApprovalDecision,
        ApprovalRequested,
        EventType,
        PermissionMode,
        TakeoverRequested,
        PreparedTurnRequest,
        TurnRequest,
        UserOrigin,
        RoutineOrigin,
        RoutineTrigger,
        FactoryRunOrigin,
    )
)


class ChatModel(Protocol):
    def astream(self, messages: Sequence[BaseMessage]) -> AsyncIterator[AIMessageChunk]: ...
    def bind_tools(self, tools: Sequence[StructuredTool]) -> ChatModel: ...


ModelFactory = Callable[[ModelName], ChatModel]


@dataclass
class ModelState:
    turn: PreparedTurnRequest
    messages: Annotated[list[AnyMessage], add_messages] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    #: The tools the routine's grants allow in this turn: those granted when it started, and those
    #: the user allowed in the routine since. A resume replays the gate over the parked step and
    #: matches answers to asks in order, so a grant made or revoked elsewhere meanwhile must not
    #: change which calls ask.
    granted: tuple[str, ...] = ()
    #: The hash of the routine's directory when the turn started. A grant from the turn holds
    #: only while the routine is unchanged, since any edit revokes its grants (ADR 0112).
    revision: FileHash | None = None
    #: The authored tools the user marked read-only: those marked when the turn started, and
    #: those marked from its approvals since, kept for a resume's replay as *granted* is.
    read_only: tuple[str, ...] = ()


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
    #: The approval the user gave the call, which a hub tool shows the hub.
    approval_id: UUID | None


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
    message: str
    payload: str | None


_EPHEMERAL = {"type": "ephemeral"}


def _init_model(model: ModelName) -> ChatModel:
    return cast(ChatModel, init_model(model))


def _cacheable_text_block(text: str) -> dict[str, object]:
    return {"type": "text", "text": text, "cache_control": _EPHEMERAL}


def _with_cache_control(message: BaseMessage) -> BaseMessage:
    content = message.content
    marked_content = [content] if isinstance(content, str) else list(content)
    if not marked_content:
        return message
    last_block = marked_content[-1]
    marked_content[-1] = (
        _cacheable_text_block(last_block)
        if isinstance(last_block, str)
        else {**last_block, "cache_control": _EPHEMERAL}
    )
    return message.model_copy(update={"content": marked_content})


def _mark_cache_prefix(
    model: ModelName,
    messages: Sequence[BaseMessage],
) -> Sequence[BaseMessage]:
    if not is_anthropic_model(model):
        return messages
    # Anthropic caches everything before each marker. The first covers tools and the
    # stable system prompt; the last advances the cached conversation without storing
    # provider metadata in checkpointed messages.
    marked_messages = list(messages)
    marked_messages[0] = _with_cache_control(marked_messages[0])
    marked_messages[-1] = _with_cache_control(marked_messages[-1])
    return marked_messages


class LangGraphRunner:
    def __init__(
        self,
        instance: Instance,
        *,
        event_log: EventLog | None = None,
        model_factory: ModelFactory = _init_model,
        model_override: str | None = None,
        gate_policy: GatePolicy | None = None,
        today: Callable[[], date] = utc_today,
        tokenizer: Tokenizer | None = None,
        browser: BrowserService | None = None,
    ) -> None:
        """Count each profile version with *tokenizer*. Without one, the profile goes uncounted.
        The browser tools reach *browser*, or else the hub's browser service."""
        self._instance = instance
        self._event_log = (
            event_log if event_log is not None else EventLog(instance.manifest.state_dir)
        )
        self._model_factory = model_factory
        self._model_override = model_override
        self._gate_policy_override = gate_policy
        self._gate_policy = gate_policy if gate_policy is not None else SHIPPED_POLICY
        self._today = today
        self._tokenizer = tokenizer
        self._browser = browser if browser is not None else HubBrowser()
        #: The profile versions turns were prepared with and no count holds yet, by their text.
        self._uncounted_profiles: dict[ProfileVersion, str] = {}
        self._counting_profiles = asyncio.Lock()
        instance.manifest.state_dir.mkdir(parents=True, exist_ok=True)
        self._tools = ToolRegistry(
            instance.path,
            tools=instance.manifest.tools,
        )
        self._emitted_tool_calls: set[tuple[UUID, UUID, str]] = set()
        self._checkpoint_path = instance.manifest.state_dir / _CHECKPOINTS_NAME
        self._checkpoint_setup = asyncio.Lock()
        graph_builder = StateGraph(ModelState, context_schema=ModelContext)
        graph_builder.add_node("model", self._call_model)
        graph_builder.add_node("tools", self._call_tools)
        graph_builder.add_edge(START, "model")
        graph_builder.add_conditional_edges("model", _after_model)
        graph_builder.add_edge("tools", "model")
        self._graph_builder = graph_builder

    def prepare_for_turn(self) -> TurnPreparation:
        manifest = reload_manifest(self._instance, model_override=self._model_override)
        if manifest.tools != self._instance.manifest.tools:
            self._tools = ToolRegistry(self._instance.path, tools=manifest.tools)
        self._instance = replace(self._instance, manifest=manifest)
        self._gate_policy = self._load_gate_policy()
        sections = self._prompt_sections()
        profile = next(
            (section for section in sections if section.name is PromptSectionName.PROFILE), None
        )
        version = None
        if profile is not None:
            version = profile_version(profile)
            if self._tokenizer is not None:
                self._uncounted_profiles[version] = profile.text
        limit = manifest.budgets.usd_per_day
        daily_budget = None
        if limit is not None:
            prices = price_map(manifest.prices)
            daily_budget = DailyBudget(
                limit=limit,
                cost=daily_cost(
                    self._event_log.all_events(),
                    prices,
                    self._today(),
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
            prompt_version=prompt_version(sections),
            system_prompt=render_system_prompt(sections),
            profile_version=version,
            daily_budget=daily_budget,
            budgets=manifest.budgets,
        )

    def routine_mode(self, name: RoutineName) -> PermissionMode | None:
        routines, _ = load_routines(self._instance, policy=self._gate_policy)
        return next((routine.mode for routine in routines if routine.name == name), None)

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

    async def run(self, turn: PreparedTurnRequest, context: TurnContext) -> TurnResult:
        return await self._closing_browser(turn.turn_id, self._run(turn, context))

    async def _run(self, turn: PreparedTurnRequest, context: TurnContext) -> TurnResult:
        await self._count_profiles(turn.model, context.emit)
        config, start_step = await self._start_config(turn)
        progress = _BudgetProgress(context.budgets, start_step)
        return await self._invoke(
            turn,
            context,
            ModelState(
                turn=turn,
                granted=self._granted_tools(turn),
                revision=self._routine_revision(turn),
                read_only=tuple(sorted(read_only_tools(self._instance.path))),
            ),
            config,
            progress,
        )

    async def restore(self, thread_id: UUID, turn_id: UUID) -> PreparedTurnRequest | None:
        config: RunnableConfig = {"configurable": {"thread_id": str(thread_id)}}
        async with self._graph() as (graph, _):
            state = await graph.aget_state(config)
        if not any(task.interrupts for task in state.tasks):
            return None
        values = dict(state.values)
        stored = values.get("turn")
        if not isinstance(stored, TurnRequest):
            raise InvalidParkedTurn("The parked turn checkpoint is invalid.")
        values["turn"] = self._prepare_restored_turn(stored)
        restored = _MODEL_STATE.validate_python(values).turn
        if restored.thread_id != thread_id or restored.turn_id != turn_id:
            return None
        return restored

    async def resume(
        self,
        turn: PreparedTurnRequest,
        answer: ApprovalAnswer,
        context: TurnContext,
    ) -> TurnResult:
        return await self._closing_browser(turn.turn_id, self._resume(turn, answer, context))

    async def _resume(
        self,
        turn: PreparedTurnRequest,
        answer: ApprovalAnswer,
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
        if answer.decision is ApprovalDecision.ALLOW_IN_ROUTINE:
            await self._grant_parked_tool(turn, context.emit)
        if answer.decision is ApprovalDecision.MARK_READ_ONLY:
            await self._mark_parked_tool_read_only(turn)
        return await self._invoke(
            turn,
            context,
            Command(resume=answer),
            _graph_config(turn),
            replace(progress, resumes=progress.resumes + 1),
        )

    async def _closing_browser(self, turn_id: UUID, running: Awaitable[TurnResult]) -> TurnResult:
        """Await the turn *running*, and close its browser once the turn ends. A park is not an
        end: the approved action, or the user's takeover, acts on the page the turn left."""
        try:
            result = await running
        except BaseException:
            await self._browser.close(turn_id)
            raise
        if not isinstance(result, ParkedTurn):
            await self._browser.close(turn_id)
        return result

    def _granted_tools(self, turn: PreparedTurnRequest) -> tuple[str, ...]:
        """The tools the routine's grants allow, when a routine started the turn."""
        if not isinstance(turn.origin, RoutineOrigin):
            return ()
        grants = RoutineGrants(self._instance.manifest.state_dir).of(turn.origin.name)
        return tuple(grant.tool for grant in grants)

    def _routine_revision(self, turn: PreparedTurnRequest) -> FileHash | None:
        if not isinstance(turn.origin, RoutineOrigin):
            return None
        return directory_hash(self._instance.path / routine_config_file(turn.origin.name))

    async def _grant_parked_tool(self, turn: PreparedTurnRequest, emit: Emit) -> None:
        """Grant the tool of the approval the turn is parked on to the routine that started it,
        unless the routine changed since the turn started."""
        pending = pending_approval(self._event_log.stored(turn.thread_id))
        if not isinstance(turn.origin, RoutineOrigin) or pending is None:
            raise GrantOutsideRoutine("Only a routine's parked turn grants a tool to the routine.")
        async with self._graph() as (graph, _):
            state = await graph.aget_state(_graph_config(turn))
        if state.values.get("revision") != self._routine_revision(turn):
            await emit(
                Warning(
                    sources=(routine_config_file(turn.origin.name),),
                    message=(
                        f'Routine "{turn.origin.name}" changed since this turn started, so '
                        f"{pending.request.name} was approved once and not allowed in it."
                    ),
                )
            )
            return
        RoutineGrants(self._instance.manifest.state_dir).grant(
            RoutineGrant(
                routine=turn.origin.name,
                tool=pending.request.name,
                granted_at=datetime.now(UTC),
                approval_id=pending.request.approval_id,
            )
        )

    async def _mark_parked_tool_read_only(self, turn: PreparedTurnRequest) -> None:
        """Mark the authored tool of the approval the turn is parked on read-only, as the user."""
        pending = pending_approval(self._event_log.stored(turn.thread_id))
        if pending is None or pending.request.rule != AUTHORED_RULE:
            raise ReadOnlyMarkRefused("Only an authored tool's approval marks it read-only.")
        await asyncio.to_thread(
            partial(set_read_only, read_only=True), self._instance, pending.request.name
        )

    async def _count_profiles(self, model: ModelName, emit: Emit) -> None:
        """Count each profile version a turn was prepared with, once."""
        tokenizer = self._tokenizer
        if tokenizer is None:
            return
        async with self._counting_profiles:
            counted = {
                event.payload.version
                for event in self._event_log.all_events()
                if isinstance(event.payload, ProfileCounted)
            }
            for version, text in list(self._uncounted_profiles.items()):
                if version not in counted:
                    try:
                        tokens = await asyncio.to_thread(tokenizer, model, text)
                    except Exception:
                        # Without a count its turns' memory tokens read as estimated, and the
                        # next turn retries.
                        logger.warning("The profile's tokens could not be counted.", exc_info=True)
                        continue
                    await emit(ProfileCounted(version=version, tokens=tokens))
                del self._uncounted_profiles[version]

    def _prepare_restored_turn(self, turn: TurnRequest) -> PreparedTurnRequest:
        if isinstance(turn, PreparedTurnRequest):
            return turn
        return turn.prepare(render_system_prompt(self._prompt_sections()))

    def _prompt_sections(self) -> tuple[PromptSection, ...]:
        skills, _ = load_skills(self._instance)
        return assemble_system_prompt(self._instance, skills, self._today())

    async def _invoke(
        self,
        turn: PreparedTurnRequest,
        context: TurnContext,
        graph_input: ModelState | Command,
        config: RunnableConfig,
        progress: _BudgetProgress,
    ) -> TurnResult:
        async with mcp_tools(self._instance) as picked:
            return await self._invoke_with(turn, context, graph_input, config, progress, picked)

    async def _invoke_with(
        self,
        turn: PreparedTurnRequest,
        context: TurnContext,
        graph_input: ModelState | Command,
        config: RunnableConfig,
        progress: _BudgetProgress,
        picked: tuple[Tool, ...],
    ) -> TurnResult:
        """Run the turn with the tools of the MCP servers the instance *picked*."""
        budgets = progress.budgets
        emit = context.emit
        skills, skill_warnings = load_skills(self._instance)
        discovered_tools, tool_warnings = self._tools.refresh()
        discovered_tools = ToolSnapshot((*discovered_tools.tools, *picked))
        tools, core_tool_warnings = discovered_tools.with_core(
            *core_tools(self._instance, skills, today=self._today)
        )
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
                            system_message=SystemMessage(content=turn.system_prompt),
                            gate_policy=self._gate_policy,
                            permission_mode=turn.permission_mode,
                            tools=tools,
                            tool_context=ToolContext(
                                instance=self._instance,
                                thread_id=turn.thread_id,
                                turn_id=turn.turn_id,
                                routine=(
                                    turn.origin.name
                                    if isinstance(turn.origin, RoutineOrigin)
                                    else None
                                ),
                                dreaming_stage=self._opened_with_dreaming_step(turn),
                                notification_source=self._notification_source(turn),
                                run_reporter=_run_reporter(emit),
                                recall_reporter=_recall_reporter(emit),
                                notification_reporter=emit,
                                view_reporter=emit,
                                browser=self._browser,
                            ),
                            user_message=HumanMessage(
                                content=render_wake(turn.origin, prepared.message, prepared.payload)
                            ),
                            budgets=budgets,
                        ),
                        # ADR 0011 keeps interrupted tool calls out of completed history.
                        durability="exit",
                    )
            interrupts = _INTERRUPTS.validate_python(graph_result.get("__interrupt__", ()))
            parked = interrupts[0].value if interrupts else None
        except TimeoutError as exc:
            if not seconds_timeout.expired() or budgets.seconds is None:
                raise
            raise BudgetExceeded("seconds", budgets.seconds) from exc
        except GraphRecursionError as exc:
            if budgets.steps is None:
                raise
            raise BudgetExceeded("steps", budgets.steps) from exc
        if isinstance(parked, TakeoverRequested):
            # The user drives the page on the hub. With no browser free, the turn parks all the
            # same, and the user's live view opens the page once one comes free.
            with suppress(Exception):
                await self._browser.open(turn.turn_id)
            await emit(parked)
            if raises_notifications():
                source = self._notification_source(turn) or UNTITLED
                await raise_notification(takeover_notification(source, parked), emit)
            return ParkedTurn()
        if interrupts:
            approval = interrupts[0].value
            if not isinstance(approval, ApprovalRequested):
                raise InvalidApprovalRequest("The graph returned an invalid approval request.")
            await emit(approval)
            if raises_notifications():
                source = self._notification_source(turn) or UNTITLED
                await raise_notification(
                    approval_notification(
                        source,
                        turn.turn_id,
                        approval,
                        in_routine=isinstance(turn.origin, RoutineOrigin),
                    ),
                    emit,
                )
            return ParkedTurn()
        result = _MODEL_STATE.validate_python(graph_result)
        return TurnOutcome(
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
        )

    async def _prepare_turn(
        self,
        turn: PreparedTurnRequest,
        graph_input: ModelState | Command,
        tools: ToolSnapshot,
        progress: _BudgetProgress,
        emit: Emit,
    ) -> _PreparedTurn | TurnOutcome:
        budgets = progress.budgets
        message = turn.message
        payload = None
        if not isinstance(turn.origin, UserOrigin):
            tools = ToolSnapshot(tuple(tool for tool in tools.tools if not tool.hub))
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
            message = routine.prompt
            delivery = self._delivery_for_turn(turn)
            declared = routine.code_step
            consolidate = (
                consolidate_tool(
                    GraphStore(self._instance.path),
                    self._event_log,
                    turn.thread_id,
                    turn.turn_id,
                    today=self._today,
                )
                if declared == CONSOLIDATION_STEP
                else None
            )
            if declared is None:
                code_step = None
            elif consolidate is not None:
                writes = ToolCall(
                    call_id=str(uuid4()), name=consolidate.name, arguments={}, write=True
                )
                unasked = evaluate(
                    self._gate_policy,
                    turn.permission_mode,
                    writes,
                    consolidate,
                    self._instance.manifest.workspace.path,
                )
                code_step = consolidation_step(
                    GraphStore(self._instance.path),
                    self._event_log,
                    today=self._today,
                    prune=unasked.action is GateAction.ALLOW,
                )
            elif declared == REFLECTION_STEP:
                code_step = reflection_step(
                    GraphStore(self._instance.path), self._event_log, routine.name
                )
            elif declared == LEARNING_REVIEW_STEP:
                code_step = learning_review_step(
                    GraphStore(self._instance.path), self._event_log, today=self._today
                )
                # The review only reports, so it is offered nothing that writes (ADR 0101).
                tools = ToolSnapshot(tuple(tool for tool in tools.tools if not tool.write))
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
            if consolidate is not None:
                tools = ToolSnapshot((*tools.tools, consolidate))
            if code_step is not None and isinstance(graph_input, ModelState):
                code_started = asyncio.get_running_loop().time()
                output = await self._run_code_step(
                    routine,
                    code_step,
                    turn.permission_mode,
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
        return _PreparedTurn(tools, progress, message, payload)

    def _opened_with_dreaming_step(self, turn: PreparedTurnRequest) -> bool:
        """Whether a routine turn's code step was a dreaming stage's. The log keeps the call
        from when the turn started, so a rewrite of the routine during the turn cannot change
        it."""
        if not isinstance(turn.origin, RoutineOrigin):
            return False
        call = next(
            (
                event.payload
                for event in self._event_log.stored(turn.thread_id)
                if event.turn_id == turn.turn_id and isinstance(event.payload, ToolCall)
            ),
            None,
        )
        return call is not None and SharedCodeStep(call.name) in DREAMING_STEPS

    def _notification_source(self, turn: PreparedTurnRequest) -> str | None:
        """Where a notification raised in the turn came from: its routine, its factory step, or
        its thread's title, when it has one."""
        match turn.origin:
            case RoutineOrigin(name=name):
                return name
            case FactoryRunOrigin(factory=factory, step=step):
                return f"{factory} · {step}"
        thread = ThreadStore(self._instance.manifest.state_dir).thread(turn.thread_id)
        return thread.title if thread is not None else None

    def _delivery_for_turn(self, turn: PreparedTurnRequest) -> Delivery | None:
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
        mode: PermissionMode,
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
            mode,
            call,
            code_step,
            self._instance.manifest.workspace.path,
            marked_read_only=code_step.name in read_only_tools(self._instance.path),
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
                context = ToolContext(
                    instance=self._instance,
                    thread_id=thread_id,
                    routine=routine.name,
                    run_reporter=_run_reporter(emit),
                    recall_reporter=_recall_reporter(emit),
                    view_reporter=emit,
                )
                output = await code_step.ainvoke_raw(call.arguments, context)
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

    async def _start_config(self, turn: PreparedTurnRequest) -> tuple[RunnableConfig, int]:
        async with self._graph() as (graph, checkpointer):
            async for state in graph.aget_state_history(_graph_config(turn)):
                interrupted = any(task.interrupts for task in state.tasks)
                if not state.next and not interrupted:
                    return state.config, _checkpoint_step(state.metadata) + 1
            await checkpointer.adelete_thread(str(turn.thread_id))
        return _graph_config(turn), -1

    async def _resume_budget_progress(
        self,
        turn: PreparedTurnRequest,
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
            # Two turns that set up a new checkpoint file at once fail with "database is locked".
            async with self._checkpoint_setup:
                await checkpointer.setup()
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
        started_at = asyncio.get_running_loop().time()
        request_messages = [runtime.context.system_message, *messages]
        async for chunk in runtime.context.model.astream(
            _mark_cache_prefix(state.turn.model, request_messages)
        ):
            response = chunk if response is None else response + chunk
            if chunk.text:
                await runtime.context.emit(MessageDelta(text=chunk.text))
        if response is None:
            raise ModelNoResponse("The model returned no response.")
        completed = completed_model_call(
            state.turn.model,
            response.usage_metadata,
            started_at=started_at,
            completed_at=asyncio.get_running_loop().time(),
        )
        await runtime.context.emit(completed)
        input_tokens = state.input_tokens + completed.input_tokens
        output_tokens = state.output_tokens + completed.output_tokens
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
            granted=state.granted,
            revision=state.revision,
            read_only=state.read_only,
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
        granted = list(state.granted)
        read_only = list(state.read_only)
        seen_urls = _seen_urls(state.messages)
        message_index = len(state.messages) - 1
        for call_index, model_call in enumerate(response.tool_calls):
            name = model_call["name"]
            call_id = model_call["id"] or str(
                uuid5(state.turn.turn_id, f"{message_index}:{call_index}")
            )
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
                granted=name in granted,
                marked_read_only=name in read_only,
                seen_urls=seen_urls,
            )
            approval_id = None
            if (
                selected is not None
                and name == browser_takeover.name
                and gate_decision.action is not GateAction.DENY
                and isinstance(reason := arguments.get("reason"), str)
            ):
                # The takeover asks the user itself, so it stands in for an approval. It comes
                # before any call of the message runs, and the turn waits as on an approval.
                interrupt(
                    TakeoverRequested(
                        takeover_id=uuid5(state.turn.turn_id, f"takeover:{call_id}"),
                        reason=reason,
                    )
                )
                gate = ToolGated(
                    call_id=call.call_id,
                    name=call.name,
                    action=GateOutcome.ALLOW,
                    rule=gate_decision.rule,
                    decided_by=(
                        GateDecider.USER
                        if gate_decision.action is GateAction.ASK
                        else GateDecider.POLICY
                    ),
                )
            elif gate_decision.action is GateAction.ASK:
                # The node runs again on resume, so the id must come out the same both times.
                approval_id = uuid5(state.turn.turn_id, f"approval:{call_id}")
                preview = (
                    selected.preview(arguments, runtime.context.tool_context.instance)
                    if selected is not None and selected.preview is not None
                    else None
                )
                answer: ApprovalAnswer = interrupt(
                    ApprovalRequested(
                        approval_id=approval_id,
                        name=name,
                        arguments=arguments,
                        rule=gate_decision.rule,
                        preview=preview,
                    )
                )
                approved = answer.decision is not ApprovalDecision.DENY
                if answer.decision is ApprovalDecision.ALLOW_IN_ROUTINE:
                    granted.append(name)
                if answer.decision is ApprovalDecision.MARK_READ_ONLY:
                    read_only.append(name)
                gate = ToolGated(
                    call_id=call.call_id,
                    name=call.name,
                    action=GateOutcome.ALLOW if approved else GateOutcome.DENY,
                    rule=gate_decision.rule,
                    decided_by=GateDecider.USER,
                    reason=answer.reason,
                )
            else:
                gate = ToolGated(
                    call_id=call.call_id,
                    name=call.name,
                    action=_gate_outcome(gate_decision.action),
                    rule=gate_decision.rule,
                    decided_by=GateDecider.POLICY,
                )
            calls.append(ToolCallResolution(call, gate, approval_id))

        messages: list[AnyMessage] = []
        for resolution in calls:
            call = resolution.call
            await runtime.context.emit(resolution.gate)
            if resolution.gate.action is GateOutcome.DENY:
                result = ToolResult(
                    call_id=call.call_id,
                    name=call.name,
                    output=_denial(resolution.gate),
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
                resolution.approval_id,
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
            granted=tuple(granted),
            revision=state.revision,
            read_only=tuple(read_only),
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
        approval_id: UUID | None,
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
            output = await selected.ainvoke(
                arguments, replace(runtime.context.tool_context, approval_id=approval_id)
            )
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


def _seen_urls(messages: Sequence[AnyMessage]) -> frozenset[str]:
    """The URLs the thread's user messages, routine prompts and web reads brought in (ADR 0110).

    Another tool's result can echo the arguments the model gave it, so it brings in none.
    """
    return frozenset(
        url.rstrip(".,;:!?")
        for message in messages
        if isinstance(message, HumanMessage)
        or (
            isinstance(message, ToolMessage)
            and message.name in _URL_SOURCES
            and message.status == "success"
        )
        for url in _URL.findall(message.text)
    )


def _elapsed_ms(started_at: float) -> int:
    return round((asyncio.get_running_loop().time() - started_at) * 1000)


def _run_reporter(emit: Emit) -> RunReporter:
    return lambda run: emit(RunDelegated(run=run))


def _recall_reporter(emit: Emit) -> RecallReporter:
    return lambda nodes: emit(MemoryRecalled(nodes=nodes))


def _denial(gate: ToolGated) -> str:
    """The tool result the model reads for a denied call, with the user's reason when given."""
    denied = f'Tool "{gate.name}" was denied by {gate_denial_source(gate)}'
    return f"{denied}: {gate.reason}" if gate.reason else f"{denied}."


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


def _graph_config(turn: PreparedTurnRequest) -> RunnableConfig:
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
