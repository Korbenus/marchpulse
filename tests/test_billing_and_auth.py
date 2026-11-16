"""Tests for API key authentication, quotas, RapidAPI, LemonSqueezy and Stripe webhooks."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from pathlib import Path

import httpx
import pytest

from marchepulse.config import Settings
from marchepulse.db.database import Database
from marchepulse.services.auth import AuthError, authenticate_caller, issue_api_key
from marchepulse.services.billing import (
    BillingSignatureError,
    process_lemonsqueezy_webhook,
    process_stripe_webhook,
)
from marchepulse.services.notifier import Notifier


def _make_settings(tmp_path: Path) -> Settings:
    return Settings(
        env="test",
        log_level="INFO",
        base_url="https://api.example.com",
        db_path=tmp_path / "test.db",
        feed_dir=tmp_path / "feed",
        admin_secret="admin-secret-123",
        rapidapi_proxy_secret="rapid-secret-xyz",
        lemonsqueezy_webhook_secret="ls-secret-abc",
        lemonsqueezy_checkout_url="https://pay.example.com/ls",
        stripe_webhook_secret="whsec_test_123",
        stripe_checkout_url="https://pay.example.com/stripe",
        resend_api_key="re_test_key",
        email_from="MarchePulse <alerts@example.com>",
        boamp_fetch_limit=20,
        enrich_max_per_run=5,
    )


def test_api_key_quota_and_rapidapi_auth(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    db = Database(settings.db_path)

    raw_key, _ = issue_api_key(db, customer_email="client@acme.fr", plan="free")
    # Force quota to 2 for test
    with db.connect() as conn:
        conn.execute("UPDATE api_keys SET monthly_quota = 2")

    info1 = authenticate_caller(db=db, settings=settings, x_api_key=raw_key)
    assert info1.requests_used == 1
    info2 = authenticate_caller(db=db, settings=settings, authorization=f"Bearer {raw_key}")
    assert info2.requests_used == 2

    with pytest.raises(AuthError) as exc_info:
        authenticate_caller(db=db, settings=settings, x_api_key=raw_key)
    assert exc_info.value.status_code == 429

    rapid_info = authenticate_caller(
        db=db,
        settings=settings,
        x_rapidapi_proxy_secret="rapid-secret-xyz",
        x_rapidapi_user="dev_user_42",
    )
    assert rapid_info.provider == "rapidapi"
    assert rapid_info.customer_email == "dev_user_42@rapidapi.local"


def test_lemonsqueezy_webhook_provisioning_idempotency_and_cancel(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    db = Database(settings.db_path)
    sent_emails: list[dict[str, object]] = []

    def resend_handler(request: httpx.Request) -> httpx.Response:
        sent_emails.append(json.loads(request.content.decode("utf-8")))
        return httpx.Response(200, json={"id": "email_1"})

    notifier = Notifier(settings, transport=httpx.MockTransport(resend_handler))

    created_payload = json.dumps(
        {
            "meta": {"event_name": "subscription_created", "webhook_id": "wh_ls_1"},
            "data": {
                "id": "sub_ls_100",
                "attributes": {
                    "user_email": "buyer@startup.fr",
                    "customer_id": 99,
                    "variant_name": "MarchePulse Pro",
                    "status": "active",
                },
            },
        }
    ).encode("utf-8")
    sig = hmac.new(
        settings.lemonsqueezy_webhook_secret.encode("utf-8"),
        created_payload,
        hashlib.sha256,
    ).hexdigest()

    res = process_lemonsqueezy_webhook(
        db=db,
        settings=settings,
        notifier=notifier,
        raw_body=created_payload,
        signature_header=sig,
    )
    assert res.action == "key_issued"
    assert res.details["plan"] == "pro"
    assert len(sent_emails) == 1

    # Duplicate event is ignored idempotently
    dup = process_lemonsqueezy_webhook(
        db=db,
        settings=settings,
        notifier=notifier,
        raw_body=created_payload,
        signature_header=sig,
    )
    assert dup.status == "ignored_duplicate"

    # Cancellation deactivates key
    cancel_payload = json.dumps(
        {
            "meta": {"event_name": "subscription_cancelled", "webhook_id": "wh_ls_2"},
            "data": {"id": "sub_ls_100", "attributes": {"status": "cancelled"}},
        }
    ).encode("utf-8")
    cancel_sig = hmac.new(
        settings.lemonsqueezy_webhook_secret.encode("utf-8"),
        cancel_payload,
        hashlib.sha256,
    ).hexdigest()
    cancel_res = process_lemonsqueezy_webhook(
        db=db,
        settings=settings,
        notifier=notifier,
        raw_body=cancel_payload,
        signature_header=cancel_sig,
    )
    assert cancel_res.action == "key_deactivated"
    key_info = db.find_key_by_subscription("sub_ls_100")
    assert key_info is not None and key_info.active is False


def test_stripe_webhook_signature_and_provisioning(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    db = Database(settings.db_path)
    notifier = Notifier(
        settings, transport=httpx.MockTransport(lambda _req: httpx.Response(200, json={"id": "e1"}))
    )

    body = json.dumps(
        {
            "id": "evt_stripe_1",
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "id": "cs_1",
                    "subscription": "sub_stripe_9",
                    "customer": "cus_9",
                    "customer_details": {"email": "cto@pme.fr"},
                    "metadata": {"plan": "enterprise"},
                }
            },
        }
    ).encode("utf-8")
    ts = int(time.time())
    signed = f"{ts}.".encode("utf-8") + body
    v1 = hmac.new(
        settings.stripe_webhook_secret.encode("utf-8"),
        signed,
        hashlib.sha256,
    ).hexdigest()

    res = process_stripe_webhook(
        db=db,
        settings=settings,
        notifier=notifier,
        raw_body=body,
        signature_header=f"t={ts},v1={v1}",
    )
    assert res.action == "key_issued"
    assert res.details["plan"] == "enterprise"

    with pytest.raises(BillingSignatureError):
        process_stripe_webhook(
            db=db,
            settings=settings,
            notifier=notifier,
            raw_body=body,
            signature_header=f"t={ts},v1=deadbeef",
        )
