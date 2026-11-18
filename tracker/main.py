"""Main orchestrator — runs all sources in parallel, merges, scores, and alerts."""

import logging
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

from tracker.alerts import (
    send_daily_digest,
    send_instant_alerts,
    send_low_inventory_warning,
    send_vehicle_anomaly_warning,
)
from tracker.config import CARFAX_ENABLED, SCORE_DAILY_DIGEST, SCORE_INSTANT_ALERT, VEHICLES
from tracker.scorer import compute_market_averages, market_avg_for, score_listing
from tracker.sources.carfax import fetch_carfax
from tracker.sources.marketcheck import fetch_marketcheck
from tracker.store import (
    get_alerted_vins,
    get_market_snapshot,
    get_recently_gone,
    get_stored_market_averages,
    get_vehicle_count_history,
    init_db,
    log_run,
    mark_alerted,
    merge_listings,
    update_scores,
    upsert_listings,
)

# A vehicle needs at least this average count over the lookback window before
# a 0 this run counts as an anomaly — avoids flagging vehicles that are
# legitimately, consistently thin on inventory (e.g. Outlander PHEV).
VEHICLE_ANOMALY_MIN_BASELINE = 3
VEHICLE_ANOMALY_LOOKBACK_RUNS = 7


def _detect_vehicle_anomalies(current_counts: dict[str, int]) -> list[dict]:
    history = get_vehicle_count_history(VEHICLE_ANOMALY_LOOKBACK_RUNS)
    anomalies = []
    for vehicle, past_counts in history.items():
        if not past_counts:
            continue
        baseline = sum(past_counts) / len(past_counts)
        if baseline >= VEHICLE_ANOMALY_MIN_BASELINE and current_counts.get(vehicle, 0) == 0:
            anomalies.append({
                "vehicle": vehicle,
                "baseline": baseline,
                "lookback": len(past_counts),
            })
    return anomalies

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)


def main() -> None:
    logger.info("Starting PHEV/4xe tracker run at %s", datetime.now(timezone.utc).isoformat())
    init_db()

    all_listings: list[dict] = []
    source_status: dict[str, bool] = {}
    errors: list[str] = []

    sources = {"marketcheck": fetch_marketcheck}
    if CARFAX_ENABLED:
        sources["carfax"] = fetch_carfax

    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(fn): name for name, fn in sources.items()}
        for future in as_completed(futures):
            name = futures[future]
            try:
                result = future.result()
                all_listings.extend(result)
                source_status[name] = True
                logger.info("  ✓ %s: %d listings", name, len(result))
            except Exception as e:
                source_status[name] = False
                err_msg = f"{name}: {e}"
                errors.append(err_msg)
                logger.error("  ✗ %s", err_msg)

    logger.info("Raw listings collected: %d", len(all_listings))

    # Merge and dedupe by VIN
    merged = merge_listings(all_listings)
    logger.info("After deduplication: %d unique VINs", len(merged))

    # Per-vehicle MarketCheck counts, for both the anomaly check below and the
    # history this check compares future runs against.
    # Every tracked vehicle gets an entry, so a vehicle stuck at 0 (e.g.
    # Outlander PHEV) is recorded and shows up in the digest instead of
    # silently missing.
    vehicle_counts: dict[str, int] = {f"{v['make']} {v['model_label']}": 0 for v in VEHICLES}
    for l in all_listings:
        if l.get("source") == "marketcheck":
            key = f"{l.get('make', '')} {l.get('model', '')}"
            vehicle_counts[key] = vehicle_counts.get(key, 0) + 1

    # Catches a single vehicle silently dropping to 0 (e.g. an MC model/
    # powertrain query breaking) even when the other tracked vehicles keep the
    # aggregate count healthy enough to pass the check below.
    anomalies = _detect_vehicle_anomalies(vehicle_counts)
    if anomalies:
        logger.warning("Vehicle count anomalies detected: %s", anomalies)
        send_vehicle_anomaly_warning(anomalies)

    # Sanity check: MarketCheck should return ≥10 listings under normal conditions.
    # Fewer usually means quota exhaustion or an API outage — warn and bail out.
    mc_count = sum(1 for l in all_listings if l.get("source") == "marketcheck")
    if mc_count < 10:
        logger.warning("Low MarketCheck count (%d) — sending warning email", mc_count)
        send_low_inventory_warning(mc_count, errors)
        log_run({"total": len(merged), "new": 0, "price_drops": 0, "alerts_sent": 0},
                source_status, errors, vehicle_counts)
        return

    # Upsert first: it attaches each listing's price history, which the
    # price-drop component needs. Scores are written back afterwards.
    stats = upsert_listings(merged)

    # Mileage-adjusted market model from this run's listings; falls back to
    # the last run's active listings if this run produced no usable prices.
    market_avgs = compute_market_averages(merged) or get_stored_market_averages()
    for listing in merged:
        listing["composite_score"] = score_listing(listing, market_avg_for(listing, market_avgs))
    update_scores(merged)

    already_alerted = get_alerted_vins()

    # Alert logic
    great_deals = [
        l for l in merged
        if (l.get("composite_score") or 0) >= SCORE_INSTANT_ALERT
        and l["vin"] not in already_alerted
    ]
    good_deals = [
        l for l in merged
        if (l.get("composite_score") or 0) >= SCORE_DAILY_DIGEST
    ]

    alerts_sent = 0
    if great_deals:
        logger.info("Sending instant alerts for %d great deals", len(great_deals))
        sent_vins = send_instant_alerts(great_deals, market_avgs)
        if sent_vins:
            mark_alerted(sent_vins)
        alerts_sent = len(sent_vins)
        stats["alerts_sent"] = alerts_sent

    snapshot = get_market_snapshot()
    snapshot["vehicle_counts"] = vehicle_counts
    gone = get_recently_gone(SCORE_DAILY_DIGEST)
    digest_sent = send_daily_digest(good_deals, market_avgs, snapshot, stats, source_status, gone)
    if digest_sent:
        logger.info("Daily digest sent with %d deals", len(good_deals))

    log_run(stats, source_status, errors, vehicle_counts)

    logger.info(
        "Done. %d listings, %d new, %d price drops, %d alerts sent.",
        stats.get("total", 0),
        stats.get("new", 0),
        stats.get("price_drops", 0),
        stats.get("alerts_sent", 0),
    )


if __name__ == "__main__":
    main()
