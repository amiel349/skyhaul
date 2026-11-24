"""Initial fleet schema: all M2 tables (m2-design section 3).

Revision ID: 0001
Revises:
Create Date: 2026-11-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


ENUMS = (
    "user_role",
    "mission_state",
    "command_status",
    "alert_code",
    "alert_severity",
    "alert_source",
)


def upgrade() -> None:
    # Case-insensitive emails. citext is a trusted extension: the database owner may create it.
    op.execute("CREATE EXTENSION IF NOT EXISTS citext")
    op.create_table(
        "drones",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("home_lat", sa.Double(), nullable=False),
        sa.Column("home_lon", sa.Double(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("id ~ '^[a-z0-9][a-z0-9-]{0,63}$'", name=op.f("ck_drones_drone_id")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_drones")),
    )
    op.create_table(
        "telemetry",
        sa.Column("msg_id", sa.UUID(), nullable=False),
        sa.Column("drone_id", sa.Text(), nullable=False),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("correlation_id", sa.UUID(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.PrimaryKeyConstraint("msg_id", name=op.f("pk_telemetry")),
    )
    op.create_index(
        "ix_telemetry_drone_id_type_ts",
        "telemetry",
        ["drone_id", "type", sa.literal_column("ts DESC")],
        unique=False,
    )
    op.create_table(
        "users",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("email", postgresql.CITEXT(), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column(
            "role", sa.Enum("admin", "dispatcher", "viewer", name="user_role"), nullable=False
        ),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("email", name=op.f("uq_users_email")),
    )
    op.create_table(
        "alerts",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("drone_id", sa.Text(), nullable=False),
        sa.Column(
            "code",
            sa.Enum(
                "LOW_BATTERY",
                "CRITICAL_BATTERY",
                "GEOFENCE_BREACH",
                "LINK_RESTORED",
                "LINK_LOST",
                name="alert_code",
            ),
            nullable=False,
        ),
        sa.Column(
            "severity",
            sa.Enum("INFO", "WARNING", "CRITICAL", name="alert_severity"),
            nullable=False,
        ),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("value", sa.Double(), nullable=True),
        sa.Column("source", sa.Enum("DRONE", "DERIVED", name="alert_source"), nullable=False),
        sa.Column("msg_id", sa.UUID(), nullable=True),
        sa.Column("raised_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("acknowledged_by", sa.UUID(), nullable=True),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cleared_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["acknowledged_by"], ["users.id"], name=op.f("fk_alerts_acknowledged_by_users")
        ),
        sa.ForeignKeyConstraint(
            ["drone_id"], ["drones.id"], name=op.f("fk_alerts_drone_id_drones")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_alerts")),
        sa.UniqueConstraint("msg_id", name=op.f("uq_alerts_msg_id")),
    )
    op.create_index(
        "ix_alerts_cleared_at_raised_at",
        "alerts",
        ["cleared_at", sa.literal_column("raised_at DESC")],
        unique=False,
    )
    op.create_index(
        "uq_alerts_open_derived",
        "alerts",
        ["drone_id", "code"],
        unique=True,
        postgresql_where=sa.text("source = 'DERIVED' AND cleared_at IS NULL"),
    )
    op.create_table(
        "audit_log",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("actor_user_id", sa.UUID(), nullable=True),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("target_type", sa.Text(), nullable=True),
        sa.Column("target_id", sa.Text(), nullable=True),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.Column("ip", postgresql.INET(), nullable=True),
        sa.Column(
            "ts", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "data",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"], ["users.id"], name=op.f("fk_audit_log_actor_user_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_log")),
    )
    op.create_index("ix_audit_log_ts", "audit_log", [sa.literal_column("ts DESC")], unique=False)
    op.create_table(
        "missions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("drone_id", sa.Text(), nullable=False),
        sa.Column(
            "state",
            sa.Enum(
                "PLANNED",
                "DISPATCHING",
                "ACTIVE",
                "COMPLETED",
                "ABORTED",
                "FAILED",
                "CANCELLED",
                name="mission_state",
            ),
            nullable=False,
        ),
        sa.Column("waypoints", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("correlation_id", sa.UUID(), nullable=False),
        sa.Column("created_by", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name=op.f("fk_missions_created_by_users")
        ),
        sa.ForeignKeyConstraint(
            ["drone_id"], ["drones.id"], name=op.f("fk_missions_drone_id_drones")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_missions")),
    )
    op.create_index(
        "ix_missions_state_created_at",
        "missions",
        ["state", sa.literal_column("created_at DESC")],
        unique=False,
    )
    op.create_index(
        "uq_missions_live_drone",
        "missions",
        ["drone_id"],
        unique=True,
        postgresql_where=sa.text("state IN ('DISPATCHING', 'ACTIVE')"),
    )
    op.create_table(
        "commands",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("drone_id", sa.Text(), nullable=False),
        sa.Column("mission_id", sa.UUID(), nullable=True),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("correlation_id", sa.UUID(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "PENDING", "SENT", "ACKED", "NACKED", "TIMED_OUT", "FAILED", name="command_status"
            ),
            nullable=False,
        ),
        sa.Column("nack_reason", sa.Text(), nullable=True),
        sa.Column("nack_detail", sa.Text(), nullable=True),
        sa.Column("late", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("requested_by", sa.UUID(), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("replied_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["drone_id"], ["drones.id"], name=op.f("fk_commands_drone_id_drones")
        ),
        sa.ForeignKeyConstraint(
            ["mission_id"], ["missions.id"], name=op.f("fk_commands_mission_id_missions")
        ),
        sa.ForeignKeyConstraint(
            ["requested_by"], ["users.id"], name=op.f("fk_commands_requested_by_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_commands")),
        sa.UniqueConstraint(
            "requested_by", "idempotency_key", name=op.f("uq_commands_requested_by_idempotency_key")
        ),
    )
    op.create_index(
        "ix_commands_sent_at_sent",
        "commands",
        ["sent_at"],
        unique=False,
        postgresql_where=sa.text("status = 'SENT'"),
    )
    op.create_table(
        "mission_events",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("mission_id", sa.UUID(), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column(
            "data",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["mission_id"], ["missions.id"], name=op.f("fk_mission_events_mission_id_missions")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_mission_events")),
    )
    op.create_index(
        "ix_mission_events_mission_id_ts", "mission_events", ["mission_id", "ts"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_mission_events_mission_id_ts", table_name="mission_events")
    op.drop_table("mission_events")
    op.drop_index(
        "ix_commands_sent_at_sent",
        table_name="commands",
        postgresql_where=sa.text("status = 'SENT'"),
    )
    op.drop_table("commands")
    op.drop_index(
        "uq_missions_live_drone",
        table_name="missions",
        postgresql_where=sa.text("state IN ('DISPATCHING', 'ACTIVE')"),
    )
    op.drop_index("ix_missions_state_created_at", table_name="missions")
    op.drop_table("missions")
    op.drop_index("ix_audit_log_ts", table_name="audit_log")
    op.drop_table("audit_log")
    op.drop_index(
        "uq_alerts_open_derived",
        table_name="alerts",
        postgresql_where=sa.text("source = 'DERIVED' AND cleared_at IS NULL"),
    )
    op.drop_index("ix_alerts_cleared_at_raised_at", table_name="alerts")
    op.drop_table("alerts")
    op.drop_table("users")
    op.drop_index("ix_telemetry_drone_id_type_ts", table_name="telemetry")
    op.drop_table("telemetry")
    op.drop_table("drones")
    # Dropping a table keeps its enum types; drop them so upgrade can create them again.
    for name in ENUMS:
        postgresql.ENUM(name=name).drop(op.get_bind(), checkfirst=True)
