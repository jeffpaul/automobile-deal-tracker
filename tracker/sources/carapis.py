"""Carapis catalog API — unified vehicle database (GET /apix/catalog_api/vehicles/).

Auth: X-Api-Key header.
US sources available: Autotrader_Us (live), Cars.com, Carvana (on_demand).
"""

import logging
from typing import Any

import requests
from tenacity import retry, stop_after_attempt, wait_exponential

from tracker.config import (
    CARAPIS_API_KEY,
    YEAR_MIN,
    YEAR_MAX,
    CARVANA_DELIVERY_FEE,
)

logger = logging.getLogger(__name__)

BASE_URL = "https://api.carapis.com/apix/catalog_api/vehicles/"


def _headers() -> dict:
    return {"X-Api-Key": CARAPIS_API_KEY}


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
def _fetch_page(params: dict) -> dict:
    resp = requests.get(BASE_URL, headers=_headers(), params=params, timeout=30)
    if not resp.ok:
        logger.error("Carapis HTTP %s: %s", resp.status_code, resp.text[:300])
    resp.raise_for_status()
    return resp.json()


def _extract_features_text(item: dict) -> str:
    features = item.get("features") or []
    if isinstance(features, list):
        return " ".join(str(f) for f in features).lower()
    return str(features).lower()


def _normalize(item: dict) -> dict | None:
    vin = item.get("vin") or item.get("external_id") or item.get("id") or ""
    if not vin:
        return None

    # Price: Carapis stores as float in USD
    price_raw = item.get("price") or item.get("price_usd") or 0
    try:
        price = int(float(price_raw))
    except (TypeError, ValueError):
        price = None

    mileage_raw = item.get("mileage") or item.get("odometer") or 0
    try:
        mileage = int(float(str(mileage_raw).replace(",", "")))
    except (TypeError, ValueError):
        mileage = None

    # Source info
    source_obj = item.get("source") or {}
    source_name = (source_obj.get("name") or source_obj if isinstance(source_obj, str) else "carapis").lower()

    # Normalise source name to our internal labels
    if "carvana" in source_name:
        source_label = "carvana"
        pricing_type = "no-haggle"
        source_type = "online-only"
        if price:
            price += CARVANA_DELIVERY_FEE
    elif "autotrader" in source_name:
        source_label = "autotrader"
        pricing_type = "negotiable"
        source_type = "dealer"
    elif "cars.com" in source_name or "cars_com" in source_name:
        source_label = "cars_com"
        pricing_type = "negotiable"
        source_type = "dealer"
    elif "carmax" in source_name:
        source_label = "carmax"
        pricing_type = "no-haggle"
        source_type = "dealer"
    else:
        source_label = "carapis"
        pricing_type = "negotiable"
        source_type = "dealer"

    model_raw = (item.get("model_name") or item.get("model_slug") or "").lower()
    model = "Grand Cherokee 4xe" if "grand cherokee" in model_raw or "grand_cherokee" in model_raw else "Wrangler 4xe"

    # Location
    location = item.get("location") or {}
    city = location.get("city") or item.get("city") or ""
    state = location.get("state") or location.get("region") or item.get("state") or ""

    # Seller/dealer
    seller = item.get("seller") or item.get("dealer") or {}
    dealer_name = seller.get("name") or item.get("dealer_name") or ""
    dealer_rating_raw = seller.get("rating") or item.get("dealer_rating")
    try:
        dealer_rating = float(dealer_rating_raw) if dealer_rating_raw else None
    except (TypeError, ValueError):
        dealer_rating = None

    # Features / Cold Weather Group detection
    combined = _extract_features_text(item)
    desc = (item.get("description") or "").lower()
    combined += " " + desc
    heated_seats = "heated seat" in combined or "heated front seat" in combined
    heated_wheel = "heated steering" in combined
    remote_start = "remote start" in combined
    cold_weather_group = int(sum([heated_seats, heated_wheel, remote_start]) >= 2)
    blind_spot = int("blind spot" in combined or "blind-spot" in combined)

    listing_url = item.get("url") or item.get("listing_url") or item.get("original_url") or ""

    return {
        "vin": str(vin),
        "source": source_label,
        "year": item.get("year"),
        "model": model,
        "trim": item.get("trim") or item.get("trim_name") or "",
        "price": price if price and price > 1000 else None,
        "mileage": mileage,
        "city": city,
        "state": state,
        "dealer_name": dealer_name,
        "listing_url": listing_url,
        "exterior_color": item.get("color") or item.get("exterior_color") or "",
        "days_on_market": item.get("days_on_market") or item.get("dom"),
        "pricing_type": pricing_type,
        "source_type": source_type,
        "dealer_rating": dealer_rating,
        "cold_weather_group": cold_weather_group,
        "has_blind_spot_mon": blind_spot,
        # Undervalued flag from Carapis valuation engine
        "cargurus_deal_label": "Great Deal" if item.get("is_undervalued") else None,
        "has_accident": item.get("has_accident"),
    }


def fetch_carapis() -> list[dict[str, Any]]:
    if not CARAPIS_API_KEY:
        logger.warning("CARAPIS_API_KEY not set — skipping Carapis")
        return []

    results = []
    # Fetch both Wrangler 4xe and Grand Cherokee 4xe
    model_queries = ["wrangler-4xe", "grand-cherokee-4xe"]

    for model_slug in model_queries:
        page = 1
        while True:
            params = {
                "brand": "jeep",
                "model": model_slug,
                "fuel_type": "plug_hybrid",
                "min_year": YEAR_MIN,
                "max_year": YEAR_MAX,
                "available_only": "true",
                "page_size": 100,
                "page": page,
            }
            try:
                data = _fetch_page(params)
            except Exception as e:
                logger.error("Carapis fetch failed for %s page %d: %s", model_slug, page, e)
                break

            items = data.get("results") or []
            if not items:
                break

            for item in items:
                norm = _normalize(item)
                if norm and norm["vin"]:
                    results.append(norm)

            total = data.get("count") or 0
            fetched_so_far = (page - 1) * 100 + len(items)
            if fetched_so_far >= total or not data.get("next"):
                break
            page += 1

    logger.info("Carapis: fetched %d listings", len(results))
    return results
