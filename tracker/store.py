"""SQLite persistence — merge listings from all sources, track price history."""

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tracker.config import DB_PATH

logger = logging.getLogger(__name__)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS listings (
    vin                   TEXT PRIMARY KEY,
    sources               TEXT,
    year                  INTEGER,
    model                 TEXT,
    trim                  TEXT,
    price                 INTEGER,
    mileage               INTEGER,
    city                  TEXT,
    state                 TEXT,
    dealer_name           TEXT,
    listing_url           TEXT,
    exterior_color        TEXT,
    days_on_market        INTEGER,
    pricing_type          TEXT,
    source_type           TEXT,
    cargurus_deal_label   TEXT,
    cargurus_deal_score   REAL,
    cargurus_explanation  TEXT,
    truecar_market_price  INTEGER,
    no_accidents          INTEGER DEFAULT 0,
    one_owner             INTEGER DEFAULT 0,
    service_record_count  INTEGER DEFAULT 0,
    carfax_badge          TEXT,
    dealer_rating         REAL,
    cold_weather_group    INTEGER DEFAULT 0,
    has_blind_spot_mon    INTEGER DEFAULT 0,
    composite_score       REAL,
    first_seen            TEXT,
    last_seen             TEXT,
    price_history         TEXT,
    alerted               INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_at          TEXT,
    listings_found  INTEGER,
    new_listings    INTEGER,
    price_drops     INTEGER,
    alerts_sent     INTEGER,
    sources_ok      TEXT,
    errors          TEXT
);
"""


def _get_conn() -> sqlite3.Connection:
    path = Path(DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db() -> None:
    conn = _get_conn()
    conn.executescript(SCHEMA_SQL)
    conn.commit()
    conn.close()


def _coalesce(*values):
    """Return first non-None, non-empty value."""
    for v in values:
        if v is not None and v != "":
            return v
    return None


def merge_listings(raw_listings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Deduplicate by VIN. Multiple source records for the same VIN are merged
    by taking the best available data from each source.
    """
    by_vin: dict[str, dict] = {}

    for item in raw_listings:
        vin = item.get("vin", "").strip()
        if not vin:
            continue

        if vin not in by_vin:
            by_vin[vin] = dict(item)
            by_vin[vin]["_sources"] = [item.get("source", "unknown")]
        else:
            existing = by_vin[vin]
            existing["_sources"].append(item.get("source", "unknown"))

            # Merge: prefer non-None values; for scalars prefer later source if richer
            for field in (
                "year", "model", "trim", "mileage", "city", "state",
                "dealer_name", "exterior_color", "days_on_market", "dealer_rating",
            ):
                existing[field] = _coalesce(existing.get(field), item.get(field))

            # Price: take lower (better deal) but not zero
            for p in (existing.get("price"), item.get("price")):
                if p and p > 1000:
                    existing["price"] = min(filter(None, [existing.get("price"), p]))
                    break

            # Listing URL: prefer the first non-empty
            if not existing.get("listing_url") and item.get("listing_url"):
                existing["listing_url"] = item["listing_url"]

            # CarGurus signals (authoritative from cargurus source)
            if item.get("source") == "cargurus":
                for f in ("cargurus_deal_label", "cargurus_deal_score", "cargurus_explanation"):
                    if item.get(f):
                        existing[f] = item[f]

            # TrueCar market price
            if item.get("truecar_market_price"):
                existing["truecar_market_price"] = item["truecar_market_price"]

            # CARFAX signals (authoritative from carfax source)
            if item.get("source") == "carfax":
                for f in ("no_accidents", "one_owner", "service_record_count", "carfax_badge"):
                    if item.get(f) is not None:
                        existing[f] = item[f]

            # OR-merge boolean signals
            for flag in ("cold_weather_group", "has_blind_spot_mon", "no_accidents", "one_owner"):
                existing[flag] = max(int(existing.get(flag) or 0), int(item.get(flag) or 0))

            # pricing_type: no-haggle wins
            if item.get("pricing_type") == "no-haggle":
                existing["pricing_type"] = "no-haggle"

    # Finalize source list
    results = []
    for vin, merged in by_vin.items():
        merged["sources"] = json.dumps(sorted(set(merged.pop("_sources", []))))
        results.append(merged)

    return results


def upsert_listings(merged: list[dict[str, Any]]) -> dict[str, int]:
    """Upsert merged listings into SQLite. Returns run stats."""
    conn = _get_conn()
    now = datetime.now(timezone.utc).isoformat()
    stats = {"total": len(merged), "new": 0, "price_drops": 0, "alerts_sent": 0}

    for lst in merged:
        vin = lst["vin"]
        row = conn.execute("SELECT * FROM listings WHERE vin = ?", (vin,)).fetchone()

        price_history: list[dict] = []
        if row:
            # Existing record
            try:
                price_history = json.loads(row["price_history"] or "[]")
            except Exception:
                price_history = []

            old_price = row["price"]
            new_price = lst.get("price")
            if old_price and new_price and new_price != old_price:
                price_history.append({"date": now[:10], "price": old_price})
                if new_price < old_price:
                    stats["price_drops"] += 1

            conn.execute(
                """UPDATE listings SET
                    sources = ?, year = ?, model = ?, trim = ?, price = ?,
                    mileage = ?, city = ?, state = ?, dealer_name = ?,
                    listing_url = ?, exterior_color = ?, days_on_market = ?,
                    pricing_type = ?, source_type = ?,
                    cargurus_deal_label = ?, cargurus_deal_score = ?,
                    cargurus_explanation = ?, truecar_market_price = ?,
                    no_accidents = ?, one_owner = ?, service_record_count = ?,
                    carfax_badge = ?, dealer_rating = ?,
                    cold_weather_group = ?, has_blind_spot_mon = ?,
                    composite_score = ?, last_seen = ?, price_history = ?
                WHERE vin = ?""",
                (
                    lst.get("sources"),
                    lst.get("year"),
                    lst.get("model"),
                    lst.get("trim"),
                    lst.get("price"),
                    lst.get("mileage"),
                    lst.get("city"),
                    lst.get("state"),
                    lst.get("dealer_name"),
                    lst.get("listing_url"),
                    lst.get("exterior_color"),
                    lst.get("days_on_market"),
                    lst.get("pricing_type", "negotiable"),
                    lst.get("source_type", "dealer"),
                    lst.get("cargurus_deal_label"),
                    lst.get("cargurus_deal_score"),
                    lst.get("cargurus_explanation"),
                    lst.get("truecar_market_price"),
                    lst.get("no_accidents", 0),
                    lst.get("one_owner", 0),
                    lst.get("service_record_count", 0),
                    lst.get("carfax_badge"),
                    lst.get("dealer_rating"),
                    lst.get("cold_weather_group", 0),
                    lst.get("has_blind_spot_mon", 0),
                    lst.get("composite_score"),
                    now,
                    json.dumps(price_history),
                    vin,
                ),
            )
        else:
            # New listing
            stats["new"] += 1
            conn.execute(
                """INSERT INTO listings (
                    vin, sources, year, model, trim, price, mileage,
                    city, state, dealer_name, listing_url, exterior_color,
                    days_on_market, pricing_type, source_type,
                    cargurus_deal_label, cargurus_deal_score, cargurus_explanation,
                    truecar_market_price, no_accidents, one_owner,
                    service_record_count, carfax_badge, dealer_rating,
                    cold_weather_group, has_blind_spot_mon, composite_score,
                    first_seen, last_seen, price_history, alerted
                ) VALUES (
                    ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0
                )""",
                (
                    vin,
                    lst.get("sources"),
                    lst.get("year"),
                    lst.get("model"),
                    lst.get("trim"),
                    lst.get("price"),
                    lst.get("mileage"),
                    lst.get("city"),
                    lst.get("state"),
                    lst.get("dealer_name"),
                    lst.get("listing_url"),
                    lst.get("exterior_color"),
                    lst.get("days_on_market"),
                    lst.get("pricing_type", "negotiable"),
                    lst.get("source_type", "dealer"),
                    lst.get("cargurus_deal_label"),
                    lst.get("cargurus_deal_score"),
                    lst.get("cargurus_explanation"),
                    lst.get("truecar_market_price"),
                    lst.get("no_accidents", 0),
                    lst.get("one_owner", 0),
                    lst.get("service_record_count", 0),
                    lst.get("carfax_badge"),
                    lst.get("dealer_rating"),
                    lst.get("cold_weather_group", 0),
                    lst.get("has_blind_spot_mon", 0),
                    lst.get("composite_score"),
                    now,
                    now,
                    json.dumps([]),
                ),
            )

    conn.commit()
    conn.close()
    return stats


def mark_alerted(vins: list[str]) -> None:
    conn = _get_conn()
    conn.executemany(
        "UPDATE listings SET alerted = 1 WHERE vin = ?",
        [(v,) for v in vins],
    )
    conn.commit()
    conn.close()


def get_unalerted_great_deals(min_score: float = 80.0) -> list[dict]:
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM listings WHERE composite_score >= ? AND alerted = 0 ORDER BY composite_score DESC",
        (min_score,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_price_trend(days: int = 7) -> dict[str, float | None]:
    """Return average price per trim today vs. N days ago."""
    from datetime import timedelta
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    conn = _get_conn()
    rows = conn.execute(
        "SELECT trim, price, last_seen FROM listings WHERE price IS NOT NULL"
    ).fetchall()
    conn.close()

    today_prices: dict[str, list[float]] = {}
    old_prices: dict[str, list[float]] = {}
    for r in rows:
        if r["last_seen"] >= cutoff:
            today_prices.setdefault(r["trim"], []).append(r["price"])
        else:
            old_prices.setdefault(r["trim"], []).append(r["price"])

    trend = {}
    for trim in set(list(today_prices.keys()) + list(old_prices.keys())):
        today_avg = sum(today_prices.get(trim, [])) / len(today_prices[trim]) if today_prices.get(trim) else None
        old_avg = sum(old_prices.get(trim, [])) / len(old_prices[trim]) if old_prices.get(trim) else None
        trend[trim] = {"today": today_avg, "7d_ago": old_avg}
    return trend


def log_run(stats: dict, source_status: dict, errors: list[str] | None = None) -> None:
    conn = _get_conn()
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """INSERT INTO runs (run_at, listings_found, new_listings, price_drops, alerts_sent, sources_ok, errors)
           VALUES (?,?,?,?,?,?,?)""",
        (
            now,
            stats.get("total", 0),
            stats.get("new", 0),
            stats.get("price_drops", 0),
            stats.get("alerts_sent", 0),
            json.dumps(source_status),
            json.dumps(errors or []),
        ),
    )
    conn.commit()
    conn.close()


def get_market_snapshot() -> dict:
    """Return stats for the email market snapshot section."""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM listings WHERE price IS NOT NULL ORDER BY composite_score DESC"
    ).fetchall()
    conn.close()

    listings = [dict(r) for r in rows]
    total = len(listings)

    # Average price by trim
    from collections import defaultdict
    trim_prices: dict[str, list[int]] = defaultdict(list)
    for lst in listings:
        if lst.get("trim") and lst.get("price"):
            trim_prices[lst["trim"]].append(lst["price"])

    avg_by_trim = {
        trim: int(sum(prices) / len(prices))
        for trim, prices in trim_prices.items()
    }

    # CarGurus great/good deal count
    great_good = sum(
        1 for l in listings
        if l.get("cargurus_deal_label") in ("Great Deal", "Good Deal")
    )

    # Lowest Sahara or High Altitude
    preferred_trims = [l for l in listings if "sahara" in (l.get("trim") or "").lower() or "high altitude" in (l.get("trim") or "").lower()]
    lowest_preferred = min((l["price"] for l in preferred_trims if l.get("price")), default=None)

    return {
        "total": total,
        "avg_by_trim": avg_by_trim,
        "great_good_deal_count": great_good,
        "lowest_sahara_high_altitude": lowest_preferred,
    }
