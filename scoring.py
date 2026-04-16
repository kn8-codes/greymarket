from __future__ import annotations

from decimal import Decimal

from models import CompRecord, LotRecord, ScoredLot
from pricing import calculate_buyer_premium, calculate_max_bid, get_ebay_median


def score_lot(
    lot: LotRecord,
    comps: list[CompRecord],
    estimated_shipping: Decimal,
    transfer_fee: Decimal,
) -> ScoredLot:
    ebay_median = get_ebay_median(comps)
    buyer_premium = calculate_buyer_premium(lot.current_bid)
    max_bid = calculate_max_bid(
        ebay_median=ebay_median,
        estimated_shipping=estimated_shipping,
        transfer_fee=transfer_fee,
        buyer_premium=buyer_premium,
    )

    if max_bid is None:
        verdict = "PASS"
        margin = None
        reason = "no comps"
    elif lot.current_bid <= max_bid:
        verdict = "BUY"
        margin = max_bid - lot.current_bid
        reason = None
    else:
        verdict = "PASS"
        margin = max_bid - lot.current_bid
        reason = "current bid exceeds max bid"

    return ScoredLot(
        lot_record=lot,
        comps_used=comps,
        ebay_median=ebay_median,
        estimated_shipping=estimated_shipping,
        transfer_fee=transfer_fee,
        buyer_premium=buyer_premium,
        max_bid=max_bid,
        current_bid=lot.current_bid,
        verdict=verdict,
        margin=margin,
        reason=reason,
    )
