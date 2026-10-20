from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class Job:
    id: str
    source: str
    title: str
    company: str
    description: str
    url: str
    budget: str | None = None
    currency: str | None = None
    posted_at: datetime | None = None
    tags: list[str] = field(default_factory=list)
    location: str = ""
    remote: bool = False
    raw: dict[str, Any] = field(default_factory=dict, repr=False)
    score: int = 0
    reasons: list[str] = field(default_factory=list)
    country_group: str = "🌍 Remote worldwide"

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        if self.posted_at:
            data["posted_at"] = self.posted_at.isoformat()
        data.pop("raw", None)
        return data
