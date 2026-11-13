"""A workbench's runtime: the server its container runs for the hub, which runs the steps of
factory runs. It has no persona, threads or memory."""

from __future__ import annotations

import subprocess
from pathlib import Path

from aiohttp import web

from kinby.contracts import (
    CONTRACT_VERSION,
    INSTANCE_DRAIN,
    INSTANCE_PROBE,
    INTAKE_RUN,
    RUN_FOLDER_REMOVE,
    RUN_TRANSCRIPT_READ,
    STEP_RUN,
    ControlToken,
    DrainState,
    InstanceDrainCommand,
    InstanceDrainResult,
    InstanceProbeCommand,
    InstanceProbeResult,
)
from kinby.core.contract_server import ContractServer
from kinby.core.routing import Dispatcher, instance_capabilities
from kinby.instance import Serve
from kinby.workbench.steps import FACTORIES_MOUNT, WorkbenchSteps


def prepare_workspace(workspace: Path, source: str | None) -> None:
    """Clone *source* into *workspace* when it is empty, as at a workbench's first start. A
    workspace that holds anything stays as it is, so a recreation keeps the clone and what was
    installed beside it. With no source the workspace is a plain folder."""
    workspace.mkdir(parents=True, exist_ok=True)
    if source is not None and not any(workspace.iterdir()):
        subprocess.run(["git", "clone", "--quiet", source, str(workspace)], check=True)


def _dispatcher(steps: WorkbenchSteps) -> Dispatcher:
    """The hub's probe, a drain with no accepted work to wait for, the steps of runs, the
    transcripts of their agent steps, the removal of their folders, and the intakes of lines."""
    dispatcher = Dispatcher()

    async def probe(command: InstanceProbeCommand) -> InstanceProbeResult:
        return InstanceProbeResult(
            contract_version=CONTRACT_VERSION, capabilities=instance_capabilities(dispatcher)
        )

    async def drain(command: InstanceDrainCommand) -> InstanceDrainResult:
        return InstanceDrainResult(state=DrainState.DRAINED)

    dispatcher.register(INSTANCE_PROBE, probe)
    dispatcher.register(INSTANCE_DRAIN, drain)
    dispatcher.register(STEP_RUN, steps.run)
    dispatcher.register(RUN_FOLDER_REMOVE, steps.remove_folder)
    dispatcher.register(RUN_TRANSCRIPT_READ, steps.transcript)
    dispatcher.register(INTAKE_RUN, steps.intake)
    return dispatcher


class WorkbenchServer:
    """Serve the hub's health probe at ``/health``, and its control socket with *token*, which
    runs *steps*."""

    def __init__(self, listen: Serve, token: ControlToken, steps: WorkbenchSteps) -> None:
        self._listen = listen
        self._contract = ContractServer(_dispatcher(steps), token)
        self._runner: web.AppRunner | None = None

    async def start(self) -> Serve:
        """Start listening, and answer where: a port of 0 takes a free one."""
        application = web.Application()
        self._contract.add_routes(application)
        application.router.add_get("/health", self._health, allow_head=False)
        runner = web.AppRunner(application)
        await runner.setup()
        site = web.TCPSite(runner, self._listen.host, self._listen.port)
        await site.start()
        self._runner = runner
        return Serve(self._listen.host, site.port)

    async def stop(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None

    async def _health(self, _request: web.Request) -> web.Response:
        return web.json_response(
            {
                "contract_version": CONTRACT_VERSION,
                "capabilities": [capability.value for capability in self._contract.capabilities],
            }
        )


__all__ = ["FACTORIES_MOUNT", "WorkbenchServer", "WorkbenchSteps", "prepare_workspace"]
