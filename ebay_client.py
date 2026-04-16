from __future__ import annotations

import logging
import os
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from models import CompRecord

BASE_URL = "https://svcs.ebay.com/services/search/FindingService/v1"
MAX_ATTEMPTS = 3
BACKOFF_SECONDS = 2
XML_NS = {"ns": "http://www.ebay.com/marketplace/search/v1/services"}

logger = logging.getLogger(__name__)


def fetch_comps(query: str, max_results: int = 10) -> list[CompRecord]:
    app_id = os.getenv("EBAY_APP_ID")
    if not app_id:
        raise RuntimeError("EBAY_APP_ID environment variable is required")

    params = {
        "OPERATION-NAME": "findCompletedItems",
        "SERVICE-VERSION": "1.13.0",
        "SECURITY-APPNAME": app_id,
        "RESPONSE-DATA-FORMAT": "XML",
        "REST-PAYLOAD": "true",
        "keywords": query,
        "paginationInput.entriesPerPage": str(max_results),
        "itemFilter(0).name": "SoldItemsOnly",
        "itemFilter(0).value": "true",
    }

    last_error: Exception | None = None

    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            with httpx.Client(timeout=20.0) as client:
                response = client.get(BASE_URL, params=params)

            if response.status_code == 200:
                items = _extract_items_from_xml(response.text)
                comps = [_normalize_comp(item) for item in items][:max_results]

                if not comps:
                    logger.warning("No eBay sold comps returned for query: %s", query)
                    return []

                logger.info("Fetched %s eBay comps for query: %s", len(comps), query)
                return comps

            if response.status_code == 429 and attempt < MAX_ATTEMPTS:
                time.sleep(BACKOFF_SECONDS)
                continue

            raise RuntimeError(
                f"eBay Finding API returned {response.status_code}: {response.text[:300]}"
            )

        except Exception as exc:
            last_error = exc
            if attempt < MAX_ATTEMPTS:
                time.sleep(BACKOFF_SECONDS)
                continue
            break

    raise RuntimeError(f"Failed to fetch eBay comps after {MAX_ATTEMPTS} attempts: {last_error}")


def _extract_items_from_xml(xml_text: str) -> list[dict[str, Any]]:
    root = ET.fromstring(xml_text)
    ack = root.find(".//ns:ack", XML_NS)
    if ack is not None and ack.text not in {"Success", "Warning"}:
        error_message = root.find(".//ns:errorMessage/ns:error/ns:message", XML_NS)
        detail = error_message.text if error_message is not None else "unknown eBay API error"
        raise RuntimeError(f"eBay Finding API error: {detail}")

    items: list[dict[str, Any]] = []
    for item in root.findall(".//ns:searchResult/ns:item", XML_NS):
        items.append(
            {
                "itemId": _xml_text(item, "ns:itemId"),
                "title": _xml_text(item, "ns:title"),
                "listingInfo.endTime": _xml_text(item, "ns:listingInfo/ns:endTime"),
                "sellingStatus.currentPrice": _xml_attr(
                    item, "ns:sellingStatus/ns:currentPrice", "currencyId"
                ),
                "sellingStatus.currentPrice.value": _xml_text(item, "ns:sellingStatus/ns:currentPrice"),
                "shippingInfo.shippingServiceCost": _xml_text(item, "ns:shippingInfo/ns:shippingServiceCost"),
                "condition.conditionDisplayName": _xml_text(item, "ns:condition/ns:conditionDisplayName"),
                "viewItemURL": _xml_text(item, "ns:viewItemURL"),
            }
        )
    return items


def _normalize_comp(raw: dict) -> CompRecord:
    return CompRecord(
        item_id=_as_str(raw.get("itemId") or ""),
        title=_as_str(raw.get("title") or ""),
        sold_price=_to_decimal(raw.get("sellingStatus.currentPrice.value")) or Decimal("0.00"),
        sold_date=_to_datetime(raw.get("listingInfo.endTime")),
        shipping_cost=_to_decimal(raw.get("shippingInfo.shippingServiceCost")),
        condition=_nullable_str(raw.get("condition.conditionDisplayName")),
        url=_as_str(raw.get("viewItemURL") or ""),
    )


def _xml_text(element: ET.Element, path: str) -> str | None:
    node = element.find(path, XML_NS)
    if node is None or node.text is None:
        return None
    return node.text.strip()


def _xml_attr(element: ET.Element, path: str, attr_name: str) -> str | None:
    node = element.find(path, XML_NS)
    if node is None:
        return None
    value = node.attrib.get(attr_name)
    return value.strip() if value else None


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
