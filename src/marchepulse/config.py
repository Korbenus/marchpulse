"""Configuration and structured logging setup for MarchePulse."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from dotenv import load_dotenv

LOG_FORMAT: Final[str] = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"

BOAMP_BASE_URL: Final[str] = "https://www.boamp.fr/api/explore/v2.1"
ENTREPRISES_BASE_URL: Final[str] = "https://recherche-entreprises.api.gouv.fr"
RESEND_API_URL: Final[str] = "https://api.resend.com/emails"

PLAN_QUOTAS: Final[dict[str, int]] = {
    "free": 100,
    "starter": 2_500,
    "pro": 25_000,
    "enterprise": 250_000,
    "rapidapi": 50_000,
}


@dataclass(frozen=True, slots=True)
class Settings:
    """Immutable application settings loaded from environment variables."""

    env: str
    log_level: str
    base_url: str
    db_path: Path
    feed_dir: Path
    admin_secret: str
    rapidapi_proxy_secret: str
    lemonsqueezy_webhook_secret: str
    lemonsqueezy_checkout_url: str
    stripe_webhook_secret: str
    stripe_checkout_url: str
    resend_api_key: str
    email_from: str
    boamp_fetch_limit: int
    enrich_max_per_run: int
    database_url: str = ""
    rapidapi_listing_url: str = ""
    direct_billing_enabled: bool = False


def configure_logging(level_name: str = "INFO") -> None:
    """Configure root logging with the standard timestamped format."""
    level = getattr(logging, level_name.upper(), logging.INFO)
    logging.basicConfig(level=level, format=LOG_FORMAT, force=True)


def load_settings(env_file: str | Path | None = None) -> Settings:
    """Load application settings from `.env` (if present) and OS environment."""
    if env_file is not None:
        load_dotenv(dotenv_path=env_file, override=False)
    else:
        load_dotenv(override=False)

    return Settings(
        env=os.getenv("MP_ENV", "dev").strip(),
        log_level=os.getenv("MP_LOG_LEVEL", "INFO").strip(),
        base_url=os.getenv("MP_BASE_URL", "http://127.0.0.1:8000").rstrip("/"),
        db_path=Path(os.getenv("MP_DB_PATH", "data/marchepulse.db")),
        feed_dir=Path(os.getenv("MP_FEED_DIR", "data/feed")),
        admin_secret=os.getenv("MP_ADMIN_SECRET", "").strip(),
        rapidapi_proxy_secret=os.getenv("MP_RAPIDAPI_PROXY_SECRET", "").strip(),
        lemonsqueezy_webhook_secret=os.getenv("MP_LEMONSQUEEZY_WEBHOOK_SECRET", "").strip(),
        lemonsqueezy_checkout_url=os.getenv("MP_LEMONSQUEEZY_CHECKOUT_URL", "").strip(),
        stripe_webhook_secret=os.getenv("MP_STRIPE_WEBHOOK_SECRET", "").strip(),
        stripe_checkout_url=os.getenv("MP_STRIPE_CHECKOUT_URL", "").strip(),
        resend_api_key=os.getenv("MP_RESEND_API_KEY", "").strip(),
        email_from=os.getenv("MP_EMAIL_FROM", "MarchePulse <alerts@example.com>").strip(),
        boamp_fetch_limit=max(1, min(100, int(os.getenv("MP_BOAMP_FETCH_LIMIT", "50")))),
        enrich_max_per_run=max(0, min(100, int(os.getenv("MP_ENRICH_MAX_PER_RUN", "15")))),
        database_url=os.getenv("MP_DATABASE_URL", "").strip(),
        rapidapi_listing_url=os.getenv("MP_RAPIDAPI_LISTING_URL", "").strip(),
        direct_billing_enabled=os.getenv("MP_DIRECT_BILLING_ENABLED", "false").strip().lower()
        in {"1", "true", "yes"},
    )
