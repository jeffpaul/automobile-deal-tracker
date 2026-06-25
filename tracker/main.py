"""Main orchestrator — runs all sources in parallel, merges, scores, and alerts."""

import logging
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

from tracker.alerts import send_daily_digest, send_instant_alerts
from tracker.config import SCORE_DAILY_DIGEST, SCORE_INSTANT_ALERT
from tracker.scorer import compute_market_averages, score_listing
from tracker.sources.carapis import fetch_carapis as _fetch_carapis_all
from tracker.sources.carfax import fetch_carfax
from tracker.sources.enterprise import fetch_enterprise
from tracker.sources.marketcheck import fetch_marketcheck
from tracker.store import (
    get_market_snapshot,
    get_stored_market_averages,
    init_db,
    log_run,
    mark_alerted,
    merge_listings,
    upsert_listings,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)


def main() -> None:
    logger.info("Starting Jeep 4xe tracker run at %s", datetime.now(timezone.utc).isoformat())
    init_db()

    all_listings: list[dict] = []
    source_status: dict[str, bool] = {}
    errors: list[str] = []

    sources = {
        "marketcheck": fetch_marketcheck,
        "carapis": _fetch_carapis_all,   # unified: covers AutoTrader US, Cars.com, Carvana
        "carfax": fetch_carfax,
        # Enterprise last — scraper, most fragile
        "enterprise": fetch_enterprise,
    }

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

    # Merge, dedupe, score
    merged = merge_listings(all_listings)
    logger.info("After deduplication: %d unique VINs", len(merged))

    # Compute averages from this run's data, falling back to stored DB averages
    # so scoring works even when a source returns nothing new.
    market_avgs = compute_market_averages(merged)
    if not market_avgs:
        market_avgs = get_stored_market_averages()
    else:
        # Merge with stored averages for any (model, trim) combos not in this run
        stored = get_stored_market_averages()
        for k, v in stored.items():
            if k not in market_avgs:
                market_avgs[k] = v

    for listing in merged:
        avg_key = (listing.get("model", ""), listing.get("trim", ""))
        listing["composite_score"] = score_listing(listing, market_avgs.get(avg_key))

    stats = upsert_listings(merged)

    # Alert logic
    great_deals = [
        l for l in merged
        if (l.get("composite_score") or 0) >= SCORE_INSTANT_ALERT and not l.get("alerted")
    ]
    good_deals = [
        l for l in merged
        if (l.get("composite_score") or 0) >= SCORE_DAILY_DIGEST
    ]

    alerts_sent = 0
    if great_deals:
        logger.info("Sending instant alerts for %d great deals", len(great_deals))
        alerts_sent = send_instant_alerts(great_deals, market_avgs)
        if alerts_sent:
            mark_alerted([l["vin"] for l in great_deals[:alerts_sent]])
        stats["alerts_sent"] = alerts_sent

    snapshot = get_market_snapshot()
    digest_sent = send_daily_digest(good_deals, market_avgs, snapshot, stats, source_status)
    if digest_sent:
        logger.info("Daily digest sent with %d deals", len(good_deals))

    log_run(stats, source_status, errors)

    logger.info(
        "Done. %d listings, %d new, %d price drops, %d alerts sent.",
        stats.get("total", 0),
        stats.get("new", 0),
        stats.get("price_drops", 0),
        stats.get("alerts_sent", 0),
    )


if __name__ == "__main__":
    main()
