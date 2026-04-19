from __future__ import annotations

import argparse
import json
import logging
import re
import time
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from comp_filter import filter_comps
from ebay_client import fetch_comps
from macbid_client import fetch_lots
from models import ScoredLot
from scoring import score_lot

DEFAULT_ESTIMATED_SHIPPING = Decimal("15.00")
DEFAULT_TRANSFER_FEE = Decimal("0.00")
EBAY_CALL_DELAY = 1.5  # seconds between eBay API calls
CACHE_FILE = Path("output/ebay_cache.json")

# Categories that won't have eBay comps — skip before hitting API
SKIP_KEYWORDS = {
    "protein", "drink", "food", "beverage", "snack", "coffee",
    "tea", "juice", "water", "milk", "supplement", "vitamin",
    "grocery", "perishable", "candy", "chocolate", "gum",
}

logger = logging.getLogger(__name__)


def load_cache() -> dict[str, list]:
    if CACHE_FILE.exists():
        try:
            data = json.loads(CACHE_FILE.read_text())
            logger.info("Loaded %s cached queries from %s", len(data), CACHE_FILE)
            return data
        except Exception as exc:
            logger.warning("Failed to load cache: %s", exc)
    return {}


def save_cache(cache: dict[str, list]) -> None:
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(json.dumps(cache, cls=_ScoredLotEncoder, indent=2))
    logger.info("Saved %s cached queries to %s", len(cache), CACHE_FILE)


def is_consumable(title: str) -> bool:
    words = set(re.findall(r"[a-z]+", title.lower()))
    return bool(words.intersection(SKIP_KEYWORDS))

def run(
    location: str | None = None,
    output_file: str | None = None,
    limit: int | None = None,
) -> list[ScoredLot]:
    lots = fetch_lots(location)
    logger.info("Processing %s lots (limit: %s)", len(lots), limit or "none")

    cache = load_cache()
    scored_lots: list[ScoredLot] = []
    processed = 0
    cache_hits = 0
    skipped_consumable = 0

    for lot in lots:
        if limit and processed >= limit:
            break

        # Skip consumables
        if is_consumable(lot.title):
            skipped_consumable += 1
            continue

        try:
            query = _build_search_query(lot.title)

            # Use cache if available
            if query in cache:
                raw_comps = cache[query]
                cache_hits += 1
                logger.debug("Cache hit for query: %s", query)
                # Deserialize from cache
                from models import CompRecord
                comps = [CompRecord(**_deserialize_comp(c)) for c in raw_comps]
            else:
                time.sleep(EBAY_CALL_DELAY)
                comps = fetch_comps(query)
                # Serialize to cache
                cache[query] = [asdict(c) for c in comps]
                save_cache(cache)

            filtered_comps = filter_comps(comps, lot)
            scored = score_lot(
                lot,
                filtered_comps,
                estimated_shipping=DEFAULT_ESTIMATED_SHIPPING,
                transfer_fee=DEFAULT_TRANSFER_FEE,
            )
            scored_lots.append(scored)
            processed += 1

        except Exception as exc:
            logger.error("Failed to process lot %s: %s", lot.id, exc)
            continue

    logger.info(
        "Done. Processed: %s | Cache hits: %s | Skipped consumables: %s",
        processed, cache_hits, skipped_consumable,
    )

    scored_lots.sort(key=_sort_key)

    if output_file:
        output_path = Path(output_file)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(scored_lots, cls=_ScoredLotEncoder, indent=2)
        )

    return scored_lots


def _deserialize_comp(data: dict) -> dict:
    """Convert cached comp dict back to CompRecord-compatible types."""
    from decimal import Decimal
    result = dict(data)
    for field in ("sold_price", "shipping_cost"):
        if result.get(field) is not None:
            result[field] = Decimal(str(result[field]))
    if result.get("sold_date"):
        try:
            result["sold_date"] = datetime.fromisoformat(result["sold_date"])
        except (ValueError, TypeError):
            result["sold_date"] = None
    return result

def _build_search_query(title: str) -> str:
    cleaned = re.sub(r"[^\w\s]", " ", title)
    words = cleaned.split()
    return " ".join(words[:6])


def _sort_key(scored: ScoredLot) -> tuple[int, Decimal]:
    verdict_rank = 0 if scored.verdict == "BUY" else 1
    margin = scored.margin if scored.margin is not None else Decimal("-999999.99")
    return (verdict_rank, -margin)


class _ScoredLotEncoder(json.JSONEncoder):
    def default(self, obj):
        if is_dataclass(obj):
            return asdict(obj)
        if isinstance(obj, Decimal):
            return float(obj)
        if isinstance(obj, datetime):
            return obj.isoformat()
        return super().default(obj)


def _print_summary_table(results: list[ScoredLot]) -> None:
    if not results:
        print("No scorable lots found.")
        return

    header = f"{'Title':40} {'Bid':>10} {'Max Bid':>10} {'Verdict':>8} {'Margin':>10}"
    print(header)
    print("-" * len(header))

    for result in results:
        title = (result.lot_record.title[:37] + "...") if len(result.lot_record.title) > 40 else result.lot_record.title
        current_bid = f"${result.current_bid:.2f}"
        max_bid = f"${result.max_bid:.2f}" if result.max_bid is not None else "N/A"
        margin = f"${result.margin:.2f}" if result.margin is not None else "N/A"
        print(f"{title:40} {current_bid:>10} {max_bid:>10} {result.verdict:>8} {margin:>10}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the OLA MAC.BID scoring pipeline")
    parser.add_argument("--location", default=None, help="Optional MAC.BID location filter")
    parser.add_argument("--output", default=None, help="Optional JSON output file path")
    parser.add_argument("--limit", type=int, default=50, help="Max lots to process (default: 50)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)

    results = run(
        location=args.location,
        output_file=args.output,
        limit=args.limit,
    )
    _print_summary_table(results)


if __name__ == "__main__":
    main()
