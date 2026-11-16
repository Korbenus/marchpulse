"""Resilient client for the official BOAMP Opendatasoft Explore v2.1 API."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from marchepulse.config import BOAMP_BASE_URL

logger = logging.getLogger(__name__)


class BoampClientError(RuntimeError):
    """Raised when the BOAMP API fails or returns an unexpected response."""


def _escape_odsql(value: str) -> str:
    """Escape single quotes inside ODSQL string literals."""
    return value.replace("'", "''").strip()


class BoampClient:
    """HTTP client targeting `https://www.boamp.fr/api/explore/v2.1`."""

    def __init__(
        self,
        base_url: str = BOAMP_BASE_URL,
        timeout: float = 15.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.transport = transport

    def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.base_url}/{path.lstrip('/')}"
        try:
            with httpx.Client(timeout=self.timeout, transport=self.transport) as client:
                response = client.get(url, params=params)
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPStatusError as exc:
            raise BoampClientError(
                f"BOAMP HTTP {exc.response.status_code} on {url}: {exc.response.text[:200]}"
            ) from exc
        except (httpx.RequestError, ValueError) as exc:
            raise BoampClientError(f"BOAMP request failure on {url}: {exc}") from exc

        if not isinstance(data, dict):
            raise BoampClientError("Unexpected non-object JSON payload from BOAMP API.")
        return data

    def fetch_recent_tenders(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        department: str | None = None,
        query: str | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        """Fetch recent BOAMP records ordered by `dateparution DESC`."""
        where_parts: list[str] = []
        if department:
            dep = _escape_odsql(department)
            where_parts.append(f"code_departement = '{dep}'")
        if query:
            term = _escape_odsql(query)
            where_parts.append(f"(objet LIKE '%{term}%' OR nomacheteur LIKE '%{term}%')")

        params: dict[str, Any] = {
            "limit": max(1, min(100, limit)),
            "offset": max(0, offset),
            "order_by": "dateparution DESC",
        }
        if where_parts:
            params["where"] = " AND ".join(where_parts)

        payload = self._get("catalog/datasets/boamp/records", params)
        results = payload.get("results", [])
        total = int(payload.get("total_count", len(results)))
        logger.info("Fetched %d BOAMP records (total available=%d)", len(results), total)
        return results, total

    def get_tender_by_idweb(self, idweb: str) -> dict[str, Any] | None:
        """Retrieve a single BOAMP tender record by `idweb`."""
        safe_id = _escape_odsql(idweb)
        payload = self._get(
            "catalog/datasets/boamp/records",
            {"where": f"idweb = '{safe_id}'", "limit": 1},
        )
        results = payload.get("results", [])
        return results[0] if results else None
