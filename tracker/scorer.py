"""Deal scoring — calibrated for winter daily driver use case, not off-road."""

from typing import Any

# Trim weights: Sahara/High Altitude preferred; Willys/Rubicon penalized.
# M/T tires on Willys/Rubicon perform worse on packed snow/ice vs. all-season Sahara tires.
TRIM_VALUE_WEIGHTS: dict[str, float] = {
    "High Altitude":  1.15,  # Best fit: Sahara base + adaptive cruise + safety tech standard
    "Sahara":         1.10,  # Top pick: all-season tires, driver aids, daily-driver suspension
    "Rubicon X":      0.95,  # Luxury features but paying for unused off-road hardware
    "Sport S":        0.90,  # Entry 4xe — decent safety kit, good value if priced right
    "Willys '41":     0.82,  # M/T tires actively worse for winter pavement — penalized
    "Willys":         0.80,  # Same: trail hardware and tires wrong for this use case
    "Rubicon":        0.78,  # Trail suspension + A/T tires = least suitable for Chicago winter
    "Sport":          0.75,
}

COLD_WEATHER_GROUP_BONUS = 3  # pts: heated seats + wheel + remote start


def _trim_weight(trim: str) -> float:
    """Return the weight for a trim name, fuzzy-matching on partial strings.
    MarketCheck returns names like 'Sahara 4XE', 'High Altitude 4XE', etc.
    """
    lower = (trim or "").lower()
    # Check most specific first to avoid 'rubicon x' matching 'rubicon'
    if "high altitude" in lower:
        return TRIM_VALUE_WEIGHTS["High Altitude"]
    if "rubicon x" in lower:
        return TRIM_VALUE_WEIGHTS["Rubicon X"]
    if "sahara" in lower:
        return TRIM_VALUE_WEIGHTS["Sahara"]
    if "willys '41" in lower or "willys41" in lower:
        return TRIM_VALUE_WEIGHTS["Willys '41"]
    if "willys" in lower:
        return TRIM_VALUE_WEIGHTS["Willys"]
    if "rubicon" in lower:
        return TRIM_VALUE_WEIGHTS["Rubicon"]
    if "sport s" in lower:
        return TRIM_VALUE_WEIGHTS["Sport S"]
    if "sport" in lower:
        return TRIM_VALUE_WEIGHTS["Sport"]
    return 0.90  # Default for unknown trims


def score_listing(listing: dict[str, Any], market_avg_for_trim: float | None) -> float:
    score = 0.0

    # 1. Price vs. market average (max 35 pts)
    price = listing.get("price") or 0
    if market_avg_for_trim and price > 0:
        pct_below = (market_avg_for_trim - price) / market_avg_for_trim * 100
        if pct_below >= 12:
            score += 35
        elif pct_below >= 10:
            score += 28
        elif pct_below >= 7:
            score += 20
        elif pct_below >= 5:
            score += 12
        elif pct_below >= 2:
            score += 5
        elif pct_below < 0:
            score -= 10  # Overpriced vs. market

    # 2. CarGurus deal rating (max 15 pts)
    label = listing.get("cargurus_deal_label") or ""
    if label == "Great Deal":
        score += 15
    elif label == "Good Deal":
        score += 9
    elif label == "Fair Deal":
        score += 3
    elif label == "High Price":
        score -= 5

    # 3. CARFAX history signals (max 15 pts)
    if listing.get("no_accidents"):
        score += 8
    if listing.get("one_owner"):
        score += 4
    svc = listing.get("service_record_count") or 0
    if svc >= 3:
        score += 3
    elif svc >= 1:
        score += 1
    badge = listing.get("carfax_badge") or ""
    if badge == "Great Value":
        score += 3
    elif badge == "Good Value":
        score += 1

    # 4. Trim value weight — winter/safety calibrated (max 12 pts)
    w = _trim_weight(listing.get("trim", ""))
    score += (w - 0.75) * 30  # Maps 0.75–1.15 → 0–12 pts

    # 5. Mileage (max 12 pts)
    miles = listing.get("mileage") or 99999
    if miles < 10000:
        score += 12
    elif miles < 20000:
        score += 8
    elif miles < 30000:
        score += 4
    elif miles > 40000:
        score -= 4

    # 6. Days on market (max 8 pts)
    dom = listing.get("days_on_market")
    if dom is not None:
        if dom <= 3:
            score += 8    # Fresh — act fast
        elif dom <= 7:
            score += 5
        elif dom <= 14:
            score += 2
        elif dom > 60:
            score -= 4    # Why has it been sitting?

    # 7. Price drop bonus (max 8 pts)
    history = listing.get("price_history") or []
    if isinstance(history, str):
        import json
        try:
            history = json.loads(history)
        except Exception:
            history = []
    if len(history) >= 2 and price > 0:
        original_price = history[0]["price"]
        if original_price and original_price > price:
            drop_pct = (original_price - price) / original_price * 100
            if drop_pct >= 5:
                score += 8
            elif drop_pct >= 2:
                score += 4

    # 8. Source type penalties/bonuses
    if listing.get("pricing_type") == "no-haggle":
        score -= 5  # CarMax/Carvana: can't negotiate below listing
    if listing.get("source_type") == "rental-fleet":
        score += 3  # Enterprise: fleet vehicles typically well-maintained

    # 9. Cold Weather Group bonus — practical for Chicago winters
    if listing.get("cold_weather_group"):
        score += COLD_WEATHER_GROUP_BONUS

    # 10. Dealer rating signal
    rating = listing.get("dealer_rating") or 0
    if rating >= 4.5:
        score += 2
    elif 0 < rating < 3.5:
        score -= 3

    return round(min(max(score, 0), 100), 1)


def is_winter_penalized_trim(trim: str) -> bool:
    """Return True if trim has M/T or A/T tires unsuitable for winter pavement."""
    lower = (trim or "").lower()
    return "willys" in lower or ("rubicon" in lower and "rubicon x" not in lower)


def compute_market_averages(listings: list[dict]) -> dict[tuple, float]:
    """Compute mean price by (model, trim) for use in price scoring."""
    from collections import defaultdict
    buckets: dict[tuple, list[int]] = defaultdict(list)
    for lst in listings:
        price = lst.get("price") or 0
        if price > 5000:  # Sanity filter
            key = (lst.get("model", ""), lst.get("trim", ""))
            buckets[key].append(price)

    return {
        key: sum(prices) / len(prices)
        for key, prices in buckets.items()
        if prices
    }
