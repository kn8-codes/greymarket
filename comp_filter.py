from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from models import CompRecord, LotRecord

MIN_SOLD_PRICE = Decimal("1.00")
MAX_PRICE_MULTIPLIER = Decimal("10")
RECENCY_DAYS = 90
MIN_SHARED_WORDS = 2
MIN_SURVIVING_COMPS = 2

STOPWORDS = {
    "the",
    "a",
    "an",
    "in",
    "for",
    "with",
    "and",
    "or",
    "of",
}

logger = logging.getLogger(__name__)


def assess_lot_quality(lot: LotRecord) -> tuple[bool, str | None]:
    if lot.data_quality == "weak":
        return False, "weak MAC.BID record"
    if not lot.title:
        return False, "missing lot title"
    if lot.retail_value is None:
        return False, "missing retail value"
    if not lot.category:
        return False, "missing category"
    if not lot.condition:
        return False, "missing condition"
    return True, None


def filter_comps(comps: list[CompRecord], lot: LotRecord) -> list[CompRecord]:
    ok, reason = assess_lot_quality(lot)
    if not ok:
        logger.info("Excluding lot %s before comp filtering: %s", lot.id, reason)
        return []

    filtered = list(comps)
    original_count = len(filtered)

    # 1. Sold price floor
    before = len(filtered)
    filtered = [comp for comp in filtered if comp.sold_price >= MIN_SOLD_PRICE]
    logger.debug("Dropped %s comps below sold price floor", before - len(filtered))

    # 2. Sold price ceiling
    max_allowed_price = lot.current_bid * MAX_PRICE_MULTIPLIER
    before = len(filtered)
    filtered = [comp for comp in filtered if comp.sold_price <= max_allowed_price]
    logger.debug("Dropped %s comps above sold price ceiling", before - len(filtered))

    # 3. Recency
    cutoff = datetime.now(timezone.utc) - timedelta(days=RECENCY_DAYS)
    before = len(filtered)
    filtered = [
        comp
        for comp in filtered
        if comp.sold_date is not None and _normalize_datetime(comp.sold_date) >= cutoff
    ]
    logger.debug("Dropped %s stale comps older than %s days", before - len(filtered), RECENCY_DAYS)

    # 4. Keyword relevance
    lot_words = _meaningful_words(lot.title)
    before = len(filtered)
    filtered = [
        comp
        for comp in filtered
        if len(lot_words.intersection(_meaningful_words(comp.title))) >= MIN_SHARED_WORDS
    ]
    logger.debug("Dropped %s comps failing keyword relevance", before - len(filtered))

    # 5. Minimum comp count
    if len(filtered) < MIN_SURVIVING_COMPS:
        logger.debug(
            "Only %s comps survived from %s original comps, returning empty list",
            len(filtered),
            original_count,
        )
        logger.info("0 comps survived filtering for lot %s", lot.id)
        return []

    logger.info("%s comps survived filtering for lot %s", len(filtered), lot.id)
    return filtered


def _meaningful_words(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", text.lower())
    return {word for word in words if word not in STOPWORDS}


def _normalize_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
