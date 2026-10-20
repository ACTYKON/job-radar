from __future__ import annotations

import asyncio
import html
import logging
import os
import httpx

from src.models import Job

log = logging.getLogger(__name__)
SOURCE_NAMES = {
    "remotive": "Remotive",
    "remoteok": "Remote OK",
    "workingnomads": "Working Nomads",
    "jobicy": "Jobicy",
    "hackernews": "Hacker News",
    "arbeitnow": "Arbeitnow",
    "google_alerts": "Google Alerts",
}


def render(job: Job) -> str:
    posted = (
        job.posted_at.strftime("%Y-%m-%d %H:%M UTC")
        if job.posted_at
        else "data non indicata"
    )
    reason = ", ".join(job.reasons[:4]) or "corrispondenza con il profilo"
    budget = html.escape(job.budget or "non specificato")
    return (
        f"<b>{html.escape(job.country_group)} · {html.escape(job.title)}</b>\n"
        f"{html.escape(job.company or 'Cliente non indicato')} · {html.escape(SOURCE_NAMES.get(job.source, job.source))}\n"
        f"<b>Score:</b> {job.score}/100 · <b>Budget:</b> {budget}\n"
        f"<b>Motivo:</b> {html.escape(reason)}\n"
        f'<b>Pubblicato:</b> {html.escape(posted)}\n<a href="{html.escape(job.url, quote=True)}">Apri annuncio</a>'
    )


class Telegram:
    def __init__(self):
        self.token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        self.chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
        self.client = httpx.AsyncClient(timeout=15)

    @property
    def ready(self):
        return bool(self.token and self.chat_id)

    async def send(self, text: str):
        if not self.ready:
            log.info("Telegram credentials unavailable; notification skipped")
            return False
        response = await self.client.post(
            f"https://api.telegram.org/bot{self.token}/sendMessage",
            json={
                "chat_id": self.chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
        )
        response.raise_for_status()
        data = response.json()
        if not data.get("ok"):
            raise RuntimeError(f"Telegram API error: {data}")
        await asyncio.sleep(0.4)
        return True

    async def send_jobs(self, jobs: list[Job]):
        for job in sorted(
            jobs,
            key=lambda j: (
                j.country_group != "🇮🇹 Italia",
                -(j.posted_at.timestamp() if j.posted_at else 0),
            ),
        ):
            await self.send(render(job))

    async def digest(self, jobs: list[Job], stats: dict) -> bool:
        lines = [
            "<b>Job Radar · riepilogo giornaliero</b>",
            f"Fonti ok: {stats['ok']} · fallite: {stats['failed']} · candidati: {stats['found']} · inviati: {stats['sent']}",
        ]
        for job in sorted(jobs, key=lambda j: j.score, reverse=True)[:10]:
            lines.append(
                f'• <a href="{html.escape(job.url, quote=True)}">{html.escape(job.title)}</a> — {job.score}/100'
            )
        return await self.send("\n".join(lines))

    async def close(self):
        await self.client.aclose()
