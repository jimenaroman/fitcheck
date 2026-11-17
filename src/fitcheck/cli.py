"""Command line:
    uv run fitcheck extract path/to/posting.txt
    uv run fitcheck match path/to/posting.txt [--resume resume.yaml]
    uv run fitcheck check path/to/posting.txt [--resume resume.yaml] [--profile profile.yaml]
    uv run fitcheck eval [--check] [--only 002] [--provider claude|gemini|both]
    uv run fitcheck import-resume path/to/resume.pdf [--out .fitcheck/resume.draft.yaml]
Every command takes --provider (default claude: Haiku; gemini: Gemini Flash).

Real runs go through CachedProvider(BudgetedProvider(<provider>)): repeats are free, and my
spending limits (shared by both providers, one wallet) apply to every call that reaches the API.
"""

import argparse
import sys
from pathlib import Path

from fitcheck.card import render_card
from fitcheck.config import MissingKeyError
from fitcheck.evaluate import (EVALS_DIR, CaseReport, format_comparison, format_report, load_cases, run_case,
                               save_report)
from fitcheck.extract import LONG_POSTING_CHARS, ExtractionOutcome, extract
from fitcheck.match import MatchOutcome, match, skill_items
from fitcheck.providers.base import CallLog, Provider
from fitcheck.providers.budget import BudgetExceededError
from fitcheck.providers.pricing import cost_usd
from fitcheck.profile import load_profile
from fitcheck.resume import load_resume
from fitcheck.retry import FAILURE_LOG, Rejection, log_rejections
from fitcheck.score import score
from fitcheck.verify import verify


PROVIDERS = ("claude", "gemini")


def default_provider(name: str = "claude", engine=None) -> Provider:
    """Cache outside, budget inside. Files by default; with a database `engine` (the API), the usage
    log and the cache live in the database, so a deploy can't reset my limits."""
    # Imported here so `--help` and the tests never need an API key.
    from fitcheck.providers.budget import BudgetedProvider
    from fitcheck.providers.cache import CachedProvider
    from fitcheck.providers.storage import SqlCache, SqlUsageLog

    if name == "gemini":
        try:
            from fitcheck.providers.gemini_provider import GeminiProvider
        except ImportError as e:  # optional, so Railway doesn't install it
            raise MissingKeyError("Gemini isn't installed. Run: uv sync --extra gemini") from e
        model = GeminiProvider()
    else:
        from fitcheck.providers.anthropic_provider import AnthropicProvider
        model = AnthropicProvider()
    if engine is None:
        return CachedProvider(BudgetedProvider(model))
    return CachedProvider(BudgetedProvider(model, usage=SqlUsageLog(engine)), store=SqlCache(engine))


def main(argv: list[str] | None = None, provider: Provider | None = None, failure_log: Path = FAILURE_LOG) -> int:
    parser = argparse.ArgumentParser(prog="fitcheck")
    commands = parser.add_subparsers(dest="command", required=True)
    model_choice = argparse.ArgumentParser(add_help=False)  # --provider, shared by every command
    model_choice.add_argument("--provider", choices=PROVIDERS, default="claude")
    extract_cmd = commands.add_parser("extract", help="list the requirements in a posting", parents=[model_choice])
    extract_cmd.add_argument("path", type=Path)
    match_cmd = commands.add_parser("match", help="find resume evidence for each skill in a posting",
                                    parents=[model_choice])
    match_cmd.add_argument("path", type=Path)
    match_cmd.add_argument("--resume", type=Path, default=Path("resume.yaml"))
    check_cmd = commands.add_parser("check", help="the full fit card: decision, coverage, eligibility",
                                    parents=[model_choice])
    check_cmd.add_argument("path", type=Path)
    check_cmd.add_argument("--resume", type=Path, default=Path("resume.yaml"))
    check_cmd.add_argument("--profile", type=Path, default=Path("profile.yaml"))
    eval_cmd = commands.add_parser("eval", help="run the labeled postings and compare with the answer key")
    eval_cmd.add_argument("--check", action="store_true", help="only validate labels and postings; no model calls")
    eval_cmd.add_argument("--only", help="run one case, e.g. 002")
    eval_cmd.add_argument("--profile", type=Path, default=Path("profile.yaml"))
    eval_cmd.add_argument("--provider", choices=PROVIDERS + ("both",), default="claude",
                          help="both: run every case on each model and add a side-by-side comparison (M7)")
    import_cmd = commands.add_parser("import-resume", help="turn a PDF, DOCX, or TXT resume into a draft to review",
                                     parents=[model_choice])
    import_cmd.add_argument("path", type=Path)
    import_cmd.add_argument("--out", type=Path, default=Path(".fitcheck/resume.draft.yaml"))
    listings_cmd = commands.add_parser("listings", help="download the internship lists and show the best-ranked "
                                       "postings (M10); no model calls, $0")
    listings_cmd.add_argument("--resume", type=Path, default=Path("resume.yaml"))
    listings_cmd.add_argument("--profile", type=Path, default=Path("profile.yaml"))
    listings_cmd.add_argument("--top", type=int, default=15)
    args = parser.parse_args(argv)

    if args.command == "listings":
        return run_listings(args)
    if args.command == "eval":
        return run_eval(args, provider, failure_log)
    if args.command == "import-resume":
        return run_import(args, provider)

    needed = [args.path]
    if args.command in ("match", "check"):
        needed.append(args.resume)
    if args.command == "check":
        needed.append(args.profile)
    for path in needed:
        if not path.is_file():
            print(f"No file at {path}", file=sys.stderr)
            return 2
    posting = args.path.read_text(encoding="utf-8")

    try:
        # Load local files before any model call, so a bad file never costs money.
        resume = load_resume(args.resume) if args.command != "extract" else None
        profile = load_profile(args.profile) if args.command == "check" else None
        provider = provider or default_provider(args.provider)
        outcome = extract(posting, provider)
        log_rejections("extract", outcome.rejections, failure_log)
        if args.command == "extract" or (args.command == "match" and not _extraction_usable(outcome)):
            print_outcome(outcome)
            return 1 if outcome.decision_reason else 0
        if _extraction_usable(outcome):
            matched = match(outcome.requirements, resume, provider)
            log_rejections("match", matched.rejections, failure_log)
        else:
            matched = MatchOutcome([], None)  # P1/P2: score() reports it, no matching call
    except (ValueError, BudgetExceededError, MissingKeyError) as e:
        # Empty posting, unusable resume or profile, my limit, or setup: never a model failure.
        # (pydantic's ValidationError is a ValueError, so a bad profile.yaml lands here too.)
        print(e, file=sys.stderr)
        return 2

    if args.command == "match":
        print_matches(outcome, matched)
        return 1 if matched.decision_reason else 0

    result = score(outcome, matched, verify(matched.matches, outcome.requirements, resume), profile)
    print_rejections("extraction", outcome.rejections)
    print_rejections("matching", matched.rejections)
    print(render_card(result, outcome.requirements))
    print(format_cost(outcome.logs + matched.logs, outcome.calls + matched.calls))
    return 0


def run_listings(args, web=None, database: str | None = None) -> int:
    """Refresh the lists into the local database, fetch texts, and print the M1 order. Never calls a model."""
    from sqlalchemy.orm import Session

    from fitcheck.db import LOCAL_DATABASE_URL, connect
    from fitcheck.listings import ListingsError
    from fitcheck.matches import already_checked, fetch_texts, fitting_rows, get_categories, rank, refresh
    from fitcheck.web import UrllibWeb

    try:
        resume, profile = load_resume(args.resume), load_profile(args.profile)
    except (OSError, ValueError) as e:
        print(e, file=sys.stderr)
        return 2
    web = web or UrllibWeb()
    with Session(connect(database or LOCAL_DATABASE_URL)) as session:
        try:
            active = refresh(session, web, force=True)
        except ListingsError as e:
            print(e, file=sys.stderr)
            return 1
        categories = get_categories(session)
        rows = fitting_rows(session, categories, profile)
        used = already_checked(session, rows)
        rows = [r for r in rows if r.key not in used]
        fetched = fetch_texts(session, web, [r.row for r in rank(rows, resume)])
        ranked = rank(rows, resume)
    readable = sum(r.checkable for r in ranked)
    print(f"{active} active Summer 2027 postings; {len(ranked)} fit {', '.join(categories)} from the last 14 days "
          f"and aren't checked yet; {readable} readable automatically.")
    print(f"Fetched now: {fetched['ok']} texts, {fetched['unsupported']} on sites we don't read, "
          f"{fetched['failed']} failed.\n")
    for i, r in enumerate(ranked[:args.top], start=1):
        how = "text" if r.checkable else "title"
        print(f"{i:>2}. {r.row.company}: {r.row.title}  [{r.proven} proven, {r.listed} listed, by {how}"
              f"{', new' if r.is_new else ''}]")
    return 0


def run_eval(args, provider: Provider | None, failure_log: Path, evals_dir: Path = EVALS_DIR,
             providers: dict[str, Provider] | None = None) -> int:
    """`provider` runs a single model (tests pass a fake); `providers` maps names to models for
    --provider both. With neither, the real providers are built from --provider."""
    try:
        cases = load_cases(evals_dir)
        resume = load_resume(evals_dir / "fixtures" / "eval_resume_v1.yaml")  # always the frozen resume
        profile = load_profile(args.profile)
    except ValueError as e:  # CaseError, a bad label, resume, or profile
        print(e, file=sys.stderr)
        return 2
    if args.only:
        cases = [c for c in cases if c.name == args.only]
    if not cases:
        print("No labeled cases to run." if not args.only else f"No case {args.only}.", file=sys.stderr)
        return 2
    if args.check:
        print(f"{len(cases)} case(s) OK: labels valid, fingerprints match, every quote is in its posting.")
        return 0

    names = PROVIDERS if args.provider == "both" else (args.provider,)
    runs: dict[str, list[CaseReport]] = {}
    for name in names:
        try:
            model = (providers or {}).get(name) or provider or default_provider(name)
        except MissingKeyError as e:  # e.g. no GEMINI_API_KEY yet: report it, still run the others
            runs[name] = [CaseReport(case.name, error=str(e)) for case in cases[:1]]
            continue
        runs[name] = _run_cases(cases, model, resume, profile, failure_log)

    if len(runs) == 1:
        (reports,) = runs.values()
        body = format_report(reports)
    else:
        reports = [r for rs in runs.values() for r in rs]
        body = format_comparison(runs)
    print(body)
    path = save_report(reports, body, reports_dir=evals_dir / "reports")
    print(f"\nSaved to {path}")
    return 0


def run_import(args, provider: Provider | None) -> int:
    """Writes the draft to --out (gitignored by default) and never touches resume.yaml: I review first."""
    from fitcheck.resume_import import ResumeImportError, import_resume

    if not args.path.is_file():
        print(f"No file at {args.path}", file=sys.stderr)
        return 2
    try:
        result = import_resume(args.path.name, args.path.read_bytes(), provider or default_provider(args.provider))
    except (ResumeImportError, BudgetExceededError, MissingKeyError) as e:
        print(e, file=sys.stderr)
        return 1 if isinstance(e, ResumeImportError) else 2
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(result.draft, encoding="utf-8")
    print(f"Draft written to {args.out}: {result.items} items, {result.contact_removed} contact detail(s) removed "
          "before the model saw the file.")
    if result.listed_only:
        print(f"Listed only (no bullet names them, so weak at best): {', '.join(result.listed_only)}")
    print_rejections("import", result.rejections)
    print(format_cost(result.logs, result.calls))
    print("Review it, then copy it over resume.yaml (and upload with PUT /resume).")
    return 0


def _run_cases(cases, provider: Provider, resume, profile, failure_log: Path) -> list[CaseReport]:
    reports = []
    for case in cases:
        try:
            reports.append(run_case(case, provider, resume, profile, failure_log))
        except (BudgetExceededError, MissingKeyError) as e:  # my limit or setup: stop, never a model failure
            reports.append(CaseReport(case.name, error=str(e)))
            break
    return reports


def _extraction_usable(outcome: ExtractionOutcome) -> bool:
    return outcome.extraction is not None and outcome.extraction.is_job_posting


def print_outcome(outcome: ExtractionOutcome) -> None:
    note = f" (long posting: over {LONG_POSTING_CHARS:,})" if outcome.is_long else ""
    print(f"Posting: {outcome.posting_chars:,} characters{note}")

    print_rejections("extraction", outcome.rejections)
    if outcome.extraction is None:
        print("Review manually: the model's answer failed validation twice.")
    elif not outcome.extraction.is_job_posting:
        print("Review manually: this doesn't look like a job posting.")
    else:
        if not outcome.extraction.has_section_split:
            print("Required/preferred split unclear: items without 'preferred' wording are assumed required.")
        print(f"{len(outcome.requirements)} requirements:")
        for n in outcome.requirements:
            print(format_requirement(n.id, n.requirement))

    print(format_cost(outcome.logs, outcome.calls))


def format_requirement(req_id: str, req) -> str:
    label = req.type or req.eligibility_type or ""
    head = f"{req_id:>4}  {req.section:<9} {req.kind:<11} {label}"
    lines = [head.rstrip(), f"      \"{req.text}\""]
    if req.skills:
        joiner = f" {req.operator} " if req.operator else ", "
        lines.append(f"      skills: {joiner.join(req.skills)}")
    if req.examples:
        lines.append(f"      examples: {', '.join(req.examples)}")
    if req.min_years is not None:
        lines.append(f"      min years: {req.min_years}")
    if req.grad_earliest or req.grad_latest:
        lines.append(f"      graduation: {req.grad_earliest or '?'} to {req.grad_latest or '?'}")
    if req.locations:
        lines.append(f"      locations: {', '.join(req.locations)}")
    return "\n".join(lines)


def print_matches(extraction: ExtractionOutcome, matched: MatchOutcome) -> None:
    print(f"Posting: {extraction.posting_chars:,} characters")
    print_rejections("extraction", extraction.rejections)
    print_rejections("matching", matched.rejections)
    if matched.decision_reason:
        print("Review manually: the matching answer failed validation twice.")
    else:
        by_requirement: dict[str, list] = {}
        for m in matched.matches:
            by_requirement.setdefault(m.requirement_id, []).append(m)
        print("Evidence per skill (relations only; statuses come from score.py in M5):")
        for n in skill_items(extraction.requirements):
            req = n.requirement
            print(f'{n.id:>4}  {req.section:<9} {req.type}  "{req.text}"')
            for m in by_requirement.get(n.id, []):
                print(f"      {m.skill}: {m.relation}")
                if m.evidence_id:
                    print(f'        {m.evidence_id}: "{m.quote}"')
                print(f"        why: {m.reason}")
        others = len(extraction.requirements) - len(skill_items(extraction.requirements))
        if others:
            print(f"({others} soft or eligibility items not sent to matching)")
    print(format_cost(extraction.logs + matched.logs, extraction.calls + matched.calls))


def print_rejections(step: str, rejections: list[Rejection]) -> None:
    # Shown even when the retry succeeded, so a hidden first failure never goes unnoticed.
    for r in rejections:
        print(f"{step} attempt {r.attempt} rejected: {r.error[:300]}")


def format_cost(logs: list[CallLog], calls: int) -> str:
    # Calls with no token counts (e.g. network errors) are counted but their cost is unknown.
    unknown = calls - len(logs)
    unknown_note = f"; {unknown} failed call(s) not priced" if unknown else ""
    if not logs:
        return f"Cost: $0 ({calls} call(s), no answers returned{unknown_note})"
    fresh = [log for log in logs if not log.cached]
    spent = sum(cost_usd(log.model, log.input_tokens, log.output_tokens) for log in fresh)
    original = sum(cost_usd(log.model, log.input_tokens, log.output_tokens) for log in logs)
    cached = len(logs) - len(fresh)
    from_cache = f", {cached} from cache; originally ${original:.4f}" if cached else ""
    tokens_in = sum(log.input_tokens for log in fresh)
    tokens_out = sum(log.output_tokens for log in fresh)
    return (
        f"Cost: ${spent:.4f} ({calls} call(s){from_cache}{unknown_note}; "
        f"{tokens_in:,} in / {tokens_out:,} out tokens; model {logs[-1].model})"
    )

if __name__ == "__main__":
    sys.exit(main())
