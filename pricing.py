from __future__ import annotations

from decimal import Decimal
from statistics import median

from models import CompRecord

LOT_FEE = Decimal("3.00")
PROFIT_FLOOR = Decimal("25.00")
BUYER_PREMIUM_RATE = Decimal("0.15")
EBAY_FEE_RATE = Decimal("0.13")
CENT = Decimal("0.01")


def calculate_buyer_premium(current_bid: Decimal) -> Decimal:
    """Return the MAC.BID buyer premium, 15% of the current bid."""
    return (current_bid * BUYER_PREMIUM_RATE).quantize(CENT)


def calculate_ebay_fees(ebay_median: Decimal) -> Decimal:
    """Return estimated eBay fees, 13% of the median sold price."""
    return (ebay_median * EBAY_FEE_RATE).quantize(CENT)


def get_ebay_median(comps: list[CompRecord]) -> Decimal | None:
    """Return the median sold price across comps, or None when no comps exist."""
    if not comps:
        return None

    sold_prices = [comp.sold_price for comp in comps]
    return Decimal(str(median(sold_prices))).quantize(CENT)


def calculate_max_bid(
    ebay_median: Decimal | None,
    estimated_shipping: Decimal,
    transfer_fee: Decimal,
    buyer_premium: Decimal,
) -> Decimal | None:
    """Apply the locked OLA formula to produce a maximum bid."""
    if ebay_median is None:
        return None

    ebay_fees = calculate_ebay_fees(ebay_median)
    max_bid = (
        ebay_median
        - buyer_premium
        - LOT_FEE
        - ebay_fees
        - estimated_shipping
        - transfer_fee
        - PROFIT_FLOOR
    )
    return max_bid.quantize(CENT)
