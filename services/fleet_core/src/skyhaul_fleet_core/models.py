"""All fleet tables (docs/architecture/m2-design.md, section 3). Single organization: no tenant.

Enum values equal the OpenAPI contract enums of the same name. Changing a model needs a new
Alembic revision; ``alembic check`` in CI fails on drift.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any, ClassVar

from skyhaul_contracts.topology import DRONE_ID_RE
from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Identity,
    Index,
    MetaData,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import CITEXT, INET, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Stable constraint names, so migrations can refer to them.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

DRONE_ID_SQL_PATTERN = f"^{DRONE_ID_RE.pattern}$"


class Role(StrEnum):
    ADMIN = "admin"
    DISPATCHER = "dispatcher"
    VIEWER = "viewer"


class CommandStatus(StrEnum):
    PENDING = "PENDING"
    SENT = "SENT"
    ACKED = "ACKED"
    NACKED = "NACKED"
    TIMED_OUT = "TIMED_OUT"
    FAILED = "FAILED"


class MissionState(StrEnum):
    """The contract's ``MissionLifecycleState``."""

    PLANNED = "PLANNED"
    DISPATCHING = "DISPATCHING"
    ACTIVE = "ACTIVE"
    COMPLETED = "COMPLETED"
    ABORTED = "ABORTED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class AlertCode(StrEnum):
    """The contract's ``FleetAlertCode``: the drone AlertCode plus the derived LINK_LOST."""

    LOW_BATTERY = "LOW_BATTERY"
    CRITICAL_BATTERY = "CRITICAL_BATTERY"
    GEOFENCE_BREACH = "GEOFENCE_BREACH"
    LINK_RESTORED = "LINK_RESTORED"
    LINK_LOST = "LINK_LOST"


class AlertSeverity(StrEnum):
    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


class AlertSource(StrEnum):
    DRONE = "DRONE"
    DERIVED = "DERIVED"


def _enum(cls: type[StrEnum], name: str) -> Enum:
    return Enum(cls, name=name, values_callable=lambda members: [m.value for m in members])


def _created_at() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now())


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    type_annotation_map: ClassVar[dict[Any, Any]] = {
        uuid.UUID: UUID(as_uuid=True),
        datetime: DateTime(timezone=True),
        str: Text(),
        dict[str, Any]: JSONB(),
    }


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(CITEXT(), unique=True)
    password_hash: Mapped[str]
    role: Mapped[Role] = mapped_column(_enum(Role, "user_role"))
    is_active: Mapped[bool] = mapped_column(server_default=text("true"))
    created_at: Mapped[datetime] = _created_at()


class Drone(Base):
    """A registered drone. The id is global and never reused: delete is soft (``deleted_at``)."""

    __tablename__ = "drones"
    __table_args__ = (CheckConstraint(f"id ~ '{DRONE_ID_SQL_PATTERN}'", name="drone_id"),)

    id: Mapped[str] = mapped_column(primary_key=True)
    name: Mapped[str]
    home_lat: Mapped[float]
    home_lon: Mapped[float]
    created_at: Mapped[datetime] = _created_at()
    deleted_at: Mapped[datetime | None]


class Mission(Base):
    __tablename__ = "missions"
    __table_args__ = (
        # One live mission per drone.
        Index(
            "uq_missions_live_drone",
            "drone_id",
            unique=True,
            postgresql_where=text("state IN ('DISPATCHING', 'ACTIVE')"),
        ),
        Index("ix_missions_state_created_at", "state", text("created_at DESC")),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    drone_id: Mapped[str] = mapped_column(ForeignKey("drones.id"))
    state: Mapped[MissionState] = mapped_column(_enum(MissionState, "mission_state"))
    waypoints: Mapped[list[dict[str, Any]]] = mapped_column(JSONB())
    correlation_id: Mapped[uuid.UUID]
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = _created_at()
    started_at: Mapped[datetime | None]
    ended_at: Mapped[datetime | None]
    failure_reason: Mapped[str | None]


class MissionEvent(Base):
    __tablename__ = "mission_events"
    __table_args__ = (Index("ix_mission_events_mission_id_ts", "mission_id", "ts"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    mission_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("missions.id"))
    ts: Mapped[datetime]
    kind: Mapped[str]
    data: Mapped[dict[str, Any]] = mapped_column(server_default=text("'{}'::jsonb"))


class Command(Base):
    """One command message. ``id`` is the envelope ``msg_id``."""

    __tablename__ = "commands"
    __table_args__ = (
        UniqueConstraint("requested_by", "idempotency_key"),
        # The sweeper scans only commands waiting for a reply.
        Index("ix_commands_sent_at_sent", "sent_at", postgresql_where=text("status = 'SENT'")),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    drone_id: Mapped[str] = mapped_column(ForeignKey("drones.id"))
    mission_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("missions.id"))
    type: Mapped[str]
    payload: Mapped[dict[str, Any]]
    correlation_id: Mapped[uuid.UUID]
    status: Mapped[CommandStatus] = mapped_column(_enum(CommandStatus, "command_status"))
    nack_reason: Mapped[str | None]
    nack_detail: Mapped[str | None]
    late: Mapped[bool] = mapped_column(server_default=text("false"))
    requested_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    idempotency_key: Mapped[str | None]
    created_at: Mapped[datetime] = _created_at()
    sent_at: Mapped[datetime | None]
    replied_at: Mapped[datetime | None]


class Alert(Base):
    __tablename__ = "alerts"
    __table_args__ = (
        # One open derived alert per drone and code.
        Index(
            "uq_alerts_open_derived",
            "drone_id",
            "code",
            unique=True,
            postgresql_where=text("source = 'DERIVED' AND cleared_at IS NULL"),
        ),
        Index("ix_alerts_cleared_at_raised_at", "cleared_at", text("raised_at DESC")),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    drone_id: Mapped[str] = mapped_column(ForeignKey("drones.id"))
    code: Mapped[AlertCode] = mapped_column(_enum(AlertCode, "alert_code"))
    severity: Mapped[AlertSeverity] = mapped_column(_enum(AlertSeverity, "alert_severity"))
    message: Mapped[str]
    value: Mapped[float | None]
    source: Mapped[AlertSource] = mapped_column(_enum(AlertSource, "alert_source"))
    msg_id: Mapped[uuid.UUID | None] = mapped_column(unique=True)
    raised_at: Mapped[datetime]
    acknowledged_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    acknowledged_at: Mapped[datetime | None]
    cleared_at: Mapped[datetime | None]


class Telemetry(Base):
    """Stored telemetry (ADR 0012). No foreign key: the ingestor filters unregistered drones
    and retention deletes in bulk."""

    __tablename__ = "telemetry"
    __table_args__ = (
        Index("ix_telemetry_drone_id_type_ts", "drone_id", "type", text("ts DESC")),
        # Retention deletes by ts alone; without this every batch scans the table.
        Index("ix_telemetry_ts", "ts"),
    )

    msg_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    drone_id: Mapped[str]
    type: Mapped[str]
    ts: Mapped[datetime]
    received_at: Mapped[datetime] = mapped_column(server_default=func.now())
    # Null for drone-initiated telemetry (HEARTBEAT, BATTERY, ALERT) per the contracts.
    correlation_id: Mapped[uuid.UUID | None]
    payload: Mapped[dict[str, Any]]


class AuditEntry(Base):
    """Insert-only audit trail. ``data`` never holds secrets."""

    __tablename__ = "audit_log"
    __table_args__ = (Index("ix_audit_log_ts", text("ts DESC")),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    # Null for a failed login with an unknown email.
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str]
    target_type: Mapped[str | None]
    target_id: Mapped[str | None]
    outcome: Mapped[str]
    ip: Mapped[str | None] = mapped_column(INET())
    ts: Mapped[datetime] = mapped_column(server_default=func.now())
    data: Mapped[dict[str, Any]] = mapped_column(server_default=text("'{}'::jsonb"))
