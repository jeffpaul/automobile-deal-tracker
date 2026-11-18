"""Deal scoring — calibrated for winter daily driver use case, not off-road."""

import json
import re
from collections import defaultdict
from typing import Any

from tracker.config import CARVANA_DELIVERY_FEE

# Jeep 4xe trim weights: Sahara/High Altitude preferred; Willys/Rubicon penalized.
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

# Non-Jeep PHEVs ship AWD/S-AWC standard on every trim (unlike gas Wrangler
# trims where tire compound varies wildly), so the winter differentiation
# here is modest: wheel size (bigger wheels = lower-profile tires = worse
# snow traction, same logic as the Jeep Rubicon penalty) and which trims
# include heated seat/wheel packages standard vs. optional. This is a light
# nudge, not a hard cut — the Cold Weather Group component already rewards
# heated packages directly from CARFAX option data regardless of trim.
OUTLANDER_PHEV_TRIM_WEIGHTS: dict[str, float] = {
    "SE":  1.00,  # 18" wheels, heated front seats standard
    "SEL": 1.05,  # 18" wheels, heated seats + wheel standard
    "GT":  0.95,  # 20" wheels — lower-profile tires, less ideal for snow
}

TUCSON_PHEV_TRIM_WEIGHTS: dict[str, float] = {
    "SEL":     1.00,
    "LIMITED": 1.05,  # heated/ventilated seats, heated wheel, surround-view standard
}

# Grand Cherokee 4xe: Quadra-Trac 4x4 is standard on every 4xe trim, so like
# the non-Jeep PHEVs the winter nudge is mostly wheel/tire size. The Wrangler
# table above used to apply here too, which dropped every GC trim to the 0.90
# "unknown Jeep trim" default.
GRAND_CHEROKEE_4XE_TRIM_WEIGHTS: dict[str, float] = {
    "Base":           1.05,  # "4xe": 18" wheels, heated seats + wheel standard
    "Anniversary":    1.05,  # 30th Anniversary / Anniversary Edition: base + appearance
    "Overland":       1.05,  # Heated/ventilated seats, heated wheel; 20" wheels
    "Limited":        1.05,
    "Summit":         0.95,  # 21" wheels, low-profile tires, paying for luxury
    "Summit Reserve": 0.95,
    "Trailhawk":      0.95,  # All-terrain tires, off-road hardware unused here
}

RAV4_PRIME_TRIM_WEIGHTS: dict[str, float] = {
    "SE":  1.05,  # 18" wheels; optional Weather Package adds heated seats/wheel
    "XSE": 0.95,  # 19" wheels — lower-profile tires, less ideal for snow
}

COLD_WEATHER_GROUP_BONUS = 3  # pts: heated seats + wheel + remote start


def _trim_weight(model: str, trim: str) -> float:
    """Return the weight for a trim name, fuzzy-matching on partial strings.
    MarketCheck returns names like 'Sahara 4XE', 'High Altitude 4XE', etc.
    """
    lower = (trim or "").lower()
    tokens = set(re.split(r"[\s/-]+", lower))

    if model == "Grand Cherokee 4xe":
        w = GRAND_CHEROKEE_4XE_TRIM_WEIGHTS
        if "summit reserve" in lower:
            return w["Summit Reserve"]
        if "summit" in lower:
            return w["Summit"]
        if "trailhawk" in lower:
            return w["Trailhawk"]
        if "overland" in lower:
            return w["Overland"]
        if "limited" in lower:
            return w["Limited"]
        if "anniversary" in lower:
            return w["Anniversary"]
        if lower.strip() in ("4xe", "") or "laredo" in lower or "altitude" in lower:
            return w["Base"]
        return 1.00

    if model == "Wrangler 4xe":
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
        return 0.90  # Default for unknown Jeep trims

    if model == "Outlander PHEV":
        if "gt" in tokens:
            return OUTLANDER_PHEV_TRIM_WEIGHTS["GT"]
        if "sel" in tokens:
            return OUTLANDER_PHEV_TRIM_WEIGHTS["SEL"]
        if "se" in tokens:
            return OUTLANDER_PHEV_TRIM_WEIGHTS["SE"]
        return 1.00

    if model == "Tucson PHEV":
        if "limited" in tokens:
            return TUCSON_PHEV_TRIM_WEIGHTS["LIMITED"]
        if "sel" in tokens:
            return TUCSON_PHEV_TRIM_WEIGHTS["SEL"]
        return 1.00

    if model == "RAV4 Prime":
        if "xse" in tokens:
            return RAV4_PRIME_TRIM_WEIGHTS["XSE"]
        if "se" in tokens:
            return RAV4_PRIME_TRIM_WEIGHTS["SE"]
        return 1.00

    return 1.00  # Default for unrecognized model/trim combos


def effective_price(listing: dict[str, Any]) -> int:
    """Price used for scoring: asking price plus Carvana's delivery fee."""
    price = listing.get("price") or 0
    if price and "carvana" in (listing.get("dealer_name") or "").lower():
        price += CARVANA_DELIVERY_FEE
    return price


def _price_history(listing: dict[str, Any]) -> list[dict]:
    history = listing.get("price_history") or []
    if isinstance(history, str):
        try:
            history = json.loads(history)
        except Exception:
            history = []
    return history


def score_breakdown(listing: dict[str, Any], fair_price: float | None) -> list[tuple[str, float]]:
    """Every scoring component as (label, pts). score_listing() is their sum,
    so the email's score pills always add up to the score shown.

    fair_price is the mileage-adjusted market price from market_avg_for().
    """
    components: list[tuple[str, float]] = []
    price = effective_price(listing)

    # 1. Price vs. mileage-adjusted market price (max 35 pts). Comparing to a
    # plain trim/year average made cars cheap *because* of high mileage look
    # like the best deals.
    if fair_price and price > 0:
        pct_below = (fair_price - price) / fair_price * 100
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
            pts = -10  # Overpriced vs. market
        else:
            pts = 0
        if pts:
            word = "under" if pct_below >= 0 else "over"
            components.append((f"Price ({abs(pct_below):.1f}% {word} fair)", pts))

    # 2. Price vs. MarketCheck reference price (max 15 pts) — MC's per-car value.
    ref_price = listing.get("ref_price") or 0
    if ref_price > 0 and price > 0:
        pct_below_ref = (ref_price - price) / ref_price * 100
        if pct_below_ref >= 8:
            pts = 15
        elif pct_below_ref >= 5:
            pts = 11
        elif pct_below_ref >= 3:
            pts = 7
        elif pct_below_ref >= 1:
            pts = 3
        elif pct_below_ref < -5:
            pts = -5  # Significantly above MC reference
        else:
            pts = 0
        if pts:
            word = "under" if pct_below_ref >= 0 else "over"
            components.append((f"MC ref ({abs(pct_below_ref):.1f}% {word})", pts))

    # 3. History (max 15 pts). no_accidents is only set from CARFAX or a
    # hand-verified report; a clean title alone is worth less because it
    # doesn't rule out accidents.
    hist_pts = 0.0
    parts = []
    if listing.get("no_accidents"):
        hist_pts += 8
        parts.append("no accidents")
    if listing.get("one_owner"):
        hist_pts += 4
        parts.append("1 owner")
    if listing.get("clean_title") and not listing.get("no_accidents"):
        hist_pts += 2
        parts.append("clean title")
    badge = listing.get("carfax_badge") or ""
    if badge in ("Great Value", "GREAT"):
        hist_pts += 3
        parts.append("Great Value badge")
    elif badge in ("Good Value", "GOOD"):
        hist_pts += 1
        parts.append("Good Value badge")
    if hist_pts:
        components.append((f"History ({', '.join(parts)})", hist_pts))

    # 4. Trim value weight — winter/safety calibrated (max 12 pts)
    w = _trim_weight(listing.get("model", ""), listing.get("trim", ""))
    trim_pts = round((w - 0.75) * 30, 1)  # Maps 0.75–1.15 → 0–12 pts
    if trim_pts:
        components.append((f"Trim ({listing.get('trim') or '?'})", trim_pts))

    # 5. Mileage (max 12 pts)
    miles = listing.get("mileage")
    if miles is not None:
        if miles < 10000:
            pts = 12
        elif miles < 20000:
            pts = 8
        elif miles < 30000:
            pts = 4
        elif miles > 40000:
            pts = -4
        else:
            pts = 0
        if pts:
            components.append((f"Mileage ({miles:,} mi)", pts))
    else:
        components.append(("Mileage (unknown)", -4))

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

    # 7. Price drop (max 8 pts). price_history holds each *previous* price,
    # so a single entry already means one change — the first drop counts.
    # Falls back to MC's dealer-reported change on a first sighting.
    asking = listing.get("price") or 0
    history = _price_history(listing)
    drop_pct = None
    drop_label = ""
    if history and asking > 0:
        original = history[0].get("price") or 0
        if original > asking:
            drop_pct = (original - asking) / original * 100
            drop_label = f"Price drop (↓${original - asking:,})"
    elif asking > 0:
        mc_change = listing.get("price_change_percent")
        if mc_change is not None and mc_change < 0:
            drop_pct = abs(mc_change)
            drop_label = f"MC price cut ({drop_pct:.1f}%)"
    if drop_pct is not None:
        if drop_pct >= 5:
            components.append((drop_label, 8))
        elif drop_pct >= 2:
            components.append((drop_label, 4))

    # 8. No-haggle (CarMax, Carvana): can't negotiate below the listing
    if listing.get("pricing_type") == "no-haggle":
        components.append(("No-haggle pricing", -5))

    # 9. Cold Weather Group — practical for Chicago winters
    if listing.get("cold_weather_group"):
        components.append(("Cold Weather Group", COLD_WEATHER_GROUP_BONUS))

    # 10. Dealer rating
    rating = listing.get("dealer_rating") or 0
    if rating >= 4.5:
        components.append((f"Dealer ★{rating:.1f}", 2))
    elif 0 < rating < 3.5:
        components.append((f"Dealer ★{rating:.1f}", -3))

    return components


def score_listing(listing: dict[str, Any], fair_price: float | None) -> float:
    score = sum(pts for _, pts in score_breakdown(listing, fair_price))
    return round(min(max(score, 0), 100), 1)


def is_winter_penalized_trim(model: str, trim: str) -> bool:
    """Return True if trim has M/T or A/T tires unsuitable for winter pavement.

    Jeep-specific: the non-Jeep PHEVs' trim differences (wheel size) are a
    much smaller winter penalty and don't warrant this callout treatment.
    """
    if model not in ("Wrangler 4xe", "Grand Cherokee 4xe"):
        return False
    lower = (trim or "").lower()
    return "willys" in lower or ("rubicon" in lower and "rubicon x" not in lower)


# A group average built from fewer listings than this is mostly the car's own
# price, so the lookup falls back to a wider grouping instead.
MARKET_AVG_MIN_SAMPLES = 5

# $/mile depreciation used when a model has too few listings to estimate its
# own (about what Wrangler 4xe and Grand Cherokee 4xe showed in Nov 2026:
# ~$1,200–1,300 per 10k miles). Estimates are clamped to a sane range.
DEFAULT_DOLLARS_PER_MILE = -0.12
MIN_SLOPE_SAMPLES = 10
SLOPE_BOUNDS = (-0.30, 0.0)


def _market_avg_keys(model: str, trim: str, year: Any) -> list[tuple]:
    """Lookup order for a listing's market average, narrowest first.

    Trim+year is the true comparable. Trim across years comes before
    model+year because the trim gap (e.g. Sport S vs. Rubicon X) is usually
    wider than the year gap.
    """
    return [(model, trim, year), (model, trim), (model, year), (model,)]


def _mileage_slopes(listings: list[dict]) -> dict[str, float]:
    """Per-model $/mile, from a regression of price on mileage *within* each
    (model, trim, year) group — so trim and year differences don't leak into
    the mileage effect."""
    groups: dict[tuple, list[tuple[float, float]]] = defaultdict(list)
    for lst in listings:
        price = lst.get("price") or 0
        miles = lst.get("mileage")
        if price > 5000 and miles is not None:
            groups[(lst.get("model", ""), lst.get("trim", ""), lst.get("year"))].append((miles, price))

    sxy: dict[str, float] = defaultdict(float)
    sxx: dict[str, float] = defaultdict(float)
    n: dict[str, int] = defaultdict(int)
    for (model, _, _), pts in groups.items():
        if len(pts) < 2:
            continue
        m_bar = sum(m for m, _ in pts) / len(pts)
        p_bar = sum(p for _, p in pts) / len(pts)
        for m, p in pts:
            sxy[model] += (m - m_bar) * (p - p_bar)
            sxx[model] += (m - m_bar) ** 2
        n[model] += len(pts)

    slopes = {}
    for model in {lst.get("model", "") for lst in listings}:
        if n[model] >= MIN_SLOPE_SAMPLES and sxx[model] > 0:
            slope = sxy[model] / sxx[model]
            slopes[model] = min(max(slope, SLOPE_BOUNDS[0]), SLOPE_BOUNDS[1])
        else:
            slopes[model] = DEFAULT_DOLLARS_PER_MILE
    return slopes


def compute_market_averages(listings: list[dict]) -> dict[str, dict]:
    """Market model for price scoring: mean price and mean mileage per
    grouping in _market_avg_keys, plus a $/mile slope per model.

    Only groupings with at least MARKET_AVG_MIN_SAMPLES listings are kept,
    except the model-wide one, which needs 2 (the last resort for thin
    vehicles like Tucson PHEV).
    """
    prices: dict[tuple, list[int]] = defaultdict(list)
    miles: dict[tuple, list[int]] = defaultdict(list)
    for lst in listings:
        price = lst.get("price") or 0
        if price > 5000:  # Sanity filter
            for key in _market_avg_keys(lst.get("model", ""), lst.get("trim", ""), lst.get("year")):
                prices[key].append(price)
                if lst.get("mileage") is not None:
                    miles[key].append(lst["mileage"])

    groups = {
        key: {
            "price": sum(p) / len(p),
            "miles": sum(miles[key]) / len(miles[key]) if miles[key] else None,
            "n": len(p),
        }
        for key, p in prices.items()
        if len(p) >= (2 if len(key) == 1 else MARKET_AVG_MIN_SAMPLES)
    }
    if not groups:
        return {}
    return {"groups": groups, "slopes": _mileage_slopes(listings)}


def market_avg_for(listing: dict[str, Any], market: dict[str, dict]) -> float | None:
    """Mileage-adjusted fair price for this listing: the narrowest group's
    average price, moved by the model's $/mile for the difference between
    this car's mileage and the group's average mileage."""
    if not market:
        return None
    model = listing.get("model", "")
    for key in _market_avg_keys(model, listing.get("trim", ""), listing.get("year")):
        group = market["groups"].get(key)
        if not group:
            continue
        fair = group["price"]
        car_miles = listing.get("mileage")
        if car_miles is not None and group["miles"] is not None:
            slope = market["slopes"].get(model, DEFAULT_DOLLARS_PER_MILE)
            fair += slope * (car_miles - group["miles"])
        return max(fair, 1.0)
    return None
