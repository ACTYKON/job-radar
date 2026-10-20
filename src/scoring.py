from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timezone
from functools import lru_cache

from src.models import Job


def norm(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.casefold())
    return "".join(c for c in text if not unicodedata.combining(c))


@lru_cache(maxsize=1024)
def _pattern(term: str) -> re.Pattern:
    term = norm(term.strip())
    wildcard = term.endswith("*")
    if wildcard:
        term = term[:-1]
    expr = r"\s+".join(re.escape(x) for x in term.split())
    return re.compile(
        r"(?<!\w)" + expr + (r"\w*" if wildcard else r"(?!\w)"), re.IGNORECASE
    )


def match_terms(text: str, terms: list[str]) -> list[str]:
    normalized = norm(text)
    found = []
    for term in terms:
        candidates = [term]
        if not term.endswith("*") and len(term) > 2:
            low = term.casefold()
            if low.endswith("zione"):
                candidates.append(term[:-5] + "zioni")
            elif low.endswith("zioni"):
                candidates.append(term[:-5] + "zione")
            elif low.endswith("o"):
                candidates.extend((term[:-1] + "i", term + "s"))
            elif low.endswith("a"):
                candidates.extend((term[:-1] + "e", term + "s"))
            elif low.endswith("e"):
                candidates.extend((term[:-1] + "i", term + "s"))
            elif low.endswith("s"):
                candidates.append(term[:-1])
            else:
                candidates.append(term + "s")
        if any(_pattern(candidate).search(normalized) for candidate in candidates):
            found.append(term)
    return found


def parse_budget(text: str, config: dict) -> dict | None:
    """Return an explicit hourly/project range in EUR; ignore salary/monthly figures."""
    text = norm(text).replace(",", ".")
    if re.search(
        r"\b(?:ral|annual|annually|yearly|per year|annuo|annuale|monthly|per month|al mese|mensile)\b",
        text,
    ) and not re.search(
        r"\b(?:budget|project|progetto|fixed price|prezzo fisso|hourly|per hour|ora)\b",
        text,
    ):
        return None
    if re.search(
        r"\b(per year|annual|annually|yearly|ral|per month|monthly|al mese|mensil|annuo|annuale)\b",
        text,
    ):
        # Remove salary spans rather than discarding unrelated budget mentions.
        text = re.sub(
            r"[^\n.;]{0,60}(?:per year|annual|annually|yearly|ral|per month|monthly|al mese|mensil|annuo|annuale)[^\n.;]{0,60}",
            " ",
            text,
        )
    currency_codes = {
        "€": ("EUR", 1),
        "eur": ("EUR", 1),
        "euro": ("EUR", 1),
        "$": ("USD", config["profile"]["usd_eur_rate"]),
        "usd": ("USD", config["profile"]["usd_eur_rate"]),
        "£": ("GBP", config["profile"]["gbp_eur_rate"]),
        "gbp": ("GBP", config["profile"]["gbp_eur_rate"]),
    }
    sym = r"(?:€|\beur(?:o)?s?\b|\$|\busd\b|£|\bgbp\b)"
    num = r"\d{1,3}(?:[. ]\d{3})*(?:[.,]\d+)?|\d+(?:[.,]\d+)?"
    patterns = [
        re.compile(
            rf"(?P<a>{num})\s*(?P<ca>{sym})?\s*(?:-|–|to|a)\s*(?P<b>{num})\s*(?P<cb>{sym})\s*(?P<unit>/\s*(?:h|hr|hour|ora)|per\s+hour|\bora\b)?",
            re.I,
        ),
        re.compile(
            rf"(?P<ca>{sym})\s*(?P<a>{num})\s*(?:-|–|to|a)\s*(?P<cb>{sym})?\s*(?P<b>{num})\s*(?P<unit>/\s*(?:h|hr|hour|ora)|per\s+hour|\bora\b)?",
            re.I,
        ),
        re.compile(
            rf"(?P<ca>{sym})\s*(?P<a>{num})\s*(?P<unit>/\s*(?:h|hr|hour|ora)|per\s+hour|\bora\b|\s*\+)?|(?P<a2>{num})\s*(?P<ca2>{sym})\s*(?P<unit2>/\s*(?:h|hr|hour|ora)|per\s+hour|\bora\b)?",
            re.I,
        ),
    ]
    for pat in patterns:
        for m in pat.finditer(text):
            g = m.groupdict()
            a = g.get("a") or g.get("a2")
            currency = g.get("ca") or g.get("ca2") or g.get("cb")
            if not a or not currency:
                continue
            is_hour = bool(g.get("unit") or g.get("unit2")) or bool(
                re.search(
                    r"\b(?:hourly|hour|hr|ora)\b",
                    text[max(0, m.start() - 10) : m.end() + 10],
                )
            )
            is_project = bool(
                re.search(
                    r"\b(?:budget|project|progetto|fixed price|prezzo fisso|total|totale)\b",
                    text[max(0, m.start() - 30) : m.end() + 25],
                )
            )
            if not is_hour:
                # Bare currency amounts are project budgets after salary periods are removed.
                is_project = True
            if not is_hour and not is_project:
                continue
            curr = next(
                (
                    (code, rate)
                    for token, (code, rate) in currency_codes.items()
                    if token in currency.casefold()
                ),
                None,
            )
            if not curr:
                curr = (
                    ("EUR", 1)
                    if "€" in currency
                    else ("USD", config["profile"]["usd_eur_rate"])
                )

            def amount(s):
                return float(re.sub(r"[ .](?=\d{3}(?:\D|$))", "", s).replace(",", "."))

            low = amount(a)
            high = amount(g.get("b") or a)
            low_eur, high_eur = low * curr[1], high * curr[1]
            return {
                "kind": "hourly" if is_hour else "project",
                "low": low_eur,
                "high": high_eur,
                "currency": curr[0],
                "display": f"{g.get('a') or g.get('a2')}–{g.get('b') or g.get('a') or g.get('a2')} {curr[0]}"
                + ("/h" if is_hour else ""),
            }
    return None


def score_job(job: Job, config: dict) -> tuple[bool, int, list[str], str]:
    profile, scoring = config["profile"], config["scoring"]
    text = f"{job.title}\n{job.description}\n{job.company}\n{job.location}\n{' '.join(job.tags)}\n{job.budget or ''}"
    ntext = norm(text)
    title = norm(job.title)
    hard = match_terms(ntext, config["blacklist"]["hard"])
    if hard:
        return False, 0, [f"scartato: {hard[0]}"], ""
    if job.source.lower().startswith(("reddit", "hackernews")):
        self_promo = match_terms(
            ntext, config["blacklist"].get("job_seeker_signals", [])
        )
        if self_promo:
            return (
                False,
                0,
                [f"scartato: annuncio di un freelance ({self_promo[0]})"],
                "",
            )
    # Reddit self-promotion posts are explicitly advertisements by other freelancers.
    if (
        job.source.lower().startswith("reddit")
        and "[hiring]" not in title
        and "[for hire]" in title
    ):
        return False, 0, ["scartato: annuncio di un freelance"], ""
    out = match_terms(ntext, config["blacklist"]["out_of_scope"])
    if (
        job.posted_at
        and (datetime.now(timezone.utc) - job.posted_at).days
        > profile["freshness_days"]
    ):
        return False, 0, ["scartato: annuncio non recente"], ""
    all_terms = {k: match_terms(ntext, config["keywords"][k]) for k in "AB"}
    title_terms = {k: match_terms(title, config["keywords"][k]) for k in "AB"}
    if not all_terms["A"] and len(all_terms["B"]) < scoring["zero_a_min_b"]:
        return False, 0, ["nessuna corrispondenza tecnica sufficiente"], ""
    for group in "CD":
        all_terms[group] = match_terms(ntext, config["keywords"][group])
        title_terms[group] = match_terms(title, config["keywords"][group])
    freelance = bool(match_terms(ntext, config["freelance_detection"]))
    if scoring.get("require_freelance_signal", True) and not freelance:
        return (
            False,
            0,
            ["scartato: incarico freelance/contract non esplicito"],
            job.budget or "",
        )
    employment = match_terms(ntext, config["employment_signals"])
    if employment and not freelance:
        return False, 0, ["scartato: posizione dipendente"], ""
    budget = parse_budget(text, config)
    if budget:
        minimum = (
            profile["min_hourly_eur"]
            if budget["kind"] == "hourly"
            else profile["min_project_eur"]
        )
        if budget["high"] < minimum:
            return False, 0, ["scartato: budget sotto soglia"], budget["display"]
        job.budget, job.currency = budget["display"], budget["currency"]
    else:
        job.budget = None
    loc_signals = match_terms(ntext, config["location"]["italy_signals"])
    boost_signals = match_terms(ntext, config["location"].get("boost_signals", []))
    restricted = match_terms(ntext, config["location"]["restricted_signals"])
    onsite = match_terms(ntext, config["location"].get("onsite_signals", []))
    if (
        onsite
        and not loc_signals
        and not re.search(r"\b(?:remote|remoto|da remoto)\b", ntext)
    ):
        return False, 0, ["scartato: sede non confermata in Italia"], job.budget or ""
    reasons = []
    total = 0
    for group in "ABCD":
        matched = all_terms[group]
        weighted_count = len(matched) + len(title_terms[group])
        pts = min(
            weighted_count * scoring[f"group_{group.lower()}_weight"],
            scoring[f"group_{group.lower()}_max"],
        )
        if pts:
            total += pts
            reasons.extend(f"{group}: {term}" for term in matched[:3])
    if out:
        total += scoring["out_of_scope_penalty"]
        reasons.append(f"fuori ambito (-30): {out[0]}")
    for term, weight, reason in [
        (x, scoring["senior_bonus"], "senior")
        for x in match_terms(ntext, config["seniority"]["positive"])[:1]
    ]:
        total += weight
        reasons.append(f"livello: {reason} ({term})")
    junior = match_terms(ntext, config["seniority"]["negative"])
    if junior:
        total += scoring["junior_penalty"]
        reasons.append(f"junior ({junior[0]})")
    if budget:
        total += scoring["budget_bonus"]
        reasons.append("budget adeguato")
    if loc_signals or boost_signals:
        total += scoring["italian_bonus"]
        reasons.append("mercato/lingua italiana")
    if loc_signals or "italy" in ntext or "italia" in ntext:
        job.country_group = "🇮🇹 Italia"
    elif restricted:
        total += scoring["restricted_remote_penalty"]
        reasons.append(f"vincolo geografico ({restricted[0]})")
    else:
        job.country_group = "🌍 Remote worldwide"
    job.score = max(0, min(scoring.get("max_score", 100), total))
    job.reasons = reasons
    return True, job.score, reasons, job.budget or ""
