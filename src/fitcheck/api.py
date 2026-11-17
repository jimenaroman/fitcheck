"""The web API (M8): the same pipeline as `fitcheck check`, behind a secret token.

Run locally:  uv run uvicorn fitcheck.api:build_app --factory --reload --port 8001   (Transit Lab uses 8000)
Every endpoint except /health needs the header `Authorization: Bearer <APP_TOKEN>`. Without it,
anyone who finds the URL could spend my API budget.
Browsers may only call it from the addresses in ALLOWED_ORIGINS (my app; Vite's dev server by default).
Checks are saved to DATABASE_URL (Postgres on Railway), or a local SQLite file when it isn't set.
The resume and profile are uploaded once (PUT /resume, PUT /profile) and kept in the database.

Endpoints: GET /auth/check (is my token right?), POST /resume/import (file -> draft to review), PUT/GET /resume, PUT/GET /profile, POST/GET /checks, GET /checks/{id}, GET /leaderboard, PUT/DELETE /done/{skill},
PUT/DELETE /checks/{id}/applied,
GET /recheck (stale checks and estimated cost), POST /recheck (re-match them), GET /health.
"""

import hashlib
import os
import secrets
import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import yaml
from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from fitcheck.card import render_card
from fitcheck.config import MissingKeyError, load_env, require
from fitcheck.db import (LOCAL_DATABASE_URL, AppliedRow, CheckRow, DoneSkill, ListingRow, MatchRow, applied_checks,
                         connect, done_skills,
                         get_document, list_checks,
                         normalize_skill, put_document, save_check, stale_checks, update_check)
from fitcheck.leaderboard import Leaderboard, build_leaderboard
from fitcheck.listings import CATEGORIES
from fitcheck.matches import (EST_CHECK_USD, MATCHES_PER_DAY, OWN_PER_DAY, already_checked, fitting_rows,
                              get_categories, listing_for, listings_by_check, pasted_today, rank, run_daily,
                              run_state, set_categories,
                              similar_check, spent_today, start_schedule)
from fitcheck.matches import load_inputs as load_matches_inputs
from fitcheck.pipeline import check_posting, saved_fields
from fitcheck.profile import Profile, parse_profile
from fitcheck.providers.base import Provider
from fitcheck.providers.budget import MAX_DOLLARS_PER_DAY, BudgetExceededError
from fitcheck.providers.pricing import cost_usd
from fitcheck.resume import Resume, check_no_contact_info, parse_resume
from fitcheck.resume_import import MAX_BYTES, ResumeImportError, import_resume
from fitcheck.retry import FAILURE_LOG
from fitcheck.schemas import Decision, Eligibility, FitResult, NumberedRequirement
from fitcheck.web import UrllibWeb, Web


DEV_ORIGIN = "http://localhost:5180"  # the app's dev server on my laptop (web/vite.config.ts)
APPLIED_WEEK = timedelta(days=7)  # "applied this week": the last 7 days, like History's "This week"


class CheckRequest(BaseModel):
    posting: str


class CheckResponse(BaseModel):
    id: int
    created_at: datetime
    decision: Decision
    eligibility: Eligibility
    first_line: str       # usually the job title
    card: str             # the same text `fitcheck check` prints
    result: FitResult     # the structured version, for the app (M9)
    requirements: list[NumberedRequirement]  # each requirement's wording; result.requirements refers to these IDs
    cost_usd: float       # spent by the request that made it; $0 when every answer came from the cache
    calls: int
    company: str | None = None  # from the internship list when a match made it (M10); None for my pastes
    locations: list[str] = []
    url: str | None = None      # the posting on the job site, to apply
    applied_at: datetime | None = None  # when I marked "I applied" (M11)

    @classmethod
    def from_row(cls, row: CheckRow, listing: ListingRow | None = None,
                 applied_at: datetime | None = None) -> "CheckResponse":
        result = row.fit_result()
        return cls(id=row.id, created_at=row.created_utc(), decision=result.decision, eligibility=result.eligibility,
                   first_line=listing.title if listing else _first_line(row.posting),
                   card=render_card(result, row.numbered_requirements()), result=result,
                   requirements=row.numbered_requirements(), cost_usd=row.cost_usd, calls=row.calls,
                   applied_at=applied_at, **_listing_fields(listing))


class ImportResponse(BaseModel):
    draft: str             # resume YAML to review; send it to PUT /resume to save it
    items: int
    contact_removed: int   # emails, phones, and links taken out before the model saw the file
    listed_only: list[str]  # skills no bullet names: weak at best
    cost_usd: float


class RecheckPlan(BaseModel):
    stale_check_ids: list[int]
    estimated_cost_usd: float  # matching's original cost for these checks; assumes extraction is cached


class RecheckResult(BaseModel):
    rechecked_ids: list[int]
    remaining_ids: list[int]  # still stale (stopped early)
    cost_usd: float
    stopped: str | None = None  # why it stopped early, e.g. my daily limit


class MatchItem(BaseModel):
    """One of today's matches: the listing plus its saved check's verdict."""
    check_id: int
    decision: Decision
    eligibility: Eligibility
    title: str
    company: str
    locations: list[str]
    url: str
    applied_at: datetime | None = None


class OpenItem(BaseModel):
    """A posting that fits but can't be read automatically: open it, paste it on Check (1 of my 5)."""
    title: str
    company: str
    locations: list[str]
    url: str
    posted_at: datetime
    proven: int  # M1 on the title only


class MatchesResponse(BaseModel):
    day: str             # the day these matches are from (today, or the last day that has some)
    today: str
    status: str          # not_run, running, complete, partial, stopped
    message: str
    counted: int
    tries: int
    per_day: int
    autorun: bool        # the 8 AM run is on (Railway variable MATCHES_AUTORUN=1)
    categories: list[str]
    estimated_cost_usd: float  # what finding the rest of today's matches would cost, about
    applied_this_week: int     # M11: checks I marked "I applied" in the last 7 days (any check, not just matches)
    matches: list[MatchItem]
    to_open: list[OpenItem]


class Settings(BaseModel):
    categories: list[str]


class CheckSummary(BaseModel):
    """One line of my history: enough to pick a check without loading every card."""
    id: int
    created_at: datetime
    decision: Decision
    eligibility: Eligibility
    first_line: str  # usually the job title
    company: str | None = None
    locations: list[str] = []
    url: str | None = None
    applied_at: datetime | None = None

    @classmethod
    def from_row(cls, row: CheckRow, listing: ListingRow | None = None,
                 applied_at: datetime | None = None) -> "CheckSummary":
        return cls(id=row.id, created_at=row.created_utc(), decision=row.decision, eligibility=row.eligibility,
                   first_line=listing.title if listing else _first_line(row.posting), applied_at=applied_at,
                   **_listing_fields(listing))


def _listing_fields(listing: ListingRow | None) -> dict:
    if listing is None:
        return {}
    return {"company": listing.company, "locations": listing.locations, "url": listing.url}


def _first_line(posting: str) -> str:
    return next((line.strip() for line in posting.splitlines() if line.strip()), "")[:120]


def create_app(*, token: str, provider: Provider | Callable[[], Provider],
               database: str | Engine = LOCAL_DATABASE_URL, failure_log: Path = FAILURE_LOG,
               allowed_origins: list[str] | None = None, web: Web | None = None, autorun: bool = False,
               run_in_background: Callable[[Callable[[], object]], None] | None = None) -> FastAPI:
    """`provider` may be a function that builds it, so the API key is only needed on the first check.
    `web` fetches the internship lists (tests pass a fake); `run_in_background` starts a matches run
    (a thread by default; tests run it inline)."""
    if not token:
        raise MissingKeyError("APP_TOKEN is empty; refusing to start an API anyone could call.")
    app = FastAPI(title="fitcheck")
    # CORS: a browser only lets a page call this API if the page's address is listed here. The token is
    # sent in a header (not a cookie), so credentials stay off, and unlisted sites are refused by the browser.
    app.add_middleware(CORSMiddleware, allow_origins=allowed_origins or [], allow_credentials=False,
                       allow_methods=["GET", "POST", "PUT", "DELETE"],
                       allow_headers=["Authorization", "Content-Type"])
    engine = connect(database) if isinstance(database, str) else database
    bearer = HTTPBearer(auto_error=False)
    built: list[Provider] = []
    web = web or UrllibWeb()
    run_in_background = run_in_background or (lambda job: threading.Thread(target=job, daemon=True).start())

    def require_token(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)) -> None:
        # compare_digest takes the same time whether the first or the last character is wrong,
        # so response timing can't be used to guess the token one character at a time.
        if credentials is None or not secrets.compare_digest(credentials.credentials.encode(), token.encode()):
            raise HTTPException(status_code=401, detail="missing or wrong token")

    def get_provider() -> Provider:
        if not built:
            built.append(provider() if callable(provider) else provider)
        return built[0]

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}  # no token, so Railway can check the service is up

    @app.get("/auth/check", dependencies=[Depends(require_token)])
    def auth_check() -> dict[str, bool]:
        return {"ok": True}  # the app's login screen: 200 means the token I typed is right, 401 means not

    def load_inputs() -> tuple[Resume, Profile]:
        """The uploaded resume and profile, read on every request so a new upload applies at once."""
        with Session(engine) as session:
            texts = {name: get_document(session, name) for name in ("resume", "profile")}
        missing = [name for name, text in texts.items() if text is None]
        if missing:
            raise HTTPException(status_code=409, detail=f"upload first: {', '.join(f'PUT /{m}' for m in missing)}")
        return parse_resume(texts["resume"]), parse_profile(texts["profile"])  # checked when uploaded

    @app.put("/resume", dependencies=[Depends(require_token)])
    async def upload_resume(request: Request) -> dict:
        """Send resume.yaml as the request body. Rejected if it isn't a valid resume or contains contact info."""
        text = (await request.body()).decode("utf-8")
        try:
            # The whole file, not just what the model sees: contact info is never stored on the server.
            check_no_contact_info(text, "the uploaded resume")
            resume = parse_resume(text, source="the uploaded resume")
        except (ValueError, KeyError, TypeError) as e:  # ResumeError, or a section missing a field
            if isinstance(e, KeyError):
                e = ValueError(f"the uploaded resume is missing a field: {e}")
            raise HTTPException(status_code=422, detail=str(e)) from e
        with Session(engine) as session:
            put_document(session, "resume", text)
        return {"version": resume.version, "items": len(resume.items), "sha256": resume.sha256}

    @app.post("/resume/import", dependencies=[Depends(require_token)])
    def import_resume_file(file: UploadFile = File(...)) -> ImportResponse:
        """A PDF, DOCX, or TXT resume becomes a draft. Nothing is saved: I review it, then PUT /resume."""
        data = file.file.read(MAX_BYTES + 1)  # one byte over is enough to know it's too big
        try:
            result = import_resume(file.filename or "", data, get_provider())
        except ResumeImportError as e:
            raise HTTPException(status_code=e.status, detail=str(e)) from e
        except BudgetExceededError as e:
            raise HTTPException(status_code=429, detail=str(e)) from e
        except MissingKeyError as e:
            raise HTTPException(status_code=500, detail=str(e)) from e
        fresh = [log for log in result.logs if not log.cached]
        return ImportResponse(draft=result.draft, items=result.items, contact_removed=result.contact_removed,
                              listed_only=result.listed_only,
                              cost_usd=round(sum(cost_usd(g.model, g.input_tokens, g.output_tokens) for g in fresh), 6))

    @app.put("/profile", dependencies=[Depends(require_token)])
    async def upload_profile(request: Request) -> dict:
        text = (await request.body()).decode("utf-8")
        try:
            check_no_contact_info(text, "the uploaded profile")
            profile = parse_profile(text)
        except (ValueError, yaml.YAMLError) as e:  # pydantic's ValidationError is a ValueError
            raise HTTPException(status_code=422, detail=str(e)) from e
        with Session(engine) as session:
            put_document(session, "profile", text)
        return {"expected_graduation": profile.expected_graduation}

    @app.get("/resume", dependencies=[Depends(require_token)], response_class=PlainTextResponse)
    def show_resume() -> str:
        return _saved_document("resume")

    @app.get("/profile", dependencies=[Depends(require_token)], response_class=PlainTextResponse)
    def show_profile() -> str:
        return _saved_document("profile")

    def _saved_document(name: str) -> str:
        with Session(engine) as session:
            text = get_document(session, name)
        if text is None:
            raise HTTPException(status_code=404, detail=f"no {name} uploaded yet: PUT /{name}")
        return text

    @app.post("/checks", dependencies=[Depends(require_token)])
    def create_check(request: CheckRequest) -> CheckResponse:  # plain def: FastAPI runs it in a thread
        resume, profile = load_inputs()
        with Session(engine, expire_on_commit=False) as session:
            sha = hashlib.sha256(request.posting.encode()).hexdigest()
            same = session.scalars(select(CheckRow).where(CheckRow.posting_sha256 == sha,
                                                          CheckRow.resume_sha256 == resume.sha256)
                                   .order_by(CheckRow.id.desc())).first()
            if same is not None:  # identical text, same resume: the saved result, $0, nothing new saved
                return CheckResponse.from_row(same, listing_for(session, same.id), applied_checks(session).get(same.id))
            if pasted_today(session) >= OWN_PER_DAY:  # M2
                raise HTTPException(status_code=429, detail=(
                    f"You've checked {OWN_PER_DAY} of your own postings today, the daily limit. "
                    "Today's matches are on the Matches tab; more of your own tomorrow."))
        try:
            checked = check_posting(request.posting, get_provider(), resume, profile, failure_log)
        except BudgetExceededError as e:  # my limit, not a model failure
            raise HTTPException(status_code=429, detail=str(e)) from e
        except MissingKeyError as e:
            raise HTTPException(status_code=500, detail=str(e)) from e
        except ValueError as e:  # empty posting
            raise HTTPException(status_code=422, detail=str(e)) from e
        with Session(engine, expire_on_commit=False) as session:
            row = save_check(session, posting=request.posting, **saved_fields(checked, resume))
        return CheckResponse.from_row(row)

    @app.get("/checks/quota", dependencies=[Depends(require_token)])
    def check_quota() -> dict[str, int]:
        """M2: how many of my own 5 checks today are left."""
        with Session(engine) as session:
            used = pasted_today(session)
        return {"used": used, "limit": OWN_PER_DAY, "left": max(0, OWN_PER_DAY - used)}

    @app.post("/checks/similar", dependencies=[Depends(require_token)])
    def check_similar(request: CheckRequest) -> dict[str, CheckSummary | None]:
        """M4: before paying, is this probably a posting I already checked?"""
        with Session(engine) as session:
            row = similar_check(session, request.posting)
            if row is None:
                return {"check": None}
            return {"check": CheckSummary.from_row(row, listing_for(session, row.id), applied_checks(session).get(row.id))}

    @app.get("/checks", dependencies=[Depends(require_token)])
    def history() -> list[CheckSummary]:
        with Session(engine) as session:
            listings, applied = listings_by_check(session), applied_checks(session)
            return [CheckSummary.from_row(row, listings.get(row.id), applied.get(row.id)) for row in list_checks(session)]

    @app.get("/checks/{check_id}", dependencies=[Depends(require_token)])
    def one_check(check_id: int) -> CheckResponse:
        with Session(engine) as session:
            row = session.get(CheckRow, check_id)
            if row is None:
                raise HTTPException(status_code=404, detail=f"no check {check_id}")
            return CheckResponse.from_row(row, listing_for(session, row.id), applied_checks(session).get(row.id))

    @app.put("/checks/{check_id}/applied", dependencies=[Depends(require_token)])
    def mark_applied(check_id: int) -> dict:
        """M11: "I applied". Any decision can be marked: it records what I did; nothing is scored from it.
        Marking twice keeps the first date."""
        with Session(engine) as session:
            if session.get(CheckRow, check_id) is None:
                raise HTTPException(status_code=404, detail=f"no check {check_id}")
            row = session.get(AppliedRow, check_id)
            if row is None:
                row = AppliedRow(check_id=check_id, applied_at=datetime.now(UTC))
                session.add(row)
                session.commit()
            return {"check_id": check_id, "applied_at": applied_checks(session)[check_id]}

    @app.delete("/checks/{check_id}/applied", dependencies=[Depends(require_token)])
    def unmark_applied(check_id: int) -> dict:
        with Session(engine) as session:
            row = session.get(AppliedRow, check_id)
            if row is not None:
                session.delete(row)
                session.commit()
        return {"check_id": check_id, "applied_at": None}

    @app.get("/leaderboard", dependencies=[Depends(require_token)])
    def leaderboard() -> Leaderboard:
        resume, _ = load_inputs()
        with Session(engine) as session:
            checks = [row.saved_check() for row in list_checks(session)]
            return build_leaderboard(checks, done_skills(session), resume.sha256)

    @app.put("/done/{skill}", dependencies=[Depends(require_token)])
    def mark_done(skill: str) -> dict[str, str]:
        """L9: "I've done this". Moves the gap to its own board; the score still counts it as a gap."""
        with Session(engine) as session:
            session.merge(DoneSkill(skill=normalize_skill(skill), marked_at=datetime.now(UTC)))
            session.commit()
        return {"skill": normalize_skill(skill), "status": "marked"}

    @app.delete("/done/{skill}", dependencies=[Depends(require_token)])
    def unmark_done(skill: str) -> dict[str, str]:
        with Session(engine) as session:
            row = session.get(DoneSkill, normalize_skill(skill))
            if row is not None:
                session.delete(row)
                session.commit()
        return {"skill": normalize_skill(skill), "status": "unmarked"}

    @app.get("/recheck", dependencies=[Depends(require_token)])
    def recheck_plan() -> RecheckPlan:
        """Step 1 of a recheck: which checks used an older resume, and about what re-matching them costs."""
        resume, _ = load_inputs()
        with Session(engine) as session:
            stale = stale_checks(session, resume.sha256)
            return RecheckPlan(stale_check_ids=[row.id for row in stale],
                               estimated_cost_usd=round(sum(row.match_cost_usd for row in stale), 4))

    @app.post("/recheck", dependencies=[Depends(require_token)])
    def recheck() -> RecheckResult:
        """Step 2: re-run the stale checks against the current resume. Extraction comes from the cache
        (same posting), so matching is what's paid for. Stops cleanly at my budget limit."""
        resume, profile = load_inputs()
        done, spent, stopped = [], 0.0, None
        with Session(engine) as session:
            stale = stale_checks(session, resume.sha256)
            if autorun:  # M3: today's matches get the budget first
                state = run_state(session)
                left = 0 if state.status in ("complete", "partial") else MATCHES_PER_DAY - state.counted
                estimate = sum(row.match_cost_usd for row in stale)
                if left and spent_today(engine) + estimate + left * EST_CHECK_USD > MAX_DOLLARS_PER_DAY:
                    raise HTTPException(status_code=409, detail=(
                        f"This recheck (about ${estimate:.2f}) would leave too little of today's "
                        f"${MAX_DOLLARS_PER_DAY:.2f} for the {left} matches still to find. Try after they're done."))
            for row in stale:
                try:
                    checked = check_posting(row.posting, get_provider(), resume, profile, failure_log)
                except (BudgetExceededError, MissingKeyError) as e:
                    stopped = str(e)
                    break
                fields = saved_fields(checked, resume)
                update_check(session, row, **fields)
                done.append(row.id)
                spent += fields["cost_usd"]
            remaining = [row.id for row in stale if row.id not in done]
        return RecheckResult(rechecked_ids=done, remaining_ids=remaining, cost_usd=round(spent, 6), stopped=stopped)

    # ---------- M10: today's matches ----------

    @app.get("/matches", dependencies=[Depends(require_token)])
    def matches() -> MatchesResponse:
        with Session(engine) as session:
            state = run_state(session)
            categories = get_categories(session)
            day = state.day
            if not session.scalars(select(MatchRow).where(MatchRow.day == day)).first():
                day = session.scalar(select(func.max(MatchRow.day))) or day  # latest day with matches
            items, applied = [], applied_checks(session)
            for m in session.scalars(select(MatchRow).where(MatchRow.day == day, MatchRow.counted)):
                check, listing = session.get(CheckRow, m.check_id), session.get(ListingRow, m.listing_key)
                if check and listing:
                    items.append(MatchItem(check_id=check.id, decision=check.decision, eligibility=check.eligibility,
                                           title=listing.title, company=listing.company,
                                           locations=listing.locations, url=listing.url,
                                           applied_at=applied.get(check.id)))
            order = {Decision.APPLY: 0, Decision.REVIEW_MANUALLY: 1, Decision.DONT_APPLY: 2}
            items.sort(key=lambda i: order[i.decision])
            to_open = []
            inputs = load_matches_inputs(session)
            if inputs:
                resume, profile = inputs
                rows = [r for r in fitting_rows(session, categories, profile) if r.text_status in ("unsupported", "failed")]
                used = already_checked(session, rows)
                ranked = [r for r in rank([r for r in rows if r.key not in used], resume) if r.proven > 0][:10]
                to_open = [OpenItem(title=r.row.title, company=r.row.company, locations=r.row.locations,
                                    url=r.row.url, posted_at=r.row.posted_utc(), proven=r.proven) for r in ranked]
            left = 0 if state.status in ("complete", "partial") else MATCHES_PER_DAY - state.counted
            return MatchesResponse(day=day, today=state.day, status=state.status, message=state.message,
                                   counted=state.counted, tries=state.tries, per_day=MATCHES_PER_DAY,
                                   autorun=autorun, categories=categories,
                                   estimated_cost_usd=round(left * EST_CHECK_USD, 3),
                                   applied_this_week=sum(1 for at in applied.values()
                                                         if datetime.now(UTC) - at < APPLIED_WEEK),
                                   matches=items, to_open=to_open)

    @app.post("/matches/run", status_code=202, dependencies=[Depends(require_token)])
    def run_matches() -> dict[str, bool]:
        """Find the rest of today's matches now (the same run as 8 AM). Returns at once; GET /matches shows
        progress. Safe to tap twice: a second run waits its turn and only does what's left."""
        load_inputs()  # 409 if resume or profile is missing, before anything starts
        with Session(engine) as session:
            if run_state(session).status == "running":
                raise HTTPException(status_code=409, detail="Already finding today's matches.")
        run_in_background(lambda: run_daily(engine, get_provider(), web, failure_log=failure_log))
        return {"started": True}

    @app.get("/settings", dependencies=[Depends(require_token)])
    def show_settings() -> dict[str, list[str]]:
        with Session(engine) as session:
            return {"categories": get_categories(session), "all_categories": list(CATEGORIES)}

    @app.put("/settings", dependencies=[Depends(require_token)])
    def save_settings(settings: Settings) -> dict[str, list[str]]:
        with Session(engine) as session:
            try:
                return {"categories": set_categories(session, settings.categories), "all_categories": list(CATEGORIES)}
            except ValueError as e:
                raise HTTPException(status_code=422, detail=str(e)) from e

    return app


def build_app() -> FastAPI:
    """The real app: token from .env (Railway variables in production), Claude Haiku with budget and cache."""
    from fitcheck.cli import default_provider

    load_env()
    token = require("APP_TOKEN")
    engine = connect(os.environ.get("DATABASE_URL") or LOCAL_DATABASE_URL)
    # Checks, the budget log, and the cache all share one database.
    origins = [o.strip() for o in os.environ.get("ALLOWED_ORIGINS", DEV_ORIGIN).split(",") if o.strip()]
    autorun = os.environ.get("MATCHES_AUTORUN") == "1"  # Railway only: my laptop never spends at 8 AM
    if autorun:
        start_schedule(engine, lambda: default_provider("claude", engine))
    return create_app(token=token, provider=lambda: default_provider("claude", engine), database=engine,
                      allowed_origins=origins, autorun=autorun)
