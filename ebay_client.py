from __future__ import annotations

import base64
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from models import CompRecord

BROWSE_API_URL = "https://api.ebay.com/buy/browse/v1/item_summary/search"
IDENTITY_API_URL = "https://api.ebay.com/identity/v1/oauth2/token"
MAX_ATTEMPTS = 3
BACKOFF_SECONDS = 2
SOURCE_NOTE = "eBay active listings — not sold data, verify manually"

logger = logging.getLogger(__name__)

_TOKEN_CACHE: dict[str, Any] = {"token": None, "expires_at": None}


def fetch_comps(query: str, max_results: int = 10) -> list[CompRecord]:
    app_id = os.getenv("EBAY_APP_ID")
    cert_id = os.getenv("EBAY_CERT_ID") or os.getenv("EBAY_CLIENT_SECRET")
    if not app_id:
        raise RuntimeError("EBAY_APP_ID environment variable is required")
    if not cert_id:
        raise RuntimeError("EBAY_CERT_ID environment variable is required for Browse API auth")

    token = _get_app_token(app_id, cert_id)
    params = {
        "q": query,
        "filter": "conditionIds:{3000|4000}",
        "limit": str(min(max_results, 20)),
    }

    last_error: Exception | None = None

    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            with httpx.Client(timeout=20.0) as client:
                response = client.get(
                    BROWSE_API_URL,
                    params=params,
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Accept": "application/json",
                        "X-EBAY-C-MARKETPLACE-ID": "EBAY_US",
                    },
                )

            if response.status_code == 200:
                payload = response.json()
                items = payload.get("itemSummaries") or []
                comps = [_normalize_comp(item) for item in items][:max_results]

                if not comps:
                    logger.warning("No eBay active comps returned for query: %s", query)
                    return []

                logger.info("Fetched %s eBay active comps for query: %s", len(comps), query)
                return comps

            if response.status_code == 429 and attempt < MAX_ATTEMPTS:
                time.sleep(BACKOFF_SECONDS)
                continue

            raise RuntimeError(
                f"eBay Browse API returned {response.status_code}: {response.text[:300]}"
            )

        except Exception as exc:
            last_error = exc
            if attempt < MAX_ATTEMPTS:
                time.sleep(BACKOFF_SECONDS)
                continue
            break

    raise RuntimeError(f"Failed to fetch eBay comps after {MAX_ATTEMPTS} attempts: {last_error}")


def _get_app_token(app_id: str, cert_id: str) -> str:
    cached_token = _TOKEN_CACHE.get("token")
    expires_at = _TOKEN_CACHE.get("expires_at")
    if cached_token and isinstance(expires_at, datetime) and expires_at > datetime.now(timezone.utc):
        return cached_token

    credentials = base64.b64encode(f"{app_id}:{cert_id}".encode("utf-8")).decode("ascii")
    with httpx.Client(timeout=20.0) as client:
        response = client.post(
            IDENTITY_API_URL,
            headers={
                "Authorization": f"Basic {credentials}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={
                "grant_type": "client_credentials",
                "scope": "https://api.ebay.com/oauth/api_scope",
            },
        )

    if response.status_code != 200:
        raise RuntimeError(f"Failed to fetch eBay app token: {response.status_code} {response.text[:300]}")

    payload = response.json()
    token = payload.get("access_token")
    expires_in = int(payload.get("expires_in", 7200))
    if not token:
        raise RuntimeError("eBay token response missing access_token")

    _TOKEN_CACHE["token"] = token
    _TOKEN_CACHE["expires_at"] = datetime.now(timezone.utc) + timedelta(seconds=max(expires_in - 60, 60))
    return token


def _normalize_comp(raw: dict[str, Any]) -> CompRecord:
    item_id = _as_str(raw.get("itemId") or raw.get("legacyItemId") or "")
    price = _to_decimal(((raw.get("price") or {}).get("value"))) or Decimal("0.00")
    shipping_cost = _extract_shipping_cost(raw.get("shippingOptions"))
    condition = _nullable_str(raw.get("condition") or raw.get("conditionId"))
    item_web_url = _as_str(raw.get("itemWebUrl") or "")
    if item_web_url:
        separator = "&" if "?" in item_web_url else "?"
        item_web_url = f"{item_web_url}{separator}source_note={SOURCE_NOTE}"

    return CompRecord(
        item_id=item_id,
        title=_as_str(raw.get("title") or ""),
        sold_price=price,
        sold_date=_to_datetime(raw.get("itemCreationDate") or raw.get("listingDate")),
        shipping_cost=shipping_cost,
        condition=condition,
        url=item_web_url,
    )


def _extract_shipping_cost(shipping_options: Any) -> Decimal | None:
    if not isinstance(shipping_options, list):
        return None
    for option in shipping_options:
        if not isinstance(option, dict):
            continue
        shipping_cost = _to_decimal(((option.get("shippingCost") or {}).get("value")))
        if shipping_cost is not None:
            return shipping_cost
    return None


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
