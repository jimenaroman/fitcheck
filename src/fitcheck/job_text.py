"""Fetch a posting's full text from the job site's own public API (M10). No model, $0.

Only these sites, through their documented (or, for Workday, widely used) JSON endpoints. Any other link
is never fetched: a listing link is untrusted, so the server can't be pointed at an arbitrary address.
The text is untrusted too: it goes through the same pipeline (delimiters, verbatim checks) as a paste.
"""

import html
import re
from html.parser import HTMLParser
from urllib.parse import urlsplit

from fitcheck.listings import Listing
from fitcheck.web import Web

REDIRECT_HOSTS = ("go.vansh.nyc",)  # vanshb03 links: read the redirect, then check the real address
MAX_CHARS = 30_000  # far longer than any real posting; protects the model call


class UnsupportedSite(Exception):
    """The posting is on a site we don't read (Oracle, iCIMS, a company page...). Open it and paste."""


def fetch_text(listing: Listing, web: Web) -> str:
    """The posting as plain text, starting with title, company, and locations. Raises UnsupportedSite,
    or WebError / ValueError / KeyError when the site answered badly."""
    url = resolve(listing.url, web)
    body = _description(url, web)
    header = [listing.title, listing.company]
    if listing.locations:
        header.append(" · ".join(listing.locations))
    text = "\n".join(header) + "\n\n" + body.strip()
    return text[:MAX_CHARS]


def resolve(url: str, web: Web) -> str:
    """A redirect link becomes the address it points to (without visiting that address)."""
    if urlsplit(url).hostname in REDIRECT_HOSTS:
        response = web.get(url, follow_redirects=False, timeout=15)
        location = response.headers.get("location")
        if response.status not in (301, 302, 303, 307, 308) or not location:
            raise UnsupportedSite(f"redirect link didn't redirect: {url}")
        return location
    return url


def site_of(url: str) -> str | None:
    """"greenhouse", "lever", "ashby", "workday", or None for sites we don't read."""
    host = urlsplit(url).hostname or ""
    if host.endswith("greenhouse.io"):
        return "greenhouse"
    if host == "jobs.lever.co":
        return "lever"
    if host == "jobs.ashbyhq.com":
        return "ashby"
    if host.endswith(".myworkdayjobs.com"):
        return "workday"
    return None


def _description(url: str, web: Web) -> str:
    parts = urlsplit(url)
    path = [p for p in parts.path.split("/") if p]
    site = site_of(url)
    if site == "greenhouse":  # .../{board}/jobs/{id}
        board, job = path[path.index("jobs") - 1], path[path.index("jobs") + 1]
        data = _json(web, f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs/{job}")
        return html_to_text(html.unescape(data["content"]))  # Greenhouse escapes its HTML once more
    if site == "lever":  # /{company}/{id}[/apply]
        data = _json(web, f"https://api.lever.co/v0/postings/{path[0]}/{path[1]}")
        sections = [data.get("descriptionPlain", "")]
        for item in data.get("lists", []):
            sections.append(item.get("text", "") + "\n" + html_to_text(item.get("content", "")))
        sections.append(data.get("additionalPlain", ""))
        return "\n\n".join(s for s in sections if s.strip())
    if site == "ashby":  # /{org}/{id}[/application]: the board lists every job with its text
        data = _json(web, f"https://api.ashbyhq.com/posting-api/job-board/{path[0]}")
        job = next((j for j in data.get("jobs", []) if j.get("id") == path[1]), None)
        if job is None:
            raise ValueError("job no longer on the Ashby board")
        return job.get("descriptionPlain") or html_to_text(job.get("descriptionHtml", ""))
    if site == "workday":  # {tenant}.wdN.myworkdayjobs.com/[en-US/]{site}/job/...
        tenant = parts.hostname.split(".")[0]
        if re.fullmatch(r"[a-z]{2}-[A-Z]{2}", path[0]):
            path = path[1:]
        cxs = f"https://{parts.hostname}/wday/cxs/{tenant}/{'/'.join(path)}"
        return html_to_text(_json(web, cxs)["jobPostingInfo"]["jobDescription"])
    raise UnsupportedSite(f"not a site we read: {parts.hostname}")


def _json(web: Web, url: str) -> dict:
    return web.get(url, timeout=20).json()


class _Text(HTMLParser):
    """HTML -> text: block tags become line breaks, list items get "- ", everything else is dropped."""
    BLOCKS = {"p", "div", "br", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "tr", "section"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "li":
            self.out.append("\n- ")
        elif tag in self.BLOCKS:
            self.out.append("\n")

    def handle_endtag(self, tag):
        if tag in self.BLOCKS or tag == "li":
            self.out.append("\n")

    def handle_data(self, data):
        self.out.append(data)


def html_to_text(markup: str) -> str:
    parser = _Text()
    parser.feed(markup)
    lines = (" ".join(line.split()) for line in "".join(parser.out).splitlines())
    text = "\n".join(line for line in lines if line)
    return re.sub(r"\n- \n", "\n", text)
