"""End-to-end tests for FastAPI routes and the autonomous pipeline."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from marchepulse.api.app import create_app
from marchepulse.clients.boamp import BoampClient
from marchepulse.clients.entreprises import EntreprisesClient
from marchepulse.config import Settings
from marchepulse.db.database import Database, _schema_statements, adapt_sql
from marchepulse.jobs.run_pipeline import run_autonomous_pipeline
from marchepulse.services.notifier import Notifier


def test_full_api_and_autonomous_pipeline_flow(tmp_path: Path) -> None:
    future_deadline = (datetime.now(timezone.utc) + timedelta(days=14)).isoformat()

    def boamp_handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "total_count": 1,
                "results": [
                    {
                        "idweb": "26-50001",
                        "objet": "Developpement plateforme IA et cloud",
                        "nomacheteur": "Ville de Nantes",
                        "code_departement": ["44"],
                        "dateparution": "2026-11-12",
                        "datelimitereponse": future_deadline,
                        "type_marche": ["SERVICES"],
                        "descripteur_libelle": ["Services informatiques"],
                    }
                ],
            },
        )

    def entreprises_handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "siren": "214401093",
                        "nom_complet": "COMMUNE DE NANTES",
                        "etat_administratif": "A",
                        "siege": {"libelle_commune": "NANTES", "code_postal": "44000", "departement": "44"},
                    }
                ]
            },
        )

    webhook_deliveries: list[bytes] = []

    def notifier_handler(request: httpx.Request) -> httpx.Response:
        webhook_deliveries.append(request.content)
        return httpx.Response(200, json={"ok": True})

    settings = Settings(
        env="test",
        log_level="INFO",
        base_url="http://testserver",
        db_path=tmp_path / "app.db",
        feed_dir=tmp_path / "feed",
        admin_secret="super-admin-secret",
        rapidapi_proxy_secret="",
        lemonsqueezy_webhook_secret="ls-sec",
        lemonsqueezy_checkout_url="",
        stripe_webhook_secret="st-sec",
        stripe_checkout_url="",
        resend_api_key="",
        email_from="MarchePulse <alerts@example.com>",
        boamp_fetch_limit=20,
        enrich_max_per_run=5,
    )
    db = Database(settings.db_path)
    boamp = BoampClient(transport=httpx.MockTransport(boamp_handler))
    entreprises = EntreprisesClient(
        min_interval_seconds=0.0,
        transport=httpx.MockTransport(entreprises_handler),
    )
    notifier = Notifier(settings, transport=httpx.MockTransport(notifier_handler))

    app = create_app(
        settings=settings,
        db=db,
        boamp_client=boamp,
        entreprises_client=entreprises,
        notifier=notifier,
    )
    client = TestClient(app)

    # 1. Landing & Health
    assert client.get("/health").status_code == 200
    assert "MarchePulse" in client.get("/").text

    # 2. Admin issues a key
    key_resp = client.post(
        "/v1/admin/keys",
        headers={"X-Admin-Secret": "super-admin-secret"},
        json={"customer_email": "dev@agency.fr", "plan": "pro"},
    )
    assert key_resp.status_code == 200
    api_key = key_resp.json()["api_key"]

    # 3. Unauthenticated tender request fails with 401
    assert client.get("/v1/tenders").status_code == 401

    # 4. Authenticated customer creates an alert subscription
    alert_resp = client.post(
        "/v1/alerts",
        headers={"X-API-Key": api_key},
        json={
            "name": "Veille Cloud 44",
            "keywords": ["cloud", "IA"],
            "departments": ["44"],
            "market_types": ["SERVICES"],
            "channel": "webhook",
            "target": "https://hooks.client.fr/boamp",
        },
    )
    assert alert_resp.status_code == 201
    sub_id = alert_resp.json()["id"]

    # 5. Run autonomous pipeline twice: first sends webhook, second deduplicates
    stats1 = run_autonomous_pipeline(
        db=db,
        settings=settings,
        boamp_client=boamp,
        entreprises_client=entreprises,
        notifier=notifier,
    )
    assert stats1.stored == 1
    assert stats1.buyers_enriched == 1
    assert stats1.notifications_sent == 1
    assert stats1.feed_items == 1
    assert len(webhook_deliveries) == 1

    stats2 = run_autonomous_pipeline(
        db=db,
        settings=settings,
        boamp_client=boamp,
        entreprises_client=entreprises,
        notifier=notifier,
    )
    assert stats2.notifications_sent == 0
    assert len(webhook_deliveries) == 1

    # 6. Query API & SEO feeds
    tenders_resp = client.get("/v1/tenders?department=44", headers={"X-API-Key": api_key})
    assert tenders_resp.status_code == 200
    data = tenders_resp.json()
    assert data["total"] == 1
    assert data["items"][0]["buyer"]["siren"] == "214401093"

    detail_resp = client.get("/v1/tenders/26-50001", headers={"X-API-Key": api_key})
    assert detail_resp.status_code == 200
    assert detail_resp.json()["idweb"] == "26-50001"

    assert "<rss" in client.get("/feed.xml").text
    assert client.get("/feed.json").json()["count"] == 1

    # 7. Public SEO HTML pages, sitemap, and robots.txt
    dir_resp = client.get("/marches")
    assert dir_resp.status_code == 200
    assert "Developpement plateforme IA et cloud" in dir_resp.text

    dept_resp = client.get("/marches/departement/44")
    assert dept_resp.status_code == 200
    assert "Departement 44" in dept_resp.text

    tender_html = client.get("/marches/26-50001")
    assert tender_html.status_code == 200
    assert "214401093" in tender_html.text

    sitemap_resp = client.get("/sitemap.xml")
    assert sitemap_resp.status_code == 200
    assert "http://testserver/marches/26-50001" in sitemap_resp.text
    assert "http://testserver/marches/departement/44" in sitemap_resp.text

    robots_resp = client.get("/robots.txt")
    assert robots_resp.status_code == 200
    assert "Sitemap: http://testserver/sitemap.xml" in robots_resp.text

    # 8. Self-service free trial signup (with duplicate protection)
    trial_ok = client.post("/v1/trial", json={"email": "prospect@pme.fr"})
    assert trial_ok.status_code == 201
    trial_key = trial_ok.json()["api_key"]
    assert trial_key.startswith("mp_live_")
    assert client.get("/v1/tenders", headers={"X-API-Key": trial_key}).status_code == 200

    trial_dup = client.post("/v1/trial", json={"email": "PROSPECT@pme.fr"})
    assert trial_dup.status_code == 409

    trial_bad = client.post("/v1/trial", json={"email": "invalid-email"})
    assert trial_bad.status_code == 422

    # 9. Delete alert subscription
    del_resp = client.delete(f"/v1/alerts/{sub_id}", headers={"X-API-Key": api_key})
    assert del_resp.status_code == 200


def test_postgres_dialect_adaptation() -> None:
    assert adapt_sql("SELECT * FROM api_keys WHERE id = ? AND active = ?", "postgres") == (
        "SELECT * FROM api_keys WHERE id = %s AND active = %s"
    )
    assert adapt_sql("SELECT * FROM api_keys WHERE id = ?", "sqlite") == (
        "SELECT * FROM api_keys WHERE id = ?"
    )
    pg_ddl = "\n".join(_schema_statements("SERIAL PRIMARY KEY"))
    assert "SERIAL PRIMARY KEY" in pg_ddl
    assert "AUTOINCREMENT" not in pg_ddl
