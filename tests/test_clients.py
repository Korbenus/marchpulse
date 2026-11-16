"""Unit tests for BOAMP and Recherche Entreprises HTTP clients."""

from __future__ import annotations

import httpx
import pytest

from marchepulse.clients.boamp import BoampClient, BoampClientError
from marchepulse.clients.entreprises import EntreprisesClient


def test_boamp_client_fetch_recent_tenders() -> None:
    captured_params: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured_params.update(dict(request.url.params))
        return httpx.Response(
            200,
            json={
                "total_count": 1,
                "results": [{"idweb": "26-1", "objet": "Audit SI", "nomacheteur": "Region Bretagne"}],
            },
        )

    client = BoampClient(transport=httpx.MockTransport(handler))
    records, total = client.fetch_recent_tenders(limit=10, department="35", query="d'audit")
    assert total == 1
    assert len(records) == 1
    assert records[0]["idweb"] == "26-1"
    assert "d''audit" in captured_params["where"]
    assert captured_params["order_by"] == "dateparution DESC"


def test_boamp_client_http_error_raises() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="server error")

    client = BoampClient(transport=httpx.MockTransport(handler))
    with pytest.raises(BoampClientError):
        client.fetch_recent_tenders()


def test_entreprises_client_lookup_and_rate_limit_429() -> None:
    def ok_handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "siren": "213502388",
                        "nom_complet": "COMMUNE DE RENNES",
                        "etat_administratif": "A",
                        "nature_juridique": "7210",
                        "activite_principale": "84.11Z",
                        "categorie_entreprise": "GE",
                        "tranche_effectif_salarie": "51",
                        "siege": {
                            "libelle_commune": "RENNES",
                            "code_postal": "35000",
                            "departement": "35",
                        },
                    }
                ]
            },
        )

    client = EntreprisesClient(
        min_interval_seconds=0.0,
        transport=httpx.MockTransport(ok_handler),
    )
    buyer = client.lookup_buyer("Ville de Rennes")
    assert buyer is not None
    assert buyer.siren == "213502388"
    assert buyer.commune == "RENNES"

    rate_limited_client = EntreprisesClient(
        min_interval_seconds=0.0,
        transport=httpx.MockTransport(lambda _req: httpx.Response(429, text="too fast")),
    )
    assert rate_limited_client.lookup_buyer("Ville de Rennes") is None
