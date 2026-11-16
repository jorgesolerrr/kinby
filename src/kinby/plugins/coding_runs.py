"""The delegation tool: an instance's turn hands a task to a coding client signed in on it, as a
coding run in the instance's workspace, and waits for the run to end."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from dataclasses import replace

from pydantic import JsonValue

from kinby.contracts import (
    CodingClient,
    CodingSessionId,
    DelegatedRun,
    DelegatedRunOutcome,
    RunDelegated,
    StepEnding,
)
from kinby.core.coding_clients import (
    CLIENT_TIMEOUT_SECONDS,
    CodingRun,
    own_tokens,
    run_coding_client,
    signed_in_clients,
)
from kinby.instance import Instance
from kinby.plugins.tools import Tool, ToolContext, tool


def coding_run_tools(instance: Instance) -> tuple[Tool, ...]:
    """The delegation tool, in an instance with a coding client installed and signed in."""
    if not signed_in_clients(instance.manifest.state_dir):
        return ()
    return (delegate_to_coding_client,)


@tool(write=True)
async def delegate_to_coding_client(
    task: str,
    context: ToolContext,
    client: CodingClient | None = None,
    session: str | None = None,
) -> str:
    """Hand a coding task to a coding client signed in here, such as Claude Code or Codex, and
    wait for it to end. It works unattended in your workspace and may change any file there.

    Use it for one-off coding work in this workspace. Work from an issue to a pull request
    belongs to a line. Name the client only when more than one is signed in. Pass the session of
    an earlier coding run in this thread to continue it, such as to repair what it did.

    Returns JSON: what the client said last, how the run ended (completed, failed, or limited
    until resets_at), and its session.
    """
    state_dir = context.instance.manifest.state_dir
    signed_in = signed_in_clients(state_dir)
    if client is None and len(signed_in) == 1:
        client = signed_in[0]
    if client not in signed_in:
        raise ValueError(f"Name one of the coding clients signed in here: {', '.join(signed_in)}.")
    ran = await run_coding_client(
        client,
        task,
        context.workspace,
        instance=context.instance.path,
        state_dir=state_dir,
        resume=None if session is None else CodingSessionId(session),
        timeout_seconds=CLIENT_TIMEOUT_SECONDS,
        model=None,
        effort=None,
    )
    ending = _ending(ran)
    returned: dict[str, JsonValue] = {
        "client": client,
        "ending": ending,
        "message": ran.summary,
        "session": ran.session,
    }
    if ran.reported is not None:
        earlier = () if session is None else await asyncio.to_thread(_thread_runs, context)
        own = own_tokens(ran.reported, earlier)
        await context.report_run(own.model_copy(update={"outcome": ending}))
        if ran.reported.resets_at is not None and ending is DelegatedRunOutcome.LIMITED:
            returned["resets_at"] = ran.reported.resets_at.isoformat()
    return json.dumps(returned, ensure_ascii=False)


def _thread_runs(context: ToolContext) -> list[DelegatedRun]:
    """The delegated runs the thread recorded, whose tokens a continued session counts again."""
    from kinby.core.events import EventLog

    with EventLog(context.instance.manifest.state_dir).reader() as reader:
        return [
            event.payload.run
            for event in reader.thread_events(context.thread_id)
            if isinstance(event.payload, RunDelegated)
        ]


def _ending(ran: CodingRun) -> DelegatedRunOutcome:
    """How the coding run ended. One killed at its timeout failed, whatever the client said."""
    reported = ran.reported
    if reported is None or (
        ran.ending is not StepEnding.CLEAN and reported.outcome is DelegatedRunOutcome.COMPLETED
    ):
        return DelegatedRunOutcome.FAILED
    return reported.outcome


def _preview(arguments: Mapping[str, JsonValue], instance: Instance) -> str:
    """The approval names the client the task goes to."""
    named = arguments.get("client")
    client = (
        named
        if isinstance(named, str)
        else " or ".join(signed_in_clients(instance.manifest.state_dir))
    )
    return f"{client} works on this task in the workspace, unattended:\n\n{arguments.get('task')}"


delegate_to_coding_client = replace(delegate_to_coding_client, preview=_preview)
