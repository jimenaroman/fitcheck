"""Saved checks (M8, pipeline step 6), plus the API's budget log and answer cache. SQLite on my laptop
and in tests, Postgres on Railway: same code.

A check stores the posting, its numbered requirements, and the FitResult as JSON, so the card can be
re-rendered and the leaderboard (decision_rules.md section 7) computed later without a model call.
Tables are created on startup. Changing a table later needs a migration (not set up yet).
"""

import hashlib
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import JSON, DateTime, Engine, Integer, String, Text, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from fitcheck.leaderboard import SavedCheck
from fitcheck.schemas import FitResult, NumberedRequirement

LOCAL_DATABASE_URL = "sqlite:///.fitcheck/fitcheck.db"  # gitignored with the rest of .fitcheck/


class Base(DeclarativeBase):
    pass


class CheckRow(Base):
    __tablename__ = "checks"

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    posting: Mapped[str] = mapped_column(Text)
    posting_sha256: Mapped[str] = mapped_column(String(64), index=True)  # L2: duplicates count once
    decision: Mapped[str] = mapped_column(String(32))
    eligibility: Mapped[str] = mapped_column(String(32))
    requirements: Mapped[list] = mapped_column(JSON)  # NumberedRequirement dumps
    result: Mapped[dict] = mapped_column(JSON)        # FitResult dump
    model: Mapped[str] = mapped_column(String(64))
    cost_usd: Mapped[float]       # spent by the latest run (the check, or its recheck)
    calls: Mapped[int]
    resume_sha256: Mapped[str] = mapped_column(String(64))  # L7: which resume it was matched against
    match_cost_usd: Mapped[float]  # matching's original cost, even if cached: estimates a recheck
    rechecked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    def created_utc(self) -> datetime:
        # Saved in UTC. SQLite drops the timezone on the way back (Postgres keeps it), so restore it.
        return self.created_at if self.created_at.tzinfo else self.created_at.replace(tzinfo=UTC)

    def saved_check(self) -> SavedCheck:
        return SavedCheck(self.id, self.posting_sha256, self.resume_sha256, self.numbered_requirements(),
                          self.fit_result())

    def fit_result(self) -> FitResult:
        return FitResult.model_validate(self.result)

    def numbered_requirements(self) -> list[NumberedRequirement]:
        return [NumberedRequirement.model_validate(r) for r in self.requirements]


def connect(url: str) -> Engine:
    """Open the database and create missing tables. Railway gives `postgresql://...`, which SQLAlchemy
    would read as the older psycopg2 driver, so the URL is pointed at psycopg (version 3) instead."""
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            url = "postgresql+psycopg://" + url.removeprefix(prefix)
    if url.startswith("sqlite:///"):
        Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(url)
    Base.metadata.create_all(engine)
    return engine


class DoneSkill(Base):
    """A gap I marked "I've done this" (L9): moves it to its own board; it stays a gap in the score."""
    __tablename__ = "done_skills"

    skill: Mapped[str] = mapped_column(String(200), primary_key=True)
    marked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AppliedRow(Base):
    """A check I marked "I applied" (M11): my success metric. A record only; no score or match uses it."""
    __tablename__ = "applied"

    check_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    applied_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class UsageRow(Base):
    """One model call attempt, for the budget guard (providers/storage.py). Times are the server's local
    time, like the CLI's file log; on Railway that's UTC, so "today" resets at midnight UTC."""
    __tablename__ = "usage"

    id: Mapped[int] = mapped_column(primary_key=True)
    time: Mapped[datetime] = mapped_column(DateTime, index=True)
    model: Mapped[str] = mapped_column(String(64))
    input_tokens: Mapped[int] = mapped_column(Integer)
    output_tokens: Mapped[int] = mapped_column(Integer)
    cost_usd: Mapped[float]
    ok: Mapped[bool]


class CacheRow(Base):
    """One saved model answer, keyed by the hash of everything that affects it (providers/cache.py)."""
    __tablename__ = "cache"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    entry: Mapped[dict] = mapped_column(JSON)


class Document(Base):
    """My resume and profile as uploaded YAML (PUT /resume, PUT /profile), so the server needs no files."""
    __tablename__ = "documents"

    name: Mapped[str] = mapped_column(String(32), primary_key=True)  # resume, profile, settings, matches_run
    text: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ListingRow(Base):
    """One posting from the internship lists (M10), with its fetched text. Refreshed about hourly."""
    __tablename__ = "listings"

    key: Mapped[str] = mapped_column(String(120), primary_key=True)  # "simplify:<id>"
    source: Mapped[str] = mapped_column(String(16))
    company: Mapped[str] = mapped_column(String(200))
    title: Mapped[str] = mapped_column(String(300))
    locations: Mapped[list] = mapped_column(JSON)
    url: Mapped[str] = mapped_column(Text)
    posted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    category: Mapped[str] = mapped_column(String(32))
    also_on: Mapped[list] = mapped_column(JSON)
    active: Mapped[bool] = mapped_column(index=True)  # false once it leaves both lists
    text: Mapped[str | None] = mapped_column(Text)
    text_status: Mapped[str | None] = mapped_column(String(16))  # None (not tried), ok, unsupported, failed

    def posted_utc(self) -> datetime:
        return self.posted_at if self.posted_at.tzinfo else self.posted_at.replace(tzinfo=UTC)


class MatchRow(Base):
    """One attempt by the daily matches run (M3): a check made for a listing on a given day."""
    __tablename__ = "matches"

    id: Mapped[int] = mapped_column(primary_key=True)
    day: Mapped[str] = mapped_column(String(10), index=True)  # America/Chicago date, "2026-11-10"
    listing_key: Mapped[str] = mapped_column(String(120), index=True)
    check_id: Mapped[int | None] = mapped_column(Integer)
    counted: Mapped[bool]  # false for review_manually: it doesn't use up one of the 10
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


def get_document(session: Session, name: str) -> str | None:
    row = session.get(Document, name)
    return row.text if row else None


def put_document(session: Session, name: str, text: str) -> None:
    session.merge(Document(name=name, text=text, updated_at=datetime.now(UTC)))
    session.commit()


def save_check(session: Session, *, posting: str, requirements: list[NumberedRequirement], result: FitResult,
               model: str, cost_usd: float, calls: int, resume_sha256: str, match_cost_usd: float,
               now: datetime | None = None) -> CheckRow:
    row = CheckRow(created_at=now or datetime.now(UTC), posting=posting,
                   posting_sha256=hashlib.sha256(posting.encode()).hexdigest())
    _fill(row, requirements, result, model, cost_usd, calls, resume_sha256, match_cost_usd)
    session.add(row)
    session.commit()
    return row


def update_check(session: Session, row: CheckRow, *, requirements: list[NumberedRequirement], result: FitResult,
                 model: str, cost_usd: float, calls: int, resume_sha256: str, match_cost_usd: float,
                 now: datetime | None = None) -> CheckRow:
    """A recheck: same posting, new result. created_at stays, so history keeps its order."""
    _fill(row, requirements, result, model, cost_usd, calls, resume_sha256, match_cost_usd)
    row.rechecked_at = now or datetime.now(UTC)
    session.commit()
    return row


def _fill(row: CheckRow, requirements, result, model, cost_usd, calls, resume_sha256, match_cost_usd) -> None:
    row.decision = result.decision.value
    row.eligibility = result.eligibility.value
    row.requirements = [r.model_dump(mode="json") for r in requirements]
    row.result = result.model_dump(mode="json")
    row.model, row.cost_usd, row.calls = model, cost_usd, calls
    row.resume_sha256, row.match_cost_usd = resume_sha256, match_cost_usd


def list_checks(session: Session) -> list[CheckRow]:
    return list(session.scalars(select(CheckRow).order_by(CheckRow.id.desc())))  # newest first


def stale_checks(session: Session, current_resume_sha256: str) -> list[CheckRow]:
    query = select(CheckRow).where(CheckRow.resume_sha256 != current_resume_sha256).order_by(CheckRow.id)
    return list(session.scalars(query))


def done_skills(session: Session) -> dict[str, datetime]:
    """Skill -> when I marked it. SQLite drops the timezone (see created_utc), so it's restored."""
    return {row.skill: row.marked_at if row.marked_at.tzinfo else row.marked_at.replace(tzinfo=UTC)
            for row in session.scalars(select(DoneSkill))}


def applied_checks(session: Session) -> dict[int, datetime]:
    """Check id -> when I marked it applied, with the timezone restored like done_skills."""
    return {row.check_id: row.applied_at if row.applied_at.tzinfo else row.applied_at.replace(tzinfo=UTC)
            for row in session.scalars(select(AppliedRow))}


def normalize_skill(name: str) -> str:
    return " ".join(name.split()).lower()  # skills are saved lowercase ("machine learning")
