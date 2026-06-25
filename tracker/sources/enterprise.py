"""Enterprise Car Sales — Playwright scraper for ex-rental fleet vehicles."""

import logging
import random
import re
import time
from typing import Any

from tracker.config import YEAR_MIN

logger = logging.getLogger(__name__)

SEARCH_URLS = [
    "https://www.enterprisecarsales.com/search?search=Jeep+Wrangler+4xe",
    "https://www.enterprisecarsales.com/search?search=Jeep+Grand+Cherokee+4xe",
]

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)


def _parse_price(text: str) -> int | None:
    digits = re.sub(r"[^\d]", "", text)
    return int(digits) if digits else None


def _parse_mileage(text: str) -> int | None:
    digits = re.sub(r"[^\d]", "", text)
    return int(digits) if digits else None


def _scrape_url(page: Any, url: str) -> list[dict]:
    results = []
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        time.sleep(random.uniform(2, 4))

        # Try to wait for vehicle cards
        try:
            page.wait_for_selector(".vehicle-card", timeout=15000)
        except Exception:
            # Fallback selectors
            try:
                page.wait_for_selector("[data-qa='vehicle-card']", timeout=5000)
            except Exception:
                logger.warning("Enterprise: no vehicle cards found at %s", url)
                return []

        # Collect card elements
        cards = page.query_selector_all(".vehicle-card, [data-qa='vehicle-card'], .car-card")
        logger.info("Enterprise: found %d cards at %s", len(cards), url)

        for card in cards:
            try:
                text = card.inner_text()
                lines = [l.strip() for l in text.splitlines() if l.strip()]

                title_el = card.query_selector("h2, h3, .title, .vehicle-title, [data-qa='vehicle-title']")
                title = title_el.inner_text().strip() if title_el else ""

                # Extract year from title or lines
                year_match = re.search(r"\b(202[3-9]|2025)\b", title + " " + " ".join(lines))
                year = int(year_match.group(1)) if year_match else None
                if year is None or year < YEAR_MIN:
                    continue

                # Skip non-4xe
                combined = (title + " " + " ".join(lines)).lower()
                if "4xe" not in combined:
                    continue

                trim_match = re.search(r"\b(sahara|rubicon|willys|high altitude|sport s?|sport)\b", combined, re.I)
                trim = trim_match.group(0).title() if trim_match else ""

                price_el = card.query_selector(".price, .vehicle-price, [data-qa='price']")
                price_text = price_el.inner_text() if price_el else ""
                price = _parse_price(price_text)

                miles_el = card.query_selector(".mileage, .miles, [data-qa='mileage']")
                miles_text = miles_el.inner_text() if miles_el else ""
                mileage = _parse_mileage(miles_text)
                if mileage is None:
                    mi_match = re.search(r"([\d,]+)\s*mi", combined)
                    mileage = _parse_mileage(mi_match.group(1)) if mi_match else None

                link_el = card.query_selector("a")
                href = link_el.get_attribute("href") if link_el else ""
                if href and not href.startswith("http"):
                    href = "https://www.enterprisecarsales.com" + href

                location_el = card.query_selector(".location, .dealer-location, [data-qa='location']")
                location_text = location_el.inner_text().strip() if location_el else ""
                city = state = ""
                if "," in location_text:
                    parts = location_text.split(",")
                    city = parts[0].strip()
                    state = parts[1].strip()

                model = "Grand Cherokee 4xe" if "grand cherokee" in combined else "Wrangler 4xe"

                results.append({
                    "vin": "",  # Enterprise rarely exposes VIN in card; use URL as fallback key
                    "_enterprise_url": href,
                    "source": "enterprise",
                    "year": year,
                    "model": model,
                    "trim": trim,
                    "price": price,
                    "mileage": mileage,
                    "city": city,
                    "state": state,
                    "dealer_name": "Enterprise Car Sales",
                    "listing_url": href,
                    "exterior_color": "",
                    "days_on_market": None,
                    "pricing_type": "negotiable",
                    "source_type": "rental-fleet",
                    "cold_weather_group": 0,
                    "has_blind_spot_mon": 0,
                })
            except Exception as card_err:
                logger.debug("Enterprise: error parsing card: %s", card_err)

    except Exception as e:
        logger.error("Enterprise scrape failed for %s: %s", url, e)

    return results


def fetch_enterprise() -> list[dict[str, Any]]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.warning("Playwright not installed — skipping Enterprise")
        return []

    results = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(user_agent=USER_AGENT)
            page = context.new_page()

            for url in SEARCH_URLS:
                batch = _scrape_url(page, url)
                results.extend(batch)
                if url != SEARCH_URLS[-1]:
                    time.sleep(random.uniform(1, 3))

            browser.close()
    except Exception as e:
        logger.error("Enterprise Playwright session failed: %s", e)

    # Use listing URL as a pseudo-VIN for deduplication when VIN is missing
    for r in results:
        if not r.get("vin") and r.get("_enterprise_url"):
            # Hash the URL into a synthetic identifier
            url_id = r["_enterprise_url"].rstrip("/").split("/")[-1]
            r["vin"] = f"ENT-{url_id}"
        r.pop("_enterprise_url", None)

    logger.info("Enterprise: fetched %d listings", len(results))
    return results
