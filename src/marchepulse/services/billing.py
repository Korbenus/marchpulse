"""Automated billing webhook verification and API key lifecycle management."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from typing import Any

from marchepulse.config import PLAN_QUOTAS, Settings
from marchepulse.db.database import Database
from marchepulse.models import BillingWebhookResult
from marchepulse.services.auth import issue_api_key
from marchepulse.services.notifier import Notifier

logger = logging.getLogger(__name__)


class BillingSignatureError(PermissionError):
    """Raised when a LemonSqueezy or Stripe webhook signature is invalid."""


def infer_plan_from_label(label: str | None) -> str:
    """Map a product/variant/price name to one of MarchePulse's plans."""
    text = (label or "").lower()
    if "enterprise" in text:
        return "enterprise"
    if "pro" in text:
        return "pro"
    if "free" in text:
        return "free"
    return "starter"


def verify_lemonsqueezy_signature(
    raw_body: bytes, signature_header: str | None, secret: str
) -> None:
    """Verify LemonSqueezy `X-Signature` HMAC-SHA256 hex digest."""
    if not secret:
        raise BillingSignatureError("LemonSqueezy webhook secret is not configured.")
    if not signature_header:
        raise BillingSignatureError("Missing X-Signature header.")

    expected = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature_header.strip()):
        raise BillingSignatureError("Invalid LemonSqueezy webhook signature.")


def verify_stripe_signature(
    raw_body: bytes,
    signature_header: str | None,
    secret: str,
    *,
    tolerance_seconds: int = 300,
    now_ts: int | None = None,
) -> None:
    """Verify Stripe `Stripe-Signature` header (`t=...,v1=...`) without external SDK."""
    if not secret:
        raise BillingSignatureError("Stripe webhook secret is not configured.")
    if not signature_header:
        raise BillingSignatureError("Missing Stripe-Signature header.")

    parts: dict[str, list[str]] = {}
    for item in signature_header.split(","):
        if "=" not in item:
            continue
        k, v = item.split("=", 1)
        parts.setdefault(k.strip(), []).append(v.strip())

    timestamps = parts.get("t", [])
    v1_sigs = parts.get("v1", [])
    if not timestamps or not v1_sigs:
        raise BillingSignatureError("Malformed Stripe-Signature header.")

    try:
        ts = int(timestamps[0])
    except ValueError as exc:
        raise BillingSignatureError("Invalid Stripe timestamp.") from exc

    current_ts = int(time.time()) if now_ts is None else now_ts
    if abs(current_ts - ts) > tolerance_seconds:
        raise BillingSignatureError("Stripe webhook timestamp outside tolerance window.")

    signed_payload = f"{ts}.".encode("utf-8") + raw_body
    expected = hmac.new(secret.encode("utf-8"), signed_payload, hashlib.sha256).hexdigest()
    if not any(hmac.compare_digest(expected, sig) for sig in v1_sigs):
        raise BillingSignatureError("Invalid Stripe v1 signature.")


def process_lemonsqueezy_webhook(
    *,
    db: Database,
    settings: Settings,
    notifier: Notifier,
    raw_body: bytes,
    signature_header: str | None,
) -> BillingWebhookResult:
    """Verify and apply a LemonSqueezy webhook event idempotently."""
    verify_lemonsqueezy_signature(
        raw_body, signature_header, settings.lemonsqueezy_webhook_secret
    )
    payload: dict[str, Any] = json.loads(raw_body.decode("utf-8"))
    meta = payload.get("meta") or {}
    data = payload.get("data") or {}
    attrs = data.get("attributes") or {}

    event_name = str(meta.get("event_name") or "unknown")
    sub_id = str(data.get("id") or attrs.get("first_subscription_item", {}).get("subscription_id") or "")
    event_id = str(meta.get("webhook_id") or f"ls:{event_name}:{sub_id}:{attrs.get('updated_at', '')}")

    if not db.register_billing_event(event_id, "lemonsqueezy", event_name):
        return BillingWebhookResult(
            status="ignored_duplicate",
            provider="lemonsqueezy",
            event_id=event_id,
            action="none",
        )

    email = str(attrs.get("user_email") or attrs.get("customer_email") or "").strip().lower()
    plan = infer_plan_from_label(attrs.get("variant_name") or attrs.get("product_name"))
    quota = PLAN_QUOTAS[plan]

    if event_name in {"subscription_created", "order_created"} and email and sub_id:
        existing = db.find_key_by_subscription(sub_id)
        if existing is None:
            raw_key, info = issue_api_key(
                db,
                customer_email=email,
                plan=plan,
                provider="lemonsqueezy",
                external_customer_id=str(attrs.get("customer_id") or ""),
                external_subscription_id=sub_id,
            )
            notifier.send_onboarding_key_email(to_email=email, api_key=raw_key, plan=plan)
            return BillingWebhookResult(
                status="processed",
                provider="lemonsqueezy",
                event_id=event_id,
                action="key_issued",
                details={"key_id": info.id, "plan": plan},
            )

    if event_name in {"subscription_updated", "subscription_resumed"} and sub_id:
        status_str = str(attrs.get("status") or "active").lower()
        is_active = status_str in {"active", "on_trial"}
        updated = db.update_key_status_by_subscription(
            external_subscription_id=sub_id,
            active=is_active,
            plan=plan,
            monthly_quota=quota,
        )
        return BillingWebhookResult(
            status="processed",
            provider="lemonsqueezy",
            event_id=event_id,
            action="subscription_updated",
            details={"updated": updated, "active": is_active, "plan": plan},
        )

    if event_name in {"subscription_cancelled", "subscription_expired", "subscription_paused"} and sub_id:
        updated = db.update_key_status_by_subscription(
            external_subscription_id=sub_id,
            active=False,
        )
        return BillingWebhookResult(
            status="processed",
            provider="lemonsqueezy",
            event_id=event_id,
            action="key_deactivated",
            details={"updated": updated},
        )

    return BillingWebhookResult(
        status="processed",
        provider="lemonsqueezy",
        event_id=event_id,
        action="noop",
    )


def process_stripe_webhook(
    *,
    db: Database,
    settings: Settings,
    notifier: Notifier,
    raw_body: bytes,
    signature_header: str | None,
) -> BillingWebhookResult:
    """Verify and apply a Stripe webhook event idempotently."""
    verify_stripe_signature(raw_body, signature_header, settings.stripe_webhook_secret)
    event: dict[str, Any] = json.loads(raw_body.decode("utf-8"))
    event_id = str(event.get("id") or "")
    event_type = str(event.get("type") or "")
    obj = (event.get("data") or {}).get("object") or {}

    if not db.register_billing_event(event_id, "stripe", event_type):
        return BillingWebhookResult(
            status="ignored_duplicate",
            provider="stripe",
            event_id=event_id,
            action="none",
        )

    metadata = obj.get("metadata") or {}
    plan = infer_plan_from_label(metadata.get("plan"))

    if event_type == "checkout.session.completed":
        email = str(
            obj.get("customer_details", {}).get("email")
            or obj.get("customer_email")
            or ""
        ).strip().lower()
        sub_id = str(obj.get("subscription") or obj.get("id") or "")
        if email and sub_id:
            existing = db.find_key_by_subscription(sub_id)
            if existing is None:
                raw_key, info = issue_api_key(
                    db,
                    customer_email=email,
                    plan=plan,
                    provider="stripe",
                    external_customer_id=str(obj.get("customer") or ""),
                    external_subscription_id=sub_id,
                )
                notifier.send_onboarding_key_email(to_email=email, api_key=raw_key, plan=plan)
                return BillingWebhookResult(
                    status="processed",
                    provider="stripe",
                    event_id=event_id,
                    action="key_issued",
                    details={"key_id": info.id, "plan": plan},
                )

    if event_type == "customer.subscription.deleted":
        sub_id = str(obj.get("id") or "")
        updated = db.update_key_status_by_subscription(
            external_subscription_id=sub_id,
            active=False,
        )
        return BillingWebhookResult(
            status="processed",
            provider="stripe",
            event_id=event_id,
            action="key_deactivated",
            details={"updated": updated},
        )

    if event_type == "customer.subscription.updated":
        sub_id = str(obj.get("id") or "")
        is_active = str(obj.get("status") or "").lower() in {"active", "trialing"}
        updated = db.update_key_status_by_subscription(
            external_subscription_id=sub_id,
            active=is_active,
            plan=plan,
            monthly_quota=PLAN_QUOTAS[plan],
        )
        return BillingWebhookResult(
            status="processed",
            provider="stripe",
            event_id=event_id,
            action="subscription_updated",
            details={"updated": updated, "active": is_active},
        )

    return BillingWebhookResult(
        status="processed",
        provider="stripe",
        event_id=event_id,
        action="noop",
    )
