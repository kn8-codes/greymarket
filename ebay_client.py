from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from models import CompRecord

FINDING_API_URL = "https://svcs.ebay.com/services/search/FindingService/v1"
FINDING_API_VERSION = "1.0.0"
MAX_ATTEMPTS = 3
BACKOFF_SECONDS = 2
MAX_RESULTS_PER_PAGE = 20
COMPLETED_ITEMS_OPERATION = "findCompletedItems"

logger = logging.getLogger(__name__)


def fetch_comps(query: str, max_results: int = 10) -> list[CompRecord]:
    """
    Fetch recently SOLD eBay listings matching query.
    Uses the Finding API findCompletedItems operation.
    Only returns sold items (not unsold completed listings).
    """
    app_id = os.getenv("EBAY_APP_ID")
    if not app_id:
        raise RuntimeError("EBAY_APP_ID environment variable is required")

    params = {
        "OPERATION-NAME": COMPLETED_ITEMS_OPERATION,
        "SERVICE-VERSION": FINDING_API_VERSION,
        "SECURITY-APPNAME": app_id,
        "RESPONSE-DATA-FORMAT": "JSON",
        "REST-PAYLOAD": "",
        "keywords": query,
        "itemFilter(0).name": "SoldItemsOnly",
        "itemFilter(0).value": "true",
        "itemFilter(1).name": "ListingType",
        "itemFilter(1).value": "AuctionWithBIN",
        "itemFilter(2).name": "ListingType",
        "itemFilter(2).value": "FixedPrice",
        "itemFilter(3).name": "ListingType",
        "itemFilter(3).value": "Auction",
        "sortOrder": "EndTimeSoonest",
        "paginationInput.entriesPerPage": str(min(max_results, MAX_RESULTS_PER_PAGE)),
        "paginationInput.pageNumber": "1",
        "outputSelector(0)": "SellingStatus",
        "outputSelector(1)": "PictureURLSuperSize",
    }

    last_error: Exception | None = None

    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            with httpx.Client(timeout=20.0) as client:
                response = client.get(FINDING_API_URL, params=params)

            if response.status_code == 200:
                payload = response.json()
                return _parse_finding_response(payload, query, max_results)

            if response.status_code == 429 and attempt < MAX_ATTEMPTS:
                logger.warning("eBay rate limit hit, backing off %ss", BACKOFF_SECONDS)
                time.sleep(BACKOFF_SECONDS)
                continue

            raise RuntimeError(
                f"eBay Finding API returned {response.status_code}: {response.text[:300]}"
            )

        except Exception as exc:
            last_error = exc
            if attempt < MAX_ATTEMPTS:
                logger.warning("Attempt %s failed: %s — retrying", attempt, exc)
                time.sleep(BACKOFF_SECONDS)
                continue
            break

    raise RuntimeError(
        f"Failed to fetch eBay sold comps after {MAX_ATTEMPTS} attempts: {last_error}"
    )


def _parse_finding_response(
    payload: dict[str, Any], query: str, max_results: int
) -> list[CompRecord]:
    try:
        root = payload.get("findCompletedItemsResponse", [{}])[0]
        ack = root.get("ack", [""])[0]

        if ack != "Success" and ack != "Warning":
            error_msg = (
                root.get("errorMessage", [{}])[0]
                .get("error", [{}])[0]
                .get("message", ["Unknown error"])[0]
            )
            raise RuntimeError(f"eBay Finding API error: {error_msg}")

        search_result = root.get("searchResult", [{}])[0]
        items = search_result.get("item", [])

        if not items:
            logger.warning("No sold comps returned for query: %s", query)
            return []

        comps = []
        for item in items[:max_results]:
            comp = _normalize_finding_item(item)
            if comp is not None:
                comps.append(comp)

        logger.info("Fetched %s sold comps for query: %s", len(comps), query)
        return comps

    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(f"Failed to parse eBay Finding API response: {exc}") from exc


def _normalize_finding_item(raw: dict[str, Any]) -> CompRecord | None:
    try:
        item_id = _first(raw.get("itemId")) or ""
        title = _first(raw.get("title")) or ""

        selling_status = (raw.get("sellingStatus") or [{}])[0]
        current_price = (selling_status.get("currentPrice") or [{}])[0]
        sold_price = _to_decimal(current_price.get("__value__"))

        if sold_price is None or sold_price <= Decimal("0"):
            logger.debug("Skipping item %s — no valid sold price", item_id)
            return None

        shipping_info = (raw.get("shippingInfo") or [{}])[0]
        shipping_cost_raw = (shipping_info.get("shippingServiceCost") or [{}])[0]
        shipping_cost = _to_decimal(shipping_cost_raw.get("__value__"))

        end_time_str = _first(raw.get("listingInfo", [{}])[0].get("endTime"))
        sold_date = _to_datetime(end_time_str)

        condition_raw = (raw.get("condition") or [{}])[0]
        condition = _first(condition_raw.get("conditionDisplayName"))

        url = _first(raw.get("viewItemURL")) or ""

        return CompRecord(
            item_id=item_id,
            title=title,
            sold_price=sold_price,
            sold_date=sold_date,
            shipping_cost=shipping_cost,
            condition=condition,
            url=url,
        )

    except Exception as exc:
        logger.warning("Failed to normalize Finding API item: %s", exc)
        return None


def _first(value: Any) -> Any:
    """Unwrap single-element lists common in eBay Finding API JSON responses."""
    if isinstance(value, list):
        return value[0] if value else None
    return value


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
