"""Matching logic between normalized tenders and customer alert criteria."""

from __future__ import annotations

from marchepulse.models import AlertSubscription, NormalizedTender


def tender_matches_subscription(
    tender: NormalizedTender, sub: AlertSubscription
) -> bool:
    """Return True if `tender` satisfies all filters of `sub`."""
    if sub.only_open and not tender.is_open:
        return False

    if sub.departments:
        sub_deps = {d.strip().upper() for d in sub.departments}
        tender_deps = {d.strip().upper() for d in tender.departments}
        if not (sub_deps & tender_deps):
            return False

    if sub.market_types:
        sub_types = {m.strip().upper() for m in sub.market_types}
        tender_types = {m.strip().upper() for m in tender.market_types}
        if not (sub_types & tender_types):
            return False

    if sub.keywords:
        haystack = " ".join(
            [
                tender.title,
                tender.buyer_name,
                " ".join(tender.descriptors),
            ]
        ).lower()
        if not any(kw.strip().lower() in haystack for kw in sub.keywords if kw.strip()):
            return False

    return True
