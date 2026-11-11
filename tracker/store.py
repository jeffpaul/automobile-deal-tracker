"""SQLite persistence — merge listings from all sources, track price history."""

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tracker.config import DB_PATH, HISTORY_OVERRIDES_PATH

logger = logging.getLogger(__name__)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS listings (
    vin                   TEXT PRIMARY KEY,
    sources               TEXT,
    year                  INTEGER,
    make                  TEXT,
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
    clean_title           INTEGER,
    one_owner             INTEGER DEFAULT 0,
    service_record_count  INTEGER DEFAULT 0,
    carfax_badge          TEXT,
    dealer_rating         REAL,
    cold_weather_group    INTEGER DEFAULT 0,
    has_blind_spot_mon    INTEGER DEFAULT 0,
    ref_price             REAL,
    price_change_percent  REAL,
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
    errors          TEXT,
    vehicle_counts  TEXT
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
    # Migrations for columns added after initial schema
    for col, typedef in [
        ("ref_price", "REAL"),
        ("price_change_percent", "REAL"),
        ("make", "TEXT"),
        ("clean_title", "INTEGER"),
    ]:
        try:
            conn.execute(f"ALTER TABLE listings ADD COLUMN {col} {typedef}")
        except sqlite3.OperationalError:
            pass  # Column already exists
    try:
        conn.execute("ALTER TABLE runs ADD COLUMN vehicle_counts TEXT")
    except sqlite3.OperationalError:
        pass  # Column already exists
    conn.commit()
    conn.close()


def _coalesce(*values):
    """Return first non-None, non-empty value."""
    for v in values:
        if v is not None and v != "":
            return v
    return None


HISTORY_OVERRIDE_FIELDS = ("no_accidents", "one_owner", "service_record_count", "carfax_badge")


def load_history_overrides() -> dict[str, dict]:
    """VIN -> verified history fields from data/history_overrides.json.

    MarketCheck's embedded CARFAX flags can be wrong (a listing flagged
    1-owner turned out to have 2 owners and 3 damage events), so a full
    report read by hand wins over any source signal.
    """
    try:
        with open(HISTORY_OVERRIDES_PATH) as f:
            data = json.load(f)
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as e:
        logger.error("Could not read history overrides: %s", e)
        return {}
    return {vin: v for vin, v in data.items() if not vin.startswith("_") and isinstance(v, dict)}


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
                "year", "make", "model", "trim", "mileage", "city", "state",
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

            # CARFAX signals — authoritative when present; don't OR-merge these
            # (a CARFAX no_accidents=0 must not be overridden by MC's carfax_clean_title=1)
            if item.get("source") == "carfax":
                for f in ("no_accidents", "one_owner", "carfax_badge"):
                    if item.get(f) is not None:
                        existing[f] = item[f]
            else:
                # For non-CARFAX sources, only promote if no CARFAX data seen yet
                if "carfax" not in (existing.get("_sources") or []):
                    for f in ("no_accidents", "one_owner"):
                        if item.get(f) is not None:
                            existing[f] = max(int(existing.get(f) or 0), int(item[f]))

            if item.get("clean_title") is not None:
                existing["clean_title"] = item["clean_title"]

            # OR-merge feature flags (union of equipment across sources is correct)
            for flag in ("cold_weather_group", "has_blind_spot_mon"):
                existing[flag] = max(int(existing.get(flag) or 0), int(item.get(flag) or 0))

            # pricing_type: no-haggle wins
            if item.get("pricing_type") == "no-haggle":
                existing["pricing_type"] = "no-haggle"

    # Finalize source list
    overrides = load_history_overrides()
    results = []
    for vin, merged in by_vin.items():
        merged["sources"] = json.dumps(sorted(set(merged.pop("_sources", []))))
        for f in HISTORY_OVERRIDE_FIELDS:
            if f in overrides.get(vin, {}):
                merged[f] = overrides[vin][f]
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
                    lst["_price_cut_from"] = old_price
            lst["price_history"] = json.dumps(price_history)
            lst["first_seen"] = row["first_seen"]

            conn.execute(
                """UPDATE listings SET
                    sources = ?, year = ?, make = ?, model = ?, trim = ?, price = ?,
                    mileage = ?, city = ?, state = ?, dealer_name = ?,
                    listing_url = ?, exterior_color = ?, days_on_market = ?,
                    pricing_type = ?, source_type = ?,
                    cargurus_deal_label = ?, cargurus_deal_score = ?,
                    cargurus_explanation = ?, truecar_market_price = ?,
                    no_accidents = ?, clean_title = ?, one_owner = ?, service_record_count = ?,
                    carfax_badge = ?, dealer_rating = ?,
                    cold_weather_group = ?, has_blind_spot_mon = ?,
                    ref_price = ?, price_change_percent = ?,
                    composite_score = ?, last_seen = ?, price_history = ?
                WHERE vin = ?""",
                (
                    lst.get("sources"),
                    lst.get("year"),
                    lst.get("make"),
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
                    lst.get("no_accidents") or 0,
                    lst.get("clean_title"),
                    lst.get("one_owner") or 0,
                    lst.get("service_record_count", 0),
                    lst.get("carfax_badge"),
                    lst.get("dealer_rating"),
                    lst.get("cold_weather_group", 0),
                    lst.get("has_blind_spot_mon", 0),
                    lst.get("ref_price"),
                    lst.get("price_change_percent"),
                    lst.get("composite_score"),
                    now,
                    json.dumps(price_history),
                    vin,
                ),
            )
        else:
            # New listing
            stats["new"] += 1
            lst["_is_new"] = True
            lst["price_history"] = "[]"
            lst["first_seen"] = now
            conn.execute(
                """INSERT INTO listings (
                    vin, sources, year, make, model, trim, price, mileage,
                    city, state, dealer_name, listing_url, exterior_color,
                    days_on_market, pricing_type, source_type,
                    cargurus_deal_label, cargurus_deal_score, cargurus_explanation,
                    truecar_market_price, no_accidents, clean_title, one_owner,
                    service_record_count, carfax_badge, dealer_rating,
                    cold_weather_group, has_blind_spot_mon,
                    ref_price, price_change_percent,
                    composite_score, first_seen, last_seen, price_history, alerted
                ) VALUES (
                    ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0
                )""",
                (
                    vin,
                    lst.get("sources"),
                    lst.get("year"),
                    lst.get("make"),
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
                    lst.get("no_accidents") or 0,
                    lst.get("clean_title"),
                    lst.get("one_owner") or 0,
                    lst.get("service_record_count", 0),
                    lst.get("carfax_badge"),
                    lst.get("dealer_rating"),
                    lst.get("cold_weather_group", 0),
                    lst.get("has_blind_spot_mon", 0),
                    lst.get("ref_price"),
                    lst.get("price_change_percent"),
                    lst.get("composite_score"),
                    now,
                    now,
                    json.dumps([]),
                ),
            )

    conn.commit()
    conn.close()
    return stats


def get_alerted_vins() -> set[str]:
    """Return the set of VINs that have already received an instant alert."""
    conn = _get_conn()
    rows = conn.execute("SELECT vin FROM listings WHERE alerted = 1").fetchall()
    conn.close()
    return {r["vin"] for r in rows}


def mark_alerted(vins: list[str]) -> None:
    conn = _get_conn()
    conn.executemany(
        "UPDATE listings SET alerted = 1 WHERE vin = ?",
        [(v,) for v in vins],
    )
    conn.commit()
    conn.close()


def update_scores(listings: list[dict[str, Any]]) -> None:
    """Write composite scores computed after upsert (scoring needs the
    price history upsert_listings() attaches)."""
    conn = _get_conn()
    conn.executemany(
        "UPDATE listings SET composite_score = ? WHERE vin = ?",
        [(l.get("composite_score"), l["vin"]) for l in listings],
    )
    conn.commit()
    conn.close()


# Every listing upserted in a run gets that run's exact last_seen timestamp,
# so "seen in the latest run" is last_seen == MAX(last_seen). Listings that
# have sold or been delisted keep an older last_seen forever.
_ACTIVE = "last_seen = (SELECT MAX(last_seen) FROM listings)"


def _run_timestamps(conn: sqlite3.Connection, n: int = 2) -> list[str]:
    rows = conn.execute(
        "SELECT DISTINCT last_seen FROM listings ORDER BY last_seen DESC LIMIT ?", (n,)
    ).fetchall()
    return [r[0] for r in rows]


def get_active_listings() -> list[dict]:
    conn = _get_conn()
    rows = conn.execute(f"SELECT * FROM listings WHERE {_ACTIVE}").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_recently_gone(min_score: float) -> list[dict]:
    """Listings seen in the previous run but not this one — sold or pulled —
    limited to ones worth mentioning (alerted, or scored >= min_score)."""
    conn = _get_conn()
    stamps = _run_timestamps(conn, 2)
    # If nothing dropped out last run, the second timestamp belongs to an
    # older run whose departures were already reported — skip it.
    if len(stamps) < 2 or (
        datetime.fromisoformat(stamps[0]) - datetime.fromisoformat(stamps[1])
    ).total_seconds() > 36 * 3600:
        conn.close()
        return []
    rows = conn.execute(
        "SELECT * FROM listings WHERE last_seen = ? AND (alerted = 1 OR composite_score >= ?) "
        "ORDER BY composite_score DESC",
        (stamps[1], min_score),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _price_on(price_history: list[dict], current_price: int, date: str) -> int:
    """Price as of `date` (YYYY-MM-DD). Each history entry records the price a
    listing changed *away from* on that date, so the price on `date` is the
    old price of the first change after it, or the current price if none."""
    for entry in price_history:
        if entry.get("date", "") > date:
            return entry.get("price") or current_price
    return current_price


def get_price_trend(days: int = 7) -> dict[tuple, dict[str, float | None]]:
    """Average price per (model, trim) today vs. N days ago, over the same
    cars: listings active now that were already listed N days ago. (The old
    version compared active listings against sold ones.)"""
    from datetime import timedelta
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()
    trend: dict[tuple, dict[str, list[int]]] = {}
    for r in get_active_listings():
        if not r.get("price") or (r.get("first_seen") or "")[:10] > cutoff:
            continue
        try:
            history = json.loads(r.get("price_history") or "[]")
        except Exception:
            history = []
        key = (r["model"], r["trim"])
        t = trend.setdefault(key, {"today": [], "then": []})
        t["today"].append(r["price"])
        t["then"].append(_price_on(history, r["price"], cutoff))
    return {
        key: {"today": sum(v["today"]) / len(v["today"]), "7d_ago": sum(v["then"]) / len(v["then"])}
        for key, v in trend.items()
    }


def log_run(
    stats: dict,
    source_status: dict,
    errors: list[str] | None = None,
    vehicle_counts: dict[str, int] | None = None,
) -> None:
    conn = _get_conn()
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """INSERT INTO runs (run_at, listings_found, new_listings, price_drops, alerts_sent, sources_ok, errors, vehicle_counts)
           VALUES (?,?,?,?,?,?,?,?)""",
        (
            now,
            stats.get("total", 0),
            stats.get("new", 0),
            stats.get("price_drops", 0),
            stats.get("alerts_sent", 0),
            json.dumps(source_status),
            json.dumps(errors or []),
            json.dumps(vehicle_counts or {}),
        ),
    )
    conn.commit()
    conn.close()


def get_vehicle_count_history(lookback: int = 7) -> dict[str, list[int]]:
    """Return recent per-vehicle MarketCheck counts (most recent first), keyed
    by "Make Model", for anomaly detection against the current run's counts."""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT vehicle_counts FROM runs WHERE vehicle_counts IS NOT NULL AND vehicle_counts != '' "
        "ORDER BY id DESC LIMIT ?",
        (lookback,),
    ).fetchall()
    conn.close()

    history: dict[str, list[int]] = {}
    for row in rows:
        try:
            counts = json.loads(row["vehicle_counts"] or "{}")
        except Exception:
            continue
        for key, count in counts.items():
            history.setdefault(key, []).append(count)
    return history


def get_stored_market_averages() -> dict:
    """Market model built from the listings active as of the last run."""
    from tracker.scorer import compute_market_averages
    return compute_market_averages([l for l in get_active_listings() if (l.get("price") or 0) > 5000])


def get_market_snapshot() -> dict:
    """Stats for the email market snapshot — active listings only."""
    from collections import defaultdict
    listings = get_active_listings()
    listings.sort(key=lambda l: l.get("composite_score") or 0, reverse=True)

    # Average price by (model, trim) — grouping by trim alone would blend
    # e.g. "SE" across Outlander PHEV, Tucson PHEV, and RAV4 Prime
    model_trim_prices: dict[tuple, list[int]] = defaultdict(list)
    model_prices: dict[str, list[int]] = defaultdict(list)
    for lst in listings:
        if lst.get("price"):
            if lst.get("trim"):
                model_trim_prices[(lst.get("model", ""), lst["trim"])].append(lst["price"])
            if lst.get("model"):
                model_prices[lst["model"]].append(lst["price"])

    return {
        "total": len(listings),
        "avg_by_model_trim": {k: int(sum(v) / len(v)) for k, v in model_trim_prices.items()},
        "verified_clean_count": sum(1 for l in listings if l.get("no_accidents") and l.get("one_owner")),
        "lowest_by_model": {m: min(p) for m, p in model_prices.items()},
    }
