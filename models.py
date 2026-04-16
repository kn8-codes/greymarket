from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal


@dataclass
class LotRecord:
    """Raw or lightly normalized MAC.BID lot record."""

    id: str  # MAC.BID lot identifier
    title: str  # Lot title/name as listed
    category: str | None  # Category or department label, if available
    retail_value: Decimal | None  # Listed retail/MSRP value, if present
    current_bid: Decimal  # Current auction bid amount
    location: str | None  # Pickup/warehouse location
    lot_url: str  # Direct URL to the lot page
    auction_close_time: datetime | None  # Auction close timestamp
    condition: str | None  # Condition text from MAC.BID listing
    is_transferrable: bool | None  # Whether MAC.BID marks the lot as transferrable
    lot_fee_override: Decimal | None  # Auction-level lot fee override, if present
    buyers_premium_override: Decimal | None  # Auction-level buyer premium override, if present
    image_count: int  # Count of known listing images
    data_quality: Literal["full", "partial", "weak"]  # Honesty grade for downstream scoring


@dataclass
class CompRecord:
    """eBay sold comparable record."""

    item_id: str  # eBay item/listing identifier
    title: str  # Sold listing title
    sold_price: Decimal  # Final sold price excluding shipping unless otherwise noted
    sold_date: datetime | None  # Date/time the comp sold
    shipping_cost: Decimal | None  # Shipping charged on the sold listing
    condition: str | None  # Condition text from eBay listing
    url: str  # Direct URL to the sold comp


@dataclass
class ScoredLot:
    """Final scored lot output used for bid decisions."""

    lot_record: LotRecord  # Source MAC.BID lot being evaluated
    comps_used: list[CompRecord]  # Filtered eBay comps used in pricing
    ebay_median: Decimal | None  # Median sold value from usable comps
    estimated_shipping: Decimal  # Estimated outbound shipping cost for resale
    transfer_fee: Decimal  # Transfer fee applied to this lot
    buyer_premium: Decimal  # Buyer premium amount applied from MAC.BID side
    max_bid: Decimal | None  # Calculated maximum bid using locked formula
    current_bid: Decimal  # Current live bid copied for convenience
    verdict: Literal["BUY", "PASS"]  # Final decision flag
    margin: Decimal | None  # Estimated remaining margin between max bid and current bid
    reason: str | None  # Explanation for PASS, or None when verdict is BUY
