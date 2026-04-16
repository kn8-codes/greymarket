from __future__ import annotations

import argparse
import json
import logging
import re
from dataclasses import asdict, is_dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from comp_filter import filter_comps
from ebay_client import fetch_comps
from macbid_client import fetch_lots
from models import ScoredLot
from scoring import score_lot

DEFAULT_ESTIMATED_SHIPPING = Decimal("15.00")
DEFAULT_TRANSFER_FEE = Decimal("0.00")

logger = logging.getLogger(__name__)


def run(location: str | None = None, output_file: str | None = None) -> list[ScoredLot]:
    lots = fetch_lots(location)
    scored_lots: list[ScoredLot] = []

    for lot in lots:
        try:
            query = _build_search_query(lot.title)
            comps = fetch_comps(query)
            filtered_comps = filter_comps(comps, lot)
            scored = score_lot(
                lot,
                filtered_comps,
                estimated_shipping=DEFAULT_ESTIMATED_SHIPPING,
                transfer_fee=DEFAULT_TRANSFER_FEE,
            )
            scored_lots.append(scored)
        except Exception as exc:
            logger.error("Failed to process lot %s: %s", lot.id, exc)
            continue

    scored_lots.sort(key=_sort_key)

    if output_file:
        output_path = Path(output_file)
        output_path.write_text(json.dumps(scored_lots, cls=_ScoredLotEncoder, indent=2))

    return scored_lots


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
    header = f"{'Title':40} {'Current Bid':>12} {'Max Bid':>12} {'Verdict':>8} {'Margin':>12}"
    print(header)
    print("-" * len(header))

    for result in results:
        title = (result.lot_record.title[:37] + "...") if len(result.lot_record.title) > 40 else result.lot_record.title
        current_bid = f"{result.current_bid:.2f}"
        max_bid = f"{result.max_bid:.2f}" if result.max_bid is not None else "None"
        margin = f"{result.margin:.2f}" if result.margin is not None else "None"
        print(f"{title:40} {current_bid:>12} {max_bid:>12} {result.verdict:>8} {margin:>12}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the OLA MAC.BID scoring pipeline")
    parser.add_argument("--location", default=None, help="Optional MAC.BID location filter")
    parser.add_argument("--output", default=None, help="Optional JSON output file path")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)

    results = run(location=args.location, output_file=args.output)
    _print_summary_table(results)


if __name__ == "__main__":
    main()
