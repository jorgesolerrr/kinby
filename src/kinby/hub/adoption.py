"""The adoption preflight: what taking over an existing instance would mean, and what stops it.

The preflight reads. It opens no container, writes no file, and registers nothing, so an
operator can run it as often as they like. The handoff in ``service`` runs it again and
refuses on any blocking finding, because the previous manager may still be in charge.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid5

from kinby.contracts import (
    CONTRACT_VERSION,
    AdoptionFinding,
    AdoptionFindingKind,
    AdoptionHandoff,
    Capability,
    ContainerOwner,
    ControlToken,
    InstanceAdoptPreviewCommand,
    InstanceAdoptPreviewResult,
    StorageKind,
)
from kinby.hub.control import ControlEndpoint, ControlUnreachable, InstanceControl
from kinby.hub.models import ContainerDescription, ContainerRuntime
from kinby.hub.registry import HubRegistry
from kinby.instance import ManifestError, inspect_instance

#: Where an adopted instance's own directory lives inside its container.
INSTANCE_MOUNT = "/instance"
#: An adopted instance's hub id is derived from the host directory it owns, so the same
#: instance always previews as the same id and an interrupted handoff resumes on it.
ADOPTION_NAMESPACE = UUID("8f2f9b8e-4d6a-5f3b-9c2d-7a1e6b0c4d55")
#: The id of an instance whose storage the hub could not read. It owns nothing.
NO_INSTANCE = UUID(int=0)


def hub_instance_id(host_path: str) -> UUID:
    """The identity this hub gives the instance that owns *host_path*."""
    return uuid5(ADOPTION_NAMESPACE, os.path.normpath(host_path))


async def preflight(
    command: InstanceAdoptPreviewCommand,
    registry: HubRegistry,
    runtime: ContainerRuntime,
    control: InstanceControl,
) -> InstanceAdoptPreviewResult:
    """Preview adopting the instance at ``command.path`` that its named container runs."""
    described = await runtime.describe(command.runtime_id)
    if described is None:
        return _unseen(
            command,
            f'Container "{command.runtime_id}" was not found, so the hub read nothing about '
            "the instance it runs.",
        )
    observed = _Observed(
        Path(command.path).resolve(),
        described,
        await _probe(command, runtime, control),
    )
    return InstanceAdoptPreviewResult(
        instance_id=observed.instance_id,
        manifest_id=observed.manifest_id,
        persona_name=observed.persona_name,
        path=observed.path,
        runtime_id=described.runtime_id,
        image_id=described.image,
        owner=described.owner,
        owner_name=described.owner_name,
        storage=list(described.storage),
        capabilities=list(observed.probe.capabilities),
        handoff=_handoff(observed, registry),
        findings=[
            *_identity(observed),
            *_ownership(command, observed, registry),
            *_compatibility(command, observed),
        ],
    )


def blocker(preview: InstanceAdoptPreviewResult) -> AdoptionFinding | None:
    """The first finding that stops the handoff, if the preflight found one."""
    return next((finding for finding in preview.findings if finding.blocking), None)


@dataclass(frozen=True)
class _Probe:
    """What the running instance answered, and why the hub cannot drive its lifecycle."""

    capabilities: tuple[Capability, ...] = ()
    #: A container that is not running has no accepted work, so the handoff interrupts none.
    stopped: bool = False
    kind: AdoptionFindingKind | None = None
    detail: str = ""


class _Observed:
    """The instance directory, the container that runs it, and what it answered: read once."""

    def __init__(self, path: Path, described: ContainerDescription, probe: _Probe) -> None:
        self.path = path
        self.described = described
        self.probe = probe
        self.manifest_id, self.persona_name = _inspected(path)
        self.source = next(
            (
                item.source
                for item in described.storage
                if item.kind is StorageKind.BIND and item.destination == INSTANCE_MOUNT
            ),
            "",
        )
        self.instance_id = hub_instance_id(self.source) if self.source else NO_INSTANCE


def _inspected(path: Path) -> tuple[str, str | None]:
    """The identity in the directory. Metadata only: adoption reads no instance secret."""
    try:
        manifest = inspect_instance(path).manifest
    except ManifestError, OSError:
        return "", None
    return manifest.id, manifest.persona_name


async def _probe(
    command: InstanceAdoptPreviewCommand,
    runtime: ContainerRuntime,
    control: InstanceControl,
) -> _Probe:
    """Ask the running instance what it can do. Its health route needs no control token."""
    try:
        address = await runtime.address(command.runtime_id)
    except Exception as exc:
        return _unreachable(exc)
    if address is None:
        return _Probe(stopped=True)
    try:
        probed = await control.probe(ControlEndpoint(address=address, token=ControlToken("")))
    except ControlUnreachable as exc:
        return _unreachable(exc)
    if probed.contract_version != CONTRACT_VERSION:
        return _Probe(
            kind=AdoptionFindingKind.LEGACY_RUNTIME,
            detail=(
                f"The instance speaks contract version {probed.contract_version} and this hub "
                f"speaks {CONTRACT_VERSION}, so the handoff cannot drain it."
            ),
        )
    if Capability.DRAIN not in probed.capabilities:
        return _Probe(
            capabilities=tuple(probed.capabilities),
            kind=AdoptionFindingKind.LEGACY_RUNTIME,
            detail=(
                "This instance's runtime cannot drain, so the handoff would interrupt the work "
                "it has accepted."
            ),
        )
    return _Probe(capabilities=tuple(probed.capabilities))


def _unreachable(failure: Exception) -> _Probe:
    return _Probe(
        kind=AdoptionFindingKind.UNREACHABLE,
        detail=(
            f"{failure or type(failure).__name__} The hub cannot reach this runtime to drain it."
        ),
    )


def _identity(observed: _Observed) -> list[AdoptionFinding]:
    """The directory the hub was given has to be the data that container runs."""
    if not observed.manifest_id:
        return [
            _finding(
                AdoptionFindingKind.INVALID_INSTANCE,
                f"No instance could be read at {observed.path}.",
            )
        ]
    if not observed.source:
        return [
            _finding(
                AdoptionFindingKind.INVALID_INSTANCE,
                f'Container "{observed.described.runtime_id}" mounts no instance directory at '
                f"{INSTANCE_MOUNT}, so the hub cannot tell which data it owns.",
            )
        ]
    if not _same_directory(observed.path, observed.source):
        return [
            _finding(
                AdoptionFindingKind.INVALID_INSTANCE,
                f"Directory {observed.path} is not the instance mounted at {INSTANCE_MOUNT} on "
                f'container "{observed.described.runtime_id}".',
            )
        ]
    return []


def _same_directory(path: Path, host_source: str) -> bool:
    """Whether the hub-visible directory is the data that container mounts.

    When the Docker host path is not visible here, the hub cannot compare them
    and treats the operator's pairing as the correspondence.
    """
    source = Path(host_source)
    try:
        return path.samefile(source)
    except OSError:
        return not source.exists()


def _ownership(
    command: InstanceAdoptPreviewCommand,
    observed: _Observed,
    registry: HubRegistry,
) -> list[AdoptionFinding]:
    """Who manages the container now, and who already owns the storage it writes."""
    findings = []
    if observed.described.owner is not ContainerOwner.HUB:
        findings.append(
            _finding(
                AdoptionFindingKind.PREVIOUS_MANAGER,
                previous_manager(observed.described),
                blocking=not command.relinquished,
            )
        )
    existing = registry.instance(observed.instance_id)
    if existing is not None and existing.prepared:
        findings.append(
            _finding(
                AdoptionFindingKind.DUPLICATE_ADOPTION,
                f"This hub already manages the instance at {observed.source} as "
                f"{observed.instance_id}.",
            )
        )
    conflict = registry.conflicting_storage(observed.instance_id, observed.described.storage)
    if conflict is not None:
        owner = registry.instance(conflict.owner)
        retained = owner is None or not owner.active
        findings.append(
            _finding(
                AdoptionFindingKind.RETAINED_STORAGE
                if retained
                else AdoptionFindingKind.STORAGE_OWNED,
                f'Writable storage "{conflict.item.source}" is already recorded for instance '
                f"{conflict.owner}" + (", which this hub is not running." if retained else "."),
            )
        )
    held = registry.held_identity(
        observed.instance_id,
        observed.path,
        observed.described.runtime_id,
    )
    if held is not None:
        findings.append(_finding(AdoptionFindingKind.RETAINED_IDENTITY, held))
    namesake = next(
        (
            record.instance_id
            for record in registry.managed_instances()
            if record.manifest_id == observed.manifest_id
            and record.instance_id != observed.instance_id
        ),
        None,
    )
    if observed.manifest_id and namesake is not None:
        findings.append(
            _finding(
                AdoptionFindingKind.MANIFEST_ID_TAKEN,
                f'Instance {namesake} also has manifest id "{observed.manifest_id}". Adoption '
                "keeps both: the hub instance id is the identity that routes.",
                blocking=False,
            )
        )
    return findings


def previous_manager(described: ContainerDescription) -> str:
    """Who still manages a container the hub did not label, and what that manager would do."""
    match described.owner:
        case ContainerOwner.COMPOSE:
            return (
                f'Compose project "{described.owner_name}" still manages container '
                f'"{described.runtime_id}" and would recreate it.'
            )
        case ContainerOwner.OTHER_HUB:
            return f'Hub "{described.owner_name}" still manages container "{described.runtime_id}".'
        case _:
            return (
                f'Container "{described.runtime_id}" carries no manager label. Confirm that '
                "nothing else recreates or updates it before the hub takes it over."
            )


def _compatibility(
    command: InstanceAdoptPreviewCommand,
    observed: _Observed,
) -> list[AdoptionFinding]:
    """A runtime the hub cannot drain interrupts its accepted work when it goes down."""
    probe = observed.probe
    if probe.kind is None:
        return []
    return [
        _finding(
            probe.kind,
            f"{probe.detail} Upgrade the instance first, or acknowledge the interrupting stop.",
            blocking=not command.acknowledge_interrupting_stop,
        )
    ]


def _handoff(observed: _Observed, registry: HubRegistry) -> AdoptionHandoff:
    """What the operator does first, what the handoff costs, and what keeps the webhook URL."""
    alias = registry.signal_alias()
    container = observed.described.runtime_id
    return AdoptionHandoff(
        downtime=_downtime(observed.probe),
        steps=[
            "Stop the old updater so it no longer rebuilds this instance: take the "
            "docker/update.sh entry out of cron on the box.",
            f'Take container "{container}" out of Compose, so that `docker compose up` does '
            "not recreate it. The hub rewrites no Compose file.",
            "Put the container on the hub's private network, so the hub reaches its lifecycle "
            f"endpoint: `docker network connect kinby_private {container}`.",
            "Preview again, and adopt with relinquished once nothing blocks.",
        ],
        signals=(
            "The webhook URL registered before the hub existed keeps working: the hub answers "
            "/signals/<routine> and forwards the request bytes, signature included, to the "
            "instance that holds the signal alias. Adopt with claim_signals to move it here."
            + (f" Instance {alias} holds it now." if alias is not None else "")
        ),
    )


def _downtime(probe: _Probe) -> str:
    """What the operator gives up for the handoff, in the terms this runtime allows."""
    if probe.stopped:
        return (
            "The instance is already stopped, and stays unavailable until the replacement "
            "container has started."
        )
    if Capability.DRAIN in probe.capabilities:
        return (
            "The instance stops accepting work while the hub waits for its drain, and stays "
            "unavailable until the replacement container has started."
        )
    return (
        "The instance's running work is interrupted at once, and it stays unavailable until "
        "the replacement container has started."
    )


def _unseen(command: InstanceAdoptPreviewCommand, detail: str) -> InstanceAdoptPreviewResult:
    """A preview of an instance whose container the hub cannot see: identity, and one finding."""
    path = Path(command.path).resolve()
    manifest_id, persona_name = _inspected(path)
    return InstanceAdoptPreviewResult(
        instance_id=NO_INSTANCE,
        manifest_id=manifest_id,
        persona_name=persona_name,
        path=path,
        runtime_id=command.runtime_id,
        image_id="",
        owner=ContainerOwner.UNMANAGED,
        owner_name="",
        storage=[],
        capabilities=[],
        handoff=AdoptionHandoff(downtime="", steps=[], signals=""),
        findings=[_finding(AdoptionFindingKind.UNREACHABLE, detail)],
    )


def _finding(
    kind: AdoptionFindingKind,
    detail: str,
    *,
    blocking: bool = True,
) -> AdoptionFinding:
    return AdoptionFinding(kind=kind, blocking=blocking, detail=detail)
