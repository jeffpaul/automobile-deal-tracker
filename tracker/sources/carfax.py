"""CARFAX listing search via Apify actor — surfaces accident/owner/service history."""

import logging
from typing import Any

import requests
from tenacity import retry, stop_after_attempt, wait_exponential

from tracker.config import APIFY_API_TOKEN, YEAR_MIN

logger = logging.getLogger(__name__)

APIFY_RUN_URL = (
    "https://api.apify.com/v2/acts/parseforge~carfax-scraper/run-sync-get-dataset-items"
)


@retry(stop=stop_after_attempt(2), wait=wait_exponential(multiplier=2, min=5, max=30))
def _run_actor(payload: dict) -> list[dict]:
    params = {
        "token": APIFY_API_TOKEN,
        "timeout": 120,
        "memory": 1024,
    }
    resp = requests.post(APIFY_RUN_URL, params=params, json=payload, timeout=180)
    if not resp.ok:
        logger.error("CARFAX Apify HTTP %s: %s", resp.status_code, resp.text[:400])
    resp.raise_for_status()
    data = resp.json()
    if isinstance(data, list):
        return data
    return data.get("data") or data.get("items") or []


def _normalize(item: dict) -> dict | None:
    vin = item.get("vin") or item.get("id", "")
    if not vin:
        return None

    price_raw = item.get("price") or item.get("askingPrice") or 0
    try:
        price = int(str(price_raw).replace(",", "").replace("$", ""))
    except (TypeError, ValueError):
        price = None

    mileage_raw = item.get("mileage") or item.get("miles") or 0
    try:
        mileage = int(str(mileage_raw).replace(",", ""))
    except (TypeError, ValueError):
        mileage = None

    dealer = item.get("dealer") or {}
    dealer_rating_raw = dealer.get("rating") or item.get("dealerRating")
    try:
        dealer_rating = float(dealer_rating_raw) if dealer_rating_raw else None
    except (TypeError, ValueError):
        dealer_rating = None

    model_raw = (item.get("model") or "").lower()
    model = "Grand Cherokee 4xe" if "grand cherokee" in model_raw else "Wrangler 4xe"

    no_accidents = int(bool(item.get("noAccidents") or item.get("no_accidents")))
    one_owner = int(bool(item.get("oneOwner") or item.get("one_owner")))
    svc_raw = item.get("serviceRecordCount") or item.get("service_record_count") or 0
    try:
        service_record_count = int(svc_raw)
    except (TypeError, ValueError):
        service_record_count = 0

    badge_raw = item.get("reliabilityBadge") or item.get("carfax_badge") or ""
    badge = badge_raw if badge_raw in ("Great Value", "Good Value") else None

    return {
        "vin": vin,
        "source": "carfax",
        "year": item.get("year"),
        "model": model,
        "trim": item.get("trim", ""),
        "price": price,
        "mileage": mileage,
        "city": item.get("city", "") or (dealer.get("city") if isinstance(dealer, dict) else ""),
        "state": item.get("state", "") or (dealer.get("state") if isinstance(dealer, dict) else ""),
        "dealer_name": dealer.get("name") if isinstance(dealer, dict) else item.get("dealerName", ""),
        "listing_url": item.get("url") or item.get("listingUrl", ""),
        "exterior_color": item.get("exteriorColor") or item.get("exterior_color", ""),
        "days_on_market": item.get("daysOnMarket") or item.get("dom"),
        "pricing_type": "negotiable",
        "source_type": "dealer",
        "dealer_rating": dealer_rating,
        "no_accidents": no_accidents,
        "one_owner": one_owner,
        "service_record_count": service_record_count,
        "carfax_badge": badge,
        "cold_weather_group": 0,
        "has_blind_spot_mon": 0,
    }


def fetch_carfax() -> list[dict[str, Any]]:
    if not APIFY_API_TOKEN:
        logger.warning("APIFY_API_TOKEN not set — skipping CARFAX")
        return []

    results = []
    for model_query in ["Wrangler 4xe", "Grand Cherokee 4xe"]:
        payload = {
            "make": "Jeep",
            "model": model_query,
            "location": "Downers Grove, IL",
            "radius": 100,
            "maxItems": 100,
        }
        try:
            items = _run_actor(payload)
        except Exception as e:
            logger.error("CARFAX Apify actor failed for %s: %s", model_query, e)
            continue

        for item in items:
            norm = _normalize(item)
            if norm and norm["vin"]:
                results.append(norm)

    logger.info("CARFAX: fetched %d listings", len(results))
    return results
