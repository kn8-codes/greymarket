from __future__ import annotations

import json
import logging
from dataclasses import asdict, is_dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from comp_filter import assess_lot_quality, filter_comps
from ebay_client import fetch_comps
from macbid_client import fetch_lots
from pricing import PROFIT_FLOOR
from runner import _build_search_query
from scoring import score_lot

DEFAULT_ESTIMATED_SHIPPING = Decimal("15.00")
DEFAULT_TRANSFER_FEE = Decimal("0.00")
PROBE_LIMIT = 30
SHORTLIST_LIMIT = 20

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class _Encoder(json.JSONEncoder):
    def default(self, obj):
        if is_dataclass(obj):
            return asdict(obj)
        if isinstance(obj, Decimal):
            return float(obj)
        if isinstance(obj, datetime):
            return obj.isoformat()
        return super().default(obj)


def run_probe() -> tuple[list[dict], Path]:
    lots = fetch_lots()[:PROBE_LIMIT]
    today = datetime.now().date().isoformat()
    output_dir = Path(__file__).parent / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / f"probe-{today}.md"

    reviewed: list[dict] = []
    shortlist: list[dict] = []

    for lot in lots:
        lot_ok, lot_reason = assess_lot_quality(lot)
        record = {
            "lot_id": lot.id,
            "title": lot.title,
            "data_quality": lot.data_quality,
            "quality_reason": lot_reason,
            "current_bid": lot.current_bid,
            "retail_value": lot.retail_value,
            "location": lot.location,
            "category": lot.category,
            "condition": lot.condition,
        }

        if not lot_ok:
            reviewed.append(record)
            continue

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
            record.update(
                {
                    "query": query,
                    "comp_count": len(filtered_comps),
                    "verdict": scored.verdict,
                    "margin": scored.margin,
                    "reason": scored.reason,
                    "max_bid": scored.max_bid,
                    "buyer_premium": scored.buyer_premium,
                    "net": scored.margin,
                }
            )
            if lot.data_quality != "weak" and scored.margin is not None and scored.margin >= PROFIT_FLOOR:
                shortlist.append(record)
        except Exception as exc:
            record["error"] = str(exc)
        reviewed.append(record)

    shortlist.sort(key=lambda r: (r.get("net") or Decimal("-999999")), reverse=True)
    shortlist = shortlist[:SHORTLIST_LIMIT]
    out_path.write_text(_render_markdown(reviewed, shortlist), encoding="utf-8")
    return shortlist, out_path



def _render_markdown(reviewed: list[dict], shortlist: list[dict]) -> str:
    lines = []
    lines.append(f"# GREYMARKET Probe {datetime.now().date().isoformat()}")
    lines.append("")
    lines.append(f"Reviewed lots: {len(reviewed)}")
    lines.append(f"Shortlist count: {len(shortlist)}")
    lines.append(f"Filter: data_quality != weak and net >= ${PROFIT_FLOOR}")
    lines.append("")
    lines.append("## Shortlist")
    lines.append("")
    if not shortlist:
        lines.append("No qualifying lots found in this probe pass.")
    else:
        for idx, item in enumerate(shortlist, start=1):
            lines.append(f"### {idx}. {item['title']}")
            lines.append(f"- Lot ID: {item['lot_id']}")
            lines.append(f"- Data quality: {item['data_quality']}")
            lines.append(f"- Current bid: ${item['current_bid']}")
            lines.append(f"- Max bid: ${item.get('max_bid')}")
            lines.append(f"- Net: ${item.get('net')}")
            lines.append(f"- Category: {item.get('category')}")
            lines.append(f"- Condition: {item.get('condition')}")
            lines.append(f"- Location: {item.get('location')}")
            lines.append(f"- eBay comp count: {item.get('comp_count')}")
            lines.append(f"- Query: `{item.get('query')}`")
            lines.append("")
    lines.append("## Raw reviewed summary")
    lines.append("")
    lines.append("```json")
    lines.append(json.dumps(reviewed, cls=_Encoder, indent=2))
    lines.append("```")
    lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    shortlist, out_path = run_probe()
    print(f"Wrote {len(shortlist)} shortlisted lots to {out_path}")
