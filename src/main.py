from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone

import httpx
import yaml

from src.adapters.sources import SourceClient, fetch_source
from src.models import Job
from src.notifier import Telegram, render
from src.scoring import score_job
from src.storage import Storage


def load_config() -> dict:
    with open("config.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


async def rerank(jobs: list[Job], config: dict, db: Storage) -> None:
    """Optional Gemini rerank. Any API/config error leaves deterministic score intact."""
    runtime = config["runtime"]
    key = os.getenv("GEMINI_API_KEY") or os.getenv("LLM_API_KEY")
    if not runtime.get("llm_enabled") or not key:
        return
    selected = [j for j in jobs if j.score >= runtime.get("llm_threshold", 65)]
    if not selected:
        return
    fingerprint = hashlib.sha256(
        "\n".join(
            f"{j.title}|{j.company}|{j.description[:1200]}|{j.score}" for j in selected
        ).encode()
    ).hexdigest()
    cached = db.get_meta("llm_cache")
    if cached:
        try:
            cached_fingerprint, cached_json = cached.split("\n", 1)
            if cached_fingerprint == fingerprint:
                _apply_llm_rows(selected, json.loads(cached_json))
                return
        except (ValueError, json.JSONDecodeError):
            pass
    cooldown = db.get_meta("llm_cooldown_until")
    if cooldown:
        try:
            if datetime.now(timezone.utc) < datetime.fromisoformat(cooldown):
                return
        except ValueError:
            pass
    prompt = (
        "Return ONLY JSON array of {index, score, reason}. Score freelance software project fit 0-100. Keep original scores unless evidence changes them. Items:\n"
        + "\n".join(
            f"{i}. {j.title} | {j.description[:1200]}" for i, j in enumerate(selected)
        )
    )
    try:
        async with httpx.AsyncClient(timeout=12) as client:
            response = await client.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{runtime.get('llm_model', 'gemini-2.5-flash')}:generateContent",
                headers={"x-goog-api-key": key},
                json={
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {"responseMimeType": "application/json"},
                },
            )
            if response.status_code == 429:
                db.set_meta(
                    "llm_cooldown_until",
                    (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
                )
                logging.getLogger(__name__).warning(
                    "Gemini rate limit; pausing reranking for one hour"
                )
                return
            response.raise_for_status()
            rows = json.loads(
                response.json()["candidates"][0]["content"]["parts"][0]["text"]
            )
            _apply_llm_rows(selected, rows)
            db.set_meta("llm_cache", fingerprint + "\n" + json.dumps(rows))
            db.set_meta("llm_cooldown_until", "")
    except Exception as exc:
        logging.getLogger(__name__).warning(
            "Gemini unavailable; keeping rule scores: %s", exc
        )


def _apply_llm_rows(selected: list[Job], rows: list[dict]) -> None:
    for row in rows:
        try:
            i = int(row["index"])
            if 0 <= i < len(selected):
                selected[i].score = max(0, min(100, int(row["score"])))
                selected[i].reasons.append(
                    "Gemini: " + str(row.get("reason", "rivalutazione"))[:180]
                )
        except (KeyError, TypeError, ValueError):
            continue


async def run(dry_run: bool = False, send_test: bool = False) -> int:
    config = load_config()
    runtime = config["runtime"]
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    db = Storage(runtime["database"])
    client = SourceClient(runtime)
    telegram = Telegram()
    if send_test:
        try:
            if not telegram.ready:
                raise RuntimeError("Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID first")
            await telegram.send(
                "✅ <b>Job Radar attivo.</b> Test di connessione riuscito."
            )
            return 0
        finally:
            await client.close()
            await telegram.close()
            db.close()
    semaphore = asyncio.Semaphore(runtime.get("max_concurrency", 6))
    counts = {"ok": 0, "failed": 0, "found": 0, "sent": 0}
    alerts = []

    async def source_task(name: str, spec: dict):
        if not spec.get("enabled", False):
            return []
        if name == "google_alerts" and not os.getenv(
            spec.get("env_url", "GOOGLE_ALERTS_RSS_URL")
        ):
            return []
        if not db.due(name, spec.get("poll_hours", 0)):
            logging.info("Source %s skipped until poll interval", name)
            return []
        async with semaphore:
            try:
                jobs, headers = await fetch_source(
                    name, spec, client, db.cache_headers(name)
                )
                db.put_cache(name, headers)
                db.fetched(name)
                db.succeeded(name)
                counts["ok"] += 1
                logging.info("Source %s: %d jobs", name, len(jobs))
                return jobs
            except Exception:
                logging.exception("Source %s failed", name)
                counts["failed"] += 1
                if db.failed(name, runtime.get("alert_failure_after", 3)):
                    alerts.append(name)
                return []

    results = await asyncio.gather(
        *(source_task(name, spec) for name, spec in config["sources"].items())
    )
    candidates = [j for batch in results for j in batch]
    counts["found"] = len(candidates)
    accepted = []
    for job in candidates:
        keep, score, reasons, _ = score_job(job, config)
        if (
            keep
            and score >= config["scoring"]["threshold"]
            and not db.is_duplicate(job, accepted)
        ):
            job.score, job.reasons = score, reasons
            accepted.append(job)
    await rerank(accepted, config, db)
    accepted = [j for j in accepted if j.score >= config["scoring"]["threshold"]]
    ordered = sorted(
        accepted,
        key=lambda j: (
            j.country_group != "🇮🇹 Italia",
            -(j.posted_at.timestamp() if j.posted_at else 0),
        ),
    )
    if dry_run:
        for job in ordered:
            print(render(job), "\n")
        print(
            f"dry-run: {len(candidates)} raccolti, {len(accepted)} corrispondenze; fonti ok={counts['ok']} errore={counts['failed']}"
        )
    else:
        for name in alerts:
            try:
                if not await telegram.send(
                    f"⚠️ La fonte <b>{name}</b> è fallita in {runtime['alert_failure_after']} esecuzioni consecutive."
                ):
                    db.defer_failure_alert(name)
            except Exception:
                db.defer_failure_alert(name)
                raise
        if telegram.ready:
            for job in ordered:
                if await telegram.send(render(job)):
                    db.mark_seen(job)
                    counts["sent"] += 1
        elif accepted:
            logging.getLogger(__name__).warning(
                "Telegram credentials missing; jobs remain unmarked for delivery when configured"
            )
        now = datetime.now(timezone.utc)
        digest_date = db.get_meta("digest_date")
        if (
            now.hour >= runtime.get("digest_hour_utc", 17)
            and digest_date != now.date().isoformat()
        ):
            if await telegram.digest(accepted, counts):
                db.set_meta("digest_date", now.date().isoformat())
    db.prune()
    await client.close()
    await telegram.close()
    db.close()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Monitora opportunità freelance IT")
    parser.add_argument(
        "--dry-run", action="store_true", help="stampa i match senza notificare"
    )
    parser.add_argument(
        "--send-test", action="store_true", help="invia un messaggio di test Telegram"
    )
    parser.add_argument("--once", action="store_true", help="esegue un ciclo (default)")
    args = parser.parse_args()
    if args.dry_run and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    return asyncio.run(run(dry_run=args.dry_run, send_test=args.send_test))


if __name__ == "__main__":
    raise SystemExit(main())
