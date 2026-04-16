from __future__ import annotations

import logging
import time
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from models import LotRecord

BASE_URL = "https://api.macdiscount.com"
LOTS_ENDPOINT = "/auctions"
MAX_ATTEMPTS = 3
BACKOFF_SECONDS = 2

logger = logging.getLogger(__name__)


def fetch_lots(location: str | None = None) -> list[LotRecord]:
    params: dict[str, Any] = {
        "pg": 1,
        "per_pg": 100,
    }

    last_error: Exception | None = None

    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            with httpx.Client(timeout=20.0) as client:
                response = client.get(f"{BASE_URL}{LOTS_ENDPOINT}", params=params)

            if response.status_code == 200:
                payload = response.json()

                raw_lots: list[dict[str, Any]] = []
                if isinstance(payload, dict):
                    auctions = payload.get("data") or []
                    if not isinstance(auctions, list):
                        raise RuntimeError("Unexpected MAC.BID auctions response shape")

                    for auction in auctions:
                        if not isinstance(auction, dict):
                            continue
                        items = auction.get("items") or []
                        if isinstance(items, list):
                            for item in items:
                                if not isinstance(item, dict):
                                    continue
                                enriched = dict(item)
                                enriched.setdefault("location_name", auction.get("location_name"))
                                enriched.setdefault("lot_fee_override", auction.get("lot_fee_override"))
                                enriched.setdefault("buyers_premium_override", auction.get("buyers_premium_override"))
                                raw_lots.append(enriched)

                if not isinstance(raw_lots, list):
                    raise RuntimeError("Unexpected MAC.BID API response shape")

                lots = [_normalize_lot(raw) for raw in raw_lots]

                if location:
                    lots = [
                        lot for lot in lots
                        if lot.location and lot.location.lower() == location.lower()
                    ]

                logger.info("Fetched %s MAC.BID lots", len(lots))
                return lots

            if response.status_code == 429 and attempt < MAX_ATTEMPTS:
                time.sleep(BACKOFF_SECONDS)
                continue

            raise RuntimeError(
                f"MAC.BID API returned {response.status_code}: {response.text[:300]}"
            )

        except Exception as exc:
            last_error = exc
            if attempt < MAX_ATTEMPTS:
                time.sleep(BACKOFF_SECONDS)
                continue
            break

    raise RuntimeError(f"Failed to fetch MAC.BID lots after {MAX_ATTEMPTS} attempts: {last_error}")


def _normalize_lot(raw: dict) -> LotRecord:
    condition = _nullable_str(raw.get("condition") or raw.get("conditionText") or raw.get("condition_name"))
    category = _nullable_str(raw.get("category") or raw.get("department"))
    location = _nullable_str(
        raw.get("location")
        or raw.get("warehouse")
        or raw.get("pickupLocation")
        or raw.get("city")
        or raw.get("warehouse_location")
        or raw.get("location_name")
    )
    retail_value = _to_decimal(
        raw.get("retail_value")
        or raw.get("retailValue")
        or raw.get("msrp")
        or raw.get("retail")
        or raw.get("retail_price")
    )
    lot_url = _as_str(
        raw.get("lot_url")
        or raw.get("lotUrl")
        or raw.get("url")
        or raw.get("listingUrl")
        or raw.get("listing_url")
        or ""
    )
    image_count = _image_count(raw)

    return LotRecord(
        id=_as_str(
            raw.get("id")
            or raw.get("listingId")
            or raw.get("lotId")
            or raw.get("uuid")
            or ""
        ),
        title=_as_str(raw.get("title") or raw.get("name") or raw.get("product_name") or ""),
        category=category,
        retail_value=retail_value,
        current_bid=_to_decimal(
            raw.get("current_bid")
            or raw.get("currentBid")
            or raw.get("bid")
            or raw.get("price")
            or raw.get("winning_bid_amount")
            or 0
        )
        or Decimal("0.00"),
        location=location,
        lot_url=lot_url,
        auction_close_time=_to_datetime(
            raw.get("auction_close_time")
            or raw.get("auctionCloseTime")
            or raw.get("closeTime")
            or raw.get("endTime")
            or raw.get("endsAt")
            or raw.get("expected_close_date")
            or raw.get("closed_date")
        ),
        condition=condition,
        is_transferrable=_to_bool(raw.get("is_transferrable")),
        lot_fee_override=_to_decimal(raw.get("lot_fee_override")),
        buyers_premium_override=_to_decimal(raw.get("buyers_premium_override")),
        image_count=image_count,
        data_quality=_data_quality(
            title=_as_str(raw.get("title") or raw.get("name") or raw.get("product_name") or ""),
            retail_value=retail_value,
            category=category,
            condition=condition,
            location=location,
            lot_url=lot_url,
            image_count=image_count,
        ),
    )


def _to_decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _to_datetime(value: Any) -> datetime | None:
    if value is None or value == "":
        return None

    if isinstance(value, datetime):
        return value

    try:
        text = str(value).strip()
        if text.endswith("Z"):
            text = text.replace("Z", "+00:00")
        return datetime.fromisoformat(text)
    except (ValueError, TypeError):
        return None


def _as_str(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _nullable_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _to_bool(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    try:
        return bool(int(value))
    except (ValueError, TypeError):
        text = str(value).strip().lower()
        if text in {"true", "yes", "y"}:
            return True
        if text in {"false", "no", "n"}:
            return False
    return None


def _image_count(raw: dict[str, Any]) -> int:
    if isinstance(raw.get("images"), list):
        return len([img for img in raw["images"] if img])
    if isinstance(raw.get("image_urls"), list):
        return len([img for img in raw["image_urls"] if img])
    if raw.get("image_url"):
        return 1
    return 0


def _data_quality(
    *,
    title: str,
    retail_value: Decimal | None,
    category: str | None,
    condition: str | None,
    location: str | None,
    lot_url: str,
    image_count: int,
) -> str:
    strong_signals = [
        bool(title),
        retail_value is not None,
        bool(category),
        bool(condition),
        bool(location),
        bool(lot_url),
        image_count > 0,
    ]
    score = sum(strong_signals)
    if score >= 6:
        return "full"
    if score >= 3:
        return "partial"
    return "weak"
