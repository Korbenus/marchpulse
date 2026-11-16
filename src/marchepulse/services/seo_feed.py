"""Static RSS 2.0 and JSON feed generator for organic SEO discovery."""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path

from marchepulse.models import NormalizedTender


def build_rss_xml(tenders: list[NormalizedTender], base_url: str) -> str:
    """Serialize recent open tenders into a valid RSS 2.0 XML document."""
    rss = ET.Element("rss", version="2.0")
    channel = ET.SubElement(rss, "channel")
    ET.SubElement(channel, "title").text = "MarchePulse — Appels d'offres publics recents (France)"
    ET.SubElement(channel, "link").text = base_url
    ET.SubElement(channel, "description").text = (
        "Flux automatise des derniers marches publics BOAMP enrichis."
    )
    ET.SubElement(channel, "language").text = "fr-fr"
    ET.SubElement(channel, "lastBuildDate").text = format_datetime(datetime.now(timezone.utc))

    for tender in tenders:
        item = ET.SubElement(channel, "item")
        deps = ", ".join(tender.departments) or "FR"
        ET.SubElement(item, "title").text = f"[{deps}] {tender.title[:160]}"
        ET.SubElement(item, "link").text = tender.source_url
        ET.SubElement(item, "guid", isPermaLink="false").text = f"boamp:{tender.idweb}"
        buyer = tender.buyer_name or "Acheteur public"
        desc_parts = [f"Acheteur : {buyer}", f"Departement(s) : {deps}"]
        if tender.deadline:
            desc_parts.append(f"Date limite : {tender.deadline[:10]}")
        if tender.descriptors:
            desc_parts.append(f"Secteurs : {', '.join(tender.descriptors[:5])}")
        ET.SubElement(item, "description").text = " | ".join(desc_parts)

    return ET.tostring(rss, encoding="unicode", xml_declaration=True)


def write_seo_feeds(
    tenders: list[NormalizedTender], feed_dir: Path, base_url: str
) -> int:
    """Write `feed.xml` and `latest.json` into `feed_dir` and return item count."""
    feed_dir.mkdir(parents=True, exist_ok=True)
    rss_path = feed_dir / "feed.xml"
    json_path = feed_dir / "latest.json"

    rss_path.write_text(build_rss_xml(tenders, base_url), encoding="utf-8")
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "count": len(tenders),
        "items": [
            {
                "idweb": t.idweb,
                "title": t.title,
                "buyer_name": t.buyer_name,
                "departments": t.departments,
                "publication_date": t.publication_date,
                "deadline": t.deadline,
                "market_types": t.market_types,
                "source_url": t.source_url,
            }
            for t in tenders
        ],
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return len(tenders)
