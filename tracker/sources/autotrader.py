"""AutoTrader listings via Apify actor — deal ratings, VIN, and 42 fields per listing."""

import logging
from typing import Any

import requests
from tenacity import retry, stop_after_attempt, wait_exponential

from tracker.config import APIFY_API_TOKEN, SEARCH_ZIP, SEARCH_RADIUS_MILES, YEAR_MIN, YEAR_MAX

logger = logging.getLogger(__name__)

# parseforge~autotrader-scraper — same publisher as carfax scraper, same input conventions
APIFY_RUN_URL = (
    "https://api.apify.com/v2/acts/eybk9HyaMCbhEUjof/run-sync-get-dataset-items"
)

_DEAL_LABEL_MAP = {
    "great deal": "Great Deal",
    "good deal": "Good Deal",
    "fair deal": "Fair Deal",
    "high price": "High Price",
    "overpriced": "Overpriced",
}


@retry(stop=stop_after_attempt(2), wait=wait_exponential(multiplier=2, min=5, max=30))
def _run_actor(model_query: str) -> list[dict]:
    params = {
        "token": APIFY_API_TOKEN,
        "timeout": 180,
        "memory": 1024,
    }
    payload = {
        "make": "Jeep",
        "model": model_query,
        "zip": SEARCH_ZIP,
        "maxItems": 100,
    }
    resp = requests.post(APIFY_RUN_URL, params=params, json=payload, timeout=240)
    if not resp.ok:
        logger.error("AutoTrader Apify HTTP %s: %s", resp.status_code, resp.text[:400])
    resp.raise_for_status()
    data = resp.json()
    if isinstance(data, list):
        return data
    return data.get("data") or data.get("items") or []


def _parse_deal_label(raw: Any) -> str | None:
    if not raw:
        return None
    if isinstance(raw, (int, float)):
        mapping = {5: "Great Deal", 4: "Good Deal", 3: "Fair Deal", 2: "High Price", 1: "Overpriced"}
        return mapping.get(int(raw))
    return _DEAL_LABEL_MAP.get(str(raw).lower().strip())


def _normalize(item: dict) -> dict | None:
    vin = (item.get("vin") or item.get("VIN") or "").strip()
    if not vin:
        return None

    price_raw = item.get("price") or item.get("listingPrice") or item.get("askingPrice") or 0
    try:
        price = int(str(price_raw).replace(",", "").replace("$", "").replace(" ", ""))
    except (TypeError, ValueError):
        price = None

    mileage_raw = item.get("mileage") or item.get("miles") or 0
    try:
        mileage = int(str(mileage_raw).replace(",", "").split()[0])
    except (TypeError, ValueError):
        mileage = None

    deal_label_raw = (
        item.get("dealRating")
        or item.get("deal_rating")
        or item.get("dealBadge")
        or item.get("deal_badge")
        or item.get("dealType")
    )
    deal_label = _parse_deal_label(deal_label_raw)

    dealer = item.get("dealer") or item.get("sellerInfo") or {}
    if not isinstance(dealer, dict):
        dealer = {}
    dealer_name = dealer.get("name") or item.get("dealerName") or item.get("seller_name", "")
    dealer_rating_raw = dealer.get("rating") or item.get("dealerRating")
    try:
        dealer_rating = float(dealer_rating_raw) if dealer_rating_raw else None
    except (TypeError, ValueError):
        dealer_rating = None

    model_raw = (item.get("model") or "").lower()
    model = "Grand Cherokee 4xe" if "grand cherokee" in model_raw else "Wrangler 4xe"

    dom_raw = item.get("daysOnMarket") or item.get("dom") or item.get("days_on_market")
    try:
        dom = int(dom_raw) if dom_raw is not None else None
    except (TypeError, ValueError):
        dom = None

    features = item.get("features") or item.get("options") or []
    if isinstance(features, str):
        features = [features]
    combined = " ".join(features).lower()
    desc = (item.get("sellerComments") or item.get("description") or "").lower()
    combined += " " + desc

    heated_seats = "heated seat" in combined or "heated front seat" in combined
    heated_wheel = "heated steering" in combined
    remote_start = "remote start" in combined
    cold_weather_group = int(sum([heated_seats, heated_wheel, remote_start]) >= 2)
    blind_spot = int("blind spot" in combined or "blind-spot" in combined or "bsm" in combined)

    return {
        "vin": vin,
        "source": "autotrader",
        "year": item.get("year") or item.get("modelYear"),
        "model": model,
        "trim": item.get("trim") or item.get("trimName", ""),
        "price": price,
        "mileage": mileage,
        "city": item.get("city") or dealer.get("city", ""),
        "state": item.get("state") or dealer.get("state", ""),
        "dealer_name": dealer_name,
        "listing_url": item.get("url") or item.get("listingUrl") or item.get("listing_url", ""),
        "exterior_color": item.get("exteriorColor") or item.get("exterior_color", ""),
        "days_on_market": dom,
        "pricing_type": "negotiable",
        "source_type": "dealer",
        "dealer_rating": dealer_rating,
        "cargurus_deal_label": deal_label,
        "cargurus_deal_score": None,
        "cold_weather_group": cold_weather_group,
        "has_blind_spot_mon": blind_spot,
    }


def fetch_autotrader() -> list[dict[str, Any]]:
    if not APIFY_API_TOKEN:
        logger.warning("APIFY_API_TOKEN not set — skipping AutoTrader")
        return []

    results = []
    for model_query in ["Wrangler 4xe", "Grand Cherokee 4xe"]:
        try:
            items = _run_actor(model_query)
        except Exception as e:
            logger.error("AutoTrader Apify actor failed for %s: %s", model_query, e)
            continue

        for item in items:
            norm = _normalize(item)
            if norm and norm["vin"]:
                results.append(norm)

    logger.info("AutoTrader: fetched %d listings", len(results))
    return results
