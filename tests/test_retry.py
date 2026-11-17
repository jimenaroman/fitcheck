import json
from datetime import datetime

from fitcheck.providers.base import InvalidOutputError
from fitcheck.providers.fake_provider import FakeProvider
from fitcheck.retry import Rejection, generate_checked, log_rejections
from fitcheck.schemas import MatchSet

T0 = datetime(2026, 11, 5, 14, 21, 19)


def run(responses, check=lambda out: None):
    provider = FakeProvider(responses)
    attempts = generate_checked(provider, system="s", user="u", schema=MatchSet, check=check, now=lambda: T0)
    return attempts, provider


def test_success_on_first_try_has_no_rejections():
    attempts, _ = run([{"matches": []}])
    assert attempts.output is not None and attempts.rejections == [] and attempts.calls == 1


def test_rejection_records_attempt_error_and_time():
    attempts, _ = run([InvalidOutputError("bad json"), {"matches": []}])
    assert attempts.rejections == [Rejection(1, "bad json", T0)]
    assert attempts.calls == 2 and len(attempts.logs) == 1  # the failed call returned no answer


def test_check_failure_is_a_rejection_too():
    answers = iter(["looks wrong", None])
    attempts, provider = run([{"matches": []}, {"matches": []}], check=lambda out: next(answers))
    assert [r.attempt for r in attempts.rejections] == [1]
    assert "looks wrong" in provider.calls[1].user


def test_failure_log_is_one_json_line_per_rejection(tmp_path):
    path = tmp_path / "failures.jsonl"
    log_rejections("extract", [Rejection(1, "bad json", T0), Rejection(2, "text not found", T0)], path)
    lines = [json.loads(line) for line in path.read_text().splitlines()]
    assert lines == [
        {"time": "2026-11-05T14:21:19", "step": "extract", "attempt": 1, "error": "bad json"},
        {"time": "2026-11-05T14:21:19", "step": "extract", "attempt": 2, "error": "text not found"},
    ]


def test_no_rejections_writes_nothing(tmp_path):
    path = tmp_path / "failures.jsonl"
    log_rejections("extract", [], path)
    assert not path.exists()


def test_billed_failure_counts_toward_cost():
    from fitcheck.providers.base import CallLog
    billed = CallLog(provider="fake", model="fake", input_tokens=10, output_tokens=2, latency_s=0.1)
    attempts, _ = run([InvalidOutputError("failed validation", billed), {"matches": []}])
    assert attempts.calls == 2 and len(attempts.logs) == 2 and attempts.logs[0] is billed
