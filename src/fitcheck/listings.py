"""Internship lists (M10): two community-run GitHub lists of Summer 2027 postings, read at run time.

Their data is never committed to this repo (SimplifyJobs has no license; tests use made-up samples).
Plain Python: download -> parse -> keep Summer 2027 -> merge postings on both lists (M4). Category and
location filters run later (rank.py), because I can change them in settings.
"""

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime

from fitcheck.profile import Profile
from fitcheck.score import METRO_ALIASES  # read only: the E6 city table (score.py is OWNER WRITES)
from fitcheck.textmatch import names_term, normalize
from fitcheck.web import Web, WebError

SOURCES = {
    "simplify": "https://raw.githubusercontent.com/SimplifyJobs/Summer2027-Internships/dev/.github/scripts/listings.json",
    "vansh": "https://raw.githubusercontent.com/vanshb03/Summer2027-Internships/dev/.github/scripts/listings.json",
}
CATEGORIES = ("Software", "AI/ML/Data", "Quant", "Product", "Hardware")
DEFAULT_CATEGORIES = ("Software",)
# vanshb03 has no categories: a title with one of these words counts as Software.
_SOFTWARE_WORDS = ("software", "engineer", "engineering", "developer", "swe", "backend", "frontend", "full stack",
                   "full-stack", "platform", "infrastructure", "mobile", "ios", "android", "web")
# Simplify spells a few categories two ways.
_CATEGORY_NAMES = {"Software Engineering": "Software", "Data Science, AI & Machine Learning": "AI/ML/Data",
                   "Product Management": "Product"}


class ListingsError(RuntimeError):
    """A list couldn't be downloaded or read (it moved, or its format changed)."""


@dataclass(frozen=True)
class Listing:
    key: str            # "simplify:<id>": stable across downloads
    source: str
    company: str
    title: str
    locations: tuple[str, ...]
    url: str
    posted_at: datetime
    category: str       # one of CATEGORIES, or "Other"
    also_on: tuple[str, ...] = field(default=())  # other lists it was merged from (M4)


def download(web: Web) -> list[Listing]:
    """Both lists, parsed, active Summer 2027 only, duplicates merged. Raises ListingsError."""
    found: list[Listing] = []
    for source, url in SOURCES.items():
        try:
            raw = web.get(url, timeout=60).json()
        except (WebError, ValueError) as e:
            raise ListingsError(f"couldn't download the {source} list: {e}") from e
        found += parse(source, raw)
    return merge_duplicates(found)


def parse(source: str, raw: object) -> list[Listing]:
    """One list's listings.json -> active, visible Summer 2027 listings. Unreadable rows are skipped."""
    if not isinstance(raw, list):
        raise ListingsError(f"the {source} list isn't a list of postings anymore: its format changed")
    out = []
    for row in raw:
        try:
            if not row.get("active") or not row.get("is_visible", True) or not _is_summer_2027(source, row):
                continue
            title = " ".join(str(row["title"]).split())
            out.append(Listing(
                key=f"{source}:{row['id']}", source=source, company=" ".join(str(row["company_name"]).split()),
                title=title, locations=tuple(row.get("locations") or ()), url=str(row["url"]).strip(),
                posted_at=datetime.fromtimestamp(int(row["date_posted"]), UTC),
                category=_category(source, row, title),
            ))
        except (KeyError, TypeError, ValueError, AttributeError):
            continue  # one broken row shouldn't hide the rest
    return out


def _is_summer_2027(source: str, row: dict) -> bool:
    if source == "vansh":  # the whole list is Summer 2027; rows only say the season
        return row.get("season") == "Summer"
    terms = row.get("terms") or []
    if "Summer 2027" in terms:
        return True
    # No season given ("N/A"): keep it only if the title says so.
    return terms in ([], ["N/A"]) and ("2027" in row.get("title", "") or "summer" in row.get("title", "").lower())


def _category(source: str, row: dict, title: str) -> str:
    if source == "simplify":
        name = _CATEGORY_NAMES.get(row.get("category"), row.get("category"))
        return name if name in CATEGORIES else "Other"
    return "Software" if any(names_term(title, w) for w in _SOFTWARE_WORDS) else "Other"


# ---------- M4: the same job on both lists ----------

_TITLE_NOISE = re.compile(r"\b(summer|fall|winter|spring|20\d\d|intern(ship)?s?|co-?op)\b|[^\w\s]")


def title_key(company: str, title: str) -> str:
    """Company + title with season, year, "intern", and punctuation removed: survives small rewording."""
    words = _TITLE_NOISE.sub(" ", normalize(title)).split()
    return f"{normalize(company)}|{' '.join(words)}"


def merge_duplicates(listings: list[Listing]) -> list[Listing]:
    """Keep one listing per company + title. Simplify wins: its link goes straight to the job site."""
    order = {source: i for i, source in enumerate(SOURCES)}
    kept: dict[str, Listing] = {}
    for listing in sorted(listings, key=lambda x: (order.get(x.source, 99), -x.posted_at.timestamp())):
        key = title_key(listing.company, listing.title)
        first = kept.get(key)
        if first is None:
            kept[key] = listing
        elif listing.source != first.source and listing.source not in first.also_on:
            kept[key] = Listing(**{**first.__dict__, "also_on": first.also_on + (listing.source,)})
    return list(kept.values())


# ---------- Filters I control: categories, and my E6 location filter ----------

def fits(listing: Listing, categories: tuple[str, ...] | list[str], profile: Profile | None) -> bool:
    if listing.category not in categories:
        return False
    return profile is None or not _filtered_out_by_location(listing, profile)


def _filtered_out_by_location(listing: Listing, profile: Profile) -> bool:
    """Same idea as E6, but only drops a listing when every place is a known city outside my metros.
    Unknown places stay in: the real check decides (it reads the posting's own location line)."""
    rules = profile.location_filter
    if not rules.enabled or not listing.locations:
        return False
    for place in listing.locations:
        if names_term(place, "remote"):
            if rules.remote_ok:
                return False
            continue
        metro = METRO_ALIASES.get(normalize(place.split(",")[0]))
        if metro is None or metro in rules.metros:
            return False
    return True
