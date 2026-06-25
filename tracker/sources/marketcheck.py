"""MarketCheck API — primary inventory source + VIN history enrichment."""

import logging
import time
from typing import Any

import requests
from tenacity import retry, stop_after_attempt, wait_exponential

from tracker.config import MARKETCHECK_API_KEY, SEARCH_ZIP, SEARCH_RADIUS_MILES, YEAR_MIN, YEAR_MAX

logger = logging.getLogger(__name__)

BASE_URL = "https://mc-api.marketcheck.com/v2/search/car/active"
HISTORY_URL = "https://mc-api.marketcheck.com/v2/history/car/{vin}"

# Basic plan: 1500 row pagination limit, 5 calls/second
PAGE_SIZE = 100
MAX_ROWS = 1500


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
def _fetch_page(model: str, start: int) -> dict:
    params = {
        "api_key": MARKETCHECK_API_KEY,
        "make": "Jeep",
        "model": model,
        "zip": SEARCH_ZIP,
        "radius": SEARCH_RADIUS_MILES,
        "year": ",".join(str(y) for y in range(YEAR_MIN, YEAR_MAX + 1)),
        "rows": PAGE_SIZE,
        "start": start,
        "include_relevant_links": "true",
    }
    resp = requests.get(BASE_URL, params=params, timeout=30)
    if not resp.ok:
        logger.error("MarketCheck HTTP %s: %s", resp.status_code, resp.text[:400])
    resp.raise_for_status()
    return resp.json()


@retry(stop=stop_after_attempt(2), wait=wait_exponential(multiplier=1, min=2, max=8))
def _fetch_vin_history(vin: str) -> dict:
    resp = requests.get(
        HISTORY_URL.format(vin=vin),
        params={"api_key": MARKETCHECK_API_KEY},
        timeout=15,
    )
    if not resp.ok:
        logger.debug("MarketCheck VIN history HTTP %s for %s", resp.status_code, vin)
    resp.raise_for_status()
    return resp.json()


def _normalize(raw: dict, model_label: str) -> dict:
    listing = raw.get("listing", raw)
    build = raw.get("build", {})

    features = listing.get("features", []) or []
    if isinstance(features, str):
        features = [features]
    options_str = " ".join(features).lower()
    desc = (listing.get("seller_comments") or "").lower()
    combined = options_str + " " + desc
    heated_seats = "heated seat" in combined or "heated front seat" in combined
    heated_wheel = "heated steering" in combined
    remote_start = "remote start" in combined
    cold_weather_group = int(sum([heated_seats, heated_wheel, remote_start]) >= 2)
    blind_spot = int(
        "blind spot" in combined or "blind-spot" in combined or "bsm" in combined
    )

    dealer = listing.get("dealer") or {}
    dealer_rating_raw = dealer.get("rating") if isinstance(dealer, dict) else None
    try:
        dealer_rating = float(dealer_rating_raw) if dealer_rating_raw else None
    except (TypeError, ValueError):
        dealer_rating = None

    return {
        "vin": listing.get("vin", ""),
        "source": "marketcheck",
        "year": build.get("year") or listing.get("year"),
        "model": model_label,
        "trim": build.get("trim") or listing.get("trim", ""),
        "price": listing.get("price"),
        "mileage": listing.get("miles"),
        "city": listing.get("city", ""),
        "state": listing.get("state", ""),
        "dealer_name": dealer.get("name", "") if isinstance(dealer, dict) else "",
        "listing_url": listing.get("vdp_url") or listing.get("listing_url", ""),
        "exterior_color": build.get("exterior_color") or listing.get("exterior_color", ""),
        "days_on_market": listing.get("dom"),
        "pricing_type": "negotiable",
        "source_type": "dealer",
        "dealer_rating": dealer_rating,
        "cold_weather_group": cold_weather_group,
        "has_blind_spot_mon": blind_spot,
    }


def _parse_history(data: dict) -> dict:
    """Extract CARFAX-style signals from MarketCheck VIN history response."""
    # MarketCheck history returns a list of ownership/event records
    items = data if isinstance(data, list) else data.get("listings") or data.get("history") or []

    no_accidents = 1
    owners = set()
    service_records = 0

    for item in items:
        listing = item.get("listing", item)
        # Accident indicator
        if listing.get("accident") or listing.get("has_accident") or listing.get("no_accidents") == 0:
            no_accidents = 0
        # Owner tracking via seller type changes
        owner = listing.get("seller_type") or listing.get("ownership")
        if owner:
            owners.add(str(owner))
        # Service records
        if listing.get("service_history") or listing.get("service_record"):
            service_records += 1

    return {
        "no_accidents": no_accidents if items else 0,
        "one_owner": int(len(owners) <= 1) if owners else 0,
        "service_record_count": service_records,
    }


def enrich_with_history(listings: list[dict], max_vins: int = 25) -> dict[str, dict]:
    """
    Fetch VIN history for the top N listings (by composite score) and return
    a dict of vin → history signals. Stays within API call budget.

    max_vins=25 per run × 3 runs/day × 31 days ≈ 2325 history calls/month,
    leaving ~2675 of the 5000 monthly quota for search pagination.
    """
    if not MARKETCHECK_API_KEY:
        return {}

    # Sort by composite_score descending, take top N with valid VINs
    candidates = sorted(
        [l for l in listings if l.get("vin") and l.get("composite_score") is not None],
        key=lambda x: x.get("composite_score", 0),
        reverse=True,
    )[:max_vins]

    history_map: dict[str, dict] = {}
    for lst in candidates:
        vin = lst["vin"]
        try:
            data = _fetch_vin_history(vin)
            history_map[vin] = _parse_history(data)
            time.sleep(0.2)  # stay under 5 calls/second
        except Exception as e:
            logger.debug("VIN history skipped for %s: %s", vin, e)

    logger.info("MarketCheck VIN history: enriched %d/%d listings", len(history_map), len(candidates))
    return history_map


def fetch_marketcheck() -> list[dict[Any, Any]]:
    if not MARKETCHECK_API_KEY:
        logger.warning("MARKETCHECK_API_KEY not set — skipping MarketCheck")
        return []

    results = []
    model_queries = {
        "Wrangler 4xe": "Wrangler 4xe",
        "Grand Cherokee 4xe": "Grand Cherokee 4xe",
    }

    for model_label, query in model_queries.items():
        start = 0
        while True:
            try:
                data = _fetch_page(query, start)
            except Exception as e:
                logger.error("MarketCheck page fetch failed for %s at start=%d: %s", query, start, e)
                break

            listings = data.get("listings", [])
            if not listings:
                break

            for raw in listings:
                norm = _normalize(raw, model_label)
                if norm["vin"]:
                    results.append(norm)

            total = data.get("totalCount") or data.get("num_found", 0)
            start += len(listings)
            if start >= min(total, MAX_ROWS) or not listings:
                break

    logger.info("MarketCheck: fetched %d listings", len(results))
    return results
