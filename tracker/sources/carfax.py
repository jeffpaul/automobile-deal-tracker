"""CARFAX listing search via Apify actor (parseforge~carfax-scraper).

CARFAX's search doesn't index "Wrangler 4xe" as a model — query by "Wrangler"
and "Grand Cherokee" then filter for 4xe trims and target years post-fetch.
"""

import logging
import time
from typing import Any

import requests
from tenacity import retry, stop_after_attempt, wait_exponential

from tracker.config import APIFY_API_TOKEN, SEARCH_ZIP, SEARCH_RADIUS_MILES, YEAR_MIN, YEAR_MAX

logger = logging.getLogger(__name__)

ACTOR_ID = "parseforge~carfax-scraper"
APIFY_BASE = "https://api.apify.com/v2"
POLL_INTERVAL = 10
MAX_POLLS = 18   # 3 minutes max per query — "Wrangler" with 100 items hangs indefinitely
ACTOR_TIMEOUT = 150  # seconds — Apify-side hard stop; must exceed expected run time


@retry(stop=stop_after_attempt(2), wait=wait_exponential(multiplier=2, min=5, max=30))
def _run_actor(model_query: str) -> list[dict]:
    payload = {
        "make": "Jeep",
        "model": model_query,
        "zipCode": SEARCH_ZIP,
        "radius": str(SEARCH_RADIUS_MILES),
        # 30 items completes in ~20s; 100 causes "Wrangler" runs to hang indefinitely
        "maxItems": 30,
    }

    resp = requests.post(
        f"{APIFY_BASE}/acts/{ACTOR_ID}/runs",
        params={"token": APIFY_API_TOKEN, "memory": 1024, "timeout": ACTOR_TIMEOUT},
        json=payload,
        timeout=30,
    )
    if not resp.ok:
        logger.error("CARFAX Apify start HTTP %s: %s", resp.status_code, resp.text[:400])
    resp.raise_for_status()

    run_data = resp.json()["data"]
    run_id = run_data["id"]
    dataset_id = run_data["defaultDatasetId"]

    final_status = None
    for _ in range(MAX_POLLS):
        time.sleep(POLL_INTERVAL)
        status_resp = requests.get(
            f"{APIFY_BASE}/actor-runs/{run_id}",
            params={"token": APIFY_API_TOKEN},
            timeout=15,
        )
        final_status = status_resp.json()["data"]["status"]
        if final_status == "SUCCEEDED":
            break
        if final_status in ("FAILED", "ABORTED", "TIMED-OUT"):
            raise RuntimeError(f"CARFAX Apify run {run_id} ended with status {final_status}")
    else:
        raise RuntimeError(f"CARFAX Apify run {run_id} still {final_status} after {MAX_POLLS * POLL_INTERVAL}s — giving up")

    items_resp = requests.get(
        f"{APIFY_BASE}/datasets/{dataset_id}/items",
        params={"token": APIFY_API_TOKEN},
        timeout=30,
    )
    items_resp.raise_for_status()
    data = items_resp.json()

    if isinstance(data, list):
        # Filter out error result objects
        return [item for item in data if "error" not in item]
    return []


def _normalize(item: dict, model_label: str) -> dict | None:
    vin = item.get("vin", "")
    if not vin:
        return None

    price = item.get("currentPrice") or item.get("listPrice")
    try:
        price = int(price) if price else None
    except (TypeError, ValueError):
        price = None

    mileage = item.get("mileage")
    try:
        mileage = int(mileage) if mileage else None
    except (TypeError, ValueError):
        mileage = None

    # CARFAX badge: "GREAT" → Great Value (+3 pts), "GOOD" → Good Value (+1 pt)
    badge_raw = item.get("badge", "")
    badge = "Great Value" if badge_raw == "GREAT" else ("Good Value" if badge_raw == "GOOD" else None)

    options = " ".join((item.get("topOptions") or []) + (item.get("otherOptions") or [])).lower()
    heated_seats = "heated seat" in options
    heated_wheel = "heated steering" in options
    remote_start = "remote start" in options
    cold_weather_group = int(sum([heated_seats, heated_wheel, remote_start]) >= 2)
    blind_spot = int("blind spot" in options or "blind-spot" in options)

    return {
        "vin": vin,
        "source": "carfax",
        "year": item.get("year"),
        "model": model_label,
        "trim": item.get("trim", ""),
        "price": price,
        "mileage": mileage,
        "city": item.get("dealerCity", ""),
        "state": item.get("dealerState", ""),
        "dealer_name": item.get("dealerName", ""),
        "listing_url": item.get("url", ""),
        "exterior_color": item.get("exteriorColor", ""),
        "days_on_market": None,
        "pricing_type": "negotiable",
        "source_type": "dealer",
        "dealer_rating": None,
        "no_accidents": int(bool(item.get("noAccidents"))),
        "one_owner": int(bool(item.get("oneOwner"))),
        "carfax_badge": badge,
        "cold_weather_group": cold_weather_group,
        "has_blind_spot_mon": blind_spot,
    }


def fetch_carfax() -> list[dict[str, Any]]:
    if not APIFY_API_TOKEN:
        logger.warning("APIFY_API_TOKEN not set — skipping CARFAX")
        return []

    # CARFAX search uses bare model names — filter for 4xe trims post-fetch
    model_queries = {
        "Wrangler 4xe": "Wrangler",
        "Grand Cherokee 4xe": "Grand Cherokee",
    }

    results = []
    for model_label, query in model_queries.items():
        try:
            items = _run_actor(query)
        except Exception as e:
            logger.error("CARFAX Apify actor failed for %s: %s", query, e)
            continue

        for item in items:
            year = item.get("year") or 0
            trim = item.get("trim") or ""
            # Keep only 4xe trims in our year range
            if "4xe" not in trim.lower():
                continue
            if not (YEAR_MIN <= int(year) <= YEAR_MAX):
                continue
            norm = _normalize(item, model_label)
            if norm:
                results.append(norm)

    logger.info("CARFAX: fetched %d listings", len(results))
    return results
