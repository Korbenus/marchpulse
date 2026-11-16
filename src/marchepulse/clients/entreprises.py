"""Rate-limited client for DINUM API Recherche d'entreprises."""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from marchepulse.config import ENTREPRISES_BASE_URL
from marchepulse.models import BuyerEnrichment

logger = logging.getLogger(__name__)


class EntreprisesClientError(RuntimeError):
    """Raised when the Recherche Entreprises API returns an unrecoverable error."""


class EntreprisesClient:
    """Client for `https://recherche-entreprises.api.gouv.fr` respecting <= 5 req/s."""

    def __init__(
        self,
        base_url: str = ENTREPRISES_BASE_URL,
        timeout: float = 10.0,
        min_interval_seconds: float = 0.2,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.min_interval_seconds = min_interval_seconds
        self.transport = transport
        self._last_request_ts: float = 0.0

    def _respect_rate_limit(self) -> None:
        if self.min_interval_seconds <= 0:
            return
        elapsed = time.monotonic() - self._last_request_ts
        if elapsed < self.min_interval_seconds:
            time.sleep(self.min_interval_seconds - elapsed)
        self._last_request_ts = time.monotonic()

    def lookup_buyer(self, name: str) -> BuyerEnrichment | None:
        """Resolve a public buyer or company name to an official SIREN profile."""
        clean_name = name.strip()
        if len(clean_name) < 2:
            return None

        self._respect_rate_limit()
        url = f"{self.base_url}/search"
        params = {"q": clean_name, "per_page": 1}

        try:
            with httpx.Client(timeout=self.timeout, transport=self.transport) as client:
                response = client.get(url, params=params)
                if response.status_code == 429:
                    logger.warning("Rate-limited by Recherche Entreprises API for '%s'", clean_name)
                    return None
                response.raise_for_status()
                payload: dict[str, Any] = response.json()
        except httpx.HTTPStatusError as exc:
            raise EntreprisesClientError(
                f"Recherche Entreprises HTTP {exc.response.status_code}: {exc.response.text[:200]}"
            ) from exc
        except (httpx.RequestError, ValueError) as exc:
            raise EntreprisesClientError(f"Recherche Entreprises error: {exc}") from exc

        results = payload.get("results") or []
        if not results:
            return BuyerEnrichment(query_name=clean_name)

        top = results[0]
        siege = top.get("siege") or {}
        return BuyerEnrichment(
            query_name=clean_name,
            siren=top.get("siren"),
            nom_complet=top.get("nom_complet"),
            etat_administratif=top.get("etat_administratif"),
            nature_juridique=top.get("nature_juridique"),
            activite_principale=top.get("activite_principale"),
            categorie_entreprise=top.get("categorie_entreprise"),
            tranche_effectif_salarie=top.get("tranche_effectif_salarie"),
            commune=siege.get("libelle_commune"),
            code_postal=siege.get("code_postal"),
            departement=siege.get("departement"),
        )
