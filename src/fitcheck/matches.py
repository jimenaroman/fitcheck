"""Today's matches (M10, decision_rules.md section 9). Plain Python around the normal pipeline.

refresh()        download the lists (about hourly) into the listings table
fetch_texts()    fetch descriptions from the job sites we can read ($0)
rank()           M1: order unchecked postings; ordering only, never a decision
run_daily()      M3: check the top 10 of the day, replacing review_manually results, up to 15 tries
pasted_today()   M2: how many of my own 5 a day I've used
similar_check()  M4: "Looks like #12, open it instead?"
"""

import hashlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, tzinfo
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from fitcheck.aliases import canonical, names_for
from fitcheck.db import (CheckRow, ListingRow, MatchRow, get_document, put_document, save_check)
from fitcheck.job_text import UnsupportedSite, fetch_text
from fitcheck.listings import DEFAULT_CATEGORIES, CATEGORIES, Listing, ListingsError, download, fits
from fitcheck.pipeline import CheckOutcome, check_posting, saved_fields
from fitcheck.profile import Profile, parse_profile
from fitcheck.providers.base import Provider
from fitcheck.providers.budget import BudgetExceededError
from fitcheck.resume import Resume, parse_resume
from fitcheck.retry import FAILURE_LOG
from fitcheck.schemas import Decision
from fitcheck.textmatch import names_term, normalize
from fitcheck.web import UrllibWeb, Web, WebError



class _UsCentral(tzinfo):
    """America/Chicago without the system time zone files (a slim server image may not have them):
    UTC-6, or UTC-5 from the 2nd Sunday of March 2 AM to the 1st Sunday of November 2 AM (US rule since 2007).
    Can be an hour off only during the switch hour itself (around 2 AM), never on which day it is."""

    def utcoffset(self, dt):
        return timedelta(hours=-5) if self._summer(dt.replace(tzinfo=None)) else timedelta(hours=-6)

    def dst(self, dt):
        return timedelta(hours=1) if self._summer(dt.replace(tzinfo=None)) else timedelta(0)

    def tzname(self, dt):
        return "CDT" if self._summer(dt.replace(tzinfo=None)) else "CST"

    @staticmethod
    def _summer(local: datetime) -> bool:
        def sunday(month: int, nth: int) -> datetime:
            first = datetime(local.year, month, 1)
            return first + timedelta(days=(6 - first.weekday()) % 7 + 7 * (nth - 1), hours=2)
        return sunday(3, 2) <= local < sunday(11, 1)


try:
    CHICAGO = ZoneInfo("America/Chicago")
except ZoneInfoNotFoundError:
    CHICAGO = _UsCentral()
MATCHES_PER_DAY = 10   # M3
MAX_TRIES_PER_DAY = 15  # M3: review_manually results are replaced, but not forever
OWN_PER_DAY = 5        # M2
RUN_HOUR = 8           # M3: about 8 AM Chicago
NEW_DAYS = 2           # M3: posted in the last 2 days counts as new
BACKLOG_DAYS = 14      # M3: older than this isn't offered
REFRESH_EVERY = timedelta(hours=1)
EST_CHECK_USD = 0.016  # shown before a run; the real cost is measured


def today(now: datetime | None = None) -> str:
    return (now or datetime.now(UTC)).astimezone(CHICAGO).date().isoformat()


# ---------- Settings (categories) ----------

def get_categories(session: Session) -> list[str]:
    saved = get_document(session, "settings")
    categories = json.loads(saved).get("categories") if saved else None
    return [c for c in (categories or DEFAULT_CATEGORIES) if c in CATEGORIES]


def set_categories(session: Session, categories: list[str]) -> list[str]:
    unknown = [c for c in categories if c not in CATEGORIES]
    if unknown or not categories:
        raise ValueError(f"pick at least one of {', '.join(CATEGORIES)}" + (f" (unknown: {unknown})" if unknown else ""))
    put_document(session, "settings", json.dumps({"categories": categories}))
    return categories


# ---------- Listings: download and texts ----------

def refresh(session: Session, web: Web, now: datetime | None = None, force: bool = False) -> int | None:
    """Download both lists if the last download is over an hour old. Returns how many are active, or None
    when it was fresh enough. Raises ListingsError (the old copy stays usable)."""
    now = now or datetime.now(UTC)
    last = get_document(session, "listings_refreshed_at")
    if not force and last and now - datetime.fromisoformat(last) < REFRESH_EVERY:
        return None
    listings = download(web)
    save_listings(session, listings)
    put_document(session, "listings_refreshed_at", now.isoformat())
    return len(listings)


def save_listings(session: Session, listings: list[Listing]) -> None:
    """Upsert: new ones added, known ones updated (text kept), ones that left the lists marked inactive."""
    seen = set()
    for listing in listings:
        seen.add(listing.key)
        row = session.get(ListingRow, listing.key)
        if row is None:
            row = ListingRow(key=listing.key, text=None, text_status=None)
            session.add(row)
        row.source, row.company, row.title = listing.source, listing.company, listing.title
        row.locations, row.url, row.posted_at = list(listing.locations), listing.url, listing.posted_at
        row.category, row.also_on, row.active = listing.category, list(listing.also_on), True
    for row in session.scalars(select(ListingRow).where(ListingRow.active)):
        if row.key not in seen:
            row.active = False
    session.commit()


def as_listing(row: ListingRow) -> Listing:
    return Listing(key=row.key, source=row.source, company=row.company, title=row.title,
                   locations=tuple(row.locations), url=row.url, posted_at=row.posted_utc(), category=row.category,
                   also_on=tuple(row.also_on))


def fitting_rows(session: Session, categories: list[str], profile: Profile | None,
                 now: datetime | None = None) -> list[ListingRow]:
    """Active listings in my categories, within the backlog window, not dropped by my location filter."""
    since = (now or datetime.now(UTC)) - timedelta(days=BACKLOG_DAYS)
    rows = session.scalars(select(ListingRow).where(ListingRow.active, ListingRow.category.in_(categories),
                                                    ListingRow.posted_at >= since))
    return [row for row in rows if fits(as_listing(row), categories, profile)]


def fetch_texts(session: Session, web: Web, rows: list[ListingRow], limit: int = 300,
                workers: int = 8) -> dict[str, int]:
    """Fetch descriptions for rows never tried, newest first. Network in threads, database here."""
    todo = sorted((r for r in rows if r.text_status is None), key=lambda r: r.posted_utc(), reverse=True)[:limit]
    listings = {r.key: as_listing(r) for r in todo}

    def one(key: str) -> tuple[str, str | None, str]:
        try:
            return key, fetch_text(listings[key], web), "ok"
        except UnsupportedSite:
            return key, None, "unsupported"
        except (WebError, ValueError, KeyError, IndexError, TypeError):
            return key, None, "failed"  # tried once; a later version of the code can reset these

    counts = {"ok": 0, "unsupported": 0, "failed": 0}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for key, text, status in pool.map(one, listings):
            row = session.get(ListingRow, key)
            row.text, row.text_status = text, status
            counts[status] += 1
    session.commit()
    return counts


# ---------- M1: ranking ----------

@dataclass(frozen=True)
class Ranked:
    row: ListingRow
    proven: int   # proven resume skills named
    listed: int   # listed-only skills named (tiebreak)
    is_new: bool  # posted in the last NEW_DAYS days

    @property
    def checkable(self) -> bool:
        return self.row.text_status == "ok"


def resume_skills(resume: Resume) -> tuple[list[list[str]], list[list[str]]]:
    """(proven, listed-only) skills, each as its names: the resume's spelling plus aliases ("js")."""
    proven, listed = [], []
    for item in resume.items:
        if item.kind != "skill":
            continue
        spellings = list(dict.fromkeys([item.text, *names_for(canonical(item.text.lower()))]))
        (proven if item.shown_in else listed).append(spellings)
    return proven, listed


def rank(rows: list[ListingRow], resume: Resume, now: datetime | None = None) -> list[Ranked]:
    """M1 + M3's order: new postings with at least one proven skill first, then everything by proven
    skills named, listed-only skills named, newest. Rows without text are scored on their title."""
    now = now or datetime.now(UTC)
    proven, listed = resume_skills(resume)

    def named(text: str, skills: list[list[str]]) -> int:
        return sum(any(names_term(text, name) for name in names) for names in skills)

    ranked = []
    for row in rows:
        text = row.text if row.text_status == "ok" else row.title
        ranked.append(Ranked(row, named(text, proven), named(text, listed),
                             now - row.posted_utc() <= timedelta(days=NEW_DAYS)))
    return sorted(ranked, key=lambda r: (not (r.is_new and r.proven > 0), -r.proven, -r.listed,
                                         -r.row.posted_utc().timestamp()))


# ---------- M4: have I checked this already? ----------

def _head(text: str, lines: int = 15) -> str:
    return normalize("\n".join([line for line in text.splitlines() if line.strip()][:lines]))


def looks_like(listing: ListingRow, posting: str) -> bool:
    """The pasted text names this listing's company and its full title near the top."""
    head = _head(posting)
    return normalize(listing.company) in head and normalize(listing.title) in head


def similar_check(session: Session, posting: str) -> CheckRow | None:
    """A saved check that's probably the same job (M4): same text, the same listing, or the same
    first two lines. Newest first."""
    sha = hashlib.sha256(posting.encode()).hexdigest()
    first_two = [normalize(line) for line in posting.splitlines() if line.strip()][:2]
    by_check = {m.check_id: m.listing_key for m in session.scalars(select(MatchRow).where(MatchRow.check_id.is_not(None)))}
    for row in session.scalars(select(CheckRow).order_by(CheckRow.id.desc())):
        if row.posting_sha256 == sha:
            return row
        listing = session.get(ListingRow, by_check[row.id]) if row.id in by_check else None
        if listing is not None and looks_like(listing, posting):
            return row
        theirs = [normalize(line) for line in row.posting.splitlines() if line.strip()][:2]
        if len(first_two) == 2 and theirs == first_two and len(first_two[0]) >= 8:
            return row
    return None


def listings_by_check(session: Session) -> dict[int, ListingRow]:
    """check id -> the listing a match made it for (my pasted checks aren't in here)."""
    pairs = session.execute(select(MatchRow.check_id, ListingRow).join(ListingRow, ListingRow.key == MatchRow.listing_key)
                            .where(MatchRow.check_id.is_not(None)))
    return {check_id: listing for check_id, listing in pairs}


def listing_for(session: Session, check_id: int) -> ListingRow | None:
    key = session.scalar(select(MatchRow.listing_key).where(MatchRow.check_id == check_id))
    return session.get(ListingRow, key) if key else None


def already_checked(session: Session, rows: list[ListingRow]) -> set[str]:
    """Listing keys that a match already used (any day), or that I already pasted (M4)."""
    used = set(session.scalars(select(MatchRow.listing_key)))
    pasted_ids = set(session.scalars(select(CheckRow.id))) - set(
        session.scalars(select(MatchRow.check_id).where(MatchRow.check_id.is_not(None))))
    pasted = [session.get(CheckRow, i).posting for i in pasted_ids]
    for row in rows:
        if row.key not in used and any(looks_like(row, text) for text in pasted):
            used.add(row.key)
    return used


# ---------- M2: my own postings, 5 a day ----------

def _chicago_day_bounds(day: str) -> tuple[datetime, datetime]:
    start = datetime.combine(date.fromisoformat(day), datetime.min.time(), CHICAGO)
    return start.astimezone(UTC), (start + timedelta(days=1)).astimezone(UTC)


def pasted_today(session: Session, now: datetime | None = None) -> int:
    """Checks I pasted today (Chicago) that cost money. Matches, cache hits, and rechecks don't count."""
    start, end = _chicago_day_bounds(today(now))
    match_ids = set(session.scalars(select(MatchRow.check_id).where(MatchRow.check_id.is_not(None))))
    rows = session.scalars(select(CheckRow).where(CheckRow.created_at >= start, CheckRow.created_at < end,
                                                  CheckRow.cost_usd > 0))
    return sum(1 for row in rows if row.id not in match_ids)


# ---------- M3: the daily run ----------

@dataclass
class RunState:
    day: str
    status: str   # not_run, running, complete, partial (fewer fit), stopped (limit or error)
    message: str
    counted: int
    tries: int
    finished_at: str | None = None


def run_state(session: Session, now: datetime | None = None) -> RunState:
    day = today(now)
    saved = get_document(session, "matches_run")
    state = json.loads(saved) if saved else None
    counted = session.scalar(select(func.count()).where(MatchRow.day == day, MatchRow.counted)) or 0
    tries = session.scalar(select(func.count()).where(MatchRow.day == day)) or 0
    if state and state.get("day") == day:
        return RunState(day, state["status"], state["message"], counted, tries, state.get("finished_at"))
    return RunState(day, "not_run", "", counted, tries)


def _save_state(session: Session, state: RunState) -> None:
    put_document(session, "matches_run", json.dumps(state.__dict__))


def load_inputs(session: Session) -> tuple[Resume, Profile] | None:
    resume, profile = get_document(session, "resume"), get_document(session, "profile")
    if resume is None or profile is None:
        return None
    return parse_resume(resume), parse_profile(profile)


_run_lock = threading.Lock()  # one run at a time (the 8 AM thread and the "Find now" button)


def run_daily(engine: Engine, provider: Provider, web: Web, now: datetime | None = None,
              failure_log: Path = FAILURE_LOG) -> RunState:
    """Check up to 10 matches for today. Safe to call again: it only does what's left of today's 10."""
    if not _run_lock.acquire(blocking=False):
        with Session(engine) as session:
            return run_state(session, now)
    try:
        return _run(engine, provider, web, now, failure_log)
    finally:
        _run_lock.release()


def _run(engine, provider, web, now, failure_log) -> RunState:
    with Session(engine, expire_on_commit=False) as session:
        state = run_state(session, now)
        inputs = load_inputs(session)
        if inputs is None:
            state.status, state.message = "stopped", "Upload your resume and profile first."
            _save_state(session, state)
            return state
        resume, profile = inputs
        state.status, state.message = "running", "Reading the internship lists…"
        _save_state(session, state)  # each phase is saved, so the app can show progress while it runs
        try:
            refresh(session, web, now)
        except ListingsError as e:
            state.message = f"Using the last copy of the lists: {e}"
        rows = fitting_rows(session, get_categories(session), profile, now)
        used = already_checked(session, rows)
        rows = [r for r in rows if r.key not in used]
        state.message = f"Reading job descriptions for {len(rows)} postings that fit…"
        _save_state(session, state)
        # Texts for the best title-ranked first, so a small fetch budget goes where it matters.
        fetch_texts(session, web, [r.row for r in rank(rows, resume, now)])
        queue = [r for r in rank(rows, resume, now) if r.checkable]

        stopped = None
        for candidate in queue:
            if state.counted >= MATCHES_PER_DAY or state.tries >= MAX_TRIES_PER_DAY:
                break
            state.message = f"Checking {candidate.row.company}: {candidate.row.title}"
            _save_state(session, state)
            try:
                checked: CheckOutcome = check_posting(candidate.row.text, provider, resume, profile, failure_log)
            except BudgetExceededError as e:
                stopped = str(e)
                break
            row = save_check(session, posting=candidate.row.text, **saved_fields(checked, resume))
            counted = checked.result.decision != Decision.REVIEW_MANUALLY
            session.add(MatchRow(day=state.day, listing_key=candidate.row.key, check_id=row.id, counted=counted,
                                 created_at=datetime.now(UTC)))
            session.commit()
            state.tries += 1
            state.counted += counted

        if stopped:
            state.status, state.message = "stopped", f"Stopped at {state.counted} of {MATCHES_PER_DAY}: {stopped}"
        elif state.counted >= MATCHES_PER_DAY:
            state.status, state.message = "complete", f"{state.counted} matches checked."
        elif state.tries >= MAX_TRIES_PER_DAY:
            state.status, state.message = "partial", (f"{state.counted} of {MATCHES_PER_DAY}: "
                                                      f"{state.tries} tries used, the rest needed a manual look.")
        else:
            state.status, state.message = "partial", (f"Only {state.counted} postings fit your filters today. "
                                                      "More appear as the lists grow.")
        state.finished_at = datetime.now(UTC).isoformat()
        _save_state(session, state)
        return state


def spent_today(engine: Engine, now: datetime | None = None) -> float:
    """What the budget guard counts for today (server time, like providers/budget.py)."""
    from fitcheck.providers.storage import SqlUsageLog
    local = (now or datetime.now()).replace(hour=0, minute=0, second=0, microsecond=0)
    return sum(r["cost_usd"] for r in SqlUsageLog(engine).since(local))


# ---------- The 8 AM schedule ----------

def due(state: RunState, now: datetime | None = None) -> bool:
    """Run if it's past 8 AM Chicago and today isn't done. A run stopped by my limit retries hourly."""
    local = (now or datetime.now(UTC)).astimezone(CHICAGO)
    if state.status == "running" and not _run_lock.locked():
        return local.hour >= RUN_HOUR  # a run that died with the server (a deploy): pick it back up
    if local.hour < RUN_HOUR or state.status in ("complete", "partial", "running"):
        return False
    if state.status == "stopped" and state.finished_at:
        return (now or datetime.now(UTC)) - datetime.fromisoformat(state.finished_at) >= timedelta(hours=1)
    return True


def start_schedule(engine: Engine, provider_factory, check_every: timedelta = timedelta(minutes=10)) -> threading.Thread:
    """A background thread in the API process: no extra Railway service. After a restart it catches up."""
    def loop():
        stop = threading.Event()
        while not stop.wait(0 if loop.first else check_every.total_seconds()):
            loop.first = False
            try:
                web = UrllibWeb()
                with Session(engine) as session:
                    state = run_state(session)
                    try:
                        refresh(session, web)  # hourly, so "open and paste" postings stay current
                    except ListingsError as e:
                        print(f"matches schedule: {e}", flush=True)
                if due(state):
                    run_daily(engine, provider_factory(), web)
            except Exception as e:  # never let the thread die; the next tick tries again
                print(f"matches schedule: {e!r}", flush=True)
    loop.first = True
    thread = threading.Thread(target=loop, name="matches-8am", daemon=True)
    thread.start()
    return thread
