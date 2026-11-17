"""Record the demo's API responses: one real check of the fictional posting against the fictional resume.

Run from the repo root: `uv run python docs/demo/record_fixtures.py --live` (about $0.02, through the
budget guard and cache). Everything else (screenshots, the video, /demo) replays docs/demo/fixtures/api.json.
"""

import argparse
import json
import sys
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

from fitcheck.api import create_app
from fitcheck.cli import default_provider
from fitcheck.config import load_env

HERE = Path(__file__).parent / "fixtures"
OUT = HERE / "api.json"
TOKEN = "demo-token"  # only for this throwaway in-process API


class NoNetwork:
    """The recording never fetches job sites; only the model call goes out."""

    def get(self, url, **_):
        raise RuntimeError(f"demo recording does not fetch {url}")


def record(client: TestClient) -> dict:
    posting = (HERE / "demo_posting.txt").read_text(encoding="utf-8")
    responses: dict[str, dict] = {}

    def call(method: str, path: str, key: str | None = None, **kwargs) -> dict:
        response = client.request(method, path, **kwargs)
        response.raise_for_status()
        is_json = response.headers["content-type"].startswith("application/json")
        responses[key or f"{method} {path}"] = {"type": "json" if is_json else "text",
                                                "body": response.json() if is_json else response.text}
        return responses[key or f"{method} {path}"]

    call("GET", "/auth/check")
    call("PUT", "/resume", content=(HERE / "demo_resume.yaml").read_bytes())
    call("PUT", "/profile", content=(HERE / "demo_profile.yaml").read_bytes())
    call("GET", "/checks/quota")
    call("POST", "/checks/similar", json={"posting": posting})
    check = call("POST", "/checks", json={"posting": posting})["body"]
    for path in ("/checks", f"/checks/{check['id']}", "/leaderboard", "/matches", "/settings",
                 "/resume", "/profile", "/recheck"):
        call("GET", path)
    call("GET", "/checks/quota", key="GET /checks/quota (after)")
    return responses


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", help="required: this makes real model calls")
    parser.add_argument("--force", action="store_true", help="overwrite an existing api.json")
    args = parser.parse_args()
    if not args.live:
        sys.exit("Refusing to spend money without --live. The demo uses the saved api.json.")
    if OUT.exists() and not args.force:
        sys.exit(f"{OUT} already exists; pass --force to re-record.")
    load_env()
    with tempfile.TemporaryDirectory() as tmp:
        app = create_app(token=TOKEN, provider=default_provider("claude"), database=f"sqlite:///{tmp}/demo.db",
                         failure_log=Path(tmp) / "failures.jsonl", web=NoNetwork())
        client = TestClient(app, headers={"Authorization": f"Bearer {TOKEN}"})
        responses = record(client)
    OUT.write_text(json.dumps(responses, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    check = responses["POST /checks"]["body"]
    print(f"Saved {len(responses)} responses to {OUT}: {check['decision']}, ${check['cost_usd']}, {check['calls']} calls")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
