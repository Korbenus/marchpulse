"""Autonomous ingestion, buyer enrichment, alert delivery, and SEO feed pipeline."""

from __future__ import annotations

import argparse
import logging

from marchepulse.clients.boamp import BoampClient, BoampClientError
from marchepulse.clients.entreprises import EntreprisesClient, EntreprisesClientError
from marchepulse.config import Settings, configure_logging, load_settings
from marchepulse.db.database import Database
from marchepulse.models import NormalizedTender, PipelineStats
from marchepulse.services.matcher import tender_matches_subscription
from marchepulse.services.normalizer import normalize_boamp_record
from marchepulse.services.notifier import Notifier
from marchepulse.services.seo_feed import write_seo_feeds

logger = logging.getLogger(__name__)


def run_autonomous_pipeline(
    *,
    db: Database,
    settings: Settings,
    boamp_client: BoampClient | None = None,
    entreprises_client: EntreprisesClient | None = None,
    notifier: Notifier | None = None,
    dry_run: bool = False,
) -> PipelineStats:
    """Execute one full autonomous cycle: ingest -> enrich -> alert -> SEO feed."""
    stats = PipelineStats()
    boamp = boamp_client or BoampClient()
    entreprises = entreprises_client or EntreprisesClient()
    mail_webhook = notifier or Notifier(settings)

    # 1. Fetch & normalize latest tenders from BOAMP
    normalized: list[NormalizedTender] = []
    try:
        raw_records, _ = boamp.fetch_recent_tenders(limit=settings.boamp_fetch_limit)
        stats.fetched = len(raw_records)
        for rec in raw_records:
            tender = normalize_boamp_record(rec)
            if tender.idweb:
                normalized.append(tender)
        stats.stored = db.upsert_tenders(normalized)
    except BoampClientError as exc:
        logger.error("Pipeline BOAMP fetch failed: %s", exc)
        stats.errors.append(str(exc))

    # 2. Enrich newly seen buyers via API Recherche Entreprises (cached & rate-limited)
    if settings.enrich_max_per_run > 0:
        buyer_names = db.list_unenriched_buyers(limit=settings.enrich_max_per_run)
        for name in buyer_names:
            try:
                buyer = entreprises.lookup_buyer(name)
                if buyer is not None:
                    db.upsert_buyer(buyer)
                    stats.buyers_enriched += 1
            except EntreprisesClientError as exc:
                logger.warning("Buyer enrichment failed for '%s': %s", name, exc)
                stats.errors.append(f"buyer:{name}: {exc}")

    # 3. Evaluate active alert subscriptions & deliver new matches
    recent_tenders, _ = db.query_tenders(limit=max(50, settings.boamp_fetch_limit), offset=0)
    subscriptions = db.list_alert_subscriptions()
    stats.alerts_evaluated = len(subscriptions)

    for sub in subscriptions:
        pending = [
            tender
            for tender in recent_tenders
            if tender_matches_subscription(tender, sub)
            and not db.has_delivered(sub.id, tender.idweb)
        ]
        if not pending:
            continue

        if dry_run:
            logger.info("[DRY-RUN] Sub %d (%s) would send %d tenders", sub.id, sub.name, len(pending))
            continue

        ok = mail_webhook.send_alert_digest(sub, pending)
        idwebs = [t.idweb for t in pending]
        if ok:
            db.record_deliveries(sub.id, idwebs, "sent")
            stats.notifications_sent += len(idwebs)
        else:
            db.record_deliveries(sub.id, idwebs, "failed")
            stats.notifications_failed += len(idwebs)

    # 4. Regenerate public RSS & JSON SEO feed
    open_tenders, _ = db.query_tenders(only_open=True, limit=30, offset=0)
    stats.feed_items = write_seo_feeds(open_tenders, settings.feed_dir, settings.base_url)

    logger.info("Pipeline completed: %s", stats.model_dump_json())
    return stats


def main() -> None:
    """CLI entrypoint for scheduled CRON runs."""
    parser = argparse.ArgumentParser(description="Run MarchePulse autonomous pipeline")
    parser.add_argument("--dry-run", action="store_true", help="Do not send external notifications")
    args = parser.parse_args()

    settings = load_settings()
    configure_logging(settings.log_level)
    db = Database(settings.db_path, database_url=settings.database_url)
    stats = run_autonomous_pipeline(db=db, settings=settings, dry_run=args.dry_run)
    if stats.errors:
        logger.warning("Pipeline finished with %d non-fatal error(s).", len(stats.errors))


if __name__ == "__main__":
    main()
