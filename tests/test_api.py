"""The web API, offline: FastAPI's test client calls the app directly, with a FakeProvider."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from fitcheck.api import create_app
from fitcheck.config import MissingKeyError
from fitcheck.db import connect
from fitcheck.providers.base import CallLog, ProviderResult
from fitcheck.providers.budget import BudgetExceededError
from fitcheck.providers.fake_provider import FakeProvider

TOKEN = "test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
POSTING = "Required\nExperience with Python\nStrong communication\n"


def extraction_answer():
    return {"is_job_posting": True, "has_section_split": True, "requirements": [
        {"text": "Experience with Python", "section": "required", "kind": "skill", "skills": ["python"],
         "type": "named_tool"},
        {"text": "Strong communication", "section": "required", "kind": "soft"},
    ]}


def matching_answer():
    return {"matches": [{"requirement_id": "r1", "skill": "python", "evidence_id": "proj-finance-b1",
                         "quote": "Built a Streamlit budget app", "relation": "exact", "reason": "t"}]}


FROZEN_RESUME = Path("evals/fixtures/eval_resume_v1.yaml").read_text()  # read only, never written
PROFILE = Path("profile.example.yaml").read_text()


@pytest.fixture
def client_for(tmp_path):
    """Builds a test client on a SQLite database in a temporary folder, then uploads `resume` and
    `profile` (None skips one). Clients built in the same test share the database, like a restart."""
    def build(provider, resume=FROZEN_RESUME, profile=PROFILE):
        client = TestClient(create_app(token=TOKEN, provider=provider, database=f"sqlite:///{tmp_path}/test.db",
                                       failure_log=tmp_path / "failures.jsonl"))
        for name, text in (("resume", resume), ("profile", profile)):
            if text is not None:
                assert client.put(f"/{name}", content=text, headers=AUTH).status_code == 200
        return client
    return build


# ---------- The token ----------

def test_health_needs_no_token(client_for):
    assert client_for(FakeProvider([])).get("/health").json() == {"status": "ok"}


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer wrong"}, {"Authorization": TOKEN}])
def test_checks_refuse_a_missing_or_wrong_token_before_any_call(headers, client_for):
    provider = FakeProvider([])
    response = client_for(provider).post("/checks", json={"posting": POSTING}, headers=headers)
    assert response.status_code == 401 and provider.calls == []


def test_an_empty_token_refuses_to_start():
    with pytest.raises(MissingKeyError, match="APP_TOKEN"):
        create_app(token="", provider=FakeProvider([]))


# ---------- A check ----------

def test_check_returns_the_card_and_the_structured_result(client_for):
    provider = FakeProvider([extraction_answer(), matching_answer()])
    response = client_for(provider).post("/checks", json={"posting": POSTING}, headers=AUTH)
    assert response.status_code == 200
    body = response.json()
    assert body["decision"] == "apply" and body["eligibility"] == "eligible"
    assert body["card"].startswith("APPLY") and "Experience with Python" in body["card"]
    assert body["result"]["requirements"][0]["status"] == "have"
    first = body["requirements"][0]  # the wording the app shows next to that status
    assert first["id"] == body["result"]["requirements"][0]["requirement_id"]
    assert first["requirement"]["text"] == "Experience with Python"
    assert body["first_line"] == POSTING.strip().splitlines()[0]
    assert (body["calls"], body["cost_usd"]) == (2, 0.0) and body["id"] == 1


def test_cost_counts_only_fresh_calls(client_for):
    class Priced(FakeProvider):
        def generate(self, **kwargs):
            result = super().generate(**kwargs)
            log = CallLog("anthropic", "claude-haiku-4-5", 1_000_000, 0, 1.0, cached=len(self.calls) == 1)
            return ProviderResult(result.output, log)

    provider = Priced([extraction_answer(), matching_answer()])
    body = client_for(provider).post("/checks", json={"posting": POSTING}, headers=AUTH).json()
    assert body["cost_usd"] == 1.0  # the cached extraction is free; the matching call cost $1


def test_budget_limit_is_429_not_a_model_failure(client_for):
    provider = FakeProvider([BudgetExceededError("Limit reached: $0.50 today.")])
    response = client_for(provider).post("/checks", json={"posting": POSTING}, headers=AUTH)
    assert response.status_code == 429 and "Limit reached" in response.json()["detail"]


def test_empty_posting_is_422_before_any_call(client_for):
    provider = FakeProvider([])
    response = client_for(provider).post("/checks", json={"posting": "   "}, headers=AUTH)
    assert response.status_code == 422 and provider.calls == []


def test_a_check_before_any_upload_says_what_to_upload_and_calls_nothing(client_for):
    provider = FakeProvider([])
    response = client_for(provider, resume=None, profile=None).post("/checks", json={"posting": POSTING}, headers=AUTH)
    assert response.status_code == 409 and response.json()["detail"] == "upload first: PUT /resume, PUT /profile"
    assert provider.calls == []


def test_provider_is_built_on_the_first_check_and_reused(client_for):
    built = []

    def factory():
        built.append(FakeProvider([extraction_answer(), matching_answer()] * 2))
        return built[-1]

    client = client_for(factory)
    assert built == []  # starting the app needs no API key
    for _ in range(2):
        client.post("/checks", json={"posting": POSTING}, headers=AUTH)
    assert len(built) == 1


def test_missing_api_key_is_a_clear_500(client_for):
    def factory():
        raise MissingKeyError("ANTHROPIC_API_KEY is not set.")

    response = client_for(factory).post("/checks", json={"posting": POSTING}, headers=AUTH)
    assert response.status_code == 500 and "ANTHROPIC_API_KEY" in response.json()["detail"]


# ---------- Saved checks ----------

def test_every_check_is_saved_and_listed_newest_first(client_for):
    client = client_for(FakeProvider([extraction_answer(), matching_answer()] * 2))
    for title in ("Data Intern", "SWE Intern"):
        client.post("/checks", json={"posting": f"{title}\n{POSTING}"}, headers=AUTH)
    history = client.get("/checks", headers=AUTH).json()
    assert [(c["id"], c["first_line"], c["decision"]) for c in history] == [(2, "SWE Intern", "apply"),
                                                                            (1, "Data Intern", "apply")]


def test_a_saved_check_comes_back_with_the_same_card(client_for):
    client = client_for(FakeProvider([extraction_answer(), matching_answer()]))
    made = client.post("/checks", json={"posting": POSTING}, headers=AUTH).json()
    again = client.get(f"/checks/{made['id']}", headers=AUTH).json()
    assert again == made  # re-rendered from the saved requirements and result: no model call


def test_saved_checks_survive_a_restart(client_for):
    client_for(FakeProvider([extraction_answer(), matching_answer()])).post(
        "/checks", json={"posting": POSTING}, headers=AUTH)
    restarted = client_for(FakeProvider([]))  # a new app on the same database file
    assert len(restarted.get("/checks", headers=AUTH).json()) == 1


def test_unknown_check_is_404(client_for):
    assert client_for(FakeProvider([])).get("/checks/99", headers=AUTH).status_code == 404


def test_i_applied_is_marked_shown_and_undone(client_for):
    client = client_for(FakeProvider([extraction_answer(), matching_answer()]))
    made = client.post("/checks", json={"posting": POSTING}, headers=AUTH).json()
    assert made["applied_at"] is None
    first = client.put(f"/checks/{made['id']}/applied", headers=AUTH).json()["applied_at"]
    assert client.put(f"/checks/{made['id']}/applied", headers=AUTH).json()["applied_at"] == first  # keeps the date
    assert client.get(f"/checks/{made['id']}", headers=AUTH).json()["applied_at"] == first
    assert client.get("/checks", headers=AUTH).json()[0]["applied_at"] == first
    assert client.delete(f"/checks/{made['id']}/applied", headers=AUTH).json()["applied_at"] is None
    assert client.get(f"/checks/{made['id']}", headers=AUTH).json()["applied_at"] is None


def test_applying_to_an_unknown_check_is_404_and_needs_the_token(client_for):
    client = client_for(FakeProvider([]))
    assert client.put("/checks/99/applied", headers=AUTH).status_code == 404
    assert client.put("/checks/1/applied").status_code == 401
    assert client.delete("/checks/1/applied").status_code == 401


@pytest.mark.parametrize("path", ["/checks", "/checks/1"])
def test_history_needs_the_token(client_for, path):
    assert client_for(FakeProvider([])).get(path).status_code == 401


def test_a_failed_check_is_not_saved(client_for):
    client = client_for(FakeProvider([BudgetExceededError("Limit reached")]))
    client.post("/checks", json={"posting": POSTING}, headers=AUTH)
    assert client.get("/checks", headers=AUTH).json() == []


@pytest.mark.parametrize("url", ["postgres://u:p@host:5432/db", "postgresql://u:p@host:5432/db"])
def test_railway_urls_use_the_psycopg_driver(url, monkeypatch):
    seen = []
    monkeypatch.setattr("fitcheck.db.create_engine", lambda u: seen.append(u) or None)
    monkeypatch.setattr("fitcheck.db.Base.metadata.create_all", lambda engine: None)
    connect(url)
    assert seen == ["postgresql+psycopg://u:p@host:5432/db"]


# ---------- Leaderboard, "I've done this", recheck ----------

# The frozen resume with one bullet reworded (in memory; the file is never touched).
EDITED_RESUME = FROZEN_RESUME.replace("monthly budget charts", "monthly and yearly budget charts")


def kubernetes_posting_answers():
    extraction = {"is_job_posting": True, "has_section_split": True, "requirements": [
        {"text": "Experience with Python", "section": "required", "kind": "skill", "skills": ["python"],
         "type": "named_tool"},
        {"text": "Kubernetes", "section": "required", "kind": "skill", "skills": ["kubernetes"], "type": "named_tool"},
    ]}
    matching = {"matches": [
        {"requirement_id": "r1", "skill": "python", "evidence_id": "proj-finance-b1",
         "quote": "Built a Streamlit budget app", "relation": "exact", "reason": "t"},
        {"requirement_id": "r2", "skill": "kubernetes", "evidence_id": None, "quote": None, "relation": "none",
         "reason": "t"}]}
    return [extraction, matching]


K8S_POSTING = "Required\nExperience with Python\nKubernetes\n"


def test_leaderboard_counts_saved_checks(client_for):
    client = client_for(FakeProvider(kubernetes_posting_answers()))
    client.post("/checks", json={"posting": K8S_POSTING}, headers=AUTH)
    lb = client.get("/leaderboard", headers=AUTH).json()
    assert lb["postings_counted"] == 1 and lb["stale_check_ids"] == []
    assert lb["decisions"] == {"apply": 1}
    assert [e["skill"] for e in lb["strengths"]] == ["python"]
    assert [e["skill"] for e in lb["gaps"]] == ["kubernetes"]


def test_marking_done_moves_a_gap_and_unmarking_moves_it_back(client_for):
    client = client_for(FakeProvider(kubernetes_posting_answers()))
    client.post("/checks", json={"posting": K8S_POSTING}, headers=AUTH)
    assert client.put("/done/ Kubernetes ", headers=AUTH).json() == {"skill": "kubernetes", "status": "marked"}
    lb = client.get("/leaderboard", headers=AUTH).json()
    assert [e["skill"] for e in lb["marked_done"]] == ["kubernetes"] and lb["gaps"] == []
    assert lb["marked_done"][0]["marked_at"].startswith("20")  # a date the app can show
    client.delete("/done/kubernetes", headers=AUTH)
    assert [e["skill"] for e in client.get("/leaderboard", headers=AUTH).json()["gaps"]] == ["kubernetes"]


def test_marking_done_twice_is_harmless(client_for):
    client = client_for(FakeProvider([]))
    for _ in range(2):
        assert client.put("/done/sql", headers=AUTH).status_code == 200


def test_recheck_shows_the_plan_then_rematches_stale_checks(client_for, tmp_path):
    first = client_for(FakeProvider(kubernetes_posting_answers()))
    check_id = first.post("/checks", json={"posting": K8S_POSTING}, headers=AUTH).json()["id"]
    assert first.get("/recheck", headers=AUTH).json()["stale_check_ids"] == []

    # I edit a bullet: the saved check was matched against the old resume.
    edited = client_for(FakeProvider(kubernetes_posting_answers()), resume=EDITED_RESUME)
    plan = edited.get("/recheck", headers=AUTH).json()
    assert plan["stale_check_ids"] == [check_id]
    assert edited.get("/leaderboard", headers=AUTH).json()["stale_check_ids"] == [check_id]

    result = edited.post("/recheck", headers=AUTH).json()
    assert result == {"rechecked_ids": [check_id], "remaining_ids": [], "cost_usd": 0.0, "stopped": None}
    assert edited.get("/recheck", headers=AUTH).json()["stale_check_ids"] == []
    assert len(edited.get("/checks", headers=AUTH).json()) == 1  # updated in place, not a new check


def test_recheck_stops_cleanly_at_the_budget_limit(client_for, tmp_path):
    first = client_for(FakeProvider(kubernetes_posting_answers()))
    check_id = first.post("/checks", json={"posting": K8S_POSTING}, headers=AUTH).json()["id"]
    edited = client_for(FakeProvider([BudgetExceededError("Limit reached: $0.50 today.")]), resume=EDITED_RESUME)
    result = edited.post("/recheck", headers=AUTH).json()
    assert result["rechecked_ids"] == [] and result["remaining_ids"] == [check_id]
    assert "Limit reached" in result["stopped"]


def test_recheck_estimate_is_the_saved_matching_cost(client_for, tmp_path):
    class Priced(FakeProvider):
        def generate(self, **kwargs):
            result = super().generate(**kwargs)
            return ProviderResult(result.output, CallLog("anthropic", "claude-haiku-4-5", 4000, 400, 1.0))

    client_for(Priced(kubernetes_posting_answers())).post("/checks", json={"posting": K8S_POSTING}, headers=AUTH)
    plan = client_for(FakeProvider([]), resume=EDITED_RESUME).get("/recheck", headers=AUTH).json()
    assert plan["estimated_cost_usd"] == 0.006  # one matching call: 4,000 in and 400 out at Haiku prices


@pytest.mark.parametrize("method,path", [("get", "/leaderboard"), ("put", "/done/sql"), ("delete", "/done/sql"),
                                         ("get", "/recheck"), ("post", "/recheck")])
def test_new_endpoints_need_the_token(client_for, method, path):
    assert getattr(client_for(FakeProvider([])), method)(path).status_code == 401


# ---------- Uploading the resume and profile ----------

def test_uploaded_resume_and_profile_come_back_unchanged(client_for):
    client = client_for(FakeProvider([]))
    assert client.get("/resume", headers=AUTH).text == FROZEN_RESUME
    assert client.get("/profile", headers=AUTH).text == PROFILE


def test_resume_upload_reports_what_was_saved(client_for):
    response = client_for(FakeProvider([]), resume=None).put("/resume", content=FROZEN_RESUME, headers=AUTH)
    body = response.json()
    assert body["items"] > 0 and len(body["sha256"]) == 64


@pytest.mark.parametrize("contact", ["  email: someone@example.com\n", "  phone: 312-555-0142\n"])
def test_a_resume_with_contact_info_is_rejected_and_not_saved(client_for, contact):
    # Anywhere in the file, even in a section the model never sees: it would be stored on the server.
    client = client_for(FakeProvider([]), resume=None)
    response = client.put("/resume", content="contact:\n" + contact + FROZEN_RESUME, headers=AUTH)
    assert response.status_code == 422 and "contact.private.yaml" in response.json()["detail"]
    assert client.get("/resume", headers=AUTH).status_code == 404


@pytest.mark.parametrize("bad", ["not: [valid", "- just\n- a list\n", "education:\n  - {degrees: [BS]}\n"])
def test_an_unusable_resume_is_rejected_with_a_reason(client_for, bad):
    response = client_for(FakeProvider([]), resume=None).put("/resume", content=bad, headers=AUTH)
    assert response.status_code == 422 and response.json()["detail"]


def test_a_profile_with_a_typo_is_rejected(client_for):
    response = client_for(FakeProvider([]), profile=None).put(
        "/profile", content=PROFILE + "remote_okk: true\n", headers=AUTH)
    assert response.status_code == 422


def test_a_new_upload_replaces_the_old_one(client_for):
    client = client_for(FakeProvider([]))
    client.put("/resume", content=EDITED_RESUME, headers=AUTH)
    assert client.get("/resume", headers=AUTH).text == EDITED_RESUME


@pytest.mark.parametrize("method,path", [("put", "/resume"), ("get", "/resume"), ("put", "/profile"),
                                         ("get", "/profile")])
def test_resume_and_profile_need_the_token(client_for, method, path):
    assert getattr(client_for(FakeProvider([]), resume=None, profile=None), method)(path).status_code == 401


# ---------- Resume import (M9) ----------

def test_import_returns_a_draft_and_saves_nothing(client_for):
    from test_resume_import import RESUME_LINES, make_pdf, model_answer
    client = client_for(FakeProvider([model_answer()]))
    response = client.post("/resume/import", files={"file": ("r.pdf", make_pdf(RESUME_LINES), "application/pdf")},
                           headers=AUTH)
    assert response.status_code == 200
    body = response.json()
    assert "proj-bike-tracker-b1" in body["draft"] and body["contact_removed"] == 4
    assert body["listed_only"] == ["SQL", "Git"]
    assert client.get("/resume", headers=AUTH).text == FROZEN_RESUME  # unchanged until I PUT the draft
    assert client.put("/resume", content=body["draft"], headers=AUTH).status_code == 200  # and the draft is valid


def test_import_refuses_images_with_415(client_for):
    provider = FakeProvider([])
    response = client_for(provider).post("/resume/import", files={"file": ("me.png", b"\x89PNG", "image/png")},
                                         headers=AUTH)
    assert response.status_code == 415 and provider.calls == []


def test_import_refuses_a_non_resume_with_422(client_for):
    from test_resume_import import NOT_A_RESUME
    response = client_for(FakeProvider([NOT_A_RESUME])).post(
        "/resume/import", files={"file": ("list.txt", b"eggs\nmilk\n", "text/plain")}, headers=AUTH)
    assert response.status_code == 422 and "doesn't look like a resume" in response.json()["detail"]


def test_import_needs_the_token(client_for):
    response = client_for(FakeProvider([])).post("/resume/import", files={"file": ("r.txt", b"x", "text/plain")})
    assert response.status_code == 401


# ---------- CORS and the login check (M9 step 2) ----------

APP_ORIGIN = "https://fitcheck-app.example"


def preflight(client, origin):
    """What a browser asks before a cross-site request with an Authorization header."""
    return client.options("/checks", headers={"Origin": origin, "Access-Control-Request-Method": "POST",
                                              "Access-Control-Request-Headers": "authorization,content-type"})


def test_my_app_is_allowed(tmp_path):
    client = TestClient(create_app(token=TOKEN, provider=FakeProvider([]), database=f"sqlite:///{tmp_path}/t.db",
                                   allowed_origins=[APP_ORIGIN]))
    response = preflight(client, APP_ORIGIN)
    assert response.status_code == 200 and response.headers["access-control-allow-origin"] == APP_ORIGIN
    assert "authorization" in response.headers["access-control-allow-headers"].lower()


def test_other_sites_are_refused(tmp_path):
    client = TestClient(create_app(token=TOKEN, provider=FakeProvider([]), database=f"sqlite:///{tmp_path}/t.db",
                                   allowed_origins=[APP_ORIGIN]))
    assert "access-control-allow-origin" not in preflight(client, "https://evil.example").headers
    reply = client.get("/health", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in reply.headers  # the browser won't hand the reply to that page


def test_no_origins_means_no_browser_access(client_for):
    assert "access-control-allow-origin" not in preflight(client_for(FakeProvider([])), APP_ORIGIN).headers


def test_auth_check_tells_the_login_screen_if_the_token_is_right(client_for):
    client = client_for(FakeProvider([]))
    assert client.get("/auth/check", headers=AUTH).json() == {"ok": True}
    assert client.get("/auth/check", headers={"Authorization": "Bearer wrong"}).status_code == 401


def test_allowed_origins_come_from_the_environment(tmp_path, monkeypatch):
    from fitcheck.api import build_app
    monkeypatch.setenv("APP_TOKEN", TOKEN)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/t.db")
    monkeypatch.setenv("ALLOWED_ORIGINS", f"{APP_ORIGIN}, http://localhost:5180")
    client = TestClient(build_app())
    assert preflight(client, APP_ORIGIN).headers["access-control-allow-origin"] == APP_ORIGIN
    assert "access-control-allow-origin" not in preflight(client, "https://evil.example").headers
