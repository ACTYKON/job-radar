from datetime import datetime, timezone

from src.models import Job
from src.scoring import parse_budget, score_job
from src.storage import Storage


def cfg():
    import yaml

    with open("config.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def test_budget_hourly_eur_and_usd():
    c = cfg()
    assert parse_budget("Rate: 20€/h", c)["kind"] == "hourly"
    parsed = parse_budget("$25/hr", c)
    assert parsed["kind"] == "hourly" and parsed["high"] == 23


def test_budget_project_and_grouped_number():
    c = cfg()
    parsed = parse_budget("budget: da 500 a 1.500 euro", c)
    assert parsed["kind"] == "project" and parsed["high"] == 1500


def test_salary_not_misread_as_project_budget():
    assert parse_budget("€50.000 RAL annual", cfg()) is None


def test_budget_below_minimum_is_discarded():
    job = Job(
        "1",
        "fixture",
        "Freelance web developer",
        "Acme",
        "Project budget 300 euro",
        "https://example.org/1",
    )
    assert score_job(job, cfg())[0] is False


def test_missing_budget_kept_and_explained():
    job = Job(
        "1",
        "fixture",
        "Freelance web developer",
        "Acme",
        "Build a custom web application remotely",
        "https://example.org/1",
    )
    keep, _, reasons, budget = score_job(job, cfg())
    assert keep and budget == "" and any("A:" in reason for reason in reasons)
    assert job.budget is None


def test_short_keywords_have_word_boundaries_and_accents_fold():
    from src.scoring import match_terms

    assert match_terms("robotica", ["bot"]) == []
    assert match_terms("integrazione API", ["API"]) == ["API"]
    assert match_terms("automazioni per PMI", ["automazione"]) == ["automazione"]


def test_storage_dedupe_and_prune(tmp_path):
    db = Storage(str(tmp_path / "jobs.sqlite3"))
    a = Job(
        "id-1",
        "source-a",
        "Freelance web developer",
        "Acme",
        "",
        "https://example.org/job?utm=x",
    )
    b = Job(
        "id-2",
        "source-b",
        "Freelance web developer",
        "Acme",
        "",
        "https://example.org/job",
    )
    assert not db.is_duplicate(a)
    db.mark_seen(a)
    assert db.is_duplicate(a)
    assert db.is_duplicate(b)
    db.close()


def test_score_location_and_seniority():
    job = Job(
        "1",
        "fixture",
        "Senior developer",
        "Società italiana",
        "Consulenza tecnica a progetto, sviluppo web da remoto",
        "https://example.org/1",
        location="Italia",
        posted_at=datetime.now(timezone.utc),
        remote=True,
    )
    keep, score, reasons, _ = score_job(job, cfg())
    assert keep and score >= 35
    assert job.country_group == "🇮🇹 Italia"
    assert reasons
