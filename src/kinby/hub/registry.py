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
class StorageConflict:
    """Storage of one instance that another record already names."""

    item: StorageItem
    owner: UUID


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

    @property
    def active(self) -> bool:
        """Prepared and neither removed nor deleted: what the hub lists, routes, and operates."""
        return self.prepared and self.intended_state in {
            IntendedState.STOPPED,
            IntendedState.RUNNING,
        }


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
                CREATE TABLE IF NOT EXISTS update_candidates (
                    instance_id TEXT PRIMARY KEY REFERENCES instances(id),
                    requested_revision TEXT NOT NULL,
                    source_revision TEXT NOT NULL,
                    image_id TEXT NOT NULL
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
            raise ValueError(f'Storage source "{conflict.item.source}" is already owned.')
        with self._connect() as connection:
            self._insert_storage(connection, instance_id, storage)
            connection.execute(
                """
                UPDATE instances
                SET source_revision = ?, image_id = ?
                WHERE id = ?
                """,
                (artifact.revision, artifact.image_id, str(instance_id)),
            )

    @staticmethod
    def _insert_storage(
        connection: sqlite3.Connection,
        instance_id: UUID,
        storage: tuple[StorageItem, ...],
    ) -> None:
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

    def begin_adoption(self, instance: ManagedInstance, operation_id: UUID) -> UUID:
        """Reserve the handoff, or return the unfinished operation already running.

        The identity is derived from the storage, so a handoff that was interrupted
        writes over its own unfinished record instead of opening a second one. A
        prepared instance, another active operation, or storage another record
        already owns is refused here, in one writer transaction.
        """
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT prepared FROM instances WHERE id = ?",
                (str(instance.instance_id),),
            ).fetchone()
            if existing is not None and existing[0]:
                raise ValueError(
                    f"This hub already manages the instance at {instance.path} as "
                    f"{instance.instance_id}."
                )
            active = connection.execute(
                """
                SELECT id FROM operations
                WHERE instance_id = ? AND state IN (?, ?)
                ORDER BY rowid LIMIT 1
                """,
                (
                    str(instance.instance_id),
                    OperationState.PENDING.value,
                    OperationState.RUNNING.value,
                ),
            ).fetchone()
            if active is not None:
                return UUID(active[0])
            conflict = self._writable_conflict(connection, instance.instance_id, instance.storage)
            if conflict is not None:
                raise ValueError(
                    f'Writable storage "{conflict.item.source}" is already recorded for instance '
                    f"{conflict.owner}."
                )
            held = self._held_identity(
                connection,
                instance.instance_id,
                instance.path,
                instance.runtime_id,
            )
            if held is not None:
                raise ValueError(held)
            connection.execute(
                "DELETE FROM storage WHERE instance_id = ?",
                (str(instance.instance_id),),
            )
            try:
                connection.execute(
                    """
                    INSERT INTO instances (
                        id, path, manifest_id, persona_name, requested_revision,
                        image_id, intended_state, runtime_id, prepared
                    ) VALUES (?, ?, ?, ?, '', ?, ?, ?, 0)
                    ON CONFLICT(id) DO UPDATE SET
                        path = excluded.path,
                        manifest_id = excluded.manifest_id,
                        persona_name = excluded.persona_name,
                        image_id = excluded.image_id,
                        intended_state = excluded.intended_state,
                        runtime_id = excluded.runtime_id
                    """,
                    (
                        str(instance.instance_id),
                        str(instance.path),
                        instance.manifest_id,
                        instance.persona_name,
                        instance.image_id,
                        instance.intended_state.value,
                        instance.runtime_id,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError(
                    self._held_identity(
                        connection,
                        instance.instance_id,
                        instance.path,
                        instance.runtime_id,
                    )
                    or "This path or container name is already recorded for another instance."
                ) from exc
            self._insert_storage(connection, instance.instance_id, instance.storage)
            self._insert_operation(
                connection,
                operation_id,
                instance.instance_id,
                OperationKind.ADOPT,
                "Adoption queued.",
            )
        return operation_id

    def record_selection(self, instance_id: UUID, revision: str, artifact: ImageArtifact) -> None:
        """Point this instance at the image its container was just built from.

        The requested revision travels with it, so a later container comes from the
        revision the user selected last rather than the one the instance was created
        from. The image this one replaces keeps its artifact row, which is what an
        explicit recovery reads.
        """
        with self._connect() as connection:
            self._write_selection(
                connection,
                instance_id,
                revision,
                artifact.revision,
                artifact.image_id,
            )

    def stage_candidate(self, instance_id: UUID, revision: str, artifact: ImageArtifact) -> None:
        """Remember the image an update prepared, without selecting it yet.

        The selection changes once a container of that image exists. Recovery reads
        this row when the process dies after creating that container and before
        the selection is recorded.
        """
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO update_candidates (
                    instance_id, requested_revision, source_revision, image_id
                ) VALUES (?, ?, ?, ?)
                ON CONFLICT(instance_id) DO UPDATE SET
                    requested_revision = excluded.requested_revision,
                    source_revision = excluded.source_revision,
                    image_id = excluded.image_id
                """,
                (str(instance_id), revision, artifact.revision, artifact.image_id),
            )

    def candidate_image(self, instance_id: UUID) -> str | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT image_id FROM update_candidates WHERE instance_id = ?",
                (str(instance_id),),
            ).fetchone()
        return row[0] if row is not None else None

    def accept_candidate(self, instance_id: UUID, image_id: str) -> None:
        """Select the staged image when the container that exists is that image."""
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT requested_revision, source_revision, image_id
                FROM update_candidates WHERE instance_id = ?
                """,
                (str(instance_id),),
            ).fetchone()
            if row is None or row[2] != image_id:
                return
            self._write_selection(connection, instance_id, row[0], row[1], row[2])

    @staticmethod
    def _write_selection(
        connection: sqlite3.Connection,
        instance_id: UUID,
        requested_revision: str,
        source_revision: str,
        image_id: str,
    ) -> None:
        connection.execute(
            """
            UPDATE instances
            SET requested_revision = ?, source_revision = ?, image_id = ?
            WHERE id = ?
            """,
            (requested_revision, source_revision, image_id, str(instance_id)),
        )
        connection.execute(
            "DELETE FROM update_candidates WHERE instance_id = ?",
            (str(instance_id),),
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
    ) -> StorageConflict | None:
        """The first of these writable sources another instance already owns, if any."""
        with self._connect() as connection:
            return self._writable_conflict(connection, instance_id, storage)

    def shared_storage(
        self,
        instance_id: UUID,
        storage: tuple[StorageItem, ...],
    ) -> StorageConflict | None:
        """The first of these writable sources any other record mounts, even read only."""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT instance_id, kind, source FROM storage WHERE instance_id != ?",
                (str(instance_id),),
            ).fetchall()
        return self._overlap(storage, rows)

    def _writable_conflict(
        self,
        connection: sqlite3.Connection,
        instance_id: UUID,
        storage: tuple[StorageItem, ...],
    ) -> StorageConflict | None:
        rows = connection.execute(
            """
            SELECT instance_id, kind, source FROM storage
            WHERE writable = 1 AND instance_id != ?
            """,
            (str(instance_id),),
        ).fetchall()
        return self._overlap(storage, rows)

    def _overlap(
        self,
        storage: tuple[StorageItem, ...],
        rows: list[tuple[str, str, str]],
    ) -> StorageConflict | None:
        for item in storage:
            if not item.writable:
                continue
            for owner, kind, source in rows:
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
                    return StorageConflict(item=item, owner=UUID(owner))
        return None

    def set_intended_state(self, instance_id: UUID, state: IntendedState) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE instances SET intended_state = ? WHERE id = ?",
                (state.value, str(instance_id)),
            )

    def release_storage(self, instance_id: UUID, items: list[StorageItem]) -> None:
        """Drop inventory entries whose storage is gone, so no retry reaches for it again."""
        with self._connect() as connection:
            connection.executemany(
                "DELETE FROM storage WHERE instance_id = ? AND destination = ?",
                [(str(instance_id), item.destination) for item in items],
            )

    def mark_deleted(self, instance_id: UUID) -> None:
        """Record that nothing owned is left. The read-only references it mounted go too.

        The row stays so its operations remain readable. Its path and container
        name stay too, and both columns are unique. A later adoption that reuses
        either one is a finding, not a second row.
        """
        with self._connect() as connection:
            connection.execute("DELETE FROM storage WHERE instance_id = ?", (str(instance_id),))
            connection.execute(
                "UPDATE instances SET intended_state = ? WHERE id = ?",
                (IntendedState.DELETED.value, str(instance_id)),
            )

    def held_identity(self, instance_id: UUID, path: Path, runtime_id: str) -> str | None:
        """Why another record already holds this path or container name, if one does."""
        with self._connect() as connection:
            return self._held_identity(connection, instance_id, path, runtime_id)

    @staticmethod
    def _held_identity(
        connection: sqlite3.Connection,
        instance_id: UUID,
        path: Path,
        runtime_id: str,
    ) -> str | None:
        row = connection.execute(
            """
            SELECT id, path, runtime_id FROM instances
            WHERE id != ? AND (path = ? OR runtime_id = ?)
            ORDER BY rowid LIMIT 1
            """,
            (str(instance_id), str(path), runtime_id),
        ).fetchone()
        if row is None:
            return None
        owner, held_path, held_runtime = row
        if held_runtime == runtime_id:
            return (
                f'Container "{runtime_id}" is still recorded for instance {owner}, '
                "so this hub will not adopt another instance under that name."
            )
        return (
            f"Directory {held_path} is still recorded for instance {owner}, "
            "so this hub will not adopt another instance at that path."
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
        """Every record the hub owns, in creation order, including creations that never finished.

        A deleted instance owns nothing any more. Its record stays only for its operations.
        """
        with self._connect() as connection:
            ids = [
                UUID(row[0])
                for row in connection.execute(
                    "SELECT id FROM instances WHERE intended_state != ? ORDER BY rowid",
                    (IntendedState.DELETED.value,),
                ).fetchall()
            ]
        return [record for instance_id in ids if (record := self.instance(instance_id)) is not None]

    def last_operation(self, instance_id: UUID) -> OperationGetResult | None:
        """The latest operation that changed this instance's container.

        Replacing secrets writes a file and leaves the container where it is. A
        queued operation that never started leaves the container where it is too.
        A removal counts even before its first step, because recovery finishes
        one that the process died inside. An adoption counts too. Recovery
        reads it to tell an unfinished handoff from a create, because the
        container it finds still belongs to the previous runtime.
        """
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT operations.id FROM operations
                WHERE instance_id = ? AND kind != ?
                  AND (
                    kind IN (?, ?)
                    OR state = ?
                    OR EXISTS (
                        SELECT 1 FROM operation_steps
                        WHERE operation_steps.operation_id = operations.id
                    )
                  )
                ORDER BY operations.rowid DESC LIMIT 1
                """,
                (
                    str(instance_id),
                    OperationKind.SECRETS.value,
                    OperationKind.REMOVE.value,
                    OperationKind.ADOPT.value,
                    OperationState.SUCCEEDED.value,
                ),
            ).fetchone()
        return self.operation(UUID(row[0])) if row is not None else None

    def list_instances(self, *, removed: bool = False) -> list[InstanceSummary]:
        """The active instances, or the removed ones whose records and storage the hub retains."""
        with self._connect() as connection:
            ids = [
                UUID(row[0])
                for row in connection.execute(
                    "SELECT id FROM instances WHERE prepared = 1 ORDER BY rowid"
                ).fetchall()
            ]
        records = [
            record
            for instance_id in ids
            if (record := self.instance(instance_id)) is not None
            and (record.intended_state is IntendedState.REMOVED if removed else record.active)
        ]
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
