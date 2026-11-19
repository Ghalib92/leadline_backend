"""Company career pages: the public job-board APIs of Greenhouse, Lever, Ashby and
Workable (no keys). A board is a company on one of them, written "greenhouse:gitlab"
or given as its career-page link (boards.greenhouse.io/gitlab, jobs.lever.co/x,
jobs.ashbyhq.com/x, apply.workable.com/x).

The boards are sources.yaml's defaults plus the companies a candidate follows (their
job preferences, through `configure`). Each board is read once per run (and shared
across runs for `cache_minutes`), then every query is matched in code: words in the
title count most. Jobs in places the candidate can't work are left out in code: on
site or hybrid away from their countries and cities, or remote only for another
country.

Each result is the whole posting, so `verified_listing` builds its facts without a
model (no verifier call per job).
"""

import re
import threading
import time
from datetime import UTC, datetime
from html import unescape
from urllib.parse import urlsplit

import httpx
from django.core.cache import cache

from apps.ai.tools.fetch.base import FetchedPage
from apps.ai.tools.http import get_http_client
from apps.ai.tools.search.base import SearchError, SearchHit

from .base import html_to_text

SYSTEMS = ("greenhouse", "lever", "ashby", "workable")
CACHE_PREFIX = "ai:careers:"
MAX_DESCRIPTION = 6000
# Where a career-page link points: host pattern → system.
_HOSTS = (
    (re.compile(r"(^|\.)greenhouse\.io$"), "greenhouse"),
    (re.compile(r"^jobs\.(eu\.)?lever\.co$"), "lever"),
    (re.compile(r"^jobs\.ashbyhq\.com$"), "ashby"),
    (re.compile(r"^apply\.workable\.com$"), "workable"),
)
_SLUG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,80}$")
# Remote with no country attached, or open to Kenya's regions.
OPEN_REGIONS = ("anywhere", "worldwide", "global", "emea", "africa", "international")
STOPWORDS = {
    "the", "and", "for", "of", "in", "to", "a", "an", "on", "at", "with", "or", "job",
    "jobs", "role", "roles", "career", "careers", "position", "vacancy", "vacancies",
    "kenya", "nairobi", "remote", "hiring",
}  # fmt: skip


def parse_board(value: str) -> tuple[str, str] | None:
    """("greenhouse", "gitlab") from "greenhouse:gitlab" or a career-page link."""
    value = (value or "").strip()
    if ":" in value and "//" not in value:
        system, _, slug = value.partition(":")
        system, slug = system.strip().lower(), slug.strip()
        return (system, slug) if system in SYSTEMS and _SLUG.match(slug) else None
    if "//" not in value:
        return None
    parts = urlsplit(value)
    host = (parts.hostname or "").lower()
    path = [segment for segment in parts.path.split("/") if segment]
    for pattern, system in _HOSTS:
        if pattern.search(host) and path and _SLUG.match(path[0]):
            if system == "greenhouse" and path[0] == "embed":
                return None
            return system, path[0]
    return None


class CareerPagesSource:
    domain = "career pages"
    complete_listings = True  # each result is the whole posting

    def __init__(self, name: str = "career_pages", *, options: dict | None = None):
        self.name = name
        self.options = options or {}
        self.boards = [b for b in map(parse_board, self.options.get("boards") or []) if b]
        self.places: dict = {}
        self._jobs: list[dict] | None = None
        self._failed: list[str] = []
        self._lock = threading.Lock()

    def configure(self, request) -> None:
        """The run's candidate: the companies they follow, and where they can work."""
        search = getattr(request, "job_search", None) or {}
        for value in search.get("companies") or []:
            board = parse_board(value)
            if board and board not in self.boards:
                self.boards.insert(0, board)  # theirs first
        self.places = {
            "countries": [c.upper() for c in search.get("countries") or []],
            "cities": [c.lower() for c in search.get("cities") or []],
            "workplaces": search.get("workplaces") or [],
        }

    def search(self, query: str, *, max_results: int) -> list[SearchHit]:
        words = [w for w in re.findall(r"[a-z0-9+#.]+", query.lower()) if w not in STOPWORDS]
        if not words:
            return []
        scored = []
        needed = max(1, (len(words) + 1) // 2)  # at least half the words
        for job in self._all_jobs():
            if not self._can_work(job):
                continue
            title = job["title"].lower()
            text = f"{job['department']} {job['description'][:3000]}".lower()
            in_title = [w for w in words if re.search(rf"\b{re.escape(w)}\b", title)]
            in_text = [
                w for w in words if w not in in_title and re.search(rf"\b{re.escape(w)}\b", text)
            ]
            # One word at least in the title, and at least half of them somewhere.
            if in_title and len(in_title) + len(in_text) >= needed:
                scored.append((3 * len(in_title) + len(in_text), job))
        # Best match first; newest first among equals.
        scored.sort(key=lambda item: item[1]["posted"] or "", reverse=True)
        scored.sort(key=lambda item: -item[0])
        return [_hit(job) for _, job in scored[:max_results]]

    def read(self, hit: SearchHit) -> FetchedPage:
        return FetchedPage(url=hit.url, title=hit.title, text=hit.content)

    def verified_listing(self, hit: SearchHit) -> dict:
        """The posting's facts, as the verifier would extract them, built in code."""
        job = hit.extra.get("job") or {}
        return {
            "is_opportunity": True,
            "is_open": True,  # listed on the company's board means open
            "opportunity_type": "job",
            "title": hit.title,
            "funder": job.get("company", ""),
            "summary": hit.extra.get("summary", ""),
            "location": job.get("location", ""),
            "deadline": "",
        }

    # -- reading boards ------------------------------------------------------------------

    def _all_jobs(self) -> list[dict]:
        with self._lock:
            if self._jobs is None:
                jobs = []
                with get_http_client(
                    timeout=float(self.options.get("timeout_seconds", 30))
                ) as client:
                    for system, slug in self.boards[: int(self.options.get("max_boards", 30))]:
                        try:
                            jobs += self._board(client, system, slug)
                        except SearchError:
                            self._failed.append(f"{system}:{slug}")
                if not jobs and self._failed and len(self._failed) == len(self.boards):
                    raise SearchError("No career page could be read.")
                self._jobs = jobs
            return self._jobs

    def _board(self, client, system: str, slug: str) -> list[dict]:
        key = f"{CACHE_PREFIX}{system}:{slug}"
        cached = cache.get(key)
        if cached is not None:
            return cached
        reader = {"greenhouse": _greenhouse, "lever": _lever, "ashby": _ashby}.get(system)
        jobs = reader(client, slug) if reader else _workable(client, slug, self.options)
        minutes = float(self.options.get("cache_minutes", 30))
        cache.set(key, jobs, timeout=int(minutes * 60))
        return jobs

    def _can_work(self, job: dict) -> bool:
        """Leave out jobs in places the candidate can't work (decided in code)."""
        if not self.places:
            return True
        countries, cities = self.places["countries"], self.places["cities"]
        workplaces = self.places["workplaces"]
        location = job["location"].lower()
        here = any(c and c in location for c in cities) or any(
            code and (code == job.get("country") or _country_named(code, location))
            for code in countries
        )
        if job["workplace"] == "remote":
            if workplaces and "remote" not in workplaces:
                return here
            # "Remote" alone is open to anyone; "Remote, Israel" only to Israel.
            return here or _remote_anywhere(location) or any(r in location for r in OPEN_REGIONS)
        if workplaces and job["workplace"] not in workplaces and job["workplace"]:
            return False
        return here or not (countries or cities)


# -- each system ---------------------------------------------------------------------


def _get(client, url, **kwargs):
    try:
        response = client.get(url, headers={"Accept": "application/json"}, **kwargs)
    except httpx.HTTPError as exc:
        raise SearchError(f"{urlsplit(url).hostname} could not be reached.") from exc
    if response.status_code >= 400:
        raise SearchError(f"{urlsplit(url).hostname} returned HTTP {response.status_code}.")
    return response.json()


def _job(**fields) -> dict:
    description = html_to_text(unescape(fields.pop("html", "") or "")) or fields.pop("text", "")
    fields.pop("text", None)
    location = " ".join(str(fields.get("location") or "").split())
    workplace = fields.get("workplace") or ("remote" if "remote" in location.lower() else "")
    return {
        "company": fields.get("company", ""),
        "title": " ".join(str(fields.get("title") or "").split()),
        "url": fields.get("url", ""),
        "apply_url": fields.get("apply_url") or fields.get("url", ""),
        "location": location,
        "country": (fields.get("country") or "").upper(),
        "workplace": workplace,
        "employment_type": fields.get("employment_type", ""),
        "department": fields.get("department", ""),
        "salary": fields.get("salary", ""),
        "posted": (fields.get("posted") or "")[:10],
        "description": description[:MAX_DESCRIPTION],
        "system": fields.get("system", ""),
    }


def _greenhouse(client, slug):
    data = _get(
        client,
        f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs",
        params={"content": "true"},
    )
    company = ""
    jobs = []
    for item in data.get("jobs") or []:
        company = item.get("company_name") or company or slug
        jobs.append(
            _job(
                system="greenhouse",
                company=company,
                title=item.get("title"),
                url=item.get("absolute_url", ""),
                location=(item.get("location") or {}).get("name", ""),
                department=", ".join(d.get("name", "") for d in item.get("departments") or []),
                posted=item.get("first_published") or item.get("updated_at") or "",
                html=item.get("content", ""),
            )
        )
    return jobs


WORKPLACES = {"remote": "remote", "hybrid": "hybrid", "on-site": "onsite", "onsite": "onsite",
              "on_site": "onsite", "unspecified": ""}  # fmt: skip


def _lever(client, slug):
    data = _get(client, f"https://api.lever.co/v0/postings/{slug}", params={"mode": "json"})
    jobs = []
    for item in data if isinstance(data, list) else []:
        categories = item.get("categories") or {}
        salary = item.get("salaryRange") or {}
        created = item.get("createdAt")
        jobs.append(
            _job(
                system="lever",
                company=slug.replace("-", " ").title(),
                title=item.get("text"),
                url=item.get("hostedUrl", ""),
                apply_url=item.get("applyUrl", ""),
                location=categories.get("location", ""),
                country=item.get("country", ""),
                workplace=WORKPLACES.get(str(item.get("workplaceType", "")).lower(), ""),
                employment_type=categories.get("commitment", ""),
                department=categories.get("team", ""),
                salary=_range(salary.get("min"), salary.get("max"), salary.get("currency")),
                posted=(
                    datetime.fromtimestamp(created / 1000, tz=UTC).isoformat() if created else ""
                ),
                text="\n\n".join(
                    part
                    for part in (
                        item.get("descriptionPlain", ""),
                        *(
                            f"{lst.get('text', '')}\n{html_to_text(lst.get('content', ''))}"
                            for lst in item.get("lists") or []
                        ),
                        item.get("additionalPlain", ""),
                    )
                    if part
                ),
            )
        )
    return jobs


def _ashby(client, slug):
    data = _get(
        client,
        f"https://api.ashbyhq.com/posting-api/job-board/{slug}",
        params={"includeCompensation": "true"},
    )
    jobs = []
    for item in data.get("jobs") or []:
        if item.get("isListed") is False:
            continue
        country = ((item.get("address") or {}).get("postalAddress") or {}).get("addressCountry", "")
        jobs.append(
            _job(
                system="ashby",
                company=slug.replace("-", " ").title(),
                title=item.get("title"),
                url=item.get("jobUrl", ""),
                apply_url=item.get("applyUrl", ""),
                location=", ".join(p for p in (item.get("location"), country) if p),
                workplace=WORKPLACES.get(str(item.get("workplaceType", "")).lower(), "")
                or ("remote" if item.get("isRemote") else ""),
                employment_type=_words(item.get("employmentType", "")),
                department=item.get("department", ""),
                salary=(item.get("compensation") or {}).get("compensationTierSummary", ""),
                posted=item.get("publishedAt", ""),
                text=item.get("descriptionPlain", ""),
            )
        )
    return jobs


def _workable(client, slug, options):
    """The list has no descriptions: each job's page is read too, up to a few per board."""
    try:
        response = client.post(
            f"https://apply.workable.com/api/v3/accounts/{slug}/jobs",
            json={"query": ""},
            headers={"Accept": "application/json"},
        )
    except httpx.HTTPError as exc:
        raise SearchError("apply.workable.com could not be reached.") from exc
    if response.status_code >= 400:
        raise SearchError(f"apply.workable.com returned HTTP {response.status_code}.")
    jobs = []
    limit = int(options.get("workable_details", 15))
    for index, item in enumerate(response.json().get("results") or []):
        shortcode = item.get("shortcode", "")
        location = item.get("location") or {}
        detail = {}
        if index < limit:
            try:
                detail = _get(
                    client, f"https://apply.workable.com/api/v2/accounts/{slug}/jobs/{shortcode}"
                )
            except SearchError:
                detail = {}
            time.sleep(float(options.get("workable_pause_seconds", 0.2)))
        url = f"https://apply.workable.com/{slug}/j/{shortcode}/"
        jobs.append(
            _job(
                system="workable",
                company=slug.replace("-", " ").title(),
                title=item.get("title"),
                url=url,
                apply_url=f"{url}apply/",
                location=", ".join(p for p in (location.get("city"), location.get("country")) if p),
                country=location.get("countryCode", ""),
                workplace=WORKPLACES.get(str(item.get("workplace", "")).lower(), "")
                or ("remote" if item.get("remote") else ""),
                employment_type={
                    "full": "Full-time",
                    "part": "Part-time",
                    "contract": "Contract",
                }.get(item.get("type", ""), ""),
                department=", ".join(item.get("department") or []),
                posted=item.get("published", ""),
                html="".join(
                    detail.get(part, "") or ""
                    for part in ("description", "requirements", "benefits")
                ),
            )
        )
    return jobs


# -- helpers -------------------------------------------------------------------------

COUNTRY_NAMES = {
    "KE": "kenya",
    "UG": "uganda",
    "TZ": "tanzania",
    "RW": "rwanda",
    "NG": "nigeria",
    "GH": "ghana",
    "ZA": "south africa",
    "ET": "ethiopia",
}


_REMOTE_WORDS = re.compile(r"\b(remote|fully|100%|work from home|wfh|anywhere)\b")


def _remote_anywhere(location: str) -> bool:
    """Nothing but "remote" in the location: no place named."""
    rest = _REMOTE_WORDS.sub(" ", location)
    return not re.search(r"[a-z]{2,}", rest)


def _country_named(code: str, location: str) -> bool:
    name = COUNTRY_NAMES.get(code)
    return bool(name and name in location)


def _words(value: str) -> str:
    """ "FullTime" → "Full time"."""
    return re.sub(r"(?<=[a-z])(?=[A-Z])", " ", value or "").capitalize()


def _range(low, high, currency) -> str:
    if not low and not high:
        return ""
    parts = [f"{int(v):,}" for v in (low, high) if v]
    return f"{' – '.join(parts)} {currency or ''}".strip()


def _hit(job: dict) -> SearchHit:
    facts = [
        ("Title", job["title"]),
        ("Company", job["company"]),
        ("Location", job["location"]),
        ("Workplace", job["workplace"]),
        ("Employment type", job["employment_type"]),
        ("Department", job["department"]),
        ("Salary", job["salary"]),
        ("Posted", job["posted"]),
        ("Opportunity type", "job"),
    ]
    head = "\n".join(f"{label}: {value}" for label, value in facts if value)
    details = {k: v for k, v in job.items() if k not in ("description", "title") and v}
    return SearchHit(
        title=job["title"],
        url=job["url"],
        snippet=f"{job['company']} · {job['location'] or job['workplace']}",
        content=f"{head}\n\n{job['description']}".strip(),
        published=job["posted"],
        extra={
            "funder": job["company"],
            "location": job["location"],
            "summary": job["description"][:600],
            "job": details,
        },
    )
