"""M10 matches (decision_rules.md section 9), offline: a fake web with made-up listings (never the real lists'
data) and a FakeProvider. Nothing here touches the network or costs money."""

import json
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from fitcheck.api import create_app
from fitcheck.db import CheckRow, ListingRow, MatchRow, connect, put_document, save_check
from fitcheck.job_text import UnsupportedSite, fetch_text, html_to_text
from fitcheck.listings import (Listing, ListingsError, fits, merge_duplicates, parse, title_key)
from fitcheck.matches import (CHICAGO, RunState, _UsCentral, due, fetch_texts, pasted_today, rank, refresh,
                              run_daily, similar_check, today)
from fitcheck.profile import parse_profile
from fitcheck.providers.base import InvalidOutputError
from fitcheck.providers.budget import BudgetExceededError
from fitcheck.providers.fake_provider import FakeProvider
from fitcheck.resume import parse_resume
from fitcheck.schemas import Decision
from fitcheck.web import Response, UrllibWeb, WebError
from test_api import AUTH, FROZEN_RESUME, PROFILE, TOKEN, extraction_answer, matching_answer

NOW = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)  # the API reads the real clock
RESUME = parse_resume(FROZEN_RESUME)
PROFILE_OBJ = parse_profile(PROFILE)
JOB_TEXT = "<p>Required</p><ul><li>Experience with Python</li><li>Strong communication</li></ul>"
NOT_A_POSTING = {"is_job_posting": False, "has_section_split": False, "requirements": []}


def simplify_row(i, **changes):
    return {"id": f"s{i}", "company_name": f"Company {i}", "title": f"Software Engineer Intern {i}",
            "url": f"https://job-boards.greenhouse.io/company{i}/jobs/{i}", "locations": ["Chicago, IL"],
            "date_posted": int((NOW - timedelta(hours=i)).timestamp()), "active": True, "is_visible": True,
            "terms": ["Summer 2027"], "category": "Software"} | changes


def vansh_row(i, **changes):
    return {"id": f"v{i}", "company_name": f"Company {i}", "title": f"Software Engineer Intern {i}",
            "url": f"https://go.vansh.nyc/{i}", "locations": ["NYC"], "season": "Summer",
            "date_posted": int((NOW - timedelta(hours=i)).timestamp()), "active": True, "is_visible": True} | changes


class FakeWeb:
    """Answers by URL. `lists` are the two listings.json files; greenhouse jobs answer with JOB_TEXT."""

    def __init__(self, simplify=(), vansh=(), pages=None):
        self.pages = {
            "https://raw.githubusercontent.com/SimplifyJobs/Summer2027-Internships/dev/.github/scripts/listings.json":
                json.dumps(list(simplify)),
            "https://raw.githubusercontent.com/vanshb03/Summer2027-Internships/dev/.github/scripts/listings.json":
                json.dumps(list(vansh)),
        } | (pages or {})
        self.calls = []

    def get(self, url, *, timeout=20, follow_redirects=True):
        self.calls.append(url)
        if url in self.pages:
            page = self.pages[url]
            if isinstance(page, Response):
                return page
            return Response(200, page.encode() if isinstance(page, str) else json.dumps(page).encode())
        if url.startswith("https://boards-api.greenhouse.io/v1/boards/"):
            return Response(200, json.dumps({"content": JOB_TEXT.replace("<", "&lt;").replace(">", "&gt;")}).encode())
        raise WebError(f"{url} answered 404", 404)


def listing(**changes) -> Listing:
    base = dict(key="simplify:1", source="simplify", company="Acme", title="Software Engineer Intern",
                locations=("Chicago, IL",), url="https://job-boards.greenhouse.io/acme/jobs/1", posted_at=NOW,
                category="Software")
    return Listing(**(base | changes))


def check_answers(n):
    return [extraction_answer(), matching_answer()] * n


@pytest.fixture
def engine(tmp_path):
    engine = connect(f"sqlite:///{tmp_path}/m.db")
    with Session(engine) as session:
        put_document(session, "resume", FROZEN_RESUME)
        put_document(session, "profile", PROFILE)
    return engine


# ---------- Reading the lists ----------

def test_only_active_visible_summer_2027_rows_are_kept():
    rows = [simplify_row(1), simplify_row(2, active=False), simplify_row(3, is_visible=False),
            simplify_row(4, terms=["Fall 2026"]), simplify_row(5, terms=["N/A"], title="Summer Intern"),
            simplify_row(6, terms=["N/A"], title="Intern"), {"id": "broken"}]
    assert [x.key for x in parse("simplify", rows)] == ["simplify:s1", "simplify:s5"]


def test_vansh_rows_are_summer_and_get_a_category_from_the_title():
    rows = [vansh_row(1), vansh_row(2, season="Winter"), vansh_row(3, title="Marketing Intern")]
    assert [(x.key, x.category) for x in parse("vansh", rows)] == [("vansh:v1", "Software"), ("vansh:v3", "Other")]


def test_simplify_category_spellings_are_merged():
    rows = [simplify_row(1, category="Software Engineering"), simplify_row(2, category="Data Science, AI & Machine Learning"),
            simplify_row(3, category="Basket weaving")]
    assert [x.category for x in parse("simplify", rows)] == ["Software", "AI/ML/Data", "Other"]


def test_a_list_whose_format_changed_says_so():
    with pytest.raises(ListingsError, match="format changed"):
        parse("simplify", {"listings": []})


def test_a_list_that_cant_be_downloaded_says_which(engine):
    web = FakeWeb()
    web.pages.pop(next(iter(web.pages)))
    with Session(engine) as session, pytest.raises(ListingsError, match="simplify"):
        refresh(session, web, NOW)


def test_the_same_job_on_both_lists_is_kept_once_with_simplifys_link():
    both = merge_duplicates(parse("vansh", [vansh_row(1, title="Software Engineer Intern - Summer 2027")])
                            + parse("simplify", [simplify_row(1, title="Software Engineer Intern")]))
    assert len(both) == 1 and both[0].source == "simplify" and both[0].also_on == ("vansh",)


def test_title_key_ignores_season_year_intern_and_punctuation():
    assert title_key("Stripe", "Software Engineer Intern (Summer 2027)") == title_key("stripe", "Software Engineer, 2027")
    assert title_key("Stripe", "Software Engineer") != title_key("Stripe", "Data Engineer")


def test_categories_and_the_location_filter():
    on = parse_profile(PROFILE.replace("enabled: false", "enabled: true"))
    assert fits(listing(), ["Software"], on)
    assert not fits(listing(category="Hardware"), ["Software"], on)
    assert not fits(listing(locations=("Boston, MA",)), ["Software"], on)       # a known city, not mine
    assert fits(listing(locations=("Boston, MA", "Remote")), ["Software"], on)   # remote is ok
    assert fits(listing(locations=("Springfield, IL",)), ["Software"], on)      # unknown: the check decides
    assert fits(listing(locations=("Boston, MA",)), ["Software"], PROFILE_OBJ)  # filter off: all kept


# ---------- Fetching a posting's text ----------

@pytest.mark.parametrize("url,api,answer", [
    ("https://job-boards.greenhouse.io/acme/jobs/1", "https://boards-api.greenhouse.io/v1/boards/acme/jobs/1",
     {"content": "&lt;p&gt;Python&lt;/p&gt;"}),
    ("https://jobs.lever.co/acme/abc/apply", "https://api.lever.co/v0/postings/acme/abc",
     {"descriptionPlain": "About us", "lists": [{"text": "Requirements", "content": "<li>Python</li>"}]}),
    ("https://jobs.ashbyhq.com/acme/abc/application", "https://api.ashbyhq.com/posting-api/job-board/acme",
     {"jobs": [{"id": "other", "descriptionPlain": "no"}, {"id": "abc", "descriptionPlain": "Python"}]}),
    ("https://acme.wd5.myworkdayjobs.com/en-US/careers/job/Chicago/Intern_R1",
     "https://acme.wd5.myworkdayjobs.com/wday/cxs/acme/careers/job/Chicago/Intern_R1",
     {"jobPostingInfo": {"jobDescription": "<ul><li>Python</li></ul>"}}),
])
def test_each_job_site_api_gives_the_text(url, api, answer):
    text = fetch_text(listing(url=url), FakeWeb(pages={api: answer}))
    assert text.startswith("Software Engineer Intern\nAcme\nChicago, IL\n\n") and "Python" in text
    assert "no" not in text.split("\n\n", 1)[1]  # Ashby: the right job from the board


def test_other_sites_are_never_fetched():
    web = FakeWeb()
    with pytest.raises(UnsupportedSite):
        fetch_text(listing(url="https://careers.example.com/job/1"), web)
    assert not any("example.com" in call for call in web.calls)


def test_a_redirect_link_is_followed_only_to_a_site_we_read():
    to_greenhouse = Response(302, b"", {"location": "https://job-boards.greenhouse.io/acme/jobs/9"})
    to_elsewhere = Response(302, b"", {"location": "http://169.254.169.254/secrets"})
    web = FakeWeb(pages={"https://go.vansh.nyc/a": to_greenhouse, "https://go.vansh.nyc/b": to_elsewhere})
    assert "Experience with Python" in fetch_text(listing(url="https://go.vansh.nyc/a"), web)
    with pytest.raises(UnsupportedSite):
        fetch_text(listing(url="https://go.vansh.nyc/b"), web)
    assert not any("169.254" in call for call in web.calls)


def test_only_https_is_fetched():
    with pytest.raises(WebError, match="only https"):
        UrllibWeb().get("http://example.com")


def test_html_becomes_readable_text():
    assert html_to_text("<h2>Requirements</h2><ul><li>Python &amp; SQL</li><li>Git</li></ul><p>Thanks</p>") == \
        "Requirements\n- Python & SQL\n- Git\nThanks"


# ---------- M1: ranking ----------

def row(key, text, hours_old=1, status="ok"):
    return ListingRow(key=key, source="simplify", company="C", title="Intern", locations=[], url="u",
                      posted_at=NOW - timedelta(hours=hours_old), category="Software", also_on=[], active=True,
                      text=text, text_status=status)


def test_rank_orders_by_proven_skills_then_listed_only_then_newest():
    rows = [row("none", "Cooking"), row("one", "Python"), row("two", "Python and pandas"),
            row("listed", "Python and SQL"), row("older", "Python and pandas", hours_old=5)]
    # SQL is on the frozen resume but no bullet shows it: listed-only, so it only breaks ties.
    assert [r.row.key for r in rank(rows, RESUME, NOW)] == ["two", "older", "listed", "one", "none"]


def test_new_postings_with_a_proven_skill_come_before_the_backlog():
    rows = [row("old-strong", "Python pandas Plotly", hours_old=24 * 5), row("new", "Python", hours_old=3)]
    assert [r.row.key for r in rank(rows, RESUME, NOW)] == ["new", "old-strong"]


def test_a_posting_without_text_is_ranked_by_its_title():
    unread = row("t", None, status="unsupported")
    unread.title = "Python Developer Intern"
    (ranked,) = rank([unread], RESUME, NOW)
    assert ranked.proven == 1 and not ranked.checkable


# ---------- M3: the daily run ----------

def seed(engine, n, **changes):
    web = FakeWeb(simplify=[simplify_row(i, **changes) for i in range(1, n + 1)])
    with Session(engine) as session:
        refresh(session, web, NOW, force=True)
    return web


def test_a_run_checks_ten_and_a_second_run_does_nothing(engine):
    web = seed(engine, 12)
    provider = FakeProvider(check_answers(10))
    state = run_daily(engine, provider, web, NOW)
    assert (state.status, state.counted, state.tries) == ("complete", 10, 10) and len(provider.calls) == 20
    again = run_daily(engine, FakeProvider([]), web, NOW)  # no answers left: any call would fail
    assert again.status == "complete" and again.counted == 10


def test_review_manually_is_replaced_up_to_15_tries(engine):
    web = seed(engine, 20)
    provider = FakeProvider([NOT_A_POSTING] * 15)
    state = run_daily(engine, provider, web, NOW)
    assert (state.status, state.counted, state.tries) == ("partial", 0, 15)
    assert "manual look" in state.message


def test_fewer_postings_than_ten_says_so_and_never_pads(engine):
    web = seed(engine, 3)
    state = run_daily(engine, FakeProvider(check_answers(3)), web, NOW)
    assert (state.status, state.counted) == ("partial", 3) and "Only 3" in state.message


def test_a_posting_is_never_checked_twice_even_on_another_day(engine):
    web = seed(engine, 3)
    run_daily(engine, FakeProvider(check_answers(3)), web, NOW)
    tomorrow = run_daily(engine, FakeProvider([]), web, NOW + timedelta(days=1))
    assert tomorrow.counted == 0 and "Only 0" in tomorrow.message


def test_my_limit_stops_the_run_cleanly(engine):
    web = seed(engine, 5)
    provider = FakeProvider([extraction_answer(), matching_answer(), BudgetExceededError("Limit reached: $0.50")])
    state = run_daily(engine, provider, web, NOW)
    assert state.status == "stopped" and state.counted == 1 and "$0.50" in state.message


def test_a_posting_i_pasted_is_not_offered_as_a_match(engine):
    web = seed(engine, 2)
    with Session(engine) as session:
        fetch_texts(session, web, list(session.query(ListingRow)))
        text = session.get(ListingRow, "simplify:s1").text
    pasted = "Apply now\n" + text  # same job, slightly different text
    client = TestClient(create_app(token=TOKEN, provider=FakeProvider(check_answers(1)), database=engine, web=web))
    client.post("/checks", json={"posting": pasted}, headers=AUTH)
    state = run_daily(engine, FakeProvider(check_answers(1)), web, NOW)
    with Session(engine) as session:
        assert [m.listing_key for m in session.query(MatchRow)] == ["simplify:s2"] and state.counted == 1


def test_categories_i_didnt_pick_are_skipped(engine):
    web = seed(engine, 2, category="Hardware")
    assert run_daily(engine, FakeProvider([]), web, NOW).counted == 0


def test_only_new_texts_are_fetched(engine):
    web = seed(engine, 2)
    with Session(engine) as session:
        rows = list(session.query(ListingRow))
        assert fetch_texts(session, web, rows) == {"ok": 2, "unsupported": 0, "failed": 0}
        calls = len(web.calls)
        assert fetch_texts(session, web, rows) == {"ok": 0, "unsupported": 0, "failed": 0}
        assert len(web.calls) == calls


def test_due_at_8_am_chicago_and_not_again_once_done():
    seven, nine = datetime(2026, 11, 10, 13, 0, tzinfo=UTC), datetime(2026, 11, 10, 15, 0, tzinfo=UTC)
    fresh = RunState("2026-11-10", "not_run", "", 0, 0)
    assert not due(fresh, seven) and due(fresh, nine)
    assert not due(RunState("2026-11-10", "complete", "", 10, 10), nine)
    stopped = RunState("2026-11-10", "stopped", "", 2, 2, finished_at=(nine - timedelta(minutes=10)).isoformat())
    assert not due(stopped, nine) and due(stopped, nine + timedelta(hours=1))
    assert due(RunState("2026-11-10", "running", "", 2, 2), nine)  # died with a deploy: resume


def test_the_day_is_chicagos():
    assert today(datetime(2026, 11, 11, 3, 0, tzinfo=UTC)) == "2026-11-10"  # 9 PM in Chicago


def test_the_fallback_time_zone_handles_daylight_saving():
    zone = _UsCentral()
    assert datetime(2026, 7, 1, 12, tzinfo=zone).utcoffset() == timedelta(hours=-5)
    assert datetime(2026, 12, 1, 12, tzinfo=zone).utcoffset() == timedelta(hours=-6)
    assert datetime(2026, 3, 8, 3, tzinfo=zone).utcoffset() == timedelta(hours=-5)  # 2nd Sunday of March
    assert datetime(2026, 11, 1, 3, tzinfo=zone).utcoffset() == timedelta(hours=-6)  # 1st Sunday of November


# ---------- M2 and M4 ----------

def paid_check(session, posting, when=NOW, cost=0.015):
    from fitcheck.pipeline import check_posting, saved_fields
    checked = check_posting(posting, FakeProvider(check_answers(1)), RESUME, PROFILE_OBJ)
    fields = saved_fields(checked, RESUME) | {"cost_usd": cost}
    return save_check(session, posting=posting, now=when, **fields)


def test_only_paid_pasted_checks_today_count(engine):
    posting = "Data Intern\nRequired\nExperience with Python\nStrong communication\n"
    with Session(engine) as session:
        paid_check(session, posting)
        paid_check(session, "x " + posting, cost=0.0)                 # a cache hit
        paid_check(session, "y " + posting, when=NOW - timedelta(days=1))  # yesterday
        assert pasted_today(session, NOW) == 1


def test_similar_finds_the_same_job_with_different_text(engine):
    posting = "Stripe Software Engineer Intern\nNew York\nRequired\nExperience with Python\nStrong communication\n"
    with Session(engine) as session:
        saved = paid_check(session, posting)
        assert similar_check(session, posting).id == saved.id
        assert similar_check(session, posting + "\nApply by Friday").id == saved.id  # same first lines
        assert similar_check(session, "Totally different\nposting text here") is None


# ---------- The API ----------

def api(engine, provider, web):
    return TestClient(create_app(token=TOKEN, provider=provider, database=engine, web=web,
                                 run_in_background=lambda job: job()))


def test_find_now_runs_and_lists_apply_first(engine):
    web = seed(engine, 2)
    no_python = {"matches": [{"requirement_id": "r1", "skill": "python", "evidence_id": None, "quote": None,
                              "relation": "none", "reason": "t"}]}  # a gap: that posting is "not yet"
    provider = FakeProvider([extraction_answer(), no_python, extraction_answer(), matching_answer()])
    client = api(engine, provider, web)
    assert client.post("/matches/run", headers=AUTH).status_code == 202
    body = client.get("/matches", headers=AUTH).json()
    assert body["status"] == "partial" and body["counted"] == 2
    assert [m["decision"] for m in body["matches"]] == ["apply", "dont_apply"]
    assert body["matches"][0]["company"].startswith("Company")
    # History and the full card name the company and link the posting, like Matches does.
    history = client.get("/checks", headers=AUTH).json()
    assert {h["company"] for h in history} == {"Company 1", "Company 2"} and all(h["url"] for h in history)
    card = client.get(f"/checks/{body['matches'][0]['check_id']}", headers=AUTH).json()
    assert card["company"].startswith("Company") and card["url"].startswith("https://")
    assert card["first_line"].startswith("Software Engineer Intern")
    # M11: "I applied" shows on the match and in this week's count, and nothing about the match changes.
    assert body["applied_this_week"] == 0
    client.put(f"/checks/{body['matches'][1]['check_id']}/applied", headers=AUTH)  # a "not yet" one is fine
    after = client.get("/matches", headers=AUTH).json()
    assert after["applied_this_week"] == 1 and after["matches"][1]["applied_at"] is not None
    assert [m["decision"] for m in after["matches"]] == ["apply", "dont_apply"]


def test_postings_we_cant_read_are_offered_to_open(engine):
    web = seed(engine, 1, url="https://careers.example.com/1", title="Python Software Intern")
    client = api(engine, FakeProvider([]), web)
    client.post("/matches/run", headers=AUTH)
    opened = client.get("/matches", headers=AUTH).json()["to_open"]
    assert [o["title"] for o in opened] == ["Python Software Intern"] and opened[0]["proven"] == 1


def test_settings_pick_categories(engine):
    client = api(engine, FakeProvider([]), FakeWeb())
    assert client.get("/settings", headers=AUTH).json()["categories"] == ["Software"]
    assert client.put("/settings", json={"categories": ["Software", "Quant"]}, headers=AUTH).json()["categories"] == \
        ["Software", "Quant"]
    assert client.put("/settings", json={"categories": ["Cooking"]}, headers=AUTH).status_code == 422
    assert client.put("/settings", json={"categories": []}, headers=AUTH).status_code == 422


def test_my_own_checks_stop_at_five_a_day(engine):
    with Session(engine) as session:
        for i in range(5):
            paid_check(session, f"Posting {i}\nRequired\nExperience with Python\nStrong communication\n",
                       when=datetime.now(UTC))
    client = api(engine, FakeProvider(check_answers(1)), FakeWeb())
    assert client.get("/checks/quota", headers=AUTH).json() == {"used": 5, "limit": 5, "left": 0}
    refused = client.post("/checks", json={"posting": "New one\nRequired\nExperience with Python\n"}, headers=AUTH)
    assert refused.status_code == 429 and "5 of your own" in refused.json()["detail"]


def test_identical_text_returns_the_saved_check_for_free(engine):
    posting = "Data Intern\nRequired\nExperience with Python\nStrong communication\n"
    client = api(engine, FakeProvider(check_answers(1)), FakeWeb())
    first = client.post("/checks", json={"posting": posting}, headers=AUTH).json()
    again = client.post("/checks", json={"posting": posting}, headers=AUTH).json()  # no answers left
    assert again["id"] == first["id"]


def test_similar_endpoint(engine):
    posting = "Data Intern\nAcme\nRequired\nExperience with Python\nStrong communication\n"
    client = api(engine, FakeProvider(check_answers(1)), FakeWeb())
    saved = client.post("/checks", json={"posting": posting}, headers=AUTH).json()
    found = client.post("/checks/similar", json={"posting": posting + "\nPosted 3 days ago"}, headers=AUTH).json()
    assert found["check"]["id"] == saved["id"]
    assert client.post("/checks/similar", json={"posting": "Other\nThing"}, headers=AUTH).json() == {"check": None}
