from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class NasConfig(Base):
    __tablename__ = "nas_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    base_url: Mapped[str] = mapped_column(String(2048))
    port: Mapped[int] = mapped_column(Integer)
    use_https: Mapped[bool] = mapped_column(Boolean)
    username: Mapped[str] = mapped_column(String(255))
    password: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (CheckConstraint("id = 1", name="single_nas_config"),)


class NasServer(Base):
    __tablename__ = "nas_servers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    base_url: Mapped[str] = mapped_column(String(2048))
    port: Mapped[int] = mapped_column(Integer)
    use_https: Mapped[bool] = mapped_column(Boolean)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    sync_interval_minutes: Mapped[int] = mapped_column(Integer, default=30)
    full_resync_interval_hours: Mapped[int] = mapped_column(
        Integer,
        default=24,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    credential: Mapped["NasCredential | None"] = relationship(
        back_populates="server",
        cascade="all, delete-orphan",
        uselist=False,
    )

    __table_args__ = (
        UniqueConstraint("name", name="uq_nas_servers_name"),
        CheckConstraint(
            "port BETWEEN 1 AND 65535",
            name="nas_server_port_range",
        ),
        CheckConstraint(
            "sync_interval_minutes >= 1",
            name="nas_server_sync_interval_positive",
        ),
        CheckConstraint(
            "full_resync_interval_hours >= 1",
            name="nas_server_full_resync_interval_positive",
        ),
    )


class NasCredential(Base):
    __tablename__ = "nas_credentials"

    nas_id: Mapped[int] = mapped_column(
        ForeignKey("nas_servers.id", ondelete="CASCADE"),
        primary_key=True,
    )
    username: Mapped[str] = mapped_column(String(255))
    password: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    server: Mapped[NasServer] = relationship(back_populates="credential")


class Entry(Base):
    __tablename__ = "entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    nas_id: Mapped[int] = mapped_column(
        Integer,
        default=1,
        server_default="1",
    )
    share_path: Mapped[str] = mapped_column(
        Text,
        default="/",
        server_default="/",
    )
    name: Mapped[str] = mapped_column(Text)
    full_path: Mapped[str] = mapped_column(Text)
    parent_path: Mapped[str] = mapped_column(Text)
    entry_type: Mapped[str] = mapped_column(String(16))
    size_bytes: Mapped[int | None] = mapped_column(Integer)
    modified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    scan_generation: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("nas_id", "full_path", name="uq_entries_nas_full_path"),
        CheckConstraint(
            "entry_type IN ('file', 'directory')",
            name="entry_type_values",
        ),
        Index("ix_entries_nas_share", "nas_id", "share_path"),
        Index("ix_entries_nas_parent_path", "nas_id", "parent_path"),
        Index("ix_entries_nas_entry_type", "nas_id", "entry_type"),
        Index("ix_entries_nas_generation", "nas_id", "scan_generation"),
    )


class ShareSyncState(Base):
    __tablename__ = "share_sync_state"

    nas_id: Mapped[int] = mapped_column(
        ForeignKey("nas_servers.id", ondelete="CASCADE"),
        primary_key=True,
    )
    share_path: Mapped[str] = mapped_column(Text, primary_key=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    last_full_synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    next_sync_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_generation: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    last_error: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'running', 'succeeded', 'failed')",
            name="share_sync_status_values",
        ),
        Index("ix_share_sync_due", "next_sync_at", "status"),
    )


class SyncRun(Base):
    __tablename__ = "sync_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    nas_id: Mapped[int] = mapped_column(
        ForeignKey("nas_servers.id", ondelete="CASCADE")
    )
    scope: Mapped[str] = mapped_column(String(16))
    share_path: Mapped[str | None] = mapped_column(Text)
    generation: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    processed_entries: Mapped[int] = mapped_column(Integer, default=0)
    current_path: Mapped[str | None] = mapped_column(Text)
    error_summary: Mapped[str | None] = mapped_column(Text)
    errors: Mapped[list["SyncError"]] = relationship(
        back_populates="sync_run",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        CheckConstraint(
            "scope IN ('nas', 'share', 'directory')",
            name="sync_scope_values",
        ),
        CheckConstraint(
            "status IN ('running', 'succeeded', 'failed', 'interrupted')",
            name="sync_status_values",
        ),
        Index("ix_sync_runs_nas_id", "nas_id"),
        Index("ix_sync_runs_generation", "generation"),
    )


class SyncError(Base):
    __tablename__ = "sync_errors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sync_run_id: Mapped[int] = mapped_column(
        ForeignKey("sync_runs.id", ondelete="CASCADE")
    )
    path: Mapped[str] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    sync_run: Mapped[SyncRun] = relationship(back_populates="errors")


class ScanRun(Base):
    __tablename__ = "scan_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    generation: Mapped[int] = mapped_column(Integer, unique=True)
    status: Mapped[str] = mapped_column(String(16))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    processed_entries: Mapped[int] = mapped_column(Integer, default=0)
    current_path: Mapped[str | None] = mapped_column(Text)
    error_summary: Mapped[str | None] = mapped_column(Text)
    errors: Mapped[list["ScanError"]] = relationship(
        back_populates="scan_run",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('running', 'succeeded', 'failed', 'interrupted')",
            name="scan_status_values",
        ),
    )


class ScanError(Base):
    __tablename__ = "scan_errors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    scan_run_id: Mapped[int] = mapped_column(
        ForeignKey("scan_runs.id", ondelete="CASCADE")
    )
    path: Mapped[str] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    scan_run: Mapped[ScanRun] = relationship(back_populates="errors")
