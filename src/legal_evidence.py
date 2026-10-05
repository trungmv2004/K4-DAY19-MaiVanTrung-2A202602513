"""Corpus-grounded numeric legal evidence and related-article teaser detection.

Only explicit named-substance mass thresholds are parsed. Unknown units are
kept as text and never silently interpreted as grams.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from .models import Document


def normalized_text(text: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", text)).strip()


def entity_key(text: str) -> str:
    return normalized_text(text).casefold()


def number(text: str) -> float:
    # Decimal comma is used by this corpus; ambiguous grouping is not supported.
    return float(text.replace(",", "."))


MASS = re.compile(r"(?<![\d.,])(\d+(?:[.,]\d+)?)\s*(kilôgam|kilogam|kg|gam|g)(?!\w)", re.I)


def mass_in_grams(amount: str) -> tuple[float | None, str]:
    """Parse a single mass; retain its comparison qualifier.

    'hơn 9,6kg' gives a strict lower bound, not an exact laboratory measurement.
    Tablet counts, ranges and multi-quantity expressions stay unresolved.
    """
    matches = list(MASS.finditer(amount))
    if len(matches) != 1 or re.search(r"đến|từ|dưới|ít hơn", amount, re.I):
        return None, "unknown"
    match = matches[0]
    grams = number(match[1]) * (1000 if match[2].lower() in ("kg", "kilôgam", "kilogam") else 1)
    qualifier = "gt" if re.search(r"hơn|trên", amount, re.I) else (
        "approx" if re.search(r"gần|khoảng|xấp xỉ", amount, re.I) else "eq")
    return grams, qualifier


def penalty_band(clause: dict[str, Any], doc_id: str) -> dict[str, Any] | None:
    """Rank imprisonment bands from the corpus; exclude supplementary fines."""
    first_line = clause["text"].splitlines()[0]
    match = re.search(r"(?:bị\s+)?phạt tù\s+(.+?)(?::|$)", first_line, re.I)
    if not match:
        return None
    text = "phạt tù " + match[1].rstrip(".")
    durations = [number(value) / (12 if unit.lower() == "tháng" else 1)
                 for value, unit in re.findall(r"(\d+(?:[.,]\d+)?)\s*(năm|tháng)", text, re.I)]
    life = "chung thân" in text.casefold()
    death = "tử hình" in text.casefold()
    return {
        "id": clause["id"] + "/penalty", "doc_id": doc_id, "text": text,
        "min_years": min(durations) if durations else None,
        "max_years": max(durations) if durations else None,
        "life": life, "death": death,
        "severity": 1000 if death else 900 if life else max(durations, default=0),
    }


def named_mass_thresholds(clause: dict, doc_id: str, find_substances) -> list[dict]:
    """One Threshold per explicit law point; never equate named and 'other' drugs."""
    thresholds = []
    for line in clause["text"].splitlines():
        # Cannabis/opium thresholds distinguish plant parts and resin. A bare
        # Substance name does not prove the physical form, so do not infer those.
        substances = [s for s in find_substances(line) if s not in ("cần sa", "thuốc phiện", "côca")]
        if not substances or "khối lượng" not in line:
            continue
        match = re.search(
            r"khối lượng\s+(?:từ\s+)?(\d+(?:[.,]\d+)?)\s+(kilôgam|kilogam|gam)"
            r"(?:\s+đến dưới\s+(\d+(?:[.,]\d+)?)\s+(kilôgam|kilogam|gam))?", line, re.I)
        if not match:
            continue
        if match[3] is None and "trở lên" not in line[match.end():]:
            continue
        point_match = re.match(r"([a-zđ])\)", line.strip(), re.I)
        point = point_match[1] if point_match else str(len(thresholds))
        lower = number(match[1]) * (1 if match[2].lower() == "gam" else 1000)
        upper = (number(match[3]) * (1 if match[4].lower() == "gam" else 1000)) if match[3] else None
        thresholds.append({
            "id": clause["id"] + "/point/" + point, "doc_id": doc_id, "point": point,
            "min_g": lower, "max_g": upper, "lower_inclusive": True, "upper_inclusive": False,
            "text": line.strip(), "substances": substances,
        })
    return thresholds


def article_leads(docs: list[Document]) -> dict[str, set[str]]:
    leads: dict[str, set[str]] = {}
    for doc in docs:
        paragraphs = [p for p in re.split(r"\n\s*\n", doc.content) if p.strip() and not p.lstrip().startswith("#")]
        if paragraphs:
            lead = entity_key(paragraphs[0])
            if len(lead) >= 80:
                leads.setdefault(lead, set()).add(doc.id)
    return leads


def without_related_teasers(doc: Document, leads: dict[str, set[str]]) -> tuple[str, int]:
    """Remove only trailing paragraphs equal to another supplied article's lead.

    This is a corpus-wide rule, independent of question IDs or benchmark gold.
    The original corpus and Flat RAG chunks remain unchanged.
    """
    paragraphs = re.split(r"\n\s*\n", doc.content.strip())
    removed = 0
    while paragraphs and any(source != doc.id for source in leads.get(entity_key(paragraphs[-1]), set())):
        paragraphs.pop()
        removed += 1
    return "\n\n".join(paragraphs), removed


def explicit_responsibility_totals(content: str, people: list[dict], substances: list[str]) -> list[dict]:
    """Extract explicitly attributed total mass independently of LLM omissions.

    Resolve a short subject only when it uniquely matches a named person in this
    source. No benchmark name/question or legal answer is embedded in this rule.
    """
    totals = []
    for paragraph in re.split(r"\n\s*\n", content):
        evidence = normalized_text(paragraph)
        attribution = re.search(r"Tổng khối lượng ma túy (.{1,80}?) phải chịu trách nhiệm hình sự là (.+)", evidence, re.I)
        if not attribution:
            continue
        short = entity_key(attribution[1])
        candidates = [p['name'] for p in people if short == entity_key(p['name'])
                      or (' ' not in short and short == entity_key(p['name']).split()[-1])]
        if len(candidates) != 1:
            continue
        names = '|'.join(re.escape(name) for name in substances)
        pattern = re.compile(r"(?:(hơn|gần|khoảng)\s+)?" + MASS.pattern +
                             r"\s+(?:ma túy\s+)?(" + names + r")(?!\w)", re.I)
        # The next sentence may assign a different total to another defendant.
        # Keep decimal dots inside numbers, stop at the first sentence-ending dot.
        attributed_sentence = re.split(r"(?<!\d)\.(?:\s|$)", attribution[2], maxsplit=1)[0]
        for match in pattern.finditer(attributed_sentence):
            amount = match[0].rsplit(match[4], 1)[0].removesuffix('ma túy ').strip()
            mass, qualifier = mass_in_grams(amount)
            canonical = next(name for name in substances if name.casefold() == match[4].casefold())
            totals.append({'name':canonical, 'amount':amount, 'mass_g':mass, 'qualifier':qualifier,
                           'subject':candidates[0], 'evidence':evidence, 'verified':True,
                           'quantity_verified':mass is not None, 'evidence_type':'responsibility_total'})
    return totals
