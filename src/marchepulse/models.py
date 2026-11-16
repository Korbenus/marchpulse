"""Domain and API Pydantic models for MarchePulse."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class BuyerEnrichment(BaseModel):
    """Normalized company or public buyer profile from API Recherche Entreprises."""

    query_name: str
    siren: str | None = None
    nom_complet: str | None = None
    etat_administratif: str | None = None
    nature_juridique: str | None = None
    activite_principale: str | None = None
    categorie_entreprise: str | None = None
    tranche_effectif_salarie: str | None = None
    commune: str | None = None
    code_postal: str | None = None
    departement: str | None = None


class NormalizedTender(BaseModel):
    """Cleaned, enriched public procurement tender record."""

    idweb: str
    title: str
    buyer_name: str
    departments: list[str] = Field(default_factory=list)
    publication_date: str | None = None
    deadline: str | None = None
    days_remaining: int | None = None
    is_open: bool = True
    market_types: list[str] = Field(default_factory=list)
    nature: str | None = None
    procedure: str | None = None
    descriptor_codes: list[str] = Field(default_factory=list)
    descriptors: list[str] = Field(default_factory=list)
    family: str | None = None
    source_url: str
    quality_score: int = 0
    buyer: BuyerEnrichment | None = None


class TenderListResponse(BaseModel):
    """Paginated API response for tender queries."""

    total: int
    limit: int
    offset: int
    items: list[NormalizedTender]


class AlertSubscriptionCreate(BaseModel):
    """Payload to create a new automated tender alert subscription."""

    name: str = Field(min_length=2, max_length=80)
    keywords: list[str] = Field(default_factory=list, max_length=20)
    departments: list[str] = Field(default_factory=list, max_length=30)
    market_types: list[str] = Field(default_factory=list, max_length=10)
    only_open: bool = True
    channel: Literal["webhook", "email"] = "webhook"
    target: str = Field(min_length=3, max_length=500)


class AlertSubscription(AlertSubscriptionCreate):
    """Persisted alert subscription."""

    id: int
    owner_key_id: int
    active: bool = True
    created_at: datetime


class ApiKeyCreateRequest(BaseModel):
    """Admin request to issue an API key manually."""

    customer_email: str = Field(min_length=3, max_length=200)
    plan: Literal["free", "starter", "pro", "enterprise"] = "starter"
    provider: Literal["manual", "lemonsqueezy", "stripe", "rapidapi", "trial"] = "manual"


class TrialSignupRequest(BaseModel):
    """Self-service free trial signup payload."""

    email: str = Field(min_length=5, max_length=200)


class ApiKeyPublicInfo(BaseModel):
    """Non-secret metadata for an issued API key."""

    id: int
    customer_email: str
    plan: str
    provider: str
    monthly_quota: int
    requests_used: int
    active: bool


class ApiKeyIssuedResponse(ApiKeyPublicInfo):
    """Returned once when a new API key is created."""

    api_key: str


class PipelineStats(BaseModel):
    """Summary metrics emitted by one autonomous pipeline execution."""

    fetched: int = 0
    stored: int = 0
    buyers_enriched: int = 0
    alerts_evaluated: int = 0
    notifications_sent: int = 0
    notifications_failed: int = 0
    feed_items: int = 0
    errors: list[str] = Field(default_factory=list)


class BillingWebhookResult(BaseModel):
    """Outcome of processing a LemonSqueezy or Stripe billing webhook."""

    status: str
    provider: str
    event_id: str
    action: str
    details: dict[str, Any] = Field(default_factory=dict)
