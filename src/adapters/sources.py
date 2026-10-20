"""Public API/RSS source adapters."""

from __future__ import annotations

import asyncio
import logging
import os
import random
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

import feedparser
import httpx

from src.models import Job

log = logging.getLogger(__name__)


def _date(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc)
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
    except ValueError:
        try:
            dt = parsedate_to_datetime(str(value))
            return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
        except (TypeError, ValueError):
            return None


def _job(source: str, row: dict[str, Any]) -> Job | None:
    title = str(
        row.get("title")
        or row.get("position")
        or row.get("name")
        or row.get("jobTitle")
        or ""
    ).strip()
    url = str(
        row.get("url") or row.get("job_url") or row.get("apply_url") or ""
    ).strip()
    if not title or not url:
        return None
    description = (
        row.get("description")
        or row.get("content")
        or row.get("text")
        or row.get("jobDescription")
        or row.get("jobExcerpt")
        or ""
    )
    if isinstance(description, dict):
        description = description.get("text", "")
    tags = row.get("tags") or row.get("skills") or []
    if isinstance(tags, str):
        tags = [x.strip() for x in tags.split(",") if x.strip()]
    job_type = row.get("jobType") or row.get("job_type") or row.get("job_types") or []
    if isinstance(job_type, str):
        job_type = [job_type]
    tags = [*tags, *(str(value) for value in job_type)]
    location = (
        row.get("location")
        or row.get("candidate_required_location")
        or row.get("jobGeo")
        or ""
    )
    remote = (
        bool(row.get("remote", False))
        or "remote" in str(location).casefold()
        or "remote" in str(row.get("title", "")).casefold()
    )
    job = Job(
        id=str(row.get("id") or row.get("slug") or url),
        source=source,
        title=title,
        company=str(
            row.get("company_name")
            or row.get("companyName")
            or row.get("company")
            or row.get("employer_name")
            or ""
        ),
        description=str(description),
        url=url,
        posted_at=_date(
            row.get("publication_date")
            or row.get("date")
            or row.get("created_at")
            or row.get("epoch")
            or row.get("pubDate")
            or row.get("pub_date")
        ),
        tags=[str(t) for t in tags],
        location=str(location),
        remote=remote,
        raw=row,
    )
    salary_period = str(
        row.get("salaryPeriod") or row.get("salary_period") or ""
    ).casefold()
    salary_min, salary_max = (
        row.get("salaryMin") or row.get("salary_min"),
        row.get("salaryMax") or row.get("salary_max"),
    )
    salary_currency = str(row.get("salaryCurrency") or row.get("salary_currency") or "")
    if (
        salary_min is not None
        and salary_currency
        and salary_period in {"hourly", "hour", "project", "fixed", "fixed-price"}
    ):
        salary = f"{salary_min}-{salary_max or salary_min} {salary_currency}"
        if salary_period in {"hourly", "hour"}:
            salary += "/h"
        else:
            salary = "budget " + salary
        job.budget = salary
        job.currency = salary_currency
    return job


class SourceClient:
    def __init__(self, runtime: dict[str, Any]):
        self.runtime = runtime
        self.client = httpx.AsyncClient(
            timeout=runtime.get("request_timeout_seconds", 15),
            headers={
                "User-Agent": runtime.get("user_agent", "JobRadarPersonal/1.0"),
                "Accept": "application/json, application/rss+xml, application/xml",
            },
            follow_redirects=True,
        )

    async def close(self):
        await self.client.aclose()

    async def get(
        self, url: str, params: dict | None = None, headers: dict | None = None
    ) -> httpx.Response:
        for attempt in range(self.runtime.get("retries", 2) + 1):
            try:
                response = await self.client.get(url, params=params, headers=headers)
                response.raise_for_status()
                return response
            except (httpx.HTTPError, TimeoutError):
                if attempt >= self.runtime.get("retries", 2):
                    raise
                await asyncio.sleep((2**attempt) + random.random() * 0.4)
        raise RuntimeError("unreachable")


async def fetch_source(
    name: str,
    spec: dict[str, Any],
    client: SourceClient,
    conditional: dict[str, str] | None = None,
) -> tuple[list[Job], dict[str, str]]:
    headers: dict[str, str] = {}
    if conditional:
        if conditional.get("etag"):
            headers["If-None-Match"] = conditional["etag"]
        if conditional.get("last_modified"):
            headers["If-Modified-Since"] = conditional["last_modified"]
    url = spec.get("url", "")
    params = dict(spec.get("params", {}))
    if name == "google_alerts":
        url = os.getenv(spec.get("env_url", "GOOGLE_ALERTS_RSS_URL"), "")
        if not url:
            return [], {}
    jobs: list[Job] = []
    fresh: dict[str, str] = {}
    response = await client.get(url, params=params, headers=headers)
    if response.status_code == 304:
        return [], {}
    fresh = {
        k: response.headers[v]
        for k, v in (("etag", "ETag"), ("last_modified", "Last-Modified"))
        if response.headers.get(v)
    }
    if name == "hackernews":
        semaphore = asyncio.Semaphore(3)

        async def query(specification: str | dict):
            q = (
                specification
                if isinstance(specification, str)
                else specification.get("query", "")
            )
            tags = (
                "story"
                if isinstance(specification, str)
                else specification.get("tags", "comment")
            )
            async with semaphore:
                data = (
                    await client.get(url, {"query": q, "tags": tags, "hitsPerPage": 50})
                ).json()
            return data.get("hits", [])

        results = await asyncio.gather(*(query(q) for q in spec.get("queries", [])))
        for rows in results:
            for row in rows:
                comment_text = (
                    row.get("comment_text")
                    or row.get("story_text")
                    or row.get("text")
                    or ""
                )
                title = row.get("title") or " ".join(str(comment_text).split())[:120]
                description = (
                    str(row.get("story_title", "")) + "\n" + str(comment_text)
                ).strip()
                item = _job(
                    name,
                    {
                        **row,
                        "title": title,
                        "description": description,
                        "url": row.get("url")
                        or f"https://news.ycombinator.com/item?id={row.get('objectID')}",
                        "publication_date": row.get("created_at"),
                    },
                )
                if item:
                    item.id = str(row.get("objectID", item.id))
                    item.company = "Hacker News"
                    jobs.append(item)
        # Include monthly hiring threads, allowing their comments to surface contract posts.
        return jobs, fresh
    content_type = response.headers.get("content-type", "")
    if name == "google_alerts" or "xml" in content_type or "rss" in content_type:
        parsed = feedparser.parse(response.content)
        for entry in parsed.entries:
            item = _job(
                name,
                {
                    "id": entry.get("id") or entry.get("link"),
                    "title": entry.get("title"),
                    "description": entry.get("summary", ""),
                    "url": entry.get("link"),
                    "published": entry.get("published"),
                },
            )
            if item:
                item.posted_at = _date(entry.get("published") or entry.get("updated"))
                jobs.append(item)
        return jobs, fresh
    data = response.json()
    if name == "remotive":
        rows = data.get("jobs", [])
    elif name == "remoteok":
        rows = data[1:] if isinstance(data, list) else data.get("jobs", [])
    elif name == "workingnomads":
        rows = data if isinstance(data, list) else data.get("jobs", [])
    elif name == "jobicy":
        rows = data.get("jobs", [])
    elif name == "arbeitnow":
        rows = data.get("data", [])
    else:
        rows = data.get("jobs", data if isinstance(data, list) else [])
    for row in rows:
        item = _job(name, row)
        if item:
            jobs.append(item)
    return jobs, fresh
