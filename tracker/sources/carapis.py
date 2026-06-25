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
    # Carapis accepts both Bearer and X-Api-Key; Bearer is the documented form
    return {"Authorization": f"Bearer {CARAPIS_API_KEY}"}


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
    # Carapis uses UUID `id`, no VIN field in catalog view
    item_id = item.get("id") or ""
    if not item_id:
        return None

    # Price is price_usd (float)
    price_raw = item.get("price_usd") or 0
    try:
        price = int(float(price_raw))
    except (TypeError, ValueError):
        price = None

    mileage_raw = item.get("mileage") or 0
    try:
        mileage = int(float(str(mileage_raw).replace(",", "")))
    except (TypeError, ValueError):
        mileage = None

    # Source identifier is source_code
    source_code = (item.get("source_code") or "").lower()
    if "carvana" in source_code:
        source_label = "carvana"
        pricing_type = "no-haggle"
        source_type = "online-only"
        if price:
            price += CARVANA_DELIVERY_FEE
    elif "autotrader" in source_code:
        source_label = "autotrader"
        pricing_type = "negotiable"
        source_type = "dealer"
    elif "cars" in source_code:
        source_label = "cars_com"
        pricing_type = "negotiable"
        source_type = "dealer"
    else:
        source_label = f"carapis_{source_code}"
        pricing_type = "negotiable"
        source_type = "dealer"

    model_raw = (item.get("model_name") or item.get("model_slug") or "").lower()
    model = "Grand Cherokee 4xe" if "grand cherokee" in model_raw or "grand_cherokee" in model_raw else "Wrangler 4xe"

    # Location: `region` is city/region string, `source_location` is structured (often null)
    loc = item.get("source_location") or {}
    city = loc.get("city") or item.get("region") or ""
    state = loc.get("state") or loc.get("region") or ""

    # No dealer object in catalog view
    dealer_name = ""
    dealer_rating = None

    # Features / Cold Weather Group detection from analysis text if present
    analysis = item.get("analysis") or {}
    desc = str(analysis).lower() + " " + (item.get("description") or "").lower()
    heated_seats = "heated seat" in desc or "heated front seat" in desc
    heated_wheel = "heated steering" in desc
    remote_start = "remote start" in desc
    cold_weather_group = int(sum([heated_seats, heated_wheel, remote_start]) >= 2)
    blind_spot = int("blind spot" in desc or "blind-spot" in desc)

    # Use the Carapis UUID as the VIN key (prefixed) so dedup works
    # Real VIN only available on the detail endpoint /vehicles/{id}/
    vin = f"CRPS-{item_id}"

    return {
        "vin": vin,
        "source": source_label,
        "year": item.get("year"),
        "model": model,
        "trim": item.get("trim") or "",
        "price": price if price and price > 1000 else None,
        "mileage": mileage,
        "city": city,
        "state": state,
        "dealer_name": dealer_name,
        "listing_url": "",  # Not in catalog response; available on detail endpoint
        "exterior_color": item.get("color") or "",
        "days_on_market": None,  # Not in catalog response
        "pricing_type": pricing_type,
        "source_type": source_type,
        "dealer_rating": dealer_rating,
        "cold_weather_group": cold_weather_group,
        "has_blind_spot_mon": blind_spot,
        "no_accidents": int(not bool(item.get("has_accident"))) if item.get("has_accident") is not None else None,
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
                "source": "autotrader_us",  # US live source; add more as on_demand sources activate
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
