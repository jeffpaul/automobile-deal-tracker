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

    # 2. Price vs. MarketCheck reference price (max 15 pts)
    # ref_price is MC's market-derived value for this specific used vehicle.
    # Distinct from market_avg_for_trim (population average) — ref_price is per-car.
    ref_price = listing.get("ref_price") or 0
    if ref_price > 0 and price > 0:
        pct_below_ref = (ref_price - price) / ref_price * 100
        if pct_below_ref >= 8:
            score += 15
        elif pct_below_ref >= 5:
            score += 11
        elif pct_below_ref >= 3:
            score += 7
        elif pct_below_ref >= 1:
            score += 3
        elif pct_below_ref < -5:
            score -= 5  # Significantly above MC reference — overpriced

    # 3. CARFAX history signals (max 15 pts)
    if listing.get("no_accidents"):
        score += 8
    if listing.get("one_owner"):
        score += 4
    badge = listing.get("carfax_badge") or ""
    if badge in ("Great Value", "GREAT"):
        score += 3
    elif badge in ("Good Value", "GOOD"):
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
    # Primary: DB price history (cross-run drops). Fallback: MC's price_change_percent
    # (dealer-reported drop, available on first sighting).
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
    elif price > 0:
        # Use MC's price_change_percent if no DB history yet (negative = price was cut)
        mc_change = listing.get("price_change_percent")
        if mc_change is not None and mc_change < 0:
            drop_pct = abs(mc_change)
            if drop_pct >= 5:
                score += 8
            elif drop_pct >= 2:
                score += 4

    # 8. Source type penalties
    if listing.get("pricing_type") == "no-haggle":
        score -= 5  # CarMax/Carvana: can't negotiate below listing

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


def score_breakdown(listing: dict[str, Any], market_avg_for_trim: float | None) -> list[tuple[str, float]]:
    """Return each scoring component as (label, pts) — non-zero entries only."""
    import json
    components: list[tuple[str, float]] = []

    price = listing.get("price") or 0

    # 1. Price vs market (max 35 pts)
    if market_avg_for_trim and price > 0:
        pct_below = (market_avg_for_trim - price) / market_avg_for_trim * 100
        if pct_below >= 12:
            pts = 35
        elif pct_below >= 10:
            pts = 28
        elif pct_below >= 7:
            pts = 20
        elif pct_below >= 5:
            pts = 12
        elif pct_below >= 2:
            pts = 5
        elif pct_below < 0:
            pts = -10
        else:
            pts = 0
        if pts != 0:
            pct_label = f"{pct_below:+.1f}%" if pct_below != 0 else "at market"
            components.append((f"Price ({pct_label} vs mkt)", pts))
    else:
        components.append(("Price (no market data)", 0))

    # 2. Price vs. MC reference price (max 15 pts)
    ref_price = listing.get("ref_price") or 0
    if ref_price > 0 and price > 0:
        pct_below_ref = (ref_price - price) / ref_price * 100
        if pct_below_ref >= 8:
            ref_pts = 15
        elif pct_below_ref >= 5:
            ref_pts = 11
        elif pct_below_ref >= 3:
            ref_pts = 7
        elif pct_below_ref >= 1:
            ref_pts = 3
        elif pct_below_ref < -5:
            ref_pts = -5
        else:
            ref_pts = 0
        if ref_pts != 0:
            components.append((f"vs. MC ref ({pct_below_ref:+.1f}%)", ref_pts))

    # 3. CARFAX signals (max 15 pts)
    cf_pts = 0.0
    cf_parts = []
    if listing.get("no_accidents"):
        cf_pts += 8
        cf_parts.append("no accidents")
    if listing.get("one_owner"):
        cf_pts += 4
        cf_parts.append("1 owner")
    badge = listing.get("carfax_badge") or ""
    if badge in ("Great Value", "GREAT"):
        cf_pts += 3
        cf_parts.append("Great Value badge")
    elif badge in ("Good Value", "GOOD"):
        cf_pts += 1
        cf_parts.append("Good Value badge")
    if cf_pts != 0:
        components.append((f"CARFAX ({', '.join(cf_parts)})", cf_pts))

    # 4. Trim weight (max 12 pts)
    w = _trim_weight(listing.get("trim", ""))
    trim_pts = round((w - 0.75) * 30, 1)
    if trim_pts != 0:
        components.append((f"Trim ({listing.get('trim', '?')})", trim_pts))

    # 5. Mileage (max 12 pts)
    miles = listing.get("mileage") or 99999
    if miles < 10000:
        components.append((f"Mileage ({miles:,} mi)", 12))
    elif miles < 20000:
        components.append((f"Mileage ({miles:,} mi)", 8))
    elif miles < 30000:
        components.append((f"Mileage ({miles:,} mi)", 4))
    elif miles > 40000:
        components.append((f"Mileage ({miles:,} mi)", -4))

    # 6. Days on market (max 8 pts)
    dom = listing.get("days_on_market")
    if dom is not None:
        if dom <= 3:
            components.append((f"DOM ({dom}d — fresh)", 8))
        elif dom <= 7:
            components.append((f"DOM ({dom}d)", 5))
        elif dom <= 14:
            components.append((f"DOM ({dom}d)", 2))
        elif dom > 60:
            components.append((f"DOM ({dom}d — stale)", -4))

    # 7. Price drop bonus (max 8 pts)
    history = listing.get("price_history") or []
    if isinstance(history, str):
        try:
            history = json.loads(history)
        except Exception:
            history = []
    if len(history) >= 2 and price > 0:
        original_price = history[0]["price"]
        if original_price and original_price > price:
            drop_pct = (original_price - price) / original_price * 100
            drop_amt = original_price - price
            if drop_pct >= 5:
                components.append((f"Price drop (↓${drop_amt:,})", 8))
            elif drop_pct >= 2:
                components.append((f"Price drop (↓${drop_amt:,})", 4))
    elif price > 0:
        mc_change = listing.get("price_change_percent")
        if mc_change is not None and mc_change < 0:
            drop_pct = abs(mc_change)
            if drop_pct >= 5:
                components.append((f"MC price cut ({drop_pct:.1f}%)", 8))
            elif drop_pct >= 2:
                components.append((f"MC price cut ({drop_pct:.1f}%)", 4))

    # 8. Source type
    if listing.get("pricing_type") == "no-haggle":
        components.append(("No-haggle pricing", -5))

    # 9. Cold Weather Group
    if listing.get("cold_weather_group"):
        components.append(("Cold Weather Group", COLD_WEATHER_GROUP_BONUS))

    # 10. Dealer rating
    rating = listing.get("dealer_rating") or 0
    if rating >= 4.5:
        components.append((f"Dealer ★{rating:.1f}", 2))
    elif 0 < rating < 3.5:
        components.append((f"Dealer ★{rating:.1f}", -3))

    return components


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
