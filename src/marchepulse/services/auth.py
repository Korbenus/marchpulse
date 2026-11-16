"""API key generation, hashing, RapidAPI proxy verification, and quota control."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from typing import Final

from marchepulse.config import PLAN_QUOTAS, Settings
from marchepulse.db.database import Database
from marchepulse.models import ApiKeyPublicInfo

KEY_PREFIX: Final[str] = "mp_live_"


class AuthError(PermissionError):
    """Raised when API authentication fails or monthly quota is exceeded."""

    def __init__(self, message: str, status_code: int = 401) -> None:
        super().__init__(message)
        self.status_code = status_code


def hash_api_key(raw_key: str) -> str:
    """Return a deterministic SHA-256 hex digest for storing API keys safely."""
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def generate_raw_api_key() -> str:
    """Generate a high-entropy customer API key."""
    return f"{KEY_PREFIX}{secrets.token_urlsafe(24)}"


def issue_api_key(
    db: Database,
    *,
    customer_email: str,
    plan: str = "starter",
    provider: str = "manual",
    external_customer_id: str | None = None,
    external_subscription_id: str | None = None,
) -> tuple[str, ApiKeyPublicInfo]:
    """Generate, hash, and store a new API key in the database."""
    normalized_plan = plan.lower().strip()
    quota = PLAN_QUOTAS.get(normalized_plan, PLAN_QUOTAS["starter"])
    raw_key = generate_raw_api_key()
    info = db.create_api_key(
        key_hash=hash_api_key(raw_key),
        customer_email=customer_email.strip().lower(),
        plan=normalized_plan,
        provider=provider,
        monthly_quota=quota,
        external_customer_id=external_customer_id,
        external_subscription_id=external_subscription_id,
    )
    return raw_key, info


def authenticate_caller(
    *,
    db: Database,
    settings: Settings,
    x_api_key: str | None = None,
    authorization: str | None = None,
    x_rapidapi_proxy_secret: str | None = None,
    x_rapidapi_user: str | None = None,
) -> ApiKeyPublicInfo:
    """Authenticate an API caller via RapidAPI proxy secret or direct API key."""
    if settings.rapidapi_proxy_secret and x_rapidapi_proxy_secret:
        if hmac.compare_digest(x_rapidapi_proxy_secret, settings.rapidapi_proxy_secret):
            rapid_user = (x_rapidapi_user or "rapidapi-anonymous").strip().lower()
            sub_id = f"rapidapi:{rapid_user}"
            existing = db.find_key_by_subscription(sub_id)
            if existing is None:
                raw_key, _ = issue_api_key(
                    db,
                    customer_email=f"{rapid_user}@rapidapi.local",
                    plan="rapidapi",
                    provider="rapidapi",
                    external_customer_id=rapid_user,
                    external_subscription_id=sub_id,
                )
                key_hash = hash_api_key(raw_key)
                info, err = db.consume_api_key_quota(key_hash)
                if info is not None:
                    return info
                raise AuthError(f"RapidAPI key provisioning failed: {err}", status_code=401)
            # Consume quota via stable deterministic key hash for rapidapi user?
            # Since existing key_hash is unknown in plaintext, track/allow RapidAPI gateway directly:
            if not existing.active:
                raise AuthError("RapidAPI subscription is inactive.", status_code=403)
            return existing
        raise AuthError("Invalid RapidAPI proxy secret.", status_code=401)

    token = (x_api_key or "").strip()
    if not token and authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()

    if not token:
        raise AuthError(
            "Missing API key. Provide 'X-API-Key' or 'Authorization: Bearer <key>'.",
            status_code=401,
        )

    info, error_code = db.consume_api_key_quota(hash_api_key(token))
    if info is not None:
        return info

    if error_code == "quota_exceeded":
        raise AuthError("Monthly API quota exceeded for this plan.", status_code=429)
    if error_code == "inactive_key":
        raise AuthError("API key is inactive or subscription cancelled.", status_code=403)
    raise AuthError("Invalid API key.", status_code=401)
