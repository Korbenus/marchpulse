"""Automated notification delivery (Webhook POST and Resend Email)."""

from __future__ import annotations

import html
import logging

import httpx

from marchepulse.config import RESEND_API_URL, Settings
from marchepulse.models import AlertSubscription, NormalizedTender

logger = logging.getLogger(__name__)


class NotificationError(RuntimeError):
    """Raised when sending an email or webhook notification fails."""


class Notifier:
    """Delivers tender alert digests and onboarding API keys."""

    def __init__(
        self,
        settings: Settings,
        timeout: float = 10.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.settings = settings
        self.timeout = timeout
        self.transport = transport

    def send_alert_digest(
        self, sub: AlertSubscription, tenders: list[NormalizedTender]
    ) -> bool:
        """Send matched tenders to a subscription's target channel."""
        if not tenders:
            return True

        if sub.channel == "webhook":
            return self._post_webhook(sub, tenders)
        if sub.channel == "email":
            return self._send_tender_email(sub, tenders)

        logger.warning("Unsupported notification channel '%s' on sub %d", sub.channel, sub.id)
        return False

    def _post_webhook(
        self, sub: AlertSubscription, tenders: list[NormalizedTender]
    ) -> bool:
        payload = {
            "source": "MarchePulse",
            "subscription_id": sub.id,
            "subscription_name": sub.name,
            "count": len(tenders),
            "tenders": [t.model_dump() for t in tenders],
            # Discord/Slack friendly fallback text
            "text": (
                f"[MarchePulse] {len(tenders)} nouveau(x) marche(s) pour '{sub.name}':\n"
                + "\n".join(
                    f"- {t.title[:100]} ({', '.join(t.departments) or 'FR'}) -> {t.source_url}"
                    for t in tenders[:5]
                )
            ),
        }
        try:
            with httpx.Client(timeout=self.timeout, transport=self.transport) as client:
                resp = client.post(sub.target, json=payload)
                resp.raise_for_status()
            return True
        except (httpx.HTTPError, ValueError) as exc:
            logger.error("Webhook delivery failed for sub %d: %s", sub.id, exc)
            return False

    def _send_tender_email(
        self, sub: AlertSubscription, tenders: list[NormalizedTender]
    ) -> bool:
        if not self.settings.resend_api_key:
            logger.warning("MP_RESEND_API_KEY not configured; cannot email sub %d", sub.id)
            return False

        items_html = "".join(
            f"<li><strong>{html.escape(t.title)}</strong> — "
            f"{html.escape(t.buyer_name or 'Acheteur public')} "
            f"({html.escape(', '.join(t.departments) or 'FR')}) — "
            f"<a href='{html.escape(t.source_url)}'>Voir l'avis</a></li>"
            for t in tenders[:20]
        )
        body_html = (
            f"<h2>MarchePulse — Alerte : {html.escape(sub.name)}</h2>"
            f"<p>{len(tenders)} nouveau(x) marche(s) detecte(s) :</p>"
            f"<ul>{items_html}</ul>"
        )
        return self.send_email(
            to_email=sub.target,
            subject=f"[MarchePulse] {len(tenders)} nouveau(x) marche(s) — {sub.name}",
            html_body=body_html,
        )

    def send_onboarding_key_email(
        self, *, to_email: str, api_key: str, plan: str
    ) -> bool:
        """Automatically deliver a freshly provisioned API key to a paying customer."""
        if not self.settings.resend_api_key:
            logger.info(
                "Skipping onboarding email to %s (MP_RESEND_API_KEY not set)", to_email
            )
            return False

        docs_url = f"{self.settings.base_url}/docs"
        html_body = (
            "<h2>Bienvenue sur MarchePulse</h2>"
            f"<p>Votre abonnement <strong>{html.escape(plan.upper())}</strong> est actif.</p>"
            f"<p>Voici votre cle API (a conserver en lieu sur) :</p>"
            f"<pre><code>{html.escape(api_key)}</code></pre>"
            f"<p>Documentation interactive : <a href='{html.escape(docs_url)}'>{html.escape(docs_url)}</a></p>"
            "<p>Exemple d'appel : <code>curl -H \"X-API-Key: VOTRE_CLE\" "
            f"{html.escape(self.settings.base_url)}/v1/tenders</code></p>"
        )
        return self.send_email(
            to_email=to_email,
            subject=f"[MarchePulse] Votre cle API ({plan.upper()}) est prete",
            html_body=html_body,
        )

    def send_email(self, *, to_email: str, subject: str, html_body: str) -> bool:
        """Send a transactional email through Resend REST API."""
        if not self.settings.resend_api_key:
            return False
        headers = {
            "Authorization": f"Bearer {self.settings.resend_api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "from": self.settings.email_from,
            "to": [to_email],
            "subject": subject,
            "html": html_body,
        }
        try:
            with httpx.Client(timeout=self.timeout, transport=self.transport) as client:
                resp = client.post(RESEND_API_URL, headers=headers, json=payload)
                resp.raise_for_status()
            return True
        except httpx.HTTPError as exc:
            logger.error("Resend email delivery failed to %s: %s", to_email, exc)
            return False
