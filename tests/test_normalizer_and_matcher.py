"""Tests for BOAMP record normalization and alert matching."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from marchepulse.models import AlertSubscription
from marchepulse.services.matcher import tender_matches_subscription
from marchepulse.services.normalizer import normalize_boamp_record


def test_normalize_boamp_record_open_tender() -> None:
    future_deadline = (datetime.now(timezone.utc) + timedelta(days=10)).isoformat()
    raw = {
        "idweb": "26-10001",
        "objet": "Maintenance reseau fibre optique et cybersécurité",
        "nomacheteur": "Ville de Rennes",
        "code_departement": ["35", "35"],
        "dateparution": "2026-11-10",
        "datelimitereponse": future_deadline,
        "type_marche": ["services"],
        "descripteur_code": ["180"],
        "descripteur_libelle": ["Informatique"],
        "nature_libelle": "Avis de marché",
        "procedure_libelle": "Procédure adaptée",
        "url_avis": "https://www.boamp.fr/pages/avis/?q=idweb:26-10001",
    }
    tender = normalize_boamp_record(raw)
    assert tender.idweb == "26-10001"
    assert tender.departments == ["35"]
    assert tender.market_types == ["SERVICES"]
    assert tender.is_open is True
    assert tender.days_remaining is not None and tender.days_remaining >= 9
    assert tender.quality_score == 100


def test_normalize_boamp_record_closed_scalar_department() -> None:
    raw = {
        "idweb": "20-123",
        "objet": "Travaux de voirie",
        "nomacheteur": "Commune X",
        "code_departement_prestation": "75",
        "datelimitereponse": "2020-01-01",
    }
    tender = normalize_boamp_record(raw)
    assert tender.departments == ["75"]
    assert tender.is_open is False
    assert tender.days_remaining is not None and tender.days_remaining < 0


def test_tender_matches_subscription_filters() -> None:
    future_deadline = (datetime.now(timezone.utc) + timedelta(days=5)).isoformat()
    tender = normalize_boamp_record(
        {
            "idweb": "26-999",
            "objet": "Refonte portail web et cloud souverain",
            "nomacheteur": "Metropole de Lyon",
            "code_departement": ["69"],
            "datelimitereponse": future_deadline,
            "type_marche": ["SERVICES"],
            "descripteur_libelle": ["Services informatiques"],
        }
    )
    sub = AlertSubscription(
        id=1,
        owner_key_id=1,
        name="Tech Lyon",
        keywords=["cloud", "cybersécurité"],
        departments=["69", "75"],
        market_types=["SERVICES"],
        only_open=True,
        channel="webhook",
        target="https://hooks.example.com/123",
        active=True,
        created_at=datetime.now(timezone.utc),
    )
    assert tender_matches_subscription(tender, sub) is True

    sub_wrong_dep = sub.model_copy(update={"departments": ["33"]})
    assert tender_matches_subscription(tender, sub_wrong_dep) is False

    sub_wrong_kw = sub.model_copy(update={"keywords": ["goudronnage"]})
    assert tender_matches_subscription(tender, sub_wrong_kw) is False
