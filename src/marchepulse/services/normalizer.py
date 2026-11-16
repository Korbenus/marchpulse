"""Normalization and quality scoring for raw BOAMP tender records."""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from marchepulse.models import NormalizedTender


def _to_str_list(value: Any, *, upper: bool = False) -> list[str]:
    if value is None:
        return []
    items = value if isinstance(value, list) else [value]
    cleaned: list[str] = []
    for item in items:
        text = str(item).strip()
        if not text:
            continue
        cleaned.append(text.upper() if upper else text)
    return list(dict.fromkeys(cleaned))


def _parse_deadline(deadline_raw: str | None, end_date_raw: str | None) -> tuple[str | None, int | None, bool]:
    now = datetime.now(timezone.utc)
    candidate = deadline_raw or end_date_raw
    if not candidate:
        return None, None, True

    text = str(candidate).strip()
    parsed: datetime | None = None
    try:
        if len(text) == 10:
            d = date.fromisoformat(text)
            parsed = datetime(d.year, d.month, d.day, 23, 59, 59, tzinfo=timezone.utc)
        else:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return text, None, True

    delta_days = (parsed.date() - now.date()).days
    return parsed.isoformat(), delta_days, parsed >= now


def normalize_boamp_record(raw: dict[str, Any]) -> NormalizedTender:
    """Transform a raw BOAMP v2.1 record into a clean `NormalizedTender`."""
    idweb = str(raw.get("idweb") or raw.get("id") or "").strip()
    title = str(raw.get("objet") or "Sans objet").strip()
    buyer_name = str(raw.get("nomacheteur") or "").strip()

    departments = _to_str_list(raw.get("code_departement"))
    if not departments and raw.get("code_departement_prestation"):
        departments = _to_str_list(raw.get("code_departement_prestation"))

    market_types = _to_str_list(raw.get("type_marche"), upper=True)
    descriptor_codes = _to_str_list(raw.get("descripteur_code"))
    descriptors = _to_str_list(raw.get("descripteur_libelle"))

    pub_date_raw = raw.get("dateparution")
    pub_date = str(pub_date_raw)[:10] if pub_date_raw else None

    deadline_iso, days_remaining, is_open = _parse_deadline(
        raw.get("datelimitereponse"),
        raw.get("datefindiffusion"),
    )

    source_url = str(
        raw.get("url_avis") or f"https://www.boamp.fr/pages/avis/?q=idweb:{idweb}"
    ).strip()

    score = 20
    if buyer_name:
        score += 15
    if departments:
        score += 15
    if descriptors:
        score += 15
    if deadline_iso:
        score += 15
    if is_open:
        score += 10
    if raw.get("procedure_libelle") or raw.get("nature_libelle"):
        score += 10

    return NormalizedTender(
        idweb=idweb,
        title=title,
        buyer_name=buyer_name,
        departments=departments,
        publication_date=pub_date,
        deadline=deadline_iso,
        days_remaining=days_remaining,
        is_open=is_open,
        market_types=market_types,
        nature=raw.get("nature_libelle") or raw.get("nature"),
        procedure=raw.get("procedure_libelle") or raw.get("type_procedure"),
        descriptor_codes=descriptor_codes,
        descriptors=descriptors,
        family=raw.get("famille_libelle") or raw.get("famille"),
        source_url=source_url,
        quality_score=min(100, score),
    )
