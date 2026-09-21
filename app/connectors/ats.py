"""Public job readers. They only read published job board data."""

import html
import json
import re
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

import httpx


class UnsupportedBoardUrl(ValueError):
    pass


@dataclass(frozen=True)
class NormalizedJob:
    provider_job_id: str
    url: str
    company: str
    title: str
    location: str | None
    description: str
    metadata: dict = field(default_factory=dict)


def _board_token(board_url: str, provider: str) -> str:
    parsed = urlparse(board_url.rstrip("/"))
    parts = [part for part in parsed.path.split("/") if part]
    if not parts:
        raise UnsupportedBoardUrl(f"Could not find {provider} board token in {board_url}")
    return parts[-1]


def _greenhouse(board_url: str, client: httpx.Client) -> list[NormalizedJob]:
    token = _board_token(board_url, "Greenhouse")
    response = client.get(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs", params={"content": "true"})
    response.raise_for_status()
    payload = response.json()
    jobs: list[NormalizedJob] = []
    for job in payload.get("jobs", []):
        location = (job.get("location") or {}).get("name")
        jobs.append(
            NormalizedJob(
                provider_job_id=str(job["id"]),
                url=job["absolute_url"],
                company=token,
                title=job["title"],
                location=location,
                description=job.get("content") or "",
                metadata={"departments": job.get("departments", []), "offices": job.get("offices", [])},
            )
        )
    return jobs


def _lever(board_url: str, client: httpx.Client) -> list[NormalizedJob]:
    token = _board_token(board_url, "Lever")
    response = client.get(f"https://api.lever.co/v0/postings/{token}", params={"mode": "json"})
    response.raise_for_status()
    jobs: list[NormalizedJob] = []
    for job in response.json():
        categories = job.get("categories") or {}
        jobs.append(
            NormalizedJob(
                provider_job_id=job["id"],
                url=job["hostedUrl"],
                company=token,
                title=job["text"],
                location=categories.get("location"),
                description=job.get("descriptionPlain") or job.get("description") or "",
                metadata={"team": categories.get("team"), "commitment": categories.get("commitment")},
            )
        )
    return jobs


def _plain_text(value: str) -> str:
    value = re.sub(r"<br\s*/?>", "\n", value, flags=re.I)
    value = re.sub(r"<[^>]+>", " ", value)
    return re.sub(r"[ \t]+", " ", html.unescape(value)).strip()


def _carrerlift(board_url: str, client: httpx.Client) -> list[NormalizedJob]:
    """Read published Carrerlift job cards and their schema.org detail pages.

    Carrerlift exposes public, server-rendered JobPosting data. We deliberately
    cap every poll to avoid turning a discovery source into aggressive crawling.
    """

    listing_response = client.get(board_url)
    listing_response.raise_for_status()
    detail_urls = sorted(
        {
            urljoin(board_url, path)
            for path in re.findall(r'href=["\'](/jobs/[^"\'#?]+)["\']', listing_response.text)
            if path != "/jobs"
        }
    )
    jobs: list[NormalizedJob] = []
    for detail_url in detail_urls[:25]:
        response = client.get(detail_url)
        if response.status_code == 404:
            continue
        response.raise_for_status()
        json_ld = re.search(
            r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', response.text, re.I | re.S
        )
        if not json_ld:
            continue
        try:
            posting = json.loads(html.unescape(json_ld.group(1)))
        except json.JSONDecodeError:
            continue
        if posting.get("@type") != "JobPosting" or not posting.get("title"):
            continue
        identifier = posting.get("identifier") or {}
        provider_job_id = str(identifier.get("value") or detail_url.rstrip("/").rsplit("/", 1)[-1])
        company = str((posting.get("hiringOrganization") or {}).get("name") or "Carrerlift listing")
        apply_match = re.search(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>Apply now', response.text, re.I)
        apply_url = html.unescape(apply_match.group(1)) if apply_match else detail_url
        remote = posting.get("jobLocationType") == "TELECOMMUTE"
        location = "Remote" if remote else None
        description = _plain_text(str(posting.get("description") or ""))
        jobs.append(
            NormalizedJob(
                provider_job_id=provider_job_id,
                url=apply_url,
                company=company,
                title=str(posting["title"]),
                location=location,
                description=description,
                metadata={
                    "listing_url": detail_url,
                    "date_posted": posting.get("datePosted"),
                    "valid_through": posting.get("validThrough"),
                    "employment_type": posting.get("employmentType"),
                    "direct_apply": posting.get("directApply"),
                    "source": "carrerlift_public_jobposting",
                },
            )
        )
    return jobs


def fetch_board_jobs(provider: str, board_url: str, timeout_seconds: float = 20) -> list[NormalizedJob]:
    with httpx.Client(timeout=timeout_seconds, headers={"User-Agent": "CareerSystem/0.1 (public-job-indexer)"}) as client:
        if provider == "greenhouse":
            return _greenhouse(board_url, client)
        if provider == "lever":
            return _lever(board_url, client)
        if provider == "carrerlift":
            return _carrerlift(board_url, client)
    raise UnsupportedBoardUrl(f"Provider '{provider}' is not supported")
