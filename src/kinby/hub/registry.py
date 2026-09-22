"""SQLite ownership records for the single-user hub."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

from kinby.contracts import (
    InstanceSummary,
    IntendedState,
    OperationGetResult,
    OperationKind,
    OperationState,
    OperationStep,
    PackageSelection,
    PackageSummary,
    StorageItem,
    StorageKind,
)
from kinby.hub.models import ImageArtifact


@dataclass(frozen=True)
class ManagedInstance:
    instance_id: UUID
    path: Path
    manifest_id: str
    persona_name: str | None
    requested_revision: str
    source_revision: str | None
    image_id: str | None
    intended_state: IntendedState
    runtime_id: str
    prepared: bool
    storage: tuple[StorageItem, ...]
    package: PackageSelection | None = None


class HubRegistry:
    """One durable owner of hub management state."""

    def __init__(self, directory: Path) -> None:
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / "registry.sqlite"
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS instances (
                    id TEXT PRIMARY KEY,
                    path TEXT NOT NULL UNIQUE,
                    manifest_id TEXT NOT NULL,
                    persona_name TEXT,
                    requested_revision TEXT NOT NULL,
                    source_revision TEXT,
                    image_id TEXT,
                    intended_state TEXT NOT NULL,
                    runtime_id TEXT NOT NULL UNIQUE,
                    prepared INTEGER NOT NULL DEFAULT 0,
                    package_id TEXT,
                    package_distribution TEXT,
                    package_version TEXT,
                    package_image_recipe TEXT
                );
                CREATE TABLE IF NOT EXISTS storage (
                    instance_id TEXT NOT NULL REFERENCES instances(id),
                    kind TEXT NOT NULL,
                    source TEXT NOT NULL,
                    destination TEXT NOT NULL,
                    writable INTEGER NOT NULL,
                    PRIMARY KEY (instance_id, destination)
                );
                CREATE TABLE IF NOT EXISTS operations (
                    id TEXT PRIMARY KEY,
                    instance_id TEXT NOT NULL REFERENCES instances(id),
                    kind TEXT NOT NULL,
                    state TEXT NOT NULL,
                    detail TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS operation_steps (
                    operation_id TEXT NOT NULL REFERENCES operations(id),
                    position INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    state TEXT NOT NULL,
                    detail TEXT NOT NULL,
                    PRIMARY KEY (operation_id, name)
                );
                CREATE TABLE IF NOT EXISTS image_artifacts (
                    input_key TEXT PRIMARY KEY,
                    image_id TEXT NOT NULL,
                    revision TEXT NOT NULL,
                    dependency_id TEXT NOT NULL,
                    base_images TEXT NOT NULL,
                    dependencies TEXT NOT NULL,
                    package_selection TEXT
                );
                CREATE TABLE IF NOT EXISTS hub_metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    token_hash TEXT PRIMARY KEY
                );
                """
            )
            self._add_columns(
                connection,
                "instances",
                {
                    "package_id": "TEXT",
                    "package_distribution": "TEXT",
                    "package_version": "TEXT",
                    "package_image_recipe": "TEXT",
                },
            )
            self._add_columns(
                connection,
                "image_artifacts",
                {"package_selection": "TEXT"},
            )

    @staticmethod
    def _add_columns(
        connection: sqlite3.Connection,
        table: str,
        columns: dict[str, str],
    ) -> None:
        existing = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
        for name, declaration in columns.items():
            if name not in existing:
                connection.execute(f"ALTER TABLE {table} ADD COLUMN {name} {declaration}")

    def hub_id(self) -> str:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT value FROM hub_metadata WHERE key = 'hub_id'"
            ).fetchone()
            if row is not None:
                return row[0]
            identifier = str(uuid4())
            connection.execute(
                "INSERT INTO hub_metadata (key, value) VALUES ('hub_id', ?)",
                (identifier,),
            )
            return identifier

    def signal_alias(self) -> UUID | None:
        """The instance that answers the public signal path, when adoption has claimed it."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT value FROM hub_metadata WHERE key = 'signal_alias'"
            ).fetchone()
        return UUID(row[0]) if row is not None else None

    def set_signal_alias(self, instance_id: UUID) -> None:
        """Point the public signal path at a managed instance, so its webhook URL keeps working."""
        if self.instance(instance_id) is None:
            raise ValueError(f'Instance "{instance_id}" was not found.')
        with self._connect() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO hub_metadata (key, value) VALUES ('signal_alias', ?)",
                (str(instance_id),),
            )

    def access_token_hash(self) -> str | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT value FROM hub_metadata WHERE key = 'access_token_hash'"
            ).fetchone()
        return row[0] if row is not None else None

    def try_set_access_token_hash(self, token_hash: str) -> bool:
        """Store the first hash. A second issuer leaves the existing one in place."""
        try:
            with self._connect() as connection:
                connection.execute(
                    "INSERT INTO hub_metadata (key, value) VALUES ('access_token_hash', ?)",
                    (token_hash,),
                )
        except sqlite3.IntegrityError:
            return False
        return True

    def replace_access_token_hash(self, token_hash: str) -> None:
        """Swap the hash and drop every session in one transaction."""
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO hub_metadata (key, value)
                VALUES ('access_token_hash', ?)
                """,
                (token_hash,),
            )
            connection.execute("DELETE FROM sessions")

    def open_session(self, token_hash: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO sessions (token_hash) VALUES (?)",
                (token_hash,),
            )

    def open_session_if_current(self, token_hash: str, session_hash: str) -> bool:
        """Insert the session only if this token hash is still the stored one."""
        with self._connect() as connection:
            inserted = connection.execute(
                """
                INSERT INTO sessions (token_hash)
                SELECT ? WHERE EXISTS (
                    SELECT 1 FROM hub_metadata
                    WHERE key = 'access_token_hash' AND value = ?
                )
                """,
                (session_hash, token_hash),
            )
            return inserted.rowcount == 1

    def session_open(self, token_hash: str) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM sessions WHERE token_hash = ?",
                (token_hash,),
            ).fetchone()
        return row is not None

    def begin_create(
        self,
        instance: ManagedInstance,
        operation_id: UUID,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO instances (
                    id, path, manifest_id, persona_name, requested_revision,
                    intended_state, runtime_id, prepared, package_id,
                    package_distribution, package_version, package_image_recipe
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?)
                """,
                (
                    str(instance.instance_id),
                    str(instance.path),
                    instance.manifest_id,
                    instance.persona_name,
                    instance.requested_revision,
                    instance.intended_state.value,
                    instance.runtime_id,
                    instance.package.id if instance.package is not None else None,
                    instance.package.distribution if instance.package is not None else None,
                    instance.package.version if instance.package is not None else None,
                    instance.package.image_recipe if instance.package is not None else None,
                ),
            )
            self._insert_operation(
                connection,
                operation_id,
                instance.instance_id,
                OperationKind.CREATE,
                "Creation queued.",
            )

    def record_operation(
        self,
        operation_id: UUID,
        instance_id: UUID,
        kind: OperationKind,
        detail: str,
    ) -> None:
        """Open this operation on its own, even while another one is unfinished."""
        with self._connect() as connection:
            self._insert_operation(connection, operation_id, instance_id, kind, detail)

    @staticmethod
    def _insert_operation(
        connection: sqlite3.Connection,
        operation_id: UUID,
        instance_id: UUID,
        kind: OperationKind,
        detail: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO operations (id, instance_id, kind, state, detail)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                str(operation_id),
                str(instance_id),
                kind.value,
                OperationState.PENDING.value,
                detail,
            ),
        )

    def fail_interrupted_operations(self, detail: str) -> None:
        """Fail operations the previous process left unfinished.

        A start that is still pending after a restart would otherwise be returned
        forever, and nothing would run it.
        """
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT id FROM operations WHERE state IN (?, ?)",
                (OperationState.PENDING.value, OperationState.RUNNING.value),
            ).fetchall()
        for (operation_id,) in rows:
            self.finish_operation(UUID(operation_id), OperationState.FAILED, detail)

    def begin_operation(
        self,
        operation_id: UUID,
        instance_id: UUID,
        kind: OperationKind,
        detail: str,
    ) -> UUID:
        """Open this operation, or return the unfinished one of the same kind.

        A second start while the first is still running would hide the first from
        `instance.status`, and that is how a client finds a response it lost.
        """
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                """
                SELECT id FROM operations
                WHERE instance_id = ? AND kind = ? AND state IN (?, ?)
                ORDER BY rowid
                LIMIT 1
                """,
                (
                    str(instance_id),
                    kind.value,
                    OperationState.PENDING.value,
                    OperationState.RUNNING.value,
                ),
            ).fetchone()
            if existing is not None:
                return UUID(existing[0])
            self._insert_operation(connection, operation_id, instance_id, kind, detail)
        return operation_id

    def advance_operation(self, operation_id: UUID, step: str, detail: str) -> None:
        """Succeed the step that was running and open the named one, both operation and step."""
        with self._connect() as connection:
            self._close_running_step(connection, operation_id, OperationState.SUCCEEDED, None)
            position = connection.execute(
                "SELECT COUNT(*) FROM operation_steps WHERE operation_id = ?",
                (str(operation_id),),
            ).fetchone()[0]
            connection.execute(
                """
                INSERT INTO operation_steps (operation_id, position, name, state, detail)
                VALUES (?, ?, ?, ?, ?)
                """,
                (str(operation_id), position, step, OperationState.RUNNING.value, detail),
            )
            connection.execute(
                "UPDATE operations SET state = ?, detail = ? WHERE id = ?",
                (OperationState.RUNNING.value, detail, str(operation_id)),
            )

    def finish_operation(
        self,
        operation_id: UUID,
        state: OperationState,
        detail: str,
    ) -> None:
        """Record the outcome on the operation and on the step it stopped in."""
        with self._connect() as connection:
            self._close_running_step(connection, operation_id, state, detail)
            connection.execute(
                "UPDATE operations SET state = ?, detail = ? WHERE id = ?",
                (state.value, detail, str(operation_id)),
            )

    @staticmethod
    def _close_running_step(
        connection: sqlite3.Connection,
        operation_id: UUID,
        state: OperationState,
        detail: str | None,
    ) -> None:
        """A step that ends without its own outcome keeps the detail it was opened with."""
        connection.execute(
            """
            UPDATE operation_steps SET state = ?, detail = COALESCE(?, detail)
            WHERE operation_id = ? AND state = ?
            """,
            (state.value, detail, str(operation_id), OperationState.RUNNING.value),
        )

    def active_operation(self, instance_id: UUID) -> UUID | None:
        """The earliest lifecycle operation this instance has not finished.

        A client that lost a response finds that operation here. A later operation
        does not take its place while this one is still running.
        """
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT id FROM operations
                WHERE instance_id = ? AND state IN (?, ?)
                ORDER BY rowid LIMIT 1
                """,
                (
                    str(instance_id),
                    OperationState.PENDING.value,
                    OperationState.RUNNING.value,
                ),
            ).fetchone()
        return UUID(row[0]) if row is not None else None

    def operation(self, operation_id: UUID) -> OperationGetResult | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT id, instance_id, kind, state, detail FROM operations WHERE id = ?",
                (str(operation_id),),
            ).fetchone()
            steps = connection.execute(
                """
                SELECT name, state, detail FROM operation_steps
                WHERE operation_id = ? ORDER BY position
                """,
                (str(operation_id),),
            ).fetchall()
        if row is None:
            return None
        return OperationGetResult(
            operation_id=UUID(row[0]),
            instance_id=UUID(row[1]),
            kind=OperationKind(row[2]),
            state=OperationState(row[3]),
            detail=row[4],
            steps=[
                OperationStep(name=name, state=OperationState(state), detail=detail)
                for name, state, detail in steps
            ],
        )

    def record_preparation(
        self,
        instance_id: UUID,
        artifact: ImageArtifact,
        storage: tuple[StorageItem, ...],
    ) -> None:
        conflict = self.conflicting_storage(instance_id, storage)
        if conflict is not None:
            raise ValueError(f'Storage source "{conflict.source}" is already owned.')
        with self._connect() as connection:
            connection.executemany(
                """
                INSERT INTO storage (instance_id, kind, source, destination, writable)
                VALUES (?, ?, ?, ?, ?)
                """,
                [
                    (
                        str(instance_id),
                        item.kind.value,
                        item.source,
                        item.destination,
                        item.writable,
                    )
                    for item in storage
                ],
            )
            connection.execute(
                """
                UPDATE instances
                SET source_revision = ?, image_id = ?
                WHERE id = ?
                """,
                (artifact.revision, artifact.image_id, str(instance_id)),
            )

    def record_selection(self, instance_id: UUID, revision: str, artifact: ImageArtifact) -> None:
        """Point this instance at the image its container was just built from.

        The requested revision travels with it, so a later container comes from the
        revision the user selected last rather than the one the instance was created
        from. The image this one replaces keeps its artifact row, which is what an
        explicit recovery reads.
        """
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE instances
                SET requested_revision = ?, source_revision = ?, image_id = ?
                WHERE id = ?
                """,
                (revision, artifact.revision, artifact.image_id, str(instance_id)),
            )

    def mark_prepared(self, instance_id: UUID) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE instances SET prepared = 1 WHERE id = ?",
                (str(instance_id),),
            )

    @staticmethod
    def _binds_overlap(first: str, second: str) -> bool:
        first_path = Path(first).resolve()
        second_path = Path(second).resolve()
        return (
            first_path == second_path
            or first_path in second_path.parents
            or second_path in first_path.parents
        )

    def conflicting_storage(
        self,
        instance_id: UUID,
        storage: tuple[StorageItem, ...],
    ) -> StorageItem | None:
        """The first of these writable sources another instance already owns, if any."""
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT instance_id, kind, source FROM storage
                WHERE writable = 1 AND instance_id != ?
                """,
                (str(instance_id),),
            ).fetchall()
        for item in storage:
            if not item.writable:
                continue
            for _, kind, source in rows:
                conflict = (
                    item.kind is StorageKind.VOLUME
                    and kind == StorageKind.VOLUME.value
                    and item.source == source
                ) or (
                    item.kind is StorageKind.BIND
                    and kind == StorageKind.BIND.value
                    and self._binds_overlap(item.source, source)
                )
                if conflict:
                    return item
        return None

    def set_intended_state(self, instance_id: UUID, state: IntendedState) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE instances SET intended_state = ? WHERE id = ?",
                (state.value, str(instance_id)),
            )

    def instance(self, instance_id: UUID) -> ManagedInstance | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT id, path, manifest_id, persona_name, requested_revision,
                       source_revision, image_id, intended_state, runtime_id, prepared,
                       package_id, package_distribution, package_version,
                       package_image_recipe
                FROM instances WHERE id = ?
                """,
                (str(instance_id),),
            ).fetchone()
            storage_rows = connection.execute(
                """
                SELECT kind, source, destination, writable
                FROM storage WHERE instance_id = ? ORDER BY destination
                """,
                (str(instance_id),),
            ).fetchall()
        if row is None:
            return None
        return ManagedInstance(
            instance_id=UUID(row[0]),
            path=Path(row[1]),
            manifest_id=row[2],
            persona_name=row[3],
            requested_revision=row[4],
            source_revision=row[5],
            image_id=row[6],
            intended_state=IntendedState(row[7]),
            runtime_id=row[8],
            prepared=bool(row[9]),
            storage=tuple(
                StorageItem(
                    kind=StorageKind(kind),
                    source=source,
                    destination=destination,
                    writable=bool(writable),
                )
                for kind, source, destination, writable in storage_rows
            ),
            package=(
                PackageSelection(
                    id=row[10],
                    distribution=row[11],
                    version=row[12],
                    image_recipe=row[13],
                )
                if row[10] is not None
                else None
            ),
        )

    def managed_instances(self) -> list[ManagedInstance]:
        """Every record the hub owns, in creation order, including creations that never finished."""
        with self._connect() as connection:
            ids = [
                UUID(row[0])
                for row in connection.execute("SELECT id FROM instances ORDER BY rowid").fetchall()
            ]
        return [record for instance_id in ids if (record := self.instance(instance_id)) is not None]

    def last_operation(self, instance_id: UUID) -> OperationGetResult | None:
        """The latest operation that changed this instance's container.

        Replacing secrets writes a file and leaves the container where it is, so a
        later secrets operation does not hide an earlier start, stop, or recreation.
        """
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT id FROM operations
                WHERE instance_id = ? AND kind != ?
                ORDER BY rowid DESC LIMIT 1
                """,
                (str(instance_id), OperationKind.SECRETS.value),
            ).fetchone()
        return self.operation(UUID(row[0])) if row is not None else None

    def list_instances(self) -> list[InstanceSummary]:
        with self._connect() as connection:
            ids = [
                UUID(row[0])
                for row in connection.execute(
                    "SELECT id FROM instances WHERE prepared = 1 ORDER BY rowid"
                ).fetchall()
            ]
        records = [self.instance(instance_id) for instance_id in ids]
        return [
            InstanceSummary(
                instance_id=record.instance_id,
                manifest_id=record.manifest_id,
                persona_name=record.persona_name,
                source_revision=record.source_revision or "",
                image_id=record.image_id or "",
                intended_state=record.intended_state,
                runtime_id=record.runtime_id,
                storage=list(record.storage),
                package=(
                    PackageSummary(
                        id=record.package.id,
                        distribution=record.package.distribution,
                        version=record.package.version,
                    )
                    if record.package is not None
                    else None
                ),
            )
            for record in records
            if record is not None
        ]

    def image_artifact(self, input_key: str) -> ImageArtifact | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT image_id, revision, dependency_id, base_images, dependencies,
                       package_selection
                FROM image_artifacts WHERE input_key = ?
                """,
                (input_key,),
            ).fetchone()
        if row is None:
            return None
        return ImageArtifact(
            image_id=row[0],
            revision=row[1],
            dependency_id=row[2],
            base_images=tuple(json.loads(row[3])),
            dependencies=tuple(json.loads(row[4])),
            package=(PackageSelection.model_validate_json(row[5]) if row[5] is not None else None),
        )

    def record_image_artifact(self, input_key: str, artifact: ImageArtifact) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO image_artifacts (
                    input_key, image_id, revision, dependency_id, base_images, dependencies,
                    package_selection
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    input_key,
                    artifact.image_id,
                    artifact.revision,
                    artifact.dependency_id,
                    json.dumps(artifact.base_images),
                    json.dumps(artifact.dependencies),
                    artifact.package.model_dump_json() if artifact.package is not None else None,
                ),
            )

    def image_artifacts(self) -> list[ImageArtifact]:
        with self._connect() as connection:
            keys = [
                row[0]
                for row in connection.execute(
                    "SELECT input_key FROM image_artifacts ORDER BY rowid"
                ).fetchall()
            ]
        return [artifact for key in keys if (artifact := self.image_artifact(key)) is not None]
