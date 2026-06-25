"""Carapis unified parser — covers CarGurus, AutoTrader, Cars.com, CarMax, Carvana, TrueCar."""

import logging
from typing import Any

import requests
from tenacity import retry, stop_after_attempt, wait_exponential

from tracker.config import (
    CARAPIS_API_KEY,
    SEARCH_LOCATION,
    YEAR_MIN,
    CARVANA_DELIVERY_FEE,
)

logger = logging.getLogger(__name__)

CARAPIS_BASE = "https://api.carapis.com/v1/{source}/search"

# Per-source request payloads
_SOURCE_PAYLOADS: dict[str, dict] = {
    "cargurus": {
        "query": "Jeep Wrangler 4xe",
        "market": "us",
        "year_from": YEAR_MIN,
        "location": SEARCH_LOCATION,
        "fuel_type": "hybrid",
        "limit": 50,
        "sort_by": "deal_rating",
        "sort_order": "desc",
    },
    "autotrader-com": {
        "query": "Jeep Wrangler 4xe 2023",
        "market": "us",
        "location": SEARCH_LOCATION,
        "year_from": YEAR_MIN,
        "limit": 50,
        "sort_by": "price",
        "sort_order": "asc",
    },
    "cars-com": {
        "query": "Jeep Wrangler 4xe",
        "market": "us",
        "location": "Chicago, IL",
        "year_from": YEAR_MIN,
        "limit": 50,
    },
    "carmax": {
        "query": "Jeep Wrangler 4xe",
        "market": "us",
        "location": "Chicago, IL",
        "year_from": YEAR_MIN,
        "limit": 25,
    },
    "carvana": {
        "query": "Jeep Wrangler 4xe",
        "market": "us",
        "year_from": YEAR_MIN,
        "limit": 25,
        "sort_by": "price",
        "sort_order": "asc",
    },
    "truecar": {
        "query": "Jeep Wrangler 4xe",
        "market": "us",
        "location": "Chicago, IL",
        "year_from": YEAR_MIN,
        "limit": 25,
    },
}

SOURCE_LABELS = {
    "cargurus": "cargurus",
    "autotrader-com": "autotrader",
    "cars-com": "cars_com",
    "carmax": "carmax",
    "carvana": "carvana",
    "truecar": "truecar",
}


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
def _post(source: str, payload: dict) -> list[dict]:
    url = CARAPIS_BASE.format(source=source)
    headers = {"Authorization": f"Bearer {CARAPIS_API_KEY}"}
    resp = requests.post(url, json=payload, headers=headers, timeout=45)
    if not resp.ok:
        logger.error("Carapis/%s HTTP %s: %s", source, resp.status_code, resp.text[:400])
    resp.raise_for_status()
    data = resp.json()
    # Carapis returns list or {"data": [...]} or {"listings": [...]}
    if isinstance(data, list):
        return data
    return data.get("data") or data.get("listings") or data.get("results") or []


def _extract_features_text(item: dict) -> str:
    specs = item.get("specifications") or {}
    features = specs.get("features") or item.get("features") or []
    if isinstance(features, list):
        features_str = " ".join(str(f) for f in features).lower()
    else:
        features_str = str(features).lower()
    desc = (item.get("description") or "").lower()
    return features_str + " " + desc


def _normalize_item(item: dict, source_key: str) -> dict | None:
    vin = item.get("vin") or item.get("id", "")
    if not vin:
        return None

    specs = item.get("specifications") or {}
    price_info = item.get("price") or {}
    price_raw = (
        price_info.get("amount")
        or price_info.get("value")
        or item.get("price")
        or 0
    )
    try:
        price = int(price_raw)
    except (TypeError, ValueError):
        price = 0

    mileage_raw = (
        specs.get("mileage")
        or specs.get("miles")
        or item.get("mileage")
        or item.get("miles")
    )
    try:
        mileage = int(str(mileage_raw).replace(",", ""))
    except (TypeError, ValueError):
        mileage = None

    year = specs.get("year") or item.get("year")
    trim = specs.get("trim") or item.get("trim", "")
    model_raw = specs.get("model") or item.get("model", "")
    model = "Grand Cherokee 4xe" if "grand cherokee" in model_raw.lower() else "Wrangler 4xe"

    dealer_info = item.get("dealer") or item.get("seller") or {}
    dealer_name = dealer_info.get("name") or item.get("dealer_name", "")
    dealer_rating = dealer_info.get("rating") or item.get("dealer_rating")
    try:
        dealer_rating = float(dealer_rating) if dealer_rating else None
    except (TypeError, ValueError):
        dealer_rating = None

    location = item.get("location") or dealer_info.get("location") or {}
    city = location.get("city") or item.get("city", "")
    state = location.get("state") or item.get("state", "")

    listing_url = item.get("url") or item.get("vdp_url") or item.get("listing_url", "")

    # CarGurus deal rating
    deal = item.get("deal_rating") or {}
    cargurus_deal_label = deal.get("label") or item.get("deal_label")
    cargurus_deal_score = deal.get("score") or item.get("deal_score")
    cargurus_explanation = deal.get("explanation") or item.get("deal_explanation")

    # TrueCar market price
    truecar_market_price = item.get("market_price") or item.get("average_market_price")

    # Cold Weather Group detection
    combined = _extract_features_text(item)
    heated_seats = "heated seat" in combined or "heated front seat" in combined
    heated_wheel = "heated steering" in combined
    remote_start = "remote start" in combined
    cold_weather_group = int(sum([heated_seats, heated_wheel, remote_start]) >= 2)
    blind_spot = int("blind spot" in combined or "blind-spot" in combined)

    # Source-specific flags
    pricing_type = "negotiable"
    source_type = "dealer"
    if source_key in ("carmax", "carvana"):
        pricing_type = "no-haggle"
    if source_key == "carvana":
        source_type = "online-only"
        price += CARVANA_DELIVERY_FEE  # Factor in delivery

    dom = item.get("days_on_market") or item.get("dom")

    result = {
        "vin": vin,
        "source": SOURCE_LABELS.get(source_key, source_key),
        "year": year,
        "model": model,
        "trim": trim,
        "price": price if price > 0 else None,
        "mileage": mileage,
        "city": city,
        "state": state,
        "dealer_name": dealer_name,
        "listing_url": listing_url,
        "exterior_color": specs.get("exterior_color") or item.get("exterior_color", ""),
        "days_on_market": int(dom) if dom is not None else None,
        "pricing_type": pricing_type,
        "source_type": source_type,
        "dealer_rating": dealer_rating,
        "cold_weather_group": cold_weather_group,
        "has_blind_spot_mon": blind_spot,
    }

    if source_key == "cargurus":
        result["cargurus_deal_label"] = cargurus_deal_label
        result["cargurus_deal_score"] = cargurus_deal_score
        result["cargurus_explanation"] = cargurus_explanation
    if source_key == "truecar":
        result["truecar_market_price"] = int(truecar_market_price) if truecar_market_price else None

    return result


def fetch_carapis(source: str) -> list[dict[str, Any]]:
    if not CARAPIS_API_KEY:
        logger.warning("CARAPIS_API_KEY not set — skipping %s", source)
        return []

    payload = _SOURCE_PAYLOADS.get(source, {})
    if not payload:
        logger.warning("No payload configured for Carapis source: %s", source)
        return []

    # Also run Grand Cherokee query where applicable
    results = []
    queries_to_run = [payload]
    if "Wrangler" in payload.get("query", ""):
        gc_payload = {**payload, "query": payload["query"].replace("Wrangler", "Grand Cherokee")}
        queries_to_run.append(gc_payload)

    for q_payload in queries_to_run:
        try:
            items = _post(source, q_payload)
        except Exception as e:
            logger.error("Carapis %s fetch failed: %s", source, e)
            continue

        for item in items:
            norm = _normalize_item(item, source)
            if norm and norm["vin"]:
                results.append(norm)

    logger.info("Carapis/%s: fetched %d listings", source, len(results))
    return results
