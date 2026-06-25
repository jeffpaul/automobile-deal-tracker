"""CarGurus listings via Apify actor — primary source for deal ratings and VIN-matched signals."""

import logging
from typing import Any

import requests
from tenacity import retry, stop_after_attempt, wait_exponential

from tracker.config import APIFY_API_TOKEN, SEARCH_ZIP, SEARCH_RADIUS_MILES, YEAR_MIN, YEAR_MAX

logger = logging.getLogger(__name__)

# stealth_mode~cargurus-com-cars-search-scraper — returns 40+ fields including deal rating
APIFY_RUN_URL = (
    "https://api.apify.com/v2/acts/GSTSi6etXwtW4Ikhn/run-sync-get-dataset-items"
)

# CarGurus search URLs — zip + distance filter included in URL params
_SEARCH_URLS = [
    # Wrangler 4XE
    (
        "Wrangler 4xe",
        f"https://www.cargurus.com/Cars/new/nl_Jeep_Wrangler-d2313"
        f"?zip={SEARCH_ZIP}&distance={SEARCH_RADIUS_MILES}"
        f"&minYear={YEAR_MIN}&maxYear={YEAR_MAX}&trim=4XE",
    ),
    # Grand Cherokee 4XE
    (
        "Grand Cherokee 4xe",
        f"https://www.cargurus.com/Cars/new/nl_Jeep_Grand_Cherokee-d2378"
        f"?zip={SEARCH_ZIP}&distance={SEARCH_RADIUS_MILES}"
        f"&minYear={YEAR_MIN}&maxYear={YEAR_MAX}&trim=4XE",
    ),
]

# CarGurus deal label mapping (may appear as numeric score or string)
_DEAL_LABEL_MAP = {
    "great deal": "Great Deal",
    "good deal": "Good Deal",
    "fair deal": "Fair Deal",
    "high price": "High Price",
    "overpriced": "Overpriced",
    "no price analysis": None,
}


@retry(stop=stop_after_attempt(2), wait=wait_exponential(multiplier=2, min=5, max=30))
def _run_actor(start_url: str) -> list[dict]:
    params = {
        "token": APIFY_API_TOKEN,
        "timeout": 180,
        "memory": 1024,
    }
    payload = {
        "startUrls": [{"url": start_url}],
        "maxItems": 150,
    }
    resp = requests.post(APIFY_RUN_URL, params=params, json=payload, timeout=240)
    if not resp.ok:
        logger.error("CarGurus Apify HTTP %s: %s", resp.status_code, resp.text[:400])
    resp.raise_for_status()
    data = resp.json()
    if isinstance(data, list):
        return data
    return data.get("data") or data.get("items") or []


def _parse_deal_label(raw: Any) -> str | None:
    """Normalise CarGurus deal label — may be a string or numeric rating."""
    if not raw:
        return None
    if isinstance(raw, (int, float)):
        # Some actors return numeric scores: 5=Great, 4=Good, 3=Fair, 2=High, 1=Overpriced
        mapping = {5: "Great Deal", 4: "Good Deal", 3: "Fair Deal", 2: "High Price", 1: "Overpriced"}
        return mapping.get(int(raw))
    return _DEAL_LABEL_MAP.get(str(raw).lower().strip(), str(raw) if raw else None)


def _normalize(item: dict, model_label: str) -> dict | None:
    vin = (item.get("vin") or item.get("VIN") or "").strip()
    if not vin:
        return None

    # Price — CarGurus uses various field names
    price_raw = (
        item.get("price")
        or item.get("listingPrice")
        or item.get("listing_price")
        or 0
    )
    try:
        price = int(str(price_raw).replace(",", "").replace("$", "").replace(" ", ""))
    except (TypeError, ValueError):
        price = None

    # Mileage
    mileage_raw = item.get("mileage") or item.get("miles") or 0
    try:
        mileage = int(str(mileage_raw).replace(",", "").split()[0])
    except (TypeError, ValueError):
        mileage = None

    # Deal label — CarGurus is the authoritative source
    deal_label_raw = (
        item.get("dealRating")
        or item.get("deal_rating")
        or item.get("dealType")
        or item.get("deal_type")
        or item.get("priceAnalysis")
    )
    deal_label = _parse_deal_label(deal_label_raw)

    # CarGurus deal score (savings vs market, if provided)
    deal_score_raw = item.get("dealScore") or item.get("deal_score") or item.get("savings")
    try:
        deal_score = float(deal_score_raw) if deal_score_raw is not None else None
    except (TypeError, ValueError):
        deal_score = None

    # Dealer
    dealer = item.get("dealer") or item.get("sellerInfo") or {}
    if not isinstance(dealer, dict):
        dealer = {}
    dealer_name = (
        dealer.get("name")
        or item.get("dealerName")
        or item.get("seller_name", "")
    )
    dealer_rating_raw = dealer.get("rating") or item.get("dealerRating")
    try:
        dealer_rating = float(dealer_rating_raw) if dealer_rating_raw else None
    except (TypeError, ValueError):
        dealer_rating = None

    # Trim — CarGurus may embed trim in model field
    trim = item.get("trim") or item.get("trimName") or ""

    # Location
    city = item.get("city") or dealer.get("city") or item.get("localCity", "")
    state = item.get("state") or dealer.get("state") or item.get("localState", "")

    # Days on market
    dom_raw = item.get("daysOnMarket") or item.get("dom") or item.get("days_on_market")
    try:
        dom = int(dom_raw) if dom_raw is not None else None
    except (TypeError, ValueError):
        dom = None

    return {
        "vin": vin,
        "source": "cargurus",
        "year": item.get("year") or item.get("modelYear"),
        "model": model_label,
        "trim": trim,
        "price": price,
        "mileage": mileage,
        "city": city,
        "state": state,
        "dealer_name": dealer_name,
        "listing_url": item.get("url") or item.get("listingUrl") or item.get("listing_url", ""),
        "exterior_color": item.get("exteriorColor") or item.get("exterior_color", ""),
        "days_on_market": dom,
        "pricing_type": "negotiable",
        "source_type": "dealer",
        "dealer_rating": dealer_rating,
        "cargurus_deal_label": deal_label,
        "cargurus_deal_score": deal_score,
        "cold_weather_group": 0,
        "has_blind_spot_mon": 0,
    }


def fetch_cargurus() -> list[dict[str, Any]]:
    if not APIFY_API_TOKEN:
        logger.warning("APIFY_API_TOKEN not set — skipping CarGurus")
        return []

    results = []
    for model_label, url in _SEARCH_URLS:
        try:
            items = _run_actor(url)
        except Exception as e:
            logger.error("CarGurus Apify actor failed for %s: %s", model_label, e)
            continue

        for item in items:
            norm = _normalize(item, model_label)
            if norm and norm["vin"]:
                results.append(norm)

    logger.info("CarGurus: fetched %d listings", len(results))
    return results
