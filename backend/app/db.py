"""Database layer: SQLAlchemy 2.0 engine, sessions, and models.

The default SQLite database lives under the repo ``data/`` directory;
a relative ``DATABASE_URL`` is resolved against the repo root (BASE_DIR).
"""
from __future__ import annotations

import os
from datetime import date, datetime
from typing import Optional

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    create_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from .config import BASE_DIR, settings


class Base(DeclarativeBase):
    pass


def _resolve_db_url() -> str:
    """Resolve a relative sqlite path in DATABASE_URL against the repo root."""
    url = settings.DATABASE_URL
    prefix = "sqlite:///"
    if url.startswith(prefix):
        path = url[len(prefix):]
        if path and not os.path.isabs(path) and not path.startswith(":"):
            url = prefix + str(BASE_DIR / path)
    return url


DB_URL = _resolve_db_url()

# Make sure the sqlite file's parent directory exists.
if DB_URL.startswith("sqlite:///"):
    _db_file = DB_URL[len("sqlite:///"):]
    if _db_file and not _db_file.startswith(":"):
        parent = os.path.dirname(os.path.abspath(_db_file))
        if parent:
            os.makedirs(parent, exist_ok=True)

_engine_kwargs = (
    {"connect_args": {"check_same_thread": False, "timeout": 30}}
    if DB_URL.startswith("sqlite")
    else {}
)
engine = create_engine(DB_URL, **_engine_kwargs)

if DB_URL.startswith("sqlite"):
    from sqlalchemy import event

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_connection, _connection_record):
        """WAL journaling + a hard busy timeout: readers never queue
        behind a writer, and a stuck write lock surfaces as an error
        in 30s instead of hanging the request forever."""
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=30000")
            cursor.execute("PRAGMA synchronous=NORMAL")
        finally:
            cursor.close()
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class CompanyProfile(Base):
    __tablename__ = "company_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    sector: Mapped[str] = mapped_column(String(255), default="")
    registration_type: Mapped[str] = mapped_column(String(255), default="")
    udyam_registered: Mapped[bool] = mapped_column(Boolean, default=False)
    dpiit_startup: Mapped[bool] = mapped_column(Boolean, default=False)
    turnover_avg_3y: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    turnover_yearly: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    experience_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    local_content_pct: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    certifications_json: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    blacklisted: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class Tender(Base):
    __tablename__ = "tenders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tender_id_external: Mapped[Optional[str]] = mapped_column(
        String(255), unique=True, nullable=True
    )
    title: Mapped[str] = mapped_column(String(1024), nullable=False)
    organisation: Mapped[str] = mapped_column(String(512), default="")
    ref_no: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    category: Mapped[str] = mapped_column(String(64), default="works")
    est_value: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    est_value_known: Mapped[bool] = mapped_column(Boolean, default=True)
    emd_amount: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    emd_type: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)  # fixed/percent/none/unknown
    emd_exemption_allowed: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    bid_end_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    bid_opening_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    bid_validity_days: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    source: Mapped[str] = mapped_column(String(255), default="")
    source_url: Mapped[Optional[str]] = mapped_column(String(2048), nullable=True)
    capture_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    is_seed: Mapped[bool] = mapped_column(Boolean, default=False)
    raw_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class TenderChunk(Base):
    __tablename__ = "tender_chunks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tender_id: Mapped[int] = mapped_column(
        ForeignKey("tenders.id"), nullable=False, index=True
    )
    section: Mapped[str] = mapped_column(String(255), default="")
    page: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    chunk_index: Mapped[int] = mapped_column(Integer, default=0)
    text: Mapped[str] = mapped_column(Text, nullable=False)


class PipelineRun(Base):
    __tablename__ = "pipeline_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("company_profiles.id"), nullable=True
    )
    tender_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("tenders.id"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(64), default="pending")
    steps_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True, default=dict)
    result_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class EligibilityVerdict(Base):
    __tablename__ = "eligibility_verdicts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(
        ForeignKey("pipeline_runs.id"), nullable=False, index=True
    )
    criterion: Mapped[str] = mapped_column(String(255), nullable=False)
    required: Mapped[str] = mapped_column(Text, default="")
    actual: Mapped[str] = mapped_column(Text, default="")
    verdict: Mapped[str] = mapped_column(String(16), default="UNKNOWN")  # PASS/FAIL/UNKNOWN
    note: Mapped[str] = mapped_column(Text, default="")
    citation: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class Deadline(Base):
    __tablename__ = "deadlines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tender_id: Mapped[int] = mapped_column(
        ForeignKey("tenders.id"), nullable=False, index=True
    )
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    due_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    kind: Mapped[str] = mapped_column(String(64), default="")
    notified: Mapped[bool] = mapped_column(Boolean, default=False)


class BidPackItem(Base):
    __tablename__ = "bid_pack_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tender_id: Mapped[int] = mapped_column(
        ForeignKey("tenders.id"), nullable=False, index=True
    )
    item: Mapped[str] = mapped_column(String(512), nullable=False)
    required: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[str] = mapped_column(String(64), default="pending")


class Corrigendum(Base):
    __tablename__ = "corrigenda"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tender_id: Mapped[int] = mapped_column(
        ForeignKey("tenders.id"), nullable=False, index=True
    )
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    summary: Mapped[str] = mapped_column(Text, default="")
    raw_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


def get_db():
    """FastAPI dependency: yields a session and closes it after the request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """Create all tables (no-op if they already exist)."""
    Base.metadata.create_all(bind=engine)
