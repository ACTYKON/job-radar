import json
from pathlib import Path

import pytest

from src.adapters.sources import fetch_source

FIXTURES = Path(__file__).parent / "fixtures"


class Response:
    def __init__(self, payload, content_type="application/json"):
        self.payload = payload
        self.headers = {"content-type": content_type}
        self.content = (
            payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        )
        self.status_code = 200

    def json(self):
        return self.payload


class FixtureClient:
    async def get(self, url, params=None, headers=None):
        if "hn.algolia.com" in url:
            return Response(
                json.loads((FIXTURES / "hackernews.json").read_text(encoding="utf-8"))
            )
        if url.endswith(".xml"):
            return Response((FIXTURES / "rss.xml").read_bytes(), "application/rss+xml")
        source = next(
            name
            for name in ("remotive", "remoteok", "workingnomads", "jobicy", "arbeitnow")
            if name in url
            or {
                "remotive": "remotive",
                "remoteok": "remoteok",
                "workingnomads": "workingnomads",
                "jobicy": "jobicy",
                "arbeitnow": "arbeitnow",
            }[name]
            in url
        )
        return Response(
            json.loads((FIXTURES / f"{source}.json").read_text(encoding="utf-8"))
        )


@pytest.mark.parametrize(
    "source,url",
    [
        ("remotive", "https://remotive"),
        ("remoteok", "https://remoteok"),
        ("workingnomads", "https://workingnomads"),
        ("jobicy", "https://jobicy"),
        ("arbeitnow", "https://arbeitnow"),
        ("hackernews", "https://hn.algolia.com/api/v1/search_by_date"),
    ],
)
@pytest.mark.anyio
async def test_json_adapters(source, url):
    jobs, _ = await fetch_source(
        source, {"url": url, "queries": ["freelancer"]}, FixtureClient()
    )
    assert jobs and jobs[0].source == source and jobs[0].title and jobs[0].url
    if source == "jobicy":
        assert jobs[0].budget == "25-35 USD/h"


@pytest.mark.anyio
async def test_google_alerts_rss_adapter(monkeypatch):
    monkeypatch.setenv("TEST_RSS_URL", "https://example.org/feed.xml")
    jobs, _ = await fetch_source(
        "google_alerts", {"env_url": "TEST_RSS_URL"}, FixtureClient()
    )
    assert jobs[0].title == "Freelance sito web"
    assert jobs[0].posted_at is not None
