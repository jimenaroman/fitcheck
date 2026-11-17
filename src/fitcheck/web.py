"""The smallest HTTP client M10 needs, on Python's own urllib (no dependency). Tests pass a fake with the
same `get` method, so nothing in the test suite touches the network."""

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Protocol

USER_AGENT = "fitcheck (personal internship fit checker; github.com/jimenaroman/fitcheck)"


class WebError(Exception):
    """No answer, or an error status. `status` is None when there was no answer at all."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


@dataclass
class Response:
    status: int
    body: bytes
    headers: dict[str, str] = field(default_factory=dict)  # lowercase names

    def json(self):
        return json.loads(self.body)


class Web(Protocol):
    def get(self, url: str, *, timeout: float = 20, follow_redirects: bool = True) -> Response: ...


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None  # hand the 30x back instead of following it


class UrllibWeb:
    def get(self, url: str, *, timeout: float = 20, follow_redirects: bool = True) -> Response:
        if not url.startswith("https://"):
            raise WebError(f"only https links are fetched: {url}")
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
        opener = urllib.request.build_opener() if follow_redirects else urllib.request.build_opener(_NoRedirect)
        try:
            with opener.open(request, timeout=timeout) as answer:
                return Response(answer.status, answer.read(), {k.lower(): v for k, v in answer.headers.items()})
        except urllib.error.HTTPError as e:
            if not follow_redirects and 300 <= e.code < 400:
                return Response(e.code, b"", {k.lower(): v for k, v in e.headers.items()})
            raise WebError(f"{url} answered {e.code}", e.code) from e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise WebError(f"couldn't reach {url}: {e}") from e
