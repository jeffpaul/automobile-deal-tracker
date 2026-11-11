"""MarketCheck API — primary inventory source."""

import logging
import time
from typing import Any

import requests
from tenacity import retry, stop_after_attempt, wait_exponential

from tracker.config import (
    MARKETCHECK_API_KEY,
    MC_MAX_MILES,
    MC_MAX_ROWS,
    MC_MIN_PRICE,
    NO_HAGGLE_DEALERS,
    SEARCH_RADIUS_MILES,
    SEARCH_ZIP,
    VEHICLES,
)

logger = logging.getLogger(__name__)

BASE_URL = "https://mc-api.marketcheck.com/v2/search/car/active"

# 5 calls/second on both Free and Basic; the row cap per query is MC_MAX_ROWS
PAGE_SIZE = 100

# Paces requests comfortably under the 5 calls/second cap. Without this, back
# to back page/vehicle requests reliably trip 429s that then get absorbed by
# the retry decorator below — harmless, but noisy in the logs every run.
REQUEST_DELAY_SECONDS = 0.3


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
def _fetch_page(make: str, model: str, powertrain_type: str | None, years: list[int], start: int) -> dict:
    time.sleep(REQUEST_DELAY_SECONDS)
    params = {
        "api_key": MARKETCHECK_API_KEY,
        "make": make,
        "model": model,
        "zip": SEARCH_ZIP,
        "radius": SEARCH_RADIUS_MILES,
        "year": ",".join(str(y) for y in years),
        "miles_range": f"0-{MC_MAX_MILES}",
        "price_range": f"{MC_MIN_PRICE}-1000000",
        "rows": PAGE_SIZE,
        "start": start,
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

    # CARFAX signals embedded directly in every MC search result. A clean
    # title only means no salvage/rebuilt/lemon brand — it says nothing about
    # accidents, so it's kept separate. no_accidents stays unknown (None)
    # unless CARFAX or a hand-verified override in history_overrides.json
    # supplies it.
    carfax_1_owner = listing.get("carfax_1_owner")
    carfax_clean_title = listing.get("carfax_clean_title")
    clean_title = int(bool(carfax_clean_title)) if carfax_clean_title is not None else None
    one_owner = int(bool(carfax_1_owner)) if carfax_1_owner is not None else None

    dealer_name = dealer.get("name", "") if isinstance(dealer, dict) else ""
    no_haggle = any(d in dealer_name.lower() for d in NO_HAGGLE_DEALERS)

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
        # MC nests location under the dealer object, not the listing
        "city": listing.get("city") or (dealer.get("city", "") if isinstance(dealer, dict) else ""),
        "state": listing.get("state") or (dealer.get("state", "") if isinstance(dealer, dict) else ""),
        "dealer_name": dealer_name,
        "listing_url": listing.get("vdp_url") or listing.get("listing_url", ""),
        "exterior_color": build.get("exterior_color") or listing.get("exterior_color", ""),
        "days_on_market": listing.get("dom_active") or listing.get("dom"),
        "pricing_type": "no-haggle" if no_haggle else "negotiable",
        "source_type": "dealer",
        "dealer_rating": dealer_rating,
        "cold_weather_group": cold_weather_group,
        "has_blind_spot_mon": blind_spot,
        "no_accidents": None,
        "clean_title": clean_title,
        "one_owner": one_owner,
        "price_change_percent": price_change_pct,
        "ref_price": ref_price,
    }


def _fetch_query(make: str, model_label: str, query: str, powertrain_type: str | None,
                 years: list[int], bail_if_over_cap: bool = False) -> tuple[list[dict], int]:
    """Page through one query. Returns (listings, total result count).

    With bail_if_over_cap, stops after the first page when the total exceeds
    MC_MAX_ROWS, so the caller can split the query without wasting calls.
    """
    results: list[dict] = []
    total = 0
    start = 0
    while True:
        try:
            data = _fetch_page(make, query, powertrain_type, years, start)
        except Exception as e:
            logger.error("MarketCheck page fetch failed for %s %s at start=%d: %s", query, years, start, e)
            break

        listings = data.get("listings", [])
        total = data.get("num_found") or data.get("totalCount") or 0
        if not listings or (bail_if_over_cap and total > MC_MAX_ROWS):
            break

        for raw in listings:
            norm = _normalize(raw, make, model_label)
            if norm["vin"]:
                results.append(norm)

        start += len(listings)
        if start >= min(total, MC_MAX_ROWS):
            break
    return results, total


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
        years = list(range(vehicle["year_min"], vehicle["year_max"] + 1))

        listings, total = _fetch_query(make, model_label, query, powertrain_type, years,
                                       bail_if_over_cap=len(years) > 1)
        if total > MC_MAX_ROWS and len(years) > 1:
            # More results than the plan's pagination cap — re-query one
            # model year at a time so the tail isn't silently dropped.
            logger.info("MarketCheck: %s %s has %d results (> %d cap) — splitting by year",
                        make, model_label, total, MC_MAX_ROWS)
            listings = []
            for year in years:
                year_listings, year_total = _fetch_query(make, model_label, query, powertrain_type, [year])
                if year_total > MC_MAX_ROWS:
                    logger.warning("MarketCheck: %s %s %d still has %d results (> %d cap) — truncated",
                                   make, model_label, year, year_total, MC_MAX_ROWS)
                listings.extend(year_listings)

        results.extend(listings)
        logger.info("MarketCheck: %s %s -> %d listings", make, model_label, len(listings))

    logger.info("MarketCheck: fetched %d listings total", len(results))
    return results
