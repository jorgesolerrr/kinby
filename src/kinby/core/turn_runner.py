"""Run a model turn with LangGraph working state beneath the event stream."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, time
from functools import partial
from pathlib import Path
from typing import Annotated, Protocol, cast
from uuid import UUID, uuid4, uuid5

from langchain_core.exceptions import ContextOverflowError
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
from langgraph.types import Command, Interrupt, StateSnapshot, interrupt
from pydantic import JsonValue, TypeAdapter

from kinby.contracts import (
    ApprovalDecision,
    ApprovalRequested,
    BuiltInToolGroup,
    CallVia,
    CompletionOutcome,
    DeliveryArrival,
    Event,
    EventType,
    FactoryRunOrigin,
    FileHash,
    GateDecider,
    GateOutcome,
    McpServerName,
    MemoryRecalled,
    MessageDelta,
    ModelCompleted,
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
    ThreadCompacted,
    ThreadContextResult,
    ToolCall,
    ToolGated,
    ToolResult,
    ToolState,
    TurnStarted,
    UserOrigin,
    Warning,
    gate_denial_source,
    is_turn_closing,
)
from kinby.core.budgets import DAILY_COST_EVENTS, DailyBudget, daily_cost
from kinby.core.clock import utc_today
from kinby.core.context_breakdown import context_rows
from kinby.core.errors import (
    BudgetExceeded,
    CodeStepFailed,
    CodeStepNotFound,
    ContextOverflow,
    GrantOutsideRoutine,
    InvalidApprovalRequest,
    InvalidParkedTurn,
    ModelNoResponse,
    NothingToCompact,
    PermissionDenied,
    ReadOnlyMarkRefused,
    RoutineNotFound,
)
from kinby.core.events import EventLog
from kinby.core.gate import (
    AUTHORED_RULE,
    INVALID_ARGUMENTS,
    PREVIEW_REFUSED,
    URL_READS,
    GateDecision,
    asks_every_call,
    evaluate,
)
from kinby.core.model_calls import completed_model_call
from kinby.core.models import Tokenizer, init_model, is_anthropic_model
from kinby.core.pricing import SHIPPED_WINDOWS, price_map
from kinby.core.prompt import (
    PromptSection,
    PromptSectionName,
    assemble_system_prompt,
    profile_version,
    prompt_version,
    render_skill_references,
    render_system_prompt,
    render_wake,
    with_searchable_groups,
)
from kinby.core.saved_outputs import cut_to_cap, turn_outputs
from kinby.core.sent_history import (
    context_size,
    effective_trigger,
    kept_from,
    latest_compaction,
    latest_model_call,
    passes_window,
    sent_history,
    summary_request,
    turn_start,
    unanswered,
)
from kinby.core.session_recalls import SessionAttempt
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
from kinby.core.uploads import sent_uploads, user_message
from kinby.instance import (
    Budgets,
    Instance,
    Manifest,
    ModelName,
    ModelPrice,
    ToolStates,
    reload_manifest,
)
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
from kinby.instance.views import holds_page
from kinby.memory import GraphStore, MemoryDelivery, deliver_memory, memory_index
from kinby.memory.consolidation import consolidate_tool, consolidation_step
from kinby.memory.learning_review import learning_review_step
from kinby.memory.reflection import reflection_step
from kinby.plugins.browser import BrowserService
from kinby.plugins.browser_tools import browser_takeover, browser_tools
from kinby.plugins.core import built_in_states, by_state, core_tools
from kinby.plugins.errors import exception_message
from kinby.plugins.hub_browser import HubBrowser
from kinby.plugins.hub_memory import owner_memory_tools
from kinby.plugins.instance_tools import routine_config_file
from kinby.plugins.listed_tools import record_authored_tools
from kinby.plugins.mcp import McpTools, mcp_tool_states, mcp_tools
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
)
from kinby.plugins.skills import Skill, load_skills, referenced_skills, skill_tool
from kinby.plugins.tool_search import (
    CALL_TOOL,
    RUN_CODE,
    TOOL_SEARCH_NAMES,
    SearchableGroup,
    called,
    script_tools,
    tool_search,
    wrong_arguments,
)
from kinby.plugins.tools import (
    PreviewRefused,
    RecallReporter,
    RunReporter,
    ScriptCallFailed,
    Tool,
    ToolBroker,
    ToolContext,
)
from kinby.plugins.views import page_write, view_write

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
        ToolState,
        ToolStates,
        BuiltInToolGroup,
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
    #: Where a compaction inside the turn cut into it. Its summary stands in for the turn's
    #: messages before the cut, while the compaction is recorded at the turn's start: a turn that
    #: does not complete keeps none of its messages.
    cut: int | None = None
    #: The states of each MCP server's tools when the turn started, kept so a resume offers the
    #: tools the turn started with.
    tool_states: dict[McpServerName, ToolStates] = field(default_factory=dict)
    #: The states of each built-in group's tools when the turn started, kept for the same reason.
    built_in_states: dict[BuiltInToolGroup, ToolStates] = field(default_factory=dict)


_MODEL_STATE = TypeAdapter(ModelState)
_INTERRUPTS = TypeAdapter(tuple[Interrupt, ...])


@dataclass(frozen=True)
class ModelContext:
    emit: Emit
    model: ChatModel
    system_message: SystemMessage
    gate_policy: GatePolicy
    permission_mode: PermissionMode
    #: Whether the user started the turn, rather than a routine, a factory run or the home
    #: instance.
    user_started: bool
    #: Whether the turn keeps the instance's own views. A factory run's go to the owner of its
    #: line, whose view the hub compares a page write with (ADR 0131).
    own_views: bool
    tools: ToolSnapshot
    #: The tools a call through tool search or from a script reaches: the loaded ones but tool
    #: search's own, and the searchable ones.
    reachable: ToolSnapshot
    #: The reachable tools a script calls, by Python name.
    scripted: Mapping[str, Tool]
    tool_context: ToolContext
    user_message: HumanMessage
    budgets: Budgets
    #: The thread's latest compaction, which the model is sent in place of the older messages.
    compaction: ThreadCompacted | None
    #: The model's window in tokens, when it is known.
    window: int | None

    def tool(self, call: ToolCall) -> Tool | None:
        """The tool *call* calls: one the model was sent, or through tool search or from a script
        any reachable one."""
        tools = self.tools if call.via is None else self.reachable
        return tools.get(call.name)


@dataclass(frozen=True)
class ToolCallResolution:
    call: ToolCall
    gate: ToolGated
    #: The approval the user gave the call, which a hub tool shows the hub.
    approval_id: UUID | None
    #: What the tool's check found wrong with the arguments, which the model reads instead.
    refusal: str | None = None
    #: The result the call already has: a refused preview's is recorded as it is decided.
    made: ToolResult | None = None


@dataclass(frozen=True)
class GateAnswer:
    """The gate's answer to one tool call, before any approval is raised for it."""

    decision: GateDecision
    #: What the tool's check found wrong with the arguments, which denies the call before the
    #: gate could ask for it. The model reads it instead of a denial.
    refusal: str | None = None


@dataclass
class _ToolStep:
    """The tool calls of one model message: what the gate reads for them, the routine grants
    and read-only marks their approvals add, and the URLs its scripts' web reads bring in."""

    turn: PreparedTurnRequest
    context: ModelContext
    granted: list[str]
    read_only: list[str]
    seen_urls: set[str]
    saved_outputs: Path

    def call(self, call_id: str, name: str, arguments: dict[str, JsonValue]) -> ToolCall:
        """A call to the tool *name*, which writes when that tool does or when the turn has no
        tool by that name. A call to call_tool is a call to the tool it names, when the turn can
        reach it."""
        if name == CALL_TOOL and not isinstance(
            found := called(arguments, self.context.reachable), str
        ):
            tool, passed = found
            return ToolCall(
                call_id=call_id,
                name=tool.name,
                arguments=passed,
                write=tool.write,
                via=CallVia.TOOL_SEARCH,
            )
        tool = self.context.tools.get(name)
        return ToolCall(
            call_id=call_id,
            name=name,
            arguments=arguments,
            write=tool.write if tool is not None else True,
        )

    def gate(self, call: ToolCall) -> GateAnswer:
        """The gate's answer to *call*, without raising an approval when it asks. A call
        through tool search or from a script is refused first when its tool's schema refuses its
        arguments, as the model may never have been sent that schema."""
        tool = self.context.tool(call)
        refusal = None
        if tool is not None and call.via is not None:
            refusal = wrong_arguments(tool, call.arguments)
        if refusal is None and tool is not None and tool.check is not None:
            refusal = tool.check(call.arguments)
        if refusal is not None:
            return GateAnswer(GateDecision(GateAction.DENY, INVALID_ARGUMENTS), refusal)
        return GateAnswer(
            evaluate(
                self.context.gate_policy,
                self.context.permission_mode,
                call,
                tool,
                self.context.tool_context.workspace,
                granted=call.name in self.granted,
                marked_read_only=call.name in self.read_only,
                user_started=self.context.user_started,
                same_page=self.context.own_views
                and call.name == view_write.name
                and holds_page(
                    self.context.tool_context.instance,
                    call.arguments.get("id"),
                    call.arguments.get("page"),
                ),
                seen_urls=self.seen_urls,
                saved_outputs=self.saved_outputs,
            )
        )


@dataclass(frozen=True)
class _StartedStates:
    """The tool states a turn started with: each MCP server's, and each built-in group's."""

    mcp: Mapping[McpServerName, ToolStates]
    built_in: Mapping[BuiltInToolGroup, ToolStates]


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
class _TurnTools:
    """The tools a turn offers: those the model is sent, and the searchable ones by group."""

    loaded: ToolSnapshot
    searchable: tuple[SearchableGroup, ...]

    @property
    def search_set(self) -> ToolSnapshot:
        return ToolSnapshot(tuple(tool for group in self.searchable for tool in group.tools))

    def kept(self, keep: Callable[[Tool], bool]) -> _TurnTools:
        """The tools *keep* keeps, loaded or searchable."""
        return _TurnTools(
            ToolSnapshot(tuple(filter(keep, self.loaded.tools))),
            tuple(
                replace(group, tools=found)
                for group in self.searchable
                if (found := tuple(filter(keep, group.tools)))
            ),
        )


@dataclass(frozen=True)
class _PreparedTurn:
    tools: _TurnTools
    progress: _BudgetProgress
    message: str
    payload: str | None


_EPHEMERAL = {"type": "ephemeral"}


def init_chat_model(model: ModelName) -> ChatModel:
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
        model_factory: ModelFactory = init_chat_model,
        model_override: str | None = None,
        gate_policy: GatePolicy | None = None,
        today: Callable[[], date] = utc_today,
        tokenizer: Tokenizer | None = None,
        browser: BrowserService | None = None,
        memory_delivery: MemoryDelivery = MemoryDelivery.NONE,
        workbench_skills: tuple[Skill, ...] | None = None,
        owner_memory: SessionAttempt | None = None,
    ) -> None:
        """Count each profile version with *tokenizer*. Without one, the profile goes uncounted.
        The browser tools reach *browser*, or else the hub's browser service. Each turn gets its
        memory by *memory_delivery* before the agent searches.

        With *workbench_skills* the turn runs on a workbench, where no instance's files hold its
        manifest or its skills: *instance* is never read again, its skills are those, and the
        skill tool and view_write are its only core tools. The views it writes are handed to the
        run's owner, so it reads, patches and deletes none. With *owner_memory* too, it searches
        and opens, read-only, the memory of its run's line owner at that step attempt.
        """
        self._instance = instance
        self._workbench_skills = workbench_skills
        self._owner_memory = owner_memory
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
        self._memory_delivery = memory_delivery
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

    def prepare_for_turn(self, routine: RoutineName | None = None) -> TurnPreparation:
        """Prepare a turn, on *routine*'s model when it is a routine's turn."""
        manifest = self._manifest()
        if manifest.tools != self._instance.manifest.tools:
            self._tools = ToolRegistry(self._instance.path, tools=manifest.tools)
        self._instance = replace(self._instance, manifest=manifest)
        self._gate_policy = self._load_gate_policy()
        skills, _ = self._skills()
        sections = self._prompt_sections(skills)
        found = None if routine is None else self._routine(routine)
        model = manifest.models.main if found is None else found.turn_model(manifest.models)
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
            today = self._today()
            daily_budget = DailyBudget(
                limit=limit,
                cost=daily_cost(self._closed_on(today), prices, today),
                unpriced_model=UnpricedModel(model) if model not in prices else None,
            )
        return TurnPreparation(
            model=model,
            default_mode=self._gate_policy.mode,
            ceiling=self._gate_policy.ceiling,
            prompt_version=prompt_version(sections),
            system_prompt=render_system_prompt(sections),
            profile_version=version,
            daily_budget=daily_budget,
            budgets=manifest.budgets,
            skills=frozenset(skill.name for skill in skills),
            workspace=manifest.workspace.path,
        )

    def _manifest(self) -> Manifest:
        if self._workbench_skills is not None:
            return self._instance.manifest
        return reload_manifest(self._instance, model_override=self._model_override)

    def _skills(self) -> tuple[tuple[Skill, ...], tuple[Warning, ...]]:
        if self._workbench_skills is not None:
            return self._workbench_skills, ()
        return load_skills(self._instance)

    def _core_tools(self, skills: tuple[Skill, ...]) -> tuple[Tool, ...]:
        if self._workbench_skills is not None:
            memory = () if self._owner_memory is None else owner_memory_tools(self._owner_memory)
            return (skill_tool(skills), view_write, *browser_tools(self._instance), *memory)
        return core_tools(self._instance, skills, today=self._today)

    def routine_mode(self, name: RoutineName) -> PermissionMode | None:
        routine = self._routine(name)
        return None if routine is None else routine.mode

    def _routine(self, name: RoutineName) -> Routine | None:
        routines, _ = load_routines(self._instance, policy=self._gate_policy)
        return next((routine for routine in routines if routine.name == name), None)

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
        state = ModelState(
            turn=turn,
            granted=self._granted_tools(turn),
            revision=self._routine_revision(turn),
            read_only=tuple(sorted(read_only_tools(self._instance.path))),
            tool_states=mcp_tool_states(self._instance),
            built_in_states=built_in_states(self._instance.manifest.tools),
        )
        started = _StartedStates(state.tool_states, state.built_in_states)
        return await self._invoke(turn, context, state, config, progress, started)

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
            await self._started_states(turn),
        )

    async def _started_states(self, turn: PreparedTurnRequest) -> _StartedStates:
        async with self._graph() as (graph, _):
            state = await graph.aget_state(_graph_config(turn))
        return _StartedStates(
            state.values.get("tool_states", {}), state.values.get("built_in_states", {})
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

    def _closed_on(self, day: date) -> list[Event]:
        """The events daily_cost folds for the turns closed on *day*: those stored since the day
        began, after the start of each turn that began before it and the compactions it made
        before the day began."""
        since = datetime.combine(day, time(), UTC)
        events = self._event_log.events_of(DAILY_COST_EVENTS, since=since)
        started = {event.turn_id for event in events if isinstance(event.payload, TurnStarted)}
        earlier = [
            event
            for closing in events
            if is_turn_closing(closing.payload) and closing.turn_id not in started
            for event in self._event_log.turn_events(closing.turn_id)
            if event.timestamp < since and isinstance(event.payload, TurnStarted | ThreadCompacted)
        ]
        return [*earlier, *events]

    def _granted_tools(self, turn: PreparedTurnRequest) -> tuple[str, ...]:
        """The tools the routine's grants allow, when a routine started the turn."""
        if not isinstance(turn.origin, RoutineOrigin):
            return ()
        grants = RoutineGrants(self._instance.manifest.state_dir).of(turn.origin.name)
        return tuple(grant.tool for grant in grants)

    def _unasked_script_tools(
        self, turn: PreparedTurnRequest, tools: _TurnTools
    ) -> dict[str, Tool]:
        """The tools of *tools* a script calls, by Python name, but those the gate asks about on
        every call, as a script cannot ask for an approval."""
        granted = self._granted_tools(turn)
        read_only = read_only_tools(self._instance.path)
        return {
            name: tool
            for name, tool in script_tools(
                ToolSnapshot((*tools.loaded.tools, *tools.search_set.tools))
            ).items()
            if not asks_every_call(
                self._gate_policy,
                turn.permission_mode,
                tool,
                self._instance.manifest.workspace.path,
                granted=tool.name in granted,
                marked_read_only=tool.name in read_only,
                user_started=isinstance(turn.origin, UserOrigin),
            )
        }

    def _routine_revision(self, turn: PreparedTurnRequest) -> FileHash | None:
        if not isinstance(turn.origin, RoutineOrigin):
            return None
        return directory_hash(self._instance.path / routine_config_file(turn.origin.name))

    async def _grant_parked_tool(self, turn: PreparedTurnRequest, emit: Emit) -> None:
        """Grant the tool of the approval the turn is parked on to the routine that started it,
        unless the routine changed since the turn started."""
        pending = pending_approval(self._event_log.turn_events(turn.turn_id))
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
        pending = pending_approval(self._event_log.turn_events(turn.turn_id))
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
                for event in self._event_log.events_of({EventType.PROFILE_COUNTED})
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
        skills, _ = self._skills()
        return turn.prepare(render_system_prompt(self._prompt_sections(skills)))

    def _prompt_sections(self, skills: tuple[Skill, ...]) -> tuple[PromptSection, ...]:
        index = (
            memory_index(GraphStore(self._instance.path))
            if self._memory_delivery is MemoryDelivery.INDEX
            else None
        )
        return assemble_system_prompt(
            self._instance,
            skills,
            self._today(),
            index,
            owner_memory=self._owner_memory is not None,
        )

    async def _invoke(
        self,
        turn: PreparedTurnRequest,
        context: TurnContext,
        graph_input: ModelState | Command,
        config: RunnableConfig,
        progress: _BudgetProgress,
        started: _StartedStates,
    ) -> TurnResult:
        """Run the turn with the tools in the states it *started* with."""
        async with mcp_tools(self._instance, started.mcp) as picked:
            return await self._invoke_with(
                turn, context, graph_input, config, progress, picked, started.built_in
            )

    async def _invoke_with(
        self,
        turn: PreparedTurnRequest,
        context: TurnContext,
        graph_input: ModelState | Command,
        config: RunnableConfig,
        progress: _BudgetProgress,
        picked: McpTools,
        groups: Mapping[BuiltInToolGroup, ToolStates],
    ) -> TurnResult:
        """Run the turn with the tools of the MCP servers the instance *picked*, and the others
        in the states of their built-in *groups*."""
        budgets = progress.budgets
        emit = context.emit
        skills, skill_warnings = self._skills()
        discovered_tools, tool_warnings = self._tools.refresh()
        record_authored_tools(
            self._instance.manifest.state_dir,
            (tool for tool in discovered_tools.tools if tool.authored),
        )
        discovered_tools = ToolSnapshot((*discovered_tools.tools, *picked.loaded))
        tools, core_tool_warnings = discovered_tools.with_core(*self._core_tools(skills))
        loaded, searchable = by_state(tools, groups)
        for warning in (*tool_warnings, *core_tool_warnings, *skill_warnings):
            await emit(warning)
        prepared = await self._prepare_turn(
            turn,
            graph_input,
            _TurnTools(loaded, (*picked.searchable, *searchable)),
            progress,
            emit,
            context.delivery_body,
        )
        if isinstance(prepared, TurnOutcome):
            return prepared
        scripted = self._unasked_script_tools(turn, prepared.tools)
        turn_tools = (
            prepared.tools if scripted else prepared.tools.kept(lambda tool: tool.name != RUN_CODE)
        )
        tools, reachable = await _with_tool_search(turn_tools, emit)
        progress = prepared.progress
        budgets = progress.budgets
        window = {**SHIPPED_WINDOWS, **self._manifest().windows}.get(turn.model)
        if isinstance(graph_input, ModelState):
            compacting = asyncio.timeout(progress.seconds_remaining)
            compaction_started = asyncio.get_running_loop().time()
            try:
                async with compacting:
                    await self._compact_over_trigger(turn, window, emit)
            except TimeoutError as exc:
                if not compacting.expired() or budgets.seconds is None:
                    raise
                raise BudgetExceeded("seconds", budgets.seconds) from exc
            progress = replace(
                progress,
                seconds_used=progress.seconds_used
                + asyncio.get_running_loop().time()
                - compaction_started,
            )
        wake = render_wake(turn.origin, prepared.message, prepared.payload)
        if isinstance(graph_input, ModelState):
            wake = render_skill_references(referenced_skills(skills, turn.skills), wake)
            wake = await self._pre_searched(wake, prepared.message, emit)
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
                            system_message=SystemMessage(
                                content=with_searchable_groups(
                                    turn.system_prompt, prepared.tools.searchable
                                )
                            ),
                            gate_policy=self._gate_policy,
                            permission_mode=turn.permission_mode,
                            user_started=isinstance(turn.origin, UserOrigin),
                            own_views=not isinstance(turn.origin, FactoryRunOrigin),
                            tools=tools,
                            reachable=reachable,
                            scripted=scripted,
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
                            user_message=user_message(wake, turn.uploads),
                            budgets=budgets,
                            compaction=latest_compaction(
                                self._event_log.thread_events(turn.thread_id)
                            ),
                            window=window,
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
        except ContextOverflowError as exc:
            results = [
                event.payload
                for event in self._event_log.turn_events(turn.turn_id)
                if isinstance(event.payload, ToolResult)
            ]
            raise ContextOverflow(results) from exc
        await self.prune_checkpoints(turn.thread_id)
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
            price=self._price(turn.model),
        )

    async def _pre_searched(self, wake: str, task: str, emit: Emit) -> str:
        """*wake*, followed under pre-search by the facts a memory search with *task* finds. The
        agent reads their bodies, so their recall is recorded."""
        if self._memory_delivery is not MemoryDelivery.PRE_SEARCH:
            return wake
        found = deliver_memory(self._memory_delivery, GraphStore(self._instance.path), task)
        if found is None:
            return wake
        await emit(MemoryRecalled(nodes=found.recalled))
        return f"{wake}\n\n{found.text}"

    async def _prepare_turn(
        self,
        turn: PreparedTurnRequest,
        graph_input: ModelState | Command,
        tools: _TurnTools,
        progress: _BudgetProgress,
        emit: Emit,
        delivery_body: str | None,
    ) -> _PreparedTurn | TurnOutcome:
        """The turn's *tools*, message and payload once its routine's code step, if any, ran. A
        code step may name a loaded or a searchable tool, and is offered as neither."""
        budgets = progress.budgets
        message = turn.message
        payload = None
        if not isinstance(turn.origin, UserOrigin):
            tools = tools.kept(lambda tool: not tool.hub)
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
            arrival = self._delivery_for_turn(turn)
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
                tools = tools.kept(lambda tool: not tool.write)
            else:
                try:
                    code_step = resolve_code_step(
                        declared, ToolSnapshot((*tools.loaded.tools, *tools.search_set.tools))
                    )
                except ValueError as exc:
                    raise CodeStepNotFound(
                        f'Code step tool "{declared.name}" is not available in this turn.'
                    ) from exc
            if code_step is not None:
                step_name = code_step.name
                tools = tools.kept(lambda tool: tool.name != step_name)
            if consolidate is not None:
                tools = replace(tools, loaded=ToolSnapshot((*tools.loaded.tools, consolidate)))
            if code_step is not None and isinstance(graph_input, ModelState):
                code_started = asyncio.get_running_loop().time()
                output = await self._run_code_step(
                    routine,
                    code_step,
                    turn.permission_mode,
                    turn.origin,
                    turn.thread_id,
                    None if arrival is None or delivery_body is None else (arrival, delivery_body),
                    emit,
                    budgets,
                    granted=code_step.name in graph_input.granted,
                )
                progress = replace(
                    progress,
                    seconds_used=progress.seconds_used
                    + asyncio.get_running_loop().time()
                    - code_started,
                )
                if output is None:
                    return TurnOutcome(
                        outcome=CompletionOutcome.NO_WORK, price=self._price(turn.model)
                    )
                payload = output
            elif delivery_body is not None:
                payload = delivery_body
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
                for event in self._event_log.turn_events(turn.turn_id)
                if isinstance(event.payload, ToolCall)
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

    def _delivery_for_turn(self, turn: PreparedTurnRequest) -> DeliveryArrival | None:
        return next(
            (
                event.payload.delivery
                for event in self._event_log.turn_events(turn.turn_id)
                if isinstance(event.payload, SignalReceived)
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
        delivery: tuple[DeliveryArrival, str] | None,
        emit: Emit,
        budgets: Budgets,
        *,
        granted: bool,
    ) -> str | None:
        arguments = dict(routine.arguments)
        recorded = dict(arguments)
        if delivery is not None:
            arrival, body = delivery
            raw_signal = arrival.model_dump(
                mode="json", include={"headers", "content_type", "delivery_id", "received_at"}
            )
            raw_signal["body"] = body
            media_type = arrival.content_type.partition(";")[0].strip().lower()
            if media_type == "application/json":
                try:
                    raw_signal["body"] = json.loads(body)
                except json.JSONDecodeError as exc:
                    raise CodeStepFailed(f"The signal body is not valid JSON: {exc}") from exc
            arguments["signal"] = _TOOL_ARGUMENTS.validate_python(raw_signal)
            # The transcript store keeps the delivery's arrival, never its body (ADR 0117).
            recorded["signal"] = arrival.reference
        elif routine.signal is not None and origin.trigger is not RoutineTrigger.SIGNAL:
            arguments["signal"] = recorded["signal"] = {}
        call = ToolCall(
            call_id=str(uuid4()),
            name=code_step.name,
            arguments=recorded,
            write=code_step.write,
        )
        await emit(call)
        decision = evaluate(
            self._gate_policy,
            mode,
            call,
            code_step,
            self._instance.manifest.workspace.path,
            granted=granted,
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
            if decision.action is GateAction.ASK:
                message += " Grant it from the routine's page to let it run."
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
                output = await code_step.ainvoke_raw(arguments, context)
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

    async def compact(self, thread_id: UUID, focus: str | None) -> Event:
        """Compact the thread with the main model's summary. The summary call and the compaction
        are recorded under the thread's latest turn."""
        model = self._manifest().models.main
        completed, compaction = await self._summarize_thread(thread_id, model, focus)
        turn_id = next(
            event.turn_id
            for event in reversed(self._event_log.thread_events(thread_id))
            if isinstance(event.payload, TurnStarted)
        )
        await self._event_log.append(thread_id, turn_id, completed)
        return await self._event_log.append(thread_id, turn_id, compaction)

    async def context_breakdown(self, thread_id: UUID) -> ThreadContextResult:
        """What fills the thread's context size: the system prompt and the tool list a turn
        started now would send, and the thread's messages as its latest model call was sent
        them. Only the checkpoint of a turn that ran to its end holds those messages, so a
        latest call in a turn that failed or stopped gives its size and no rows."""
        events = self._event_log.thread_events(thread_id)
        latest = latest_model_call(events)
        if latest is None:
            return ThreadContextResult(context_size=None, rows=[])
        turn_id, call = latest
        size = call.input_tokens
        compaction = latest_compaction(events)
        async with self._graph() as (graph, _):
            completed = await _newest_completed(graph, _thread_config(thread_id))
        if completed is None or completed.values["turn"].turn_id != turn_id:
            return ThreadContextResult(context_size=size, rows=[])
        # The turn ends on the latest call's answer, which that call was not sent.
        messages: list[AnyMessage] = completed.values["messages"][:-1]
        skills, _ = self._skills()
        discovered, _ = self._tools.refresh()
        tools, _ = discovered.with_core(*self._core_tools(skills))
        tools, _ = by_state(tools, built_in_states(self._instance.manifest.tools))
        async with mcp_tools(self._instance, {}) as picked:
            rows = context_rows(
                size,
                self._prompt_sections(skills),
                tools.tools,
                picked.loaded,
                messages,
                compaction,
                completed.values.get("cut"),
            )
        return ThreadContextResult(context_size=size, rows=rows)

    async def _compact_over_trigger(
        self, turn: PreparedTurnRequest, window: int | None, emit: Emit
    ) -> None:
        """Compact the turn's thread once, before the turn's first model call, when its context
        size is past the effective compaction trigger."""
        trigger = effective_trigger(self._instance.manifest.compaction.trigger, window)
        size = context_size(self._event_log.thread_events(turn.thread_id))
        if trigger is None or size is None or size <= trigger:
            return
        compaction = await self._summary_in_turn(
            emit, self._summarize_thread(turn.thread_id, turn.model, None)
        )
        if compaction is not None:
            await emit(compaction)

    async def _compact_running(
        self,
        turn: PreparedTurnRequest,
        messages: Sequence[AnyMessage],
        compaction: ThreadCompacted | None,
        cut: int | None,
        emit: Emit,
    ) -> tuple[ThreadCompacted, int | None] | None:
        """Compact the running turn's thread over *messages*, which its next model call sends
        under *compaction* and *cut*. The messages the model has not answered yet stay whole.
        Return the compaction and where it cut into the turn, or None when the thread did not
        compact."""
        start = cut if cut is not None else 0 if compaction is None else compaction.boundary
        running = turn_start(messages)
        boundary = min(kept_from(messages, start, running), unanswered(messages))
        made = await self._summary_in_turn(
            emit,
            self._summarize(turn.thread_id, turn.model, compaction, messages, start, boundary),
        )
        if made is None:
            return None
        if made.boundary <= running:
            await emit(made)
            return made, None
        recorded = made.model_copy(update={"boundary": running})
        await emit(recorded)
        return recorded, made.boundary

    async def _summary_in_turn(
        self, emit: Emit, summarizing: Awaitable[tuple[ModelCompleted, ThreadCompacted]]
    ) -> ThreadCompacted | None:
        """The compaction *summarizing* makes for a turn, or None when there is nothing to
        compact. A failed summary call is recorded on the thread, and the turn runs uncompacted.
        The summary call is recorded only in the compaction, which carries its tokens: as a model
        call of the turn it would count in the turn's own totals."""
        try:
            _, compaction = await summarizing
        except NothingToCompact:
            return None
        except Exception as exc:
            logger.warning("The compaction failed.", exc_info=True)
            await emit(
                Warning(
                    sources=("compaction",),
                    message=(
                        "The thread could not be compacted, so the turn runs uncompacted: "
                        f"{type(exc).__name__}: {exc}"
                    ),
                )
            )
            return None
        return compaction

    async def _summarize_thread(
        self, thread_id: UUID, model: ModelName, focus: str | None
    ) -> tuple[ModelCompleted, ThreadCompacted]:
        """*model*'s summary call over the thread's turns since its last compaction, but for the
        newest turns a compaction keeps, and the compaction it makes."""
        async with self._graph() as (graph, _):
            completed = await _newest_completed(graph, _thread_config(thread_id))
        messages: list[AnyMessage] = [] if completed is None else completed.values["messages"]
        previous = latest_compaction(self._event_log.thread_events(thread_id))
        start = 0 if previous is None else previous.boundary
        boundary = kept_from(messages, start)
        return await self._summarize(thread_id, model, previous, messages, start, boundary, focus)

    async def _summarize(
        self,
        thread_id: UUID,
        model: ModelName,
        previous: ThreadCompacted | None,
        messages: Sequence[AnyMessage],
        start: int,
        boundary: int,
        focus: str | None = None,
    ) -> tuple[ModelCompleted, ThreadCompacted]:
        """*model*'s summary call over *messages* from *start*, where the *previous* compaction
        left off, to *boundary*, and the compaction it makes."""
        if boundary <= start:
            raise NothingToCompact(
                f'Thread "{thread_id}" has nothing to compact: its turns since its last '
                "compaction fit in what a compaction keeps."
            )
        request = summary_request(
            None if previous is None else previous.summary, messages[start:boundary], focus
        )
        started_at = asyncio.get_running_loop().time()
        response: AIMessageChunk | None = None
        async for chunk in self._model_factory(model).astream([request]):
            response = chunk if response is None else response + chunk
        if response is None or not response.text.strip():
            raise ModelNoResponse("The model returned no summary.")
        completed = completed_model_call(
            model,
            response.usage_metadata,
            started_at=started_at,
            completed_at=asyncio.get_running_loop().time(),
        )
        return completed, ThreadCompacted(
            summary=response.text,
            boundary=boundary,
            input_tokens=completed.input_tokens,
            output_tokens=completed.output_tokens,
            cache_read_tokens=completed.cache_read_tokens,
            cache_creation_tokens=completed.cache_creation_tokens,
            model=model,
        )

    def _price(self, model: ModelName) -> ModelPrice | None:
        return price_map(self._instance.manifest.prices).get(model)

    async def prune_checkpoints(self, thread_id: UUID) -> None:
        """Delete the thread's checkpoints older than its newest completed one. A newer one,
        such as a turn parked on an approval or a takeover, stays."""
        async with self._graph() as (graph, checkpointer):
            completed = await _newest_completed(graph, _thread_config(thread_id))
            if completed is None:
                return
            older = (str(thread_id), completed.config["configurable"]["checkpoint_id"])
            async with checkpointer.lock, checkpointer.conn.cursor() as cursor:
                await cursor.execute(
                    "DELETE FROM checkpoints WHERE thread_id = ? AND checkpoint_id < ?", older
                )
                await cursor.execute(
                    "DELETE FROM writes WHERE thread_id = ? AND checkpoint_id < ?", older
                )
                await checkpointer.conn.commit()

    async def _start_config(self, turn: PreparedTurnRequest) -> tuple[RunnableConfig, int]:
        async with self._graph() as (graph, checkpointer):
            completed = await _newest_completed(graph, _graph_config(turn))
            if completed is not None:
                return completed.config, _checkpoint_step(completed.metadata) + 1
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
        context = runtime.context
        # Keep the user message atomic with the call so failures cannot checkpoint it.
        following_tool_call = bool(state.messages) and isinstance(state.messages[-1], ToolMessage)
        messages = (
            state.messages if following_tool_call else [*state.messages, context.user_message]
        )
        events = self._event_log.turn_events(state.turn.turn_id)
        compaction = next(
            (
                event.payload
                for event in reversed(events)
                if isinstance(event.payload, ThreadCompacted)
            ),
            context.compaction,
        )
        cut = state.cut
        compacting = passes_window(context.window, events, messages)
        if compacting:
            compacted = await self._compact_running(
                state.turn, messages, compaction, cut, context.emit
            )
            if compacted is not None:
                compaction, cut = compacted
        try:
            response, completed = await self._send(state, context, messages, compaction, cut)
        except ContextOverflowError:
            # The provider's overflow compacts once, and is the only guard for a model with no
            # known window.
            if compacting:
                raise
            compacted = await self._compact_running(
                state.turn, messages, compaction, cut, context.emit
            )
            if compacted is None:
                raise
            compaction, cut = compacted
            response, completed = await self._send(state, context, messages, compaction, cut)
        await context.emit(completed)
        input_tokens = state.input_tokens + completed.input_tokens
        output_tokens = state.output_tokens + completed.output_tokens
        token_budget = context.budgets.tokens
        if token_budget is not None and input_tokens + output_tokens > token_budget:
            raise BudgetExceeded("tokens", token_budget)
        returned_messages: list[AnyMessage] = [response]
        if not following_tool_call:
            returned_messages.insert(0, context.user_message)
        return ModelState(
            turn=state.turn,
            messages=returned_messages,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            granted=state.granted,
            revision=state.revision,
            read_only=state.read_only,
            cut=cut,
            tool_states=state.tool_states,
            built_in_states=state.built_in_states,
        )

    async def _send(
        self,
        state: ModelState,
        context: ModelContext,
        messages: Sequence[AnyMessage],
        compaction: ThreadCompacted | None,
        cut: int | None,
    ) -> tuple[AIMessageChunk, ModelCompleted]:
        """Send the model *messages* under *compaction* and *cut*, streaming its text."""
        response: AIMessageChunk | None = None
        started_at = asyncio.get_running_loop().time()
        sent = sent_history(messages, compaction, cut)
        workspace = self._instance.manifest.workspace.path
        request_messages = [
            context.system_message,
            *await asyncio.to_thread(sent_uploads, sent, workspace),
        ]
        async for chunk in context.model.astream(
            _mark_cache_prefix(state.turn.model, request_messages)
        ):
            response = chunk if response is None else response + chunk
            if chunk.text:
                await context.emit(MessageDelta(text=chunk.text))
        if response is None:
            raise ModelNoResponse("The model returned no response.")
        return response, completed_model_call(
            state.turn.model,
            response.usage_metadata,
            started_at=started_at,
            completed_at=asyncio.get_running_loop().time(),
        )

    async def _call_tools(
        self,
        state: ModelState,
        runtime: Runtime[ModelContext],
    ) -> ModelState:
        response = state.messages[-1]
        if not isinstance(response, AIMessage):
            raise ModelNoResponse("The model returned an invalid tool call response.")
        step = _ToolStep(
            turn=state.turn,
            context=runtime.context,
            granted=list(state.granted),
            read_only=list(state.read_only),
            seen_urls=_seen_urls(state.messages),
            saved_outputs=turn_outputs(self._instance.manifest.state_dir, state.turn.turn_id),
        )
        message_index = len(state.messages) - 1
        calls: list[ToolCallResolution] = []
        for call_index, model_call in enumerate(response.tool_calls):
            call_id = model_call["id"] or str(
                uuid5(state.turn.turn_id, f"{message_index}:{call_index}")
            )
            call = step.call(
                call_id, model_call["name"], _TOOL_ARGUMENTS.validate_python(model_call["args"])
            )
            calls.append(await self._decide(step, call))
        # An approval runs the step again from its start, so no call runs before all are decided.
        messages: list[AnyMessage] = []
        for resolution in calls:
            result = await self._make(step, resolution)
            messages.append(
                ToolMessage(
                    content=result.output,
                    name=result.name,
                    tool_call_id=result.call_id,
                    status="error" if result.error else "success",
                )
            )
        return ModelState(
            turn=state.turn,
            messages=messages,
            input_tokens=state.input_tokens,
            output_tokens=state.output_tokens,
            granted=tuple(step.granted),
            revision=state.revision,
            read_only=tuple(step.read_only),
            cut=state.cut,
            tool_states=state.tool_states,
            built_in_states=state.built_in_states,
        )

    async def _decide(self, step: _ToolStep, call: ToolCall) -> ToolCallResolution:
        """Decide a call the model made directly, raising the approval or the takeover the gate's
        answer asks for. A resume runs this again, so its ids come out the same both times."""
        await self._emit_tool_call_once(step.turn, call, step.context.emit)
        tool = step.context.tool(call)
        answer = step.gate(call)
        decision = answer.decision
        approval_id = None
        if (
            tool is not None
            and call.name == browser_takeover.name
            and decision.action is not GateAction.DENY
            and isinstance(reason := call.arguments.get("reason"), str)
        ):
            # The takeover asks the user itself, so it stands in for an approval. It comes
            # before any call of the message runs, and the turn waits as on an approval.
            interrupt(
                TakeoverRequested(
                    takeover_id=uuid5(step.turn.turn_id, f"takeover:{call.call_id}"),
                    reason=reason,
                )
            )
            gate = ToolGated(
                call_id=call.call_id,
                name=call.name,
                action=GateOutcome.ALLOW,
                rule=decision.rule,
                decided_by=(
                    GateDecider.USER if decision.action is GateAction.ASK else GateDecider.POLICY
                ),
            )
        elif decision.action is GateAction.ASK:
            approval_id = uuid5(step.turn.turn_id, f"approval:{call.call_id}")
            if (refused := self._refused_preview(step.turn, call)) is not None:
                return refused
            try:
                preview = await self._preview(step, call, approval_id)
            except PreviewRefused as exc:
                gate = ToolGated(
                    call_id=call.call_id,
                    name=call.name,
                    action=GateOutcome.DENY,
                    rule=PREVIEW_REFUSED,
                    decided_by=GateDecider.POLICY,
                )
                result = ToolResult(
                    call_id=call.call_id, name=call.name, output=str(exc), error=True
                )
                # A later call of the message may park the turn. Its resume hands the answers
                # to the step's approvals by position, so it must find this call refused.
                await step.context.emit(gate)
                await step.context.emit(result)
                return ToolCallResolution(call, gate, None, made=result)
            approval: ApprovalAnswer = interrupt(
                ApprovalRequested(
                    approval_id=approval_id,
                    name=call.name,
                    arguments=call.arguments,
                    rule=decision.rule,
                    preview=preview,
                    page_write=page_write(
                        call.name, call.arguments, step.context.tool_context.instance
                    ),
                )
            )
            if approval.decision is ApprovalDecision.ALLOW_IN_ROUTINE:
                step.granted.append(call.name)
            if approval.decision is ApprovalDecision.MARK_READ_ONLY:
                step.read_only.append(call.name)
            gate = ToolGated(
                call_id=call.call_id,
                name=call.name,
                action=(
                    GateOutcome.DENY
                    if approval.decision is ApprovalDecision.DENY
                    else GateOutcome.ALLOW
                ),
                rule=decision.rule,
                decided_by=GateDecider.USER,
                reason=approval.reason,
            )
        else:
            gate = ToolGated(
                call_id=call.call_id,
                name=call.name,
                action=_gate_outcome(decision.action),
                rule=decision.rule,
                decided_by=GateDecider.POLICY,
            )
        return ToolCallResolution(call, gate, approval_id, answer.refusal)

    def _refused_preview(
        self, turn: PreparedTurnRequest, call: ToolCall
    ) -> ToolCallResolution | None:
        """The resolution of *call* when an earlier run of the step refused its preview."""
        events = self._event_log.turn_events(turn.turn_id)
        gate = next(
            (
                payload
                for event in events
                if isinstance(payload := event.payload, ToolGated)
                and payload.call_id == call.call_id
                and payload.rule == PREVIEW_REFUSED
            ),
            None,
        )
        result = next(
            (
                payload
                for event in events
                if isinstance(payload := event.payload, ToolResult)
                and payload.call_id == call.call_id
            ),
            None,
        )
        if gate is None or result is None:
            return None
        return ToolCallResolution(call, gate, None, made=result)

    async def _preview(self, step: _ToolStep, call: ToolCall, approval_id: UUID) -> str | None:
        """What the approval of *call* shows. Raises PreviewRefused when its tool's preview
        refuses the call. An approval the turn already raised shows what it showed: a resume hands
        the answers to the step's approvals by position, so it must raise each of them again."""
        raised = next(
            (
                payload
                for event in self._event_log.turn_events(step.turn.turn_id)
                if isinstance(payload := event.payload, ApprovalRequested)
                and payload.approval_id == approval_id
            ),
            None,
        )
        if raised is not None:
            return raised.preview
        tool = step.context.tool(call)
        if tool is None or tool.preview is None:
            return None
        return await tool.preview(call.arguments, step.context.tool_context.instance)

    async def _make(self, step: _ToolStep, resolution: ToolCallResolution) -> ToolResult:
        """Make a decided call as its tool: run it when allowed, cut its result to the cap, and
        emit its gate decision and its result. A call made as it was decided keeps its result."""
        call = resolution.call
        if resolution.made is not None:
            self._forget_tool_call(step.turn, call)
            return resolution.made
        await step.context.emit(resolution.gate)
        if resolution.gate.action is GateOutcome.DENY:
            result = ToolResult(
                call_id=call.call_id,
                name=call.name,
                output=resolution.refusal or _denial(resolution.gate),
                error=True,
            )
        else:
            broker = ToolBroker(step.context.scripted, partial(self._call_from_script, step, call))
            result = cut_to_cap(
                await self._run_tool(
                    call,
                    step.context,
                    replace(
                        step.context.tool_context,
                        approval_id=resolution.approval_id,
                        broker=broker,
                    ),
                ),
                step.saved_outputs,
            )
        await step.context.emit(result)
        self._forget_tool_call(step.turn, call)
        return result

    async def _call_from_script(
        self, step: _ToolStep, script: ToolCall, tool: Tool, arguments: Mapping[str, JsonValue]
    ) -> str:
        """Make the call to *tool* that the script of the run_code call *script* makes, as a call
        the model made, but raise no approval: a call the gate would ask about fails, for the
        model to make directly. The script gets the tool's whole output, and the call's result
        event the output cut to the cap."""
        call = ToolCall(
            call_id=str(uuid4()),
            name=tool.name,
            arguments=dict(arguments),
            write=tool.write,
            via=CallVia.SCRIPT,
            script_call_id=script.call_id,
        )
        await step.context.emit(call)
        answer = step.gate(call)
        action = answer.decision.action
        gate = ToolGated(
            call_id=call.call_id,
            name=call.name,
            action=GateOutcome.ASK if action is GateAction.ASK else _gate_outcome(action),
            rule=answer.decision.rule,
            decided_by=GateDecider.POLICY,
        )
        await step.context.emit(gate)
        if action is GateAction.ALLOW:
            result = await self._run_tool(call, step.context, step.context.tool_context)
        else:
            result = ToolResult(
                call_id=call.call_id,
                name=call.name,
                output=answer.refusal
                or (
                    f'Tool "{call.name}" needs an approval, which a script cannot ask for. '
                    "Call it directly."
                    if action is GateAction.ASK
                    else _denial(gate)
                ),
                error=True,
            )
        await step.context.emit(cut_to_cap(result, step.saved_outputs))
        if result.error:
            raise ScriptCallFailed(result.output)
        if call.name in _URL_SOURCES:
            step.seen_urls.update(_urls(result.output))
        return result.output

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
            for event in self._event_log.turn_events(turn.turn_id)
            if isinstance(payload := event.payload, ToolCall)
        )

    def _forget_tool_call(self, turn: TurnRequest, call: ToolCall) -> None:
        self._emitted_tool_calls.discard((turn.thread_id, turn.turn_id, call.call_id))

    async def _run_tool(
        self, call: ToolCall, context: ModelContext, tool_context: ToolContext
    ) -> ToolResult:
        selected = context.tool(call)
        if selected is None:
            return ToolResult(
                call_id=call.call_id,
                name=call.name,
                output=f'Tool "{call.name}" is not available in this turn.',
                error=True,
            )
        started_at = asyncio.get_running_loop().time()
        try:
            # A tool is user code. Turn its failures into model-visible tool errors.
            output = await selected.ainvoke(call.arguments, tool_context)
        except Exception as exc:
            return ToolResult(
                call_id=call.call_id,
                name=call.name,
                output=exception_message(exc),
                error=True,
                duration_ms=_elapsed_ms(started_at),
            )
        return ToolResult(
            call_id=call.call_id,
            name=call.name,
            output=output,
            error=False,
            duration_ms=_elapsed_ms(started_at),
        )


async def _with_tool_search(tools: _TurnTools, emit: Emit) -> tuple[ToolSnapshot, ToolSnapshot]:
    """The tools the model is sent, and those a call through tool search reaches. A turn with a
    searchable tool is sent tool search's own tools too, and any other tool by their names is
    left out of both, with a warning."""
    kept = tools.kept(lambda tool: tool.name not in TOOL_SEARCH_NAMES)
    search_set = kept.search_set
    if not search_set.tools:
        return tools.loaded, ToolSnapshot((*tools.loaded.tools, *tools.search_set.tools))
    reachable = ToolSnapshot((*kept.loaded.tools, *search_set.tools))
    searching = tool_search(search_set, reachable)
    for namesake in (*tools.loaded.tools, *tools.search_set.tools):
        if namesake.name in TOOL_SEARCH_NAMES:
            await emit(
                Warning(
                    sources=(str(namesake.source),),
                    message=f'Tool "{namesake.name}" was replaced by tool search\'s own.',
                )
            )
    return ToolSnapshot((*kept.loaded.tools, *searching)), reachable


def _seen_urls(messages: Sequence[AnyMessage]) -> set[str]:
    """The URLs the thread's user messages, routine prompts and web reads brought in (ADR 0110).

    Another tool's result can echo the arguments the model gave it, so it brings in none.
    """
    return {
        url
        for message in messages
        if isinstance(message, HumanMessage)
        or (
            isinstance(message, ToolMessage)
            and message.name in _URL_SOURCES
            and message.status == "success"
        )
        for url in _urls(message.text)
    }


def _urls(text: str) -> set[str]:
    return {url.rstrip(".,;:!?") for url in _URL.findall(text)}


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
    return _thread_config(turn.thread_id)


def _thread_config(thread_id: UUID) -> RunnableConfig:
    return {"configurable": {"thread_id": str(thread_id)}}


async def _newest_completed(
    graph: CompiledStateGraph[ModelState, ModelContext, ModelState, ModelState],
    config: RunnableConfig,
) -> StateSnapshot | None:
    """The thread's newest checkpoint of a turn that ran to its end, which the next turn
    starts from."""
    async for state in graph.aget_state_history(config):
        if not state.next and not any(task.interrupts for task in state.tasks):
            return state
    return None


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
