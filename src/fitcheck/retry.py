"""Shared model-call helpers for pipeline steps: untrusted-data delimiters and retry with feedback.

P1: a step gets at most two attempts. A failed attempt is retried once with the reason it failed;
the feedback changes the request, so the cache can't answer the retry with the same bad reply.
"""

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Generic

from fitcheck.providers.base import CallLog, InvalidOutputError, Provider, T

MAX_ATTEMPTS = 2
FAILURE_LOG = Path(".fitcheck/failures.jsonl")  # one line per rejected attempt (gitignored)


def wrap_untrusted(tag: str, text: str) -> str:
    """Put text inside <tag> tags, escaping any copies of the tag inside it so it can't close them early."""
    escaped = re.sub(rf"<(/?){tag}>", rf"&lt;\1{tag}&gt;", text, flags=re.IGNORECASE)
    return f"<{tag}>\n{escaped}\n</{tag}>"


@dataclass(frozen=True)
class Rejection:
    attempt: int  # 1 or 2
    error: str
    time: datetime


@dataclass
class Attempts(Generic[T]):
    output: T | None  # None when both attempts failed
    logs: list[CallLog] = field(default_factory=list)  # one per call with token counts (billed)
    rejections: list[Rejection] = field(default_factory=list)
    calls: int = 0  # every call made, including ones that returned no usable answer


def generate_checked(
    provider: Provider,
    *,
    system: str,
    user: str,
    schema: type[T],
    check: Callable[[T], str | None],
    now: Callable[[], datetime] = datetime.now,
) -> Attempts[T]:
    """Call the model; `check` returns a problem description, or None if the answer is acceptable."""
    attempts: Attempts[T] = Attempts(output=None)
    request = user
    for attempt in range(1, MAX_ATTEMPTS + 1):
        attempts.calls += 1
        try:
            result = provider.generate(system=system, user=request, schema=schema)
        except InvalidOutputError as e:
            problem = str(e)
            if e.log is not None:  # billed even though unusable, so it counts toward cost
                attempts.logs.append(e.log)
        else:
            attempts.logs.append(result.log)
            problem = check(result.output)
            if problem is None:
                attempts.output = result.output
                return attempts
        attempts.rejections.append(Rejection(attempt, problem, now()))
        request = f"{user}\n\nYour previous answer was rejected: {problem[:500]}\nTry again."
    return attempts


def log_rejections(step: str, rejections: list[Rejection], path: Path = FAILURE_LOG) -> None:
    """Append one JSON line per rejected attempt, so repeated mistakes can be spotted and fixed."""
    if not rejections:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        for r in rejections:
            record = {"time": r.time.isoformat(timespec="seconds"), "step": step,
                      "attempt": r.attempt, "error": r.error}
            f.write(json.dumps(record) + "\n")
