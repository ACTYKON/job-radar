from __future__ import annotations

import hashlib
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from urllib.parse import urlsplit, urlunsplit

from src.models import Job


class Storage:
    def __init__(self, path: str):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
          CREATE TABLE IF NOT EXISTS seen (key TEXT PRIMARY KEY, title TEXT, company TEXT, url TEXT, source TEXT, seen_at TEXT);
          CREATE TABLE IF NOT EXISTS http_cache (source TEXT PRIMARY KEY, etag TEXT, last_modified TEXT, fetched_at TEXT);
          CREATE TABLE IF NOT EXISTS failures (source TEXT PRIMARY KEY, count INTEGER NOT NULL DEFAULT 0, alerted INTEGER NOT NULL DEFAULT 0);
          CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
        """)
        self.db.commit()

    @staticmethod
    def key(job: Job) -> str:
        url = urlsplit(job.url)
        normalized = urlunsplit(
            (url.scheme.lower(), url.netloc.lower(), url.path.rstrip("/"), "", "")
        )
        base = f"{job.source}:{job.id}" if job.id and job.id != job.url else normalized
        return hashlib.sha256(base.encode()).hexdigest()

    def is_duplicate(self, job: Job, pending: list[Job] | None = None) -> bool:
        key = self.key(job)
        rows = self.db.execute("SELECT title, company, url FROM seen").fetchall()
        norm = lambda s: " ".join((s or "").casefold().split())
        title, company = norm(job.title), norm(job.company)
        for old_title, old_company, old_url in rows:
            if old_url == job.url:
                return True
            if (
                title
                and old_title
                and SequenceMatcher(None, title, norm(old_title)).ratio() >= 0.92
                and (not company or not old_company or company == norm(old_company))
            ):
                return True
        if (
            self.db.execute("SELECT 1 FROM seen WHERE key=?", (key,)).fetchone()
            is not None
        ):
            return True
        for other in pending or []:
            other_title, other_company = norm(other.title), norm(other.company)
            if key == self.key(other) or job.url == other.url:
                return True
            if (
                title
                and other_title
                and SequenceMatcher(None, title, other_title).ratio() >= 0.92
                and (not company or not other_company or company == other_company)
            ):
                return True
        return False

    def mark_seen(self, job: Job):
        self.db.execute(
            "INSERT OR IGNORE INTO seen VALUES(?,?,?,?,?,?)",
            (
                self.key(job),
                job.title,
                job.company,
                job.url,
                job.source,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        self.db.commit()

    def prune(self):
        self.db.execute(
            "DELETE FROM seen WHERE seen_at < ?",
            ((datetime.now(timezone.utc) - timedelta(days=60)).isoformat(),),
        )
        self.db.commit()

    def cache_headers(self, source: str) -> dict[str, str]:
        row = self.db.execute(
            "SELECT etag,last_modified FROM http_cache WHERE source=?", (source,)
        ).fetchone()
        return dict(zip(("etag", "last_modified"), row)) if row else {}

    def put_cache(self, source: str, headers: dict[str, str]):
        if headers:
            self.db.execute(
                "INSERT OR REPLACE INTO http_cache VALUES(?,?,?,?)",
                (
                    source,
                    headers.get("etag"),
                    headers.get("last_modified"),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            self.db.commit()

    def due(self, source: str, hours: float) -> bool:
        if hours <= 0:
            return True
        row = self.db.execute(
            "SELECT fetched_at FROM http_cache WHERE source=?", (source,)
        ).fetchone()
        if not row:
            return True
        try:
            return (
                datetime.now(timezone.utc) - datetime.fromisoformat(row[0])
            ).total_seconds() >= hours * 3600
        except ValueError:
            return True

    def fetched(self, source: str):
        self.db.execute(
            "INSERT INTO http_cache(source,fetched_at) VALUES(?,?) ON CONFLICT(source) DO UPDATE SET fetched_at=excluded.fetched_at",
            (source, datetime.now(timezone.utc).isoformat()),
        )
        self.db.commit()

    def failed(self, source: str, limit: int) -> bool:
        self.db.execute(
            "INSERT INTO failures(source,count,alerted) VALUES(?,1,0) ON CONFLICT(source) DO UPDATE SET count=count+1",
            (source,),
        )
        self.db.commit()
        row = self.db.execute(
            "SELECT count,alerted FROM failures WHERE source=?", (source,)
        ).fetchone()
        if row and row[0] >= limit and not row[1]:
            self.db.execute("UPDATE failures SET alerted=1 WHERE source=?", (source,))
            self.db.commit()
            return True
        return False

    def succeeded(self, source: str):
        self.db.execute("DELETE FROM failures WHERE source=?", (source,))
        self.db.commit()

    def defer_failure_alert(self, source: str):
        self.db.execute("UPDATE failures SET alerted=0 WHERE source=?", (source,))
        self.db.commit()

    def get_meta(self, key: str):
        row = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

    def set_meta(self, key: str, value: str):
        self.db.execute("INSERT OR REPLACE INTO meta VALUES(?,?)", (key, value))
        self.db.commit()

    def close(self):
        self.db.close()
