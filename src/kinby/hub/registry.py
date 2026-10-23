"""SQLite ownership records for the single-user hub."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Collection, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from kinby.contracts import (
    DEFAULT_AVATAR,
    Avatar,
    AvatarColor,
    AvatarShape,
    ChannelFailure,
    ChannelKindName,
    DeclaredSetup,
    DeliveryStatus,
    FactoryName,
    FactoryRun,
    FactoryRunStatus,
    FileHash,
    GitSource,
    HubSecretName,
    IntendedState,
    LoginPrompt,
    LoginState,
    Notification,
    NotificationDelivery,
    NotificationReply,
    OperationGetResult,
    OperationKind,
    OperationState,
    OperationStep,
    ReplyStatus,
    RunStop,
    SetupValue,
    SkillName,
    StepAttempt,
    StepEnding,
    StepId,
    StepResult,
    StepValue,
    StorageItem,
    StorageKind,
    ValueName,
)
from kinby.factories.file import InstanceName, RecipeName
from kinby.hub.models import ChannelNotification, ChannelUser, ImageArtifact, SentRef


@dataclass(frozen=True)
class StorageConflict:
    """Storage of one instance that another record already names."""

    item: StorageItem
    owner: UUID


@dataclass(frozen=True)
class FactoryMember:
    """The factory role an instance picked, and what its instance template declared."""

    factory: FactoryName
    #: The instance's name in the factory file.
    name: InstanceName
    #: The image recipe its template names. None is kinby's base image.
    recipe: RecipeName | None
    #: As the creation or the last update stored it, so an edit of the factory reaches the
    #: instance's setup only through an update.
    setup: DeclaredSetup
    #: The hash of each file the template supplied, by its path. None for an instance created
    #: before the hub recorded them.
    baseline: dict[str, FileHash] | None


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
    avatar: Avatar = DEFAULT_AVATAR
    factory: FactoryMember | None = None
    #: The instance registry marks it the home instance (ADR 0096). Never its manifest.
    home: bool = False

    @property
    def active(self) -> bool:
        """Prepared and neither removed nor deleted: what the hub lists, routes, and operates."""
        return self.prepared and self.intended_state in {
            IntendedState.STOPPED,
            IntendedState.RUNNING,
        }


@dataclass(frozen=True)
class Pairing:
    """The code that pairs a channel with the first user who sends it, and until when."""

    code: str
    expires_at: datetime


@dataclass(frozen=True)
class ChannelRecord:
    """A channel the user set up, and what the hub last saw of its sends."""

    channel_id: UUID
    name: str
    kind: ChannelKindName
    #: Each setting, by field name. A secret field holds the name of the hub secret with its value.
    settings: dict[str, SetupValue]
    enabled: bool
    #: Who the channel sends to and answers, once paired.
    paired_user: ChannelUser | None
    #: None once paired, and for a kind that does not pair.
    pairing: Pairing | None
    muted: tuple[UUID, ...]
    last_sent_at: datetime | None
    last_failure: ChannelFailure | None

    @property
    def sends(self) -> bool:
        """Enabled, and not waiting for its pairing code."""
        return self.enabled and self.pairing is None


@dataclass(frozen=True)
class PendingDelivery:
    """A notification still to send through one channel."""

    notification: ChannelNotification
    channel_id: UUID
    raised_at: datetime
    #: The failed sends so far.
    attempts: int
    next_attempt: datetime


@dataclass(frozen=True)
class ReceivedReply:
    """A reply recorded and not applied yet."""

    reply_id: UUID
    notification_id: UUID
    instance_id: UUID
    thread_id: UUID
    #: One of the notification's options. None for "Mute this instance".
    option: str | None
    received_at: datetime
    #: The channel it came through. None for a reply from the web app.
    channel_id: UUID | None
    #: The message it answered on that channel.
    message: SentRef | None
    #: A previous process asked the instance to apply it and never heard back.
    calling: bool


class HubRegistry:
    """One durable owner of hub management state."""

    def __init__(self, directory: Path) -> None:
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / "registry.sqlite"
        self._initialize()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """One transaction, committed on success and rolled back on an error, then closed. An
        unclosed connection holds its file until a garbage collection."""
        connection = sqlite3.connect(self.path)
        try:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA foreign_keys = ON")
            with connection:
                yield connection
        finally:
            connection.close()

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
                    avatar_shape TEXT,
                    avatar_color TEXT
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
                    instance_id TEXT REFERENCES instances(id),
                    kind TEXT NOT NULL,
                    state TEXT NOT NULL,
                    detail TEXT NOT NULL,
                    login_id TEXT
                );
                CREATE TABLE IF NOT EXISTS operation_steps (
                    operation_id TEXT NOT NULL REFERENCES operations(id),
                    position INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    state TEXT NOT NULL,
                    detail TEXT NOT NULL,
                    prompt_url TEXT,
                    prompt_code TEXT,
                    PRIMARY KEY (operation_id, name)
                );
                -- Names only: the value lives in the instance's .env (ADR 0095).
                CREATE TABLE IF NOT EXISTS secret_links (
                    instance_id TEXT NOT NULL REFERENCES instances(id),
                    variable TEXT NOT NULL,
                    hub_secret TEXT NOT NULL,
                    PRIMARY KEY (instance_id, variable)
                );
                CREATE TABLE IF NOT EXISTS logins (
                    instance_id TEXT NOT NULL REFERENCES instances(id),
                    login_id TEXT NOT NULL,
                    state TEXT NOT NULL,
                    changed_at TEXT NOT NULL,
                    PRIMARY KEY (instance_id, login_id)
                );
                CREATE TABLE IF NOT EXISTS image_artifacts (
                    input_key TEXT PRIMARY KEY,
                    image_id TEXT NOT NULL,
                    revision TEXT NOT NULL,
                    dependency_id TEXT NOT NULL,
                    base_images TEXT NOT NULL,
                    dependencies TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS image_descriptions (
                    image_id TEXT PRIMARY KEY,
                    setup TEXT NOT NULL
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
                CREATE TABLE IF NOT EXISTS factory_members (
                    instance_id TEXT PRIMARY KEY REFERENCES instances(id),
                    factory TEXT NOT NULL,
                    name TEXT NOT NULL,
                    recipe TEXT,
                    setup TEXT NOT NULL,
                    baseline TEXT
                );
                CREATE TABLE IF NOT EXISTS factory_runs (
                    id TEXT PRIMARY KEY,
                    factory TEXT NOT NULL,
                    instance_id TEXT,
                    work_item TEXT NOT NULL,
                    status TEXT NOT NULL,
                    step TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS step_attempts (
                    run_id TEXT NOT NULL REFERENCES factory_runs(id),
                    step TEXT NOT NULL,
                    attempt INTEGER NOT NULL,
                    ending TEXT,
                    outcome TEXT,
                    result_values TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    ended_at TEXT,
                    PRIMARY KEY (run_id, step, attempt)
                );
                -- Each time a run moved to done, cancelled or needs_human, for the hub to tell the
                -- instance that handed it in.
                CREATE TABLE IF NOT EXISTS run_stops (
                    run_id TEXT NOT NULL REFERENCES factory_runs(id),
                    generation INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    -- The rowid of the run's last attempt by then. The next stop tells the ones
                    -- after it.
                    last_attempt INTEGER NOT NULL,
                    told INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (run_id, generation)
                );
                CREATE TABLE IF NOT EXISTS channels (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    -- A secret setting holds the name of the hub secret with its value.
                    settings TEXT NOT NULL,
                    enabled INTEGER NOT NULL,
                    paired_user TEXT,
                    pairing_code TEXT,
                    pairing_expires_at TEXT,
                    last_sent_at TEXT,
                    last_error TEXT,
                    last_error_at TEXT
                );
                CREATE TABLE IF NOT EXISTS channel_mutes (
                    channel_id TEXT NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
                    instance_id TEXT NOT NULL,
                    PRIMARY KEY (channel_id, instance_id)
                );
                CREATE TABLE IF NOT EXISTS notifications (
                    id TEXT PRIMARY KEY,
                    instance_id TEXT NOT NULL,
                    source_key TEXT NOT NULL,
                    thread_id TEXT NOT NULL,
                    header TEXT NOT NULL,
                    title TEXT NOT NULL,
                    text TEXT NOT NULL,
                    link TEXT,
                    options TEXT NOT NULL,
                    raised_at TEXT NOT NULL,
                    UNIQUE (instance_id, source_key)
                );
                CREATE TABLE IF NOT EXISTS notification_deliveries (
                    notification_id TEXT NOT NULL REFERENCES notifications(id),
                    channel_id TEXT NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
                    status TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    next_attempt TEXT NOT NULL,
                    sent_ref TEXT,
                    last_error TEXT,
                    PRIMARY KEY (notification_id, channel_id)
                );
                CREATE TABLE IF NOT EXISTS replies (
                    id TEXT PRIMARY KEY,
                    notification_id TEXT NOT NULL REFERENCES notifications(id),
                    -- Null for a reply from the web app, or once the channel is deleted.
                    channel_id TEXT REFERENCES channels(id) ON DELETE SET NULL,
                    -- One of the notification's options. Null for "Mute this instance".
                    option TEXT,
                    received_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    outcome TEXT,
                    -- Set while the turn it starts is asked for, so no restart asks twice.
                    calling INTEGER NOT NULL DEFAULT 0
                );
                -- Kept when the user deletes the skill, so the hub never copies it again.
                CREATE TABLE IF NOT EXISTS shipped_skills (
                    name TEXT PRIMARY KEY,
                    hash TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS git_skills (
                    name TEXT PRIMARY KEY,
                    url TEXT NOT NULL,
                    path TEXT NOT NULL,
                    ref TEXT,
                    git_commit TEXT NOT NULL,
                    hash TEXT NOT NULL
                );
                """
            )
            self._add_columns(
                connection,
                "instances",
                {"avatar_shape": "TEXT", "avatar_color": "TEXT"},
            )
            self._add_columns(
                connection,
                "factory_runs",
                # The last attempt before the user restarted the run. Later ones count toward
                # retries and send-backs.
                {
                    "restarted_after": "INTEGER NOT NULL DEFAULT 0",
                    # Whether the run's needs_human report was delivered. Reset each time the
                    # run moves to needs_human, so a restart resends only an undelivered report.
                    "reported": "INTEGER NOT NULL DEFAULT 0",
                    # Bumped each time the run stops, so a report still in flight for an
                    # earlier stop can never mark a later stop as reported.
                    "stop_generation": "INTEGER NOT NULL DEFAULT 0",
                    "instance_id": "TEXT",
                    # When a run parked until its plan window resets tries its step again.
                    "wakes_at": "TEXT",
                    # Set while the factory's ended call for the run is still to make. A run
                    # that ended before the call existed owes none.
                    "ended_call": "INTEGER NOT NULL DEFAULT 0",
                },
            )
            # A run that stopped before stops were recorded gets its latest stop as told, so its
            # next stop tells only the attempts after it.
            connection.execute(
                """
                INSERT INTO run_stops (run_id, generation, status, last_attempt, told)
                SELECT id, stop_generation, status, (
                    SELECT COALESCE(MAX(rowid), 0) FROM step_attempts
                    WHERE step_attempts.run_id = factory_runs.id
                ), 1
                FROM factory_runs
                WHERE stop_generation > 0
                    AND NOT EXISTS (
                        SELECT 1 FROM run_stops WHERE run_stops.run_id = factory_runs.id
                    )
                """
            )
            self._add_columns(connection, "step_attempts", {"session": "TEXT"})
            self._add_columns(connection, "factory_members", {"baseline": "TEXT"})
            # Null for a git skill added before the hub recorded the hash it landed with.
            self._add_columns(connection, "git_skills", {"hash": "TEXT"})
            self._add_columns(
                connection,
                "operation_steps",
                {"prompt_url": "TEXT", "prompt_code": "TEXT"},
            )
        self._let_operations_stand_alone()
        # After the rebuild, which copies only the columns operations had before preparations.
        with self._connect() as connection:
            # The instance that asked the hub for the operation. Null for one the user started.
            self._add_columns(connection, "operations", {"login_id": "TEXT", "requester": "TEXT"})
            self._drop_packages(connection)

    def _let_operations_stand_alone(self) -> None:
        """Rebuild an operations table from before preparations, whose rows all name an instance.

        SQLite cannot drop a NOT NULL constraint in place. The copy keeps every row in order.
        """
        connection = sqlite3.connect(self.path)
        try:
            columns = {
                row[1]: row[3] for row in connection.execute("PRAGMA table_info(operations)")
            }
            if not columns["instance_id"]:
                return
            connection.executescript(
                """
                BEGIN;
                CREATE TABLE operations_standing_alone (
                    id TEXT PRIMARY KEY,
                    instance_id TEXT REFERENCES instances(id),
                    kind TEXT NOT NULL,
                    state TEXT NOT NULL,
                    detail TEXT NOT NULL
                );
                INSERT INTO operations_standing_alone (id, instance_id, kind, state, detail)
                SELECT id, instance_id, kind, state, detail FROM operations ORDER BY rowid;
                DROP TABLE operations;
                ALTER TABLE operations_standing_alone RENAME TO operations;
                COMMIT;
                """
            )
        finally:
            connection.close()

    @classmethod
    def _drop_packages(cls, connection: sqlite3.Connection) -> None:
        """Drop what a registry from before factories kept about packages.

        Every instance keeps its record. Of the descriptions, only the base image's still
        describes something an instance can be created from.
        """
        cls._drop_columns(
            connection,
            "instances",
            (
                "package_id",
                "package_distribution",
                "package_version",
                "package_image_recipe",
                "package_commit_url",
                "package_commit_sha",
            ),
        )
        cls._drop_columns(connection, "image_artifacts", ("package_selection",))
        cls._drop_columns(connection, "operations", ("selection",))
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master")}
        if "package_descriptions" not in tables:
            return
        row = connection.execute(
            "SELECT image_id, description FROM package_descriptions WHERE selection = 'null'"
        ).fetchone()
        if row is not None:
            described = json.loads(row[1])
            setup = DeclaredSetup(
                setup_fields=described["setup_fields"], logins=described.get("logins", [])
            )
            connection.execute(
                "INSERT OR IGNORE INTO image_descriptions (image_id, setup) VALUES (?, ?)",
                (row[0], setup.model_dump_json()),
            )
        connection.execute("DROP TABLE package_descriptions")

    @staticmethod
    def _drop_columns(connection: sqlite3.Connection, table: str, columns: tuple[str, ...]) -> None:
        existing = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
        for name in columns:
            if name in existing:
                connection.execute(f"ALTER TABLE {table} DROP COLUMN {name}")

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

    def update_token_hash(self) -> str | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT value FROM hub_metadata WHERE key = 'update_token_hash'"
            ).fetchone()
        return row[0] if row is not None else None

    def replace_update_token_hash(self, token_hash: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO hub_metadata (key, value)
                VALUES ('update_token_hash', ?)
                """,
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

    def close_session(self, token_hash: str) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))

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
        requester: UUID | None = None,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO instances (
                    id, path, manifest_id, persona_name, requested_revision,
                    intended_state, runtime_id, prepared, avatar_shape, avatar_color
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
                """,
                (
                    str(instance.instance_id),
                    str(instance.path),
                    instance.manifest_id,
                    instance.persona_name,
                    instance.requested_revision,
                    instance.intended_state.value,
                    instance.runtime_id,
                    instance.avatar.shape.value,
                    instance.avatar.color.value,
                ),
            )
            member = instance.factory
            if member is not None:
                connection.execute(
                    """
                    INSERT INTO factory_members (
                        instance_id, factory, name, recipe, setup, baseline
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(instance.instance_id),
                        member.factory,
                        member.name,
                        member.recipe,
                        member.setup.model_dump_json(),
                        None if member.baseline is None else json.dumps(member.baseline),
                    ),
                )
            if instance.home:
                # The hub creates a home instance only while it holds none, so this replaces
                # only the mark of one whose creation failed.
                connection.execute(
                    "INSERT OR REPLACE INTO hub_metadata (key, value) VALUES ('home_instance', ?)",
                    (str(instance.instance_id),),
                )
            self._insert_operation(
                connection,
                operation_id,
                instance.instance_id,
                OperationKind.CREATE,
                "Creation queued.",
                requester,
            )

    def record_operation(
        self,
        operation_id: UUID,
        instance_id: UUID | None,
        kind: OperationKind,
        detail: str,
        requester: UUID | None = None,
    ) -> None:
        """Open this operation on its own, even while another one is unfinished. One that
        belongs to no instance has no *instance_id*."""
        with self._connect() as connection:
            self._insert_operation(connection, operation_id, instance_id, kind, detail, requester)

    @staticmethod
    def _insert_operation(
        connection: sqlite3.Connection,
        operation_id: UUID,
        instance_id: UUID | None,
        kind: OperationKind,
        detail: str,
        requester: UUID | None = None,
    ) -> None:
        connection.execute(
            """
            INSERT INTO operations (id, instance_id, kind, state, detail, requester)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                str(operation_id),
                str(instance_id) if instance_id is not None else None,
                kind.value,
                OperationState.PENDING.value,
                detail,
                str(requester) if requester is not None else None,
            ),
        )

    def begin_preparation(self, operation_id: UUID, detail: str) -> UUID:
        """Open this image preparation, or return the unfinished one."""
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                """
                SELECT id FROM operations
                WHERE kind = ? AND state IN (?, ?)
                ORDER BY rowid
                LIMIT 1
                """,
                (
                    OperationKind.PREPARE.value,
                    OperationState.PENDING.value,
                    OperationState.RUNNING.value,
                ),
            ).fetchone()
            if existing is not None:
                return UUID(existing[0])
            connection.execute(
                """
                INSERT INTO operations (id, kind, state, detail)
                VALUES (?, ?, ?, ?)
                """,
                (
                    str(operation_id),
                    OperationKind.PREPARE.value,
                    OperationState.PENDING.value,
                    detail,
                ),
            )
        return operation_id

    def begin_login(
        self,
        operation_id: UUID,
        instance_id: UUID,
        login_id: str,
        detail: str,
    ) -> UUID:
        """Open this login, or return the unfinished one of the same login on this instance."""
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = self._running_login(connection, instance_id, login_id)
            if existing is not None:
                return UUID(existing[0])
            connection.execute(
                """
                INSERT INTO operations (id, instance_id, kind, state, detail, login_id)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    str(operation_id),
                    str(instance_id),
                    OperationKind.LOGIN.value,
                    OperationState.PENDING.value,
                    detail,
                    login_id,
                ),
            )
        return operation_id

    def running_login(
        self, instance_id: UUID, login_id: str | None = None
    ) -> tuple[UUID, str] | None:
        """The operation and login ids of an unfinished login of this instance, or of that login."""
        with self._connect() as connection:
            row = self._running_login(connection, instance_id, login_id)
        return (UUID(row[0]), row[1]) if row is not None else None

    @staticmethod
    def _running_login(
        connection: sqlite3.Connection,
        instance_id: UUID,
        login_id: str | None = None,
    ) -> tuple[str, str] | None:
        """The operation id and the login id of an unfinished login, if one is running."""
        return connection.execute(
            """
            SELECT id, login_id FROM operations
            WHERE instance_id = ? AND kind = ? AND state IN (?, ?)
              AND (? IS NULL OR login_id = ?)
            ORDER BY rowid LIMIT 1
            """,
            (
                str(instance_id),
                OperationKind.LOGIN.value,
                OperationState.PENDING.value,
                OperationState.RUNNING.value,
                login_id,
                login_id,
            ),
        ).fetchone()

    def seed_logins(self, instance_id: UUID, login_ids: Collection[str]) -> None:
        """Track each login a new instance declares, none of them signed in yet."""
        changed_at = _now()
        with self._connect() as connection:
            connection.executemany(
                """
                INSERT OR REPLACE INTO logins (instance_id, login_id, state, changed_at)
                VALUES (?, ?, ?, ?)
                """,
                [
                    (str(instance_id), login_id, LoginState.PENDING.value, changed_at)
                    for login_id in login_ids
                ],
            )

    def link_secrets(
        self,
        instance_id: UUID,
        links: Mapping[str, HubSecretName],
        unlinked: Collection[str] = (),
    ) -> None:
        """Link each variable the instance holds to the hub secret it took, and drop the link of
        each variable in `unlinked`."""
        with self._connect() as connection:
            connection.executemany(
                "DELETE FROM secret_links WHERE instance_id = ? AND variable = ?",
                [(str(instance_id), variable) for variable in unlinked],
            )
            connection.executemany(
                """
                INSERT OR REPLACE INTO secret_links (instance_id, variable, hub_secret)
                VALUES (?, ?, ?)
                """,
                [(str(instance_id), variable, name) for variable, name in links.items()],
            )

    def secret_links(self, instance_id: UUID) -> dict[str, HubSecretName]:
        """The hub secret each linked variable of the instance took, by variable."""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT variable, hub_secret FROM secret_links WHERE instance_id = ?",
                (str(instance_id),),
            ).fetchall()
        return dict(rows)

    def linked_to(self, hub_secret: HubSecretName) -> dict[UUID, list[str]]:
        """The variables linked to the hub secret, by instance."""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT instance_id, variable FROM secret_links WHERE hub_secret = ? "
                "ORDER BY variable",
                (hub_secret,),
            ).fetchall()
        linked: dict[UUID, list[str]] = {}
        for instance_id, variable in rows:
            linked.setdefault(UUID(instance_id), []).append(variable)
        return linked

    def unlink_hub_secret(self, hub_secret: HubSecretName) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM secret_links WHERE hub_secret = ?", (hub_secret,))

    def record_template(
        self, instance_id: UUID, setup: DeclaredSetup, baseline: dict[str, FileHash]
    ) -> None:
        """Store the setup and the baseline an update just synced the factory's instance to."""
        with self._connect() as connection:
            connection.execute(
                "UPDATE factory_members SET setup = ?, baseline = ? WHERE instance_id = ?",
                (setup.model_dump_json(), json.dumps(baseline), str(instance_id)),
            )

    def login_states(self, instance_id: UUID) -> dict[str, LoginState]:
        """How each login the hub tracks for this instance stands, by login id."""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT login_id, state FROM logins WHERE instance_id = ?",
                (str(instance_id),),
            ).fetchall()
        return {login_id: LoginState(state) for login_id, state in rows}

    def record_prompt(self, operation_id: UUID, prompt: LoginPrompt) -> None:
        """Show the URL and the code on the step that is running."""
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE operation_steps SET prompt_url = ?, prompt_code = ?
                WHERE operation_id = ? AND state = ?
                """,
                (prompt.url, prompt.code, str(operation_id), OperationState.RUNNING.value),
            )

    def record_description(self, image_id: str, setup: DeclaredSetup) -> None:
        """Keep what this image declares, so describing it never builds."""
        with self._connect() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO image_descriptions (image_id, setup) VALUES (?, ?)",
                (image_id, setup.model_dump_json()),
            )

    def description(self, image_id: str | None = None) -> DeclaredSetup | None:
        """What this image declares, or with no image the one described last, if any was."""
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT setup FROM image_descriptions
                WHERE ? IS NULL OR image_id = ?
                ORDER BY rowid DESC LIMIT 1
                """,
                (image_id, image_id),
            ).fetchone()
        return DeclaredSetup.model_validate_json(row[0]) if row is not None else None

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
        requester: UUID | None = None,
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
            self._insert_operation(connection, operation_id, instance_id, kind, detail, requester)
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

    def succeed_step(self, operation_id: UUID, detail: str) -> None:
        """Succeed the step that is running with what it did, leaving the operation running."""
        with self._connect() as connection:
            self._close_running_step(connection, operation_id, OperationState.SUCCEEDED, detail)

    def finish_operation(
        self,
        operation_id: UUID,
        state: OperationState,
        detail: str,
    ) -> None:
        """Record the outcome on the operation and on the step it stopped in.

        A login's outcome is its login's state too, however the login ended, a restart included.
        A login that fails after it once signed in stays signed in: a sign-in writes its
        credentials only when it succeeds, so the ones before it still work (ADR 0068).
        """
        login = LoginState.SIGNED_IN if state is OperationState.SUCCEEDED else LoginState.FAILED
        with self._connect() as connection:
            self._close_running_step(connection, operation_id, state, detail)
            connection.execute(
                "UPDATE operations SET state = ?, detail = ? WHERE id = ?",
                (state.value, detail, str(operation_id)),
            )
            connection.execute(
                """
                INSERT INTO logins (instance_id, login_id, state, changed_at)
                SELECT instance_id, login_id, ?, ? FROM operations WHERE id = ? AND kind = ?
                ON CONFLICT (instance_id, login_id) DO UPDATE
                SET state = excluded.state, changed_at = excluded.changed_at
                WHERE logins.state != ? OR excluded.state = ?
                """,
                (
                    login.value,
                    _now(),
                    str(operation_id),
                    OperationKind.LOGIN.value,
                    LoginState.SIGNED_IN.value,
                    LoginState.SIGNED_IN.value,
                ),
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
        does not take its place while this one is still running. A login is not one
        of them: it runs beside them, and may wait for the user for many minutes.
        """
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT id FROM operations
                WHERE instance_id = ? AND kind != ? AND state IN (?, ?)
                ORDER BY rowid LIMIT 1
                """,
                (
                    str(instance_id),
                    OperationKind.LOGIN.value,
                    OperationState.PENDING.value,
                    OperationState.RUNNING.value,
                ),
            ).fetchone()
        return UUID(row[0]) if row is not None else None

    def operation(self, operation_id: UUID) -> OperationGetResult | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT id, instance_id, kind, state, detail, requester FROM operations
                WHERE id = ?
                """,
                (str(operation_id),),
            ).fetchone()
            steps = connection.execute(
                """
                SELECT name, state, detail, prompt_url, prompt_code FROM operation_steps
                WHERE operation_id = ? ORDER BY position
                """,
                (str(operation_id),),
            ).fetchall()
        if row is None:
            return None
        return OperationGetResult(
            operation_id=UUID(row[0]),
            instance_id=UUID(row[1]) if row[1] is not None else None,
            kind=OperationKind(row[2]),
            state=OperationState(row[3]),
            detail=row[4],
            requester=UUID(row[5]) if row[5] is not None else None,
            steps=[
                OperationStep(
                    name=name,
                    state=OperationState(state),
                    detail=detail,
                    prompt=LoginPrompt(url=url, code=code) if url is not None else None,
                )
                for name, state, detail, url, code in steps
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
                       avatar_shape, avatar_color,
                       EXISTS (
                           SELECT 1 FROM hub_metadata
                           WHERE key = 'home_instance' AND value = instances.id
                       )
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
            member = connection.execute(
                """
                SELECT factory, name, recipe, setup, baseline
                FROM factory_members WHERE instance_id = ?
                """,
                (str(instance_id),),
            ).fetchone()
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
            # Instances from before avatars have none recorded, and draw the default one.
            avatar=(
                Avatar(shape=AvatarShape(row[10]), color=AvatarColor(row[11]))
                if row[10] is not None
                else DEFAULT_AVATAR
            ),
            factory=(
                FactoryMember(
                    factory=member[0],
                    name=member[1],
                    recipe=member[2],
                    setup=DeclaredSetup.model_validate_json(member[3]),
                    baseline=None if member[4] is None else json.loads(member[4]),
                )
                if member is not None
                else None
            ),
            home=bool(row[12]),
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

        Replacing secrets writes a file and leaves the container where it is, and a login
        runs in a container of its own. A queued operation that never started leaves the
        container where it is too. A removal counts even before its first step, because
        recovery finishes one that the process died inside. An adoption counts too. Recovery
        reads it to tell an unfinished handoff from a create, because the container it finds
        still belongs to the previous runtime.
        """
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT operations.id FROM operations
                WHERE instance_id = ? AND kind NOT IN (?, ?)
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
                    OperationKind.LOGIN.value,
                    OperationKind.REMOVE.value,
                    OperationKind.ADOPT.value,
                    OperationState.SUCCEEDED.value,
                ),
            ).fetchone()
        return self.operation(UUID(row[0])) if row is not None else None

    def factory_members(self, factory: FactoryName) -> list[ManagedInstance]:
        """The instances holding a role of this factory and not deleted, in creation order."""
        with self._connect() as connection:
            ids = [
                UUID(row[0])
                for row in connection.execute(
                    """
                    SELECT instance_id FROM factory_members
                    JOIN instances ON instances.id = factory_members.instance_id
                    WHERE factory = ? AND intended_state != ? ORDER BY instances.rowid
                    """,
                    (factory, IntendedState.DELETED.value),
                ).fetchall()
            ]
        return [record for instance_id in ids if (record := self.instance(instance_id)) is not None]

    def listed_instances(self, *, removed: bool = False) -> list[ManagedInstance]:
        """The active instances, or the removed ones whose records and storage the hub retains."""
        with self._connect() as connection:
            ids = [
                UUID(row[0])
                for row in connection.execute(
                    "SELECT id FROM instances WHERE prepared = 1 ORDER BY rowid"
                ).fetchall()
            ]
        return [
            record
            for instance_id in ids
            if (record := self.instance(instance_id)) is not None
            and (record.intended_state is IntendedState.REMOVED if removed else record.active)
        ]

    def image_artifact(self, input_key: str) -> ImageArtifact | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT image_id, revision, dependency_id, base_images, dependencies
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
        )

    def record_image_artifact(self, input_key: str, artifact: ImageArtifact) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO image_artifacts (
                    input_key, image_id, revision, dependency_id, base_images, dependencies
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    input_key,
                    artifact.image_id,
                    artifact.revision,
                    artifact.dependency_id,
                    json.dumps(artifact.base_images),
                    json.dumps(artifact.dependencies),
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

    def unreferenced_images(self) -> list[str]:
        """Recorded images no instance that is not deleted names, as its image or candidate.

        A removed instance keeps its image for a restoration (ADR 0053), and an instance
        whose update failed still names its previous image (ADR 0035). Deletion leaves
        the candidate row a failed update staged.
        """
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT image_id FROM image_artifacts
                WHERE image_id NOT IN (
                    SELECT image_id FROM instances
                    WHERE intended_state != ? AND image_id IS NOT NULL
                    UNION
                    SELECT update_candidates.image_id FROM update_candidates
                    JOIN instances ON instances.id = update_candidates.instance_id
                    WHERE instances.intended_state != ?
                )
                GROUP BY image_id ORDER BY MIN(rowid)
                """,
                (IntendedState.DELETED.value, IntendedState.DELETED.value),
            ).fetchall()
        return [row[0] for row in rows]

    def forget_image(self, image_id: str) -> None:
        """Drop every artifact and description of an image that is gone."""
        with self._connect() as connection:
            connection.execute("DELETE FROM image_artifacts WHERE image_id = ?", (image_id,))
            connection.execute("DELETE FROM image_descriptions WHERE image_id = ?", (image_id,))

    def preparing_images(self) -> bool:
        """Whether a create, update or image preparation is unfinished.

        Each one holds an image before any record names it.
        """
        with self._connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM operations WHERE kind IN (?, ?, ?) AND state IN (?, ?) LIMIT 1",
                (
                    OperationKind.CREATE.value,
                    OperationKind.UPDATE.value,
                    OperationKind.PREPARE.value,
                    OperationState.PENDING.value,
                    OperationState.RUNNING.value,
                ),
            ).fetchone()
        return row is not None

    def shipped_skills(self) -> dict[SkillName, FileHash]:
        """The hash of each shipped skill the hub copied into its library, by name."""
        with self._connect() as connection:
            rows = connection.execute("SELECT name, hash FROM shipped_skills").fetchall()
        return {SkillName(name): FileHash(hash) for name, hash in rows}

    def record_shipped_skill(self, name: SkillName, copied: FileHash) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO shipped_skills (name, hash) VALUES (?, ?)", (name, copied)
            )

    def git_skills(self) -> dict[SkillName, GitSource]:
        """Where each skill the user added to the library from git came from, by name."""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT name, url, path, ref, git_commit FROM git_skills"
            ).fetchall()
        return {
            SkillName(name): GitSource(url=url, path=path, ref=ref, commit=commit)
            for name, url, path, ref, commit in rows
        }

    def record_git_skill(self, name: SkillName, source: GitSource, landed: FileHash) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO git_skills (name, url, path, ref, git_commit, hash)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (name, source.url, source.path, source.ref, source.commit, landed),
            )

    def forget_git_skill(self, name: SkillName) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM git_skills WHERE name = ?", (name,))

    def landed_skills(self) -> dict[SkillName, FileHash]:
        """The hash each shipped or git skill had when it landed in the library, by name.

        A git skill's wins over a shipped one's of the same name, which the user deleted.
        """
        with self._connect() as connection:
            shipped = connection.execute("SELECT name, hash FROM shipped_skills").fetchall()
            git = connection.execute(
                "SELECT name, hash FROM git_skills WHERE hash IS NOT NULL"
            ).fetchall()
        return {SkillName(name): FileHash(hash) for name, hash in (*shipped, *git)}

    def unhashed_git_skills(self) -> list[SkillName]:
        """Each git skill added before the hub recorded the hash it landed with."""
        with self._connect() as connection:
            rows = connection.execute("SELECT name FROM git_skills WHERE hash IS NULL").fetchall()
        return [SkillName(name) for (name,) in rows]

    def record_landed_git_skill(self, name: SkillName, landed: FileHash) -> None:
        with self._connect() as connection:
            connection.execute("UPDATE git_skills SET hash = ? WHERE name = ?", (landed, name))

    def open_run(
        self,
        instance_id: UUID,
        factory: FactoryName,
        work_item: Mapping[ValueName, StepValue],
        step: StepId,
    ) -> tuple[FactoryRun, bool]:
        """Queue a new run at its first step, or find the instance's unfinished run of the same
        work item.

        True when the run is new.
        """
        key = _work_item_key(work_item)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            open_run = connection.execute(
                f"""
                SELECT {_RUN_COLUMNS} FROM factory_runs
                WHERE instance_id = ? AND work_item = ? AND status NOT IN (?, ?)
                ORDER BY rowid LIMIT 1
                """,
                (str(instance_id), key, *_FINISHED),
            ).fetchone()
            if open_run is not None:
                return _factory_run(open_run), False
            now = _now()
            run_id = uuid4()
            connection.execute(
                """
                INSERT INTO factory_runs (
                    id, factory, instance_id, work_item, status, step, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(run_id),
                    factory,
                    str(instance_id),
                    key,
                    FactoryRunStatus.QUEUED.value,
                    step,
                    now,
                    now,
                ),
            )
        return self._existing_run(run_id), True

    def attribute_runs(self, intake: Callable[[FactoryName], InstanceName | None]) -> None:
        """Give each run stored before runs recorded their instance to its factory's intake.

        *intake* names the factory's instance of the intake. The newest instance by that name gets
        the runs, or the factory's newest instance when none has that name.
        """
        with self._connect() as connection:
            factories = [
                row[0]
                for row in connection.execute(
                    "SELECT DISTINCT factory FROM factory_runs WHERE instance_id IS NULL"
                )
            ]
            for factory in factories:
                connection.execute(
                    """
                    UPDATE factory_runs SET instance_id = (
                        SELECT factory_members.instance_id FROM factory_members
                        JOIN instances ON instances.id = factory_members.instance_id
                        WHERE factory_members.factory = factory_runs.factory
                        ORDER BY factory_members.name = ? DESC, instances.rowid DESC LIMIT 1
                    )
                    WHERE factory = ? AND instance_id IS NULL
                    """,
                    (intake(factory), factory),
                )

    def run(self, run_id: UUID) -> FactoryRun | None:
        with self._connect() as connection:
            row = connection.execute(
                f"SELECT {_RUN_COLUMNS} FROM factory_runs WHERE id = ?", (str(run_id),)
            ).fetchone()
        return _factory_run(row) if row is not None else None

    def runs(
        self,
        factory: FactoryName | None,
        status: FactoryRunStatus | None,
        instance_id: UUID | None = None,
    ) -> list[FactoryRun]:
        """The factory's runs, or every factory's, oldest first, in one status or in any.

        With *instance_id*, only the runs that instance handed in.
        """
        wanted = status.value if status is not None else None
        owner = str(instance_id) if instance_id is not None else None
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT {_RUN_COLUMNS} FROM factory_runs
                WHERE (? IS NULL OR factory = ?) AND (? IS NULL OR status = ?)
                    AND (? IS NULL OR instance_id = ?)
                ORDER BY rowid
                """,
                (factory, factory, wanted, wanted, owner, owner),
            ).fetchall()
        return [_factory_run(row) for row in rows]

    def runs_in(self, status: FactoryRunStatus) -> list[FactoryRun]:
        """Every run of every factory in *status*, in the order it moved there."""
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT {_RUN_COLUMNS} FROM factory_runs WHERE status = ?
                ORDER BY updated_at, rowid
                """,
                (status.value,),
            ).fetchall()
        return [_factory_run(row) for row in rows]

    def move_run(
        self,
        run_id: UUID,
        status: FactoryRunStatus,
        step: StepId | None,
        wakes_at: datetime | None = None,
        *,
        ended_call: bool = False,
    ) -> FactoryRun:
        """Put the run in *status* at *step*, waking at *wakes_at* when it parks until then and
        owing the factory's ended call when *ended_call*.

        Moving it to done, cancelled or needs_human is a stop: it bumps the run's stop generation
        and records the stop for its instance, so each stop is told once and an earlier stop's
        report can no longer claim it. A move to needs_human also resets its reported flag, so
        this fresh stop gets its own report.
        """
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE factory_runs
                SET status = ?, step = ?, wakes_at = ?, updated_at = ?, ended_call = ?
                WHERE id = ?
                """,
                (
                    status.value,
                    step,
                    wakes_at.isoformat() if wakes_at is not None else None,
                    _now(),
                    int(ended_call),
                    str(run_id),
                ),
            )
            if status is FactoryRunStatus.NEEDS_HUMAN:
                connection.execute(
                    "UPDATE factory_runs SET reported = 0 WHERE id = ?", (str(run_id),)
                )
            if status in STOPS:
                connection.execute(
                    "UPDATE factory_runs SET stop_generation = stop_generation + 1 WHERE id = ?",
                    (str(run_id),),
                )
                connection.execute(
                    """
                    INSERT INTO run_stops (run_id, generation, status, last_attempt)
                    SELECT id, stop_generation, status, (
                        SELECT COALESCE(MAX(rowid), 0) FROM step_attempts
                        WHERE step_attempts.run_id = factory_runs.id
                    )
                    FROM factory_runs WHERE id = ?
                    """,
                    (str(run_id),),
                )
        return self._existing_run(run_id)

    def stop_generation(self, run_id: UUID) -> int:
        """How many times the run has stopped, which names its latest stop."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT stop_generation FROM factory_runs WHERE id = ?", (str(run_id),)
            ).fetchone()
        return row[0] if row is not None else 0

    def mark_reported(self, run_id: UUID, generation: int) -> None:
        """Record that the run's needs_human report for *generation* was delivered.

        A later stop already bumped the generation past it, so this leaves that fresher stop's
        reported flag untouched: a report still in flight for an earlier stop can't claim it.
        """
        with self._connect() as connection:
            connection.execute(
                "UPDATE factory_runs SET reported = 1 WHERE id = ? AND stop_generation = ?",
                (str(run_id), generation),
            )

    def unreported_runs(self) -> list[FactoryRun]:
        """The needs_human runs whose report was not yet delivered, in the order they stopped."""
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT {_RUN_COLUMNS} FROM factory_runs
                WHERE status = ? AND reported = 0
                ORDER BY updated_at, rowid
                """,
                (FactoryRunStatus.NEEDS_HUMAN.value,),
            ).fetchall()
        return [_factory_run(row) for row in rows]

    def mark_ended_called(self, run_id: UUID) -> None:
        """Record that the factory's ended call for the run was made."""
        with self._connect() as connection:
            connection.execute(
                "UPDATE factory_runs SET ended_call = 0 WHERE id = ?", (str(run_id),)
            )

    def uncalled_ends(self) -> list[FactoryRun]:
        """The runs that ended and still owe the factory's ended call, in the order they ended."""
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT {_RUN_COLUMNS} FROM factory_runs
                WHERE ended_call = 1
                ORDER BY updated_at, rowid
                """
            ).fetchall()
        return [_factory_run(row) for row in rows]

    def run_stop(self, run_id: UUID, generation: int) -> RunStop:
        """The run's stop of *generation*, with the attempts since the stop before it."""
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT factory, work_item, run_stops.status, last_attempt, (
                    SELECT COALESCE(MAX(earlier.last_attempt), 0) FROM run_stops AS earlier
                    WHERE earlier.run_id = run_stops.run_id
                        AND earlier.generation < run_stops.generation
                )
                FROM run_stops JOIN factory_runs ON factory_runs.id = run_stops.run_id
                WHERE run_stops.run_id = ? AND run_stops.generation = ?
                """,
                (str(run_id), generation),
            ).fetchone()
        if row is None:
            raise ValueError(f'Factory run "{run_id}" has no stop {generation}.')
        factory, work_item, status, last_attempt, after = row
        return RunStop(
            run_id=run_id,
            factory=factory,
            work_item=json.loads(work_item),
            status=FactoryRunStatus(status),
            generation=generation,
            attempts=self._attempts_after(run_id, after, last_attempt),
        )

    def mark_told(self, run_id: UUID, generation: int) -> None:
        """Record that the run's instance took its stop of *generation*."""
        with self._connect() as connection:
            connection.execute(
                "UPDATE run_stops SET told = 1 WHERE run_id = ? AND generation = ?",
                (str(run_id), generation),
            )

    def untold_stops(self) -> list[RunStop]:
        """The stops no instance took yet, in the order the runs stopped."""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT run_id, generation FROM run_stops WHERE told = 0 ORDER BY rowid"
            ).fetchall()
        return [self.run_stop(UUID(run_id), generation) for run_id, generation in rows]

    def restart_run(self, run_id: UUID, step: StepId | None) -> FactoryRun:
        """Queue the run at *step* for the user, its retries and send-backs counted afresh."""
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE factory_runs SET status = ?, step = ?, updated_at = ?, restarted_after = (
                    SELECT COALESCE(MAX(rowid), 0) FROM step_attempts WHERE run_id = ?
                )
                WHERE id = ?
                """,
                (FactoryRunStatus.QUEUED.value, step, _now(), str(run_id), str(run_id)),
            )
        return self._existing_run(run_id)

    def begin_attempt(
        self,
        run_id: UUID,
        step: StepId,
        status: FactoryRunStatus = FactoryRunStatus.RUNNING,
    ) -> FactoryRun:
        """Open the next attempt at *step*, and put the run in *status* at it.

        A run parked at a wait or an approval keeps its attempt open until it moves on.
        """
        now = _now()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO step_attempts (
                    run_id, step, attempt, result_values, summary, started_at
                )
                SELECT ?, ?, COUNT(*) + 1, '{}', '', ? FROM step_attempts
                WHERE run_id = ? AND step = ?
                """,
                (str(run_id), step, now, str(run_id), step),
            )
            connection.execute(
                "UPDATE factory_runs SET status = ?, step = ?, updated_at = ? WHERE id = ?",
                (status.value, step, now, str(run_id)),
            )
        return self._existing_run(run_id)

    def drop_attempt(self, run_id: UUID) -> FactoryRun:
        """Delete the run's open attempt, which is no attempt, and queue the run again."""
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM step_attempts WHERE run_id = ? AND ended_at IS NULL", (str(run_id),)
            )
            connection.execute(
                "UPDATE factory_runs SET status = ?, updated_at = ? WHERE id = ?",
                (FactoryRunStatus.QUEUED.value, _now(), str(run_id)),
            )
        return self._existing_run(run_id)

    def end_attempt(self, run_id: UUID, result: StepResult) -> None:
        """Record how the run's open attempt ended. Its result is never written again."""
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE step_attempts
                SET ending = ?, outcome = ?, result_values = ?, summary = ?, session = ?,
                    ended_at = ?
                WHERE run_id = ? AND ended_at IS NULL
                """,
                (
                    result.ending.value,
                    result.outcome,
                    json.dumps(result.values),
                    result.summary,
                    result.session,
                    _now(),
                    str(run_id),
                ),
            )

    def attempts(self, run_id: UUID) -> list[StepAttempt]:
        """Every attempt of the run, in the order they started."""
        return self._attempts_after(run_id, 0)

    def counted_attempts(self, run_id: UUID) -> list[StepAttempt]:
        """The attempts that count toward retries and send-backs: those since the user's restart."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT restarted_after FROM factory_runs WHERE id = ?", (str(run_id),)
            ).fetchone()
        return self._attempts_after(run_id, row[0] if row is not None else 0)

    def _attempts_after(
        self, run_id: UUID, after: int, through: int | None = None
    ) -> list[StepAttempt]:
        """The run's attempts whose rowid is past *after*, and up to *through* when given."""
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    step, attempt, ending, outcome, result_values, summary, session, started_at,
                    ended_at
                FROM step_attempts
                WHERE run_id = ? AND rowid > ? AND (? IS NULL OR rowid <= ?)
                ORDER BY rowid
                """,
                (str(run_id), after, through, through),
            ).fetchall()
        return [
            StepAttempt(
                step=step,
                attempt=attempt,
                ending=StepEnding(ending) if ending is not None else None,
                outcome=outcome,
                values=json.loads(values),
                summary=summary,
                session=session,
                started_at=datetime.fromisoformat(started_at),
                ended_at=datetime.fromisoformat(ended_at) if ended_at is not None else None,
            )
            for (
                step,
                attempt,
                ending,
                outcome,
                values,
                summary,
                session,
                started_at,
                ended_at,
            ) in rows
        ]

    def unfinished_attempts(self) -> list[UUID]:
        """The running runs with an attempt a previous process started and never ended.

        A parked run's open attempt is not among them: it waits on no process.
        """
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT run_id FROM step_attempts
                JOIN factory_runs ON factory_runs.id = step_attempts.run_id
                WHERE ended_at IS NULL AND factory_runs.status = ?
                ORDER BY step_attempts.rowid
                """,
                (FactoryRunStatus.RUNNING.value,),
            ).fetchall()
        return [UUID(row[0]) for row in rows]

    def _existing_run(self, run_id: UUID) -> FactoryRun:
        run = self.run(run_id)
        if run is None:
            raise ValueError(f'Factory run "{run_id}" was not found.')
        return run

    def create_channel(
        self,
        channel_id: UUID,
        name: str,
        kind: ChannelKindName,
        settings: Mapping[str, SetupValue],
        pairing: Pairing | None,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO channels (
                    id, name, kind, settings, enabled, pairing_code, pairing_expires_at
                )
                VALUES (?, ?, ?, ?, 1, ?, ?)
                """,
                (
                    str(channel_id),
                    name,
                    kind,
                    json.dumps(settings),
                    pairing.code if pairing is not None else None,
                    pairing.expires_at.isoformat() if pairing is not None else None,
                ),
            )

    def channels(self) -> list[ChannelRecord]:
        """Every channel, in the order the user set them up."""
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT {_CHANNEL_COLUMNS} FROM channels ORDER BY rowid"
            ).fetchall()
            mutes = connection.execute(
                "SELECT channel_id, instance_id FROM channel_mutes ORDER BY rowid"
            ).fetchall()
        muted: dict[str, list[UUID]] = {}
        for channel_id, instance_id in mutes:
            muted.setdefault(channel_id, []).append(UUID(instance_id))
        return [_channel(row, muted.get(row[0], [])) for row in rows]

    def channel(self, channel_id: UUID) -> ChannelRecord | None:
        return next(
            (channel for channel in self.channels() if channel.channel_id == channel_id), None
        )

    def update_channel(
        self,
        channel_id: UUID,
        *,
        name: str,
        enabled: bool,
        settings: Mapping[str, SetupValue],
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE channels SET name = ?, enabled = ?, settings = ? WHERE id = ?",
                (name, int(enabled), json.dumps(settings), str(channel_id)),
            )

    def delete_channel(self, channel_id: UUID) -> None:
        """Delete the channel, with its mutes and its deliveries."""
        with self._connect() as connection:
            connection.execute("DELETE FROM channels WHERE id = ?", (str(channel_id),))

    def pair_channel(self, channel_id: UUID, code: str, user: ChannelUser) -> None:
        """Pair the channel with *user*, unless another sender of *code* paired it first."""
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE channels
                SET paired_user = ?, pairing_code = NULL, pairing_expires_at = NULL
                WHERE id = ? AND pairing_code = ?
                """,
                (user, str(channel_id), code),
            )

    def mute(self, channel_id: UUID, instance_id: UUID) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO channel_mutes (channel_id, instance_id) VALUES (?, ?)",
                (str(channel_id), str(instance_id)),
            )

    def unmute(self, channel_id: UUID, instance_id: UUID) -> None:
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM channel_mutes WHERE channel_id = ? AND instance_id = ?",
                (str(channel_id), str(instance_id)),
            )

    def channel_sent(self, channel_id: UUID, at: datetime) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE channels SET last_sent_at = ? WHERE id = ?",
                (at.isoformat(), str(channel_id)),
            )

    def channel_failed(self, channel_id: UUID, error: str, at: datetime) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE channels SET last_error = ?, last_error_at = ? WHERE id = ?",
                (error, at.isoformat(), str(channel_id)),
            )

    def raise_notification(
        self,
        instance_id: UUID,
        key: str,
        thread_id: UUID,
        notification: ChannelNotification,
        raised_at: datetime,
        deliveries: Mapping[UUID, DeliveryStatus],
    ) -> UUID:
        """Record the notification with a delivery on each channel, unless the instance raised
        *key* before. The id of the notification the key names."""
        with self._connect() as connection:
            inserted = connection.execute(
                """
                INSERT INTO notifications (
                    id, instance_id, source_key, thread_id, header, title, text, link, options,
                    raised_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (instance_id, source_key) DO NOTHING
                """,
                (
                    str(notification.notification_id),
                    str(instance_id),
                    key,
                    str(thread_id),
                    notification.header,
                    notification.title,
                    notification.text,
                    notification.link,
                    json.dumps(notification.options),
                    raised_at.isoformat(),
                ),
            ).rowcount
            if not inserted:
                row = connection.execute(
                    "SELECT id FROM notifications WHERE instance_id = ? AND source_key = ?",
                    (str(instance_id), key),
                ).fetchone()
                return UUID(row[0])
            connection.executemany(
                """
                INSERT INTO notification_deliveries (
                    notification_id, channel_id, status, next_attempt
                )
                VALUES (?, ?, ?, ?)
                """,
                [
                    (
                        str(notification.notification_id),
                        str(channel_id),
                        status.value,
                        raised_at.isoformat(),
                    )
                    for channel_id, status in deliveries.items()
                ],
            )
        return notification.notification_id

    def pending_deliveries(self, notification_id: UUID | None = None) -> list[PendingDelivery]:
        """The deliveries still to send, of one notification or of all, oldest first."""
        only = str(notification_id) if notification_id is not None else None
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT notifications.id, header, title, text, link, options, channel_id,
                    raised_at, attempts, next_attempt
                FROM notification_deliveries
                JOIN notifications ON notifications.id = notification_deliveries.notification_id
                WHERE status = ? AND (? IS NULL OR notifications.id = ?)
                ORDER BY notification_deliveries.rowid
                """,
                (DeliveryStatus.PENDING.value, only, only),
            ).fetchall()
        return [_pending_delivery(row) for row in rows]

    def delivery_sent(self, notification_id: UUID, channel_id: UUID, ref: SentRef) -> None:
        self._end_delivery(notification_id, channel_id, DeliveryStatus.SENT, sent_ref=ref)

    def delivery_failed(self, notification_id: UUID, channel_id: UUID, error: str) -> None:
        """The delivery failed for good."""
        self._end_delivery(notification_id, channel_id, DeliveryStatus.FAILED, error=error)

    def delivery_retried(
        self, notification_id: UUID, channel_id: UUID, error: str, next_attempt: datetime
    ) -> None:
        """Count a failed send, and when the next one is due."""
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE notification_deliveries
                SET attempts = attempts + 1, last_error = ?, next_attempt = ?
                WHERE notification_id = ? AND channel_id = ?
                """,
                (error, next_attempt.isoformat(), str(notification_id), str(channel_id)),
            )

    def _end_delivery(
        self,
        notification_id: UUID,
        channel_id: UUID,
        status: DeliveryStatus,
        *,
        sent_ref: SentRef | None = None,
        error: str | None = None,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE notification_deliveries
                SET status = ?, sent_ref = ?, last_error = COALESCE(?, last_error)
                WHERE notification_id = ? AND channel_id = ?
                """,
                (status.value, sent_ref, error, str(notification_id), str(channel_id)),
            )

    def notifications(
        self,
        *,
        instance_id: UUID | None = None,
        thread_id: UUID | None = None,
        notification_id: UUID | None = None,
    ) -> list[Notification]:
        """The notifications each given filter matches, oldest first."""
        matching = """
            SELECT id FROM notifications
            WHERE (:instance IS NULL OR instance_id = :instance)
                AND (:thread IS NULL OR thread_id = :thread)
                AND (:notification IS NULL OR id = :notification)
        """
        filters = {
            "instance": str(instance_id) if instance_id is not None else None,
            "thread": str(thread_id) if thread_id is not None else None,
            "notification": str(notification_id) if notification_id is not None else None,
        }
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT id, instance_id, thread_id, header, title, text, link, options, raised_at
                FROM notifications WHERE id IN ({matching}) ORDER BY rowid
                """,
                filters,
            ).fetchall()
            deliveries = connection.execute(
                f"""
                SELECT notification_id, channel_id, status FROM notification_deliveries
                WHERE notification_id IN ({matching}) ORDER BY rowid
                """,
                filters,
            ).fetchall()
            replies = connection.execute(
                f"""
                SELECT notification_id, option, channel_id, received_at, status, outcome
                FROM replies WHERE notification_id IN ({matching}) ORDER BY rowid
                """,
                filters,
            ).fetchall()
        delivered: dict[str, list[NotificationDelivery]] = {}
        for notification, channel_id, status in deliveries:
            delivered.setdefault(notification, []).append(
                NotificationDelivery(channel_id=UUID(channel_id), status=DeliveryStatus(status))
            )
        replied: dict[str, list[NotificationReply]] = {}
        for notification, option, channel_id, received_at, status, outcome in replies:
            replied.setdefault(notification, []).append(
                NotificationReply(
                    option=option,
                    channel_id=UUID(channel_id) if channel_id is not None else None,
                    received_at=datetime.fromisoformat(received_at),
                    status=ReplyStatus(status),
                    outcome=outcome,
                )
            )
        return [
            Notification(
                notification_id=UUID(row[0]),
                instance_id=UUID(row[1]),
                thread_id=UUID(row[2]),
                header=row[3],
                title=row[4],
                text=row[5],
                link=row[6],
                options=json.loads(row[7]),
                raised_at=datetime.fromisoformat(row[8]),
                deliveries=delivered.get(row[0], []),
                replies=replied.get(row[0], []),
            )
            for row in rows
        ]

    def sent_notification(self, channel_id: UUID, message: SentRef) -> Notification | None:
        """The notification the channel sent as *message*."""
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT notification_id FROM notification_deliveries
                WHERE channel_id = ? AND sent_ref = ?
                """,
                (str(channel_id), message),
            ).fetchone()
        if row is None:
            return None
        return next(iter(self.notifications(notification_id=UUID(row[0]))), None)

    def record_reply(
        self,
        reply_id: UUID,
        notification_id: UUID,
        channel_id: UUID | None,
        option: str | None,
        received_at: datetime,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO replies (id, notification_id, channel_id, option, received_at, status)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    str(reply_id),
                    str(notification_id),
                    str(channel_id) if channel_id is not None else None,
                    option,
                    received_at.isoformat(),
                    ReplyStatus.RECEIVED.value,
                ),
            )

    def received_replies(self, reply_id: UUID | None = None) -> list[ReceivedReply]:
        """The replies still to apply, one or all, oldest first."""
        only = str(reply_id) if reply_id is not None else None
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT replies.id, replies.notification_id, instance_id, thread_id, option,
                    received_at, replies.channel_id, sent_ref, calling
                FROM replies
                JOIN notifications ON notifications.id = replies.notification_id
                LEFT JOIN notification_deliveries
                    ON notification_deliveries.notification_id = replies.notification_id
                    AND notification_deliveries.channel_id = replies.channel_id
                WHERE replies.status = ? AND (? IS NULL OR replies.id = ?)
                ORDER BY replies.rowid
                """,
                (ReplyStatus.RECEIVED.value, only, only),
            ).fetchall()
        return [
            ReceivedReply(
                reply_id=UUID(row[0]),
                notification_id=UUID(row[1]),
                instance_id=UUID(row[2]),
                thread_id=UUID(row[3]),
                option=row[4],
                received_at=datetime.fromisoformat(row[5]),
                channel_id=UUID(row[6]) if row[6] is not None else None,
                message=SentRef(row[7]) if row[7] is not None else None,
                calling=bool(row[8]),
            )
            for row in rows
        ]

    def answered(self, notification_id: UUID) -> bool:
        """Whether a reply with one of the notification's options was applied."""
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT 1 FROM replies
                WHERE notification_id = ? AND status = ? AND option IS NOT NULL
                """,
                (str(notification_id), ReplyStatus.APPLIED.value),
            ).fetchone()
        return row is not None

    def reply_calling(self, reply_id: UUID, *, calling: bool) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE replies SET calling = ? WHERE id = ?", (int(calling), str(reply_id))
            )

    def settle_reply(self, reply_id: UUID, status: ReplyStatus, outcome: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE replies SET status = ?, outcome = ? WHERE id = ?",
                (status.value, outcome, str(reply_id)),
            )


_RUN_COLUMNS = "id, factory, instance_id, work_item, status, step, wakes_at, created_at, updated_at"
_CHANNEL_COLUMNS = (
    "id, name, kind, settings, enabled, paired_user, pairing_code, pairing_expires_at, "
    "last_sent_at, last_error, last_error_at"
)
#: The statuses a run never leaves.
_FINISHED = (FactoryRunStatus.DONE.value, FactoryRunStatus.CANCELLED.value)
#: The statuses a run stops in, which the hub tells the instance that handed it in.
STOPS = (FactoryRunStatus.DONE, FactoryRunStatus.CANCELLED, FactoryRunStatus.NEEDS_HUMAN)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _work_item_key(work_item: Mapping[ValueName, StepValue]) -> str:
    """One work item as one string, whatever order its values came in."""
    return json.dumps(work_item, sort_keys=True)


def _factory_run(row: tuple[str, ...]) -> FactoryRun:
    run_id, factory, instance_id, work_item, status, step, wakes_at, created_at, updated_at = row
    return FactoryRun(
        run_id=UUID(run_id),
        factory=factory,
        instance_id=UUID(instance_id),
        work_item=json.loads(work_item),
        status=FactoryRunStatus(status),
        step=step,
        wakes_at=datetime.fromisoformat(wakes_at) if wakes_at is not None else None,
        created_at=datetime.fromisoformat(created_at),
        updated_at=datetime.fromisoformat(updated_at),
    )


def _channel(row: tuple[str, ...], muted: list[UUID]) -> ChannelRecord:
    (
        channel_id,
        name,
        kind,
        settings,
        enabled,
        paired_user,
        pairing_code,
        pairing_expires_at,
        last_sent_at,
        last_error,
        last_error_at,
    ) = row
    return ChannelRecord(
        channel_id=UUID(channel_id),
        name=name,
        kind=kind,
        settings=json.loads(settings),
        enabled=bool(enabled),
        paired_user=ChannelUser(paired_user) if paired_user is not None else None,
        pairing=(
            Pairing(pairing_code, datetime.fromisoformat(pairing_expires_at))
            if pairing_code is not None
            else None
        ),
        muted=tuple(muted),
        last_sent_at=datetime.fromisoformat(last_sent_at) if last_sent_at is not None else None,
        last_failure=(
            ChannelFailure(at=datetime.fromisoformat(last_error_at), error=last_error)
            if last_error_at is not None
            else None
        ),
    )


def _pending_delivery(row: tuple[str, ...]) -> PendingDelivery:
    (
        notification_id,
        header,
        title,
        text,
        link,
        options,
        channel_id,
        raised_at,
        attempts,
        next_attempt,
    ) = row
    return PendingDelivery(
        notification=ChannelNotification(
            notification_id=UUID(notification_id),
            header=header,
            title=title,
            text=text,
            link=link,
            options=tuple(json.loads(options)),
        ),
        channel_id=UUID(channel_id),
        raised_at=datetime.fromisoformat(raised_at),
        attempts=int(attempts),
        next_attempt=datetime.fromisoformat(next_attempt),
    )
