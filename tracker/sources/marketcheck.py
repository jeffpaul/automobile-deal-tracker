"""MarketCheck API — primary inventory source."""

import logging
import time
from typing import Any

import requests
from tenacity import retry, stop_after_attempt, wait_exponential

from tracker.config import MARKETCHECK_API_KEY, SEARCH_ZIP, SEARCH_RADIUS_MILES, VEHICLES

logger = logging.getLogger(__name__)

BASE_URL = "https://mc-api.marketcheck.com/v2/search/car/active"

# Basic plan: 1500 row pagination limit, 5 calls/second
PAGE_SIZE = 100
MAX_ROWS = 1500

# Paces requests comfortably under the 5 calls/second cap. Without this, back
# to back page/vehicle requests reliably trip 429s that then get absorbed by
# the retry decorator below — harmless, but noisy in the logs every run.
REQUEST_DELAY_SECONDS = 0.3


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
def _fetch_page(make: str, model: str, powertrain_type: str | None, year_min: int, year_max: int, start: int) -> dict:
    time.sleep(REQUEST_DELAY_SECONDS)
    params = {
        "api_key": MARKETCHECK_API_KEY,
        "make": make,
        "model": model,
        "zip": SEARCH_ZIP,
        "radius": SEARCH_RADIUS_MILES,
        "year": ",".join(str(y) for y in range(year_min, year_max + 1)),
        "rows": PAGE_SIZE,
        "start": start,
        "include_relevant_links": "true",
    }
    if powertrain_type:
        # Server-side filter — several tracked models (Grand Cherokee 4xe,
        # Outlander PHEV, Tucson PHEV, RAV4 Prime) aren't broken out as their
        # own MC model, so this is what actually isolates the PHEV trims from
        # gas/hybrid siblings sharing the same trim names.
        params["powertrain_type"] = powertrain_type
    resp = requests.get(BASE_URL, params=params, timeout=30)
    if not resp.ok:
        logger.error("MarketCheck HTTP %s: %s", resp.status_code, resp.text[:400])
    resp.raise_for_status()
    return resp.json()


def _normalize(raw: dict, make: str, model_label: str) -> dict:
    listing = raw.get("listing", raw)
    build = raw.get("build", {})

    # MC Basic plan does not return features or seller_comments — these fields
    # are always None. cold_weather_group and has_blind_spot_mon are populated
    # by the CARFAX source (topOptions) for matched VINs; default 0 here.
    cold_weather_group = 0
    blind_spot = 0

    dealer = listing.get("dealer") or {}
    dealer_rating_raw = dealer.get("rating") if isinstance(dealer, dict) else None
    try:
        dealer_rating = float(dealer_rating_raw) if dealer_rating_raw else None
    except (TypeError, ValueError):
        dealer_rating = None

    # CARFAX signals embedded directly in every MC search result
    carfax_1_owner = listing.get("carfax_1_owner")
    carfax_clean_title = listing.get("carfax_clean_title")
    no_accidents = int(bool(carfax_clean_title)) if carfax_clean_title is not None else None
    one_owner = int(bool(carfax_1_owner)) if carfax_1_owner is not None else None

    # Price drop signals
    price_change_pct = listing.get("price_change_percent")
    ref_price = listing.get("ref_price")

    return {
        "vin": listing.get("vin", ""),
        "source": "marketcheck",
        "year": build.get("year") or listing.get("year"),
        "make": make,
        "model": model_label,
        "trim": build.get("trim") or listing.get("trim", ""),
        "price": listing.get("price"),
        "mileage": listing.get("miles"),
        "city": listing.get("city", ""),
        "state": listing.get("state", ""),
        "dealer_name": dealer.get("name", "") if isinstance(dealer, dict) else "",
        "listing_url": listing.get("vdp_url") or listing.get("listing_url", ""),
        "exterior_color": build.get("exterior_color") or listing.get("exterior_color", ""),
        "days_on_market": listing.get("dom_active") or listing.get("dom"),
        "pricing_type": "negotiable",
        "source_type": "dealer",
        "dealer_rating": dealer_rating,
        "cold_weather_group": cold_weather_group,
        "has_blind_spot_mon": blind_spot,
        "no_accidents": no_accidents,
        "one_owner": one_owner,
        "price_change_percent": price_change_pct,
        "ref_price": ref_price,
    }


def fetch_marketcheck() -> list[dict[Any, Any]]:
    if not MARKETCHECK_API_KEY:
        logger.warning("MARKETCHECK_API_KEY not set — skipping MarketCheck")
        return []

    results = []

    for vehicle in VEHICLES:
        make = vehicle["make"]
        model_label = vehicle["model_label"]
        query = vehicle["mc_model"]
        powertrain_type = vehicle.get("mc_powertrain_type")
        vehicle_count = 0
        start = 0
        while True:
            try:
                data = _fetch_page(make, query, powertrain_type, vehicle["year_min"], vehicle["year_max"], start)
            except Exception as e:
                logger.error("MarketCheck page fetch failed for %s at start=%d: %s", query, start, e)
                break

            listings = data.get("listings", [])
            if not listings:
                break

            for raw in listings:
                norm = _normalize(raw, make, model_label)
                if norm["vin"]:
                    results.append(norm)
                    vehicle_count += 1

            total = data.get("totalCount") or data.get("num_found", 0)
            start += len(listings)
            if start >= min(total, MAX_ROWS) or not listings:
                break

        logger.info("MarketCheck: %s %s -> %d listings", make, model_label, vehicle_count)

    logger.info("MarketCheck: fetched %d listings total", len(results))
    return results
