# Jeep 4xe Deal Tracker

Automated deal tracker for Jeep Wrangler 4xe and Grand Cherokee 4xe (2023–2025), tuned for **winter daily driver** use in the Chicago area. Runs 3× daily via GitHub Actions, pulls inventory from MarketCheck and CARFAX, scores each listing 0–100, and emails alerts for great deals.

## Scoring rationale

Calibrated for a teen driver in Chicago winter conditions — **not** off-road capability. The Sahara and High Altitude trims score highest because they ship with all-season tires, blind spot monitoring, and on-road suspension. The Willys and Rubicon are penalized because their mud-terrain tires perform worse on packed snow and ice.

### Score components (max 100 pts)

| Component | Max pts | Signal |
|-----------|---------|--------|
| Price vs. market average | 35 | How far below the average price for this trim |
| Price vs. MC reference price | 15 | MarketCheck's per-car market value estimate |
| CARFAX history | 15 | No accidents (+8), 1 owner (+4), Great Value badge (+3) |
| Trim | 12 | High Altitude / Sahara preferred; Willys / Rubicon penalized |
| Mileage | 12 | Under 10k (+12) down to over 40k (−4) |
| Days on market | 8 | Fresh listing (+8); stale 60d+ (−4) |
| Price drop | 8 | Dealer cut or DB-tracked drop since first seen |
| Cold Weather Group | 3 | Heated seats + wheel + remote start (from CARFAX options) |
| Dealer rating | 2 | ≥4.5 stars (+2); <3.5 stars (−3) |
| Pricing type | −5 | No-haggle listings (CarMax, Carvana) penalized |

### Alert thresholds

| Score | Action |
|-------|--------|
| ≥ 65 | Instant email — fires once per VIN |
| ≥ 45 | Included in daily digest |

## Setup

### 1. Clone and install

```bash
git clone https://github.com/YOUR_USERNAME/jeep-4xe-search
cd jeep-4xe-search
pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env with your API keys and email credentials
```

Required credentials:

| Variable | Where to get it |
|----------|----------------|
| `MARKETCHECK_API_KEY` | [marketcheck.com](https://www.marketcheck.com) developer portal — Basic plan ($299/mo) or higher |
| `APIFY_API_TOKEN` | [apify.com](https://apify.com) — uses `parseforge/carfax-scraper` actor |
| `ALERT_EMAIL_TO` | Destination email address for alerts |
| `ALERT_EMAIL_FROM` | Sending address (Gmail recommended) |
| `SMTP_HOST` | SMTP server (default: `smtp.gmail.com`) |
| `SMTP_PORT` | SMTP port (default: `587`) |
| `SMTP_USER` | SMTP username |
| `SMTP_PASSWORD` | Gmail App Password (16 chars) — [myaccount.google.com > Security > App passwords](https://myaccount.google.com/apppasswords) |

### 3. Run locally

```bash
python -m tracker.main
```

### 4. Deploy to GitHub Actions

1. Push to GitHub
2. Go to **Settings → Secrets and variables → Actions**
3. Add all variables from `.env.example` as repository secrets
4. The workflow runs automatically at **8am, 2pm, and 8pm Central time**

## Architecture

```
tracker/
├── config.py          — All env vars and constants
├── main.py            — Orchestrator: parallel fetch → merge → score → alert
├── scorer.py          — Composite 0–100 scoring (winter-calibrated)
├── store.py           — SQLite persistence, VIN-keyed merge/dedup logic
├── alerts.py          — HTML email formatting and SMTP sending
└── sources/
    ├── marketcheck.py — MarketCheck REST API (primary inventory source)
    └── carfax.py      — CARFAX via Apify actor (history signals + badge)
```

### Data sources

**MarketCheck** (primary) — provides price, trim, mileage, days on market, dealer info, `ref_price` (market value estimate), `price_change_percent`, and embedded CARFAX signals (`carfax_1_owner`, `carfax_clean_title`) on every listing.

**CARFAX via Apify** (`parseforge/carfax-scraper`) — provides richer history signals: `noAccidents`, `oneOwner`, CARFAX badge (Great/Good Value), and option lists used to detect Cold Weather Group equipment. Queries `"Wrangler"` and `"Grand Cherokee"` (CARFAX doesn't index "4xe" as a model) and filters for 4xe trims post-fetch.

### Deduplication

Listings are keyed by VIN. When the same car appears in both sources, records are merged — MC provides pricing/market signals, CARFAX provides history signals. CARFAX data is authoritative for `no_accidents` and `one_owner` when present.

## Database

SQLite at `data/listings.db`. Persisted between GitHub Actions runs via Actions cache + git commit (`[skip ci]`). Key tables:

- `listings` — one row per VIN, merged from all sources, with full price history as JSON
- `runs` — one row per tracker run with stats and per-source health flags

## Customizing

Key constants in [`tracker/config.py`](tracker/config.py):

| Constant | Default | Description |
|----------|---------|-------------|
| `SEARCH_ZIP` | `60515` | Center of search radius (Downers Grove, IL) |
| `SEARCH_RADIUS_MILES` | `100` | Search radius |
| `YEAR_MIN` / `YEAR_MAX` | `2023` / `2025` | Model year range |
| `SCORE_INSTANT_ALERT` | `65` | Minimum score for instant email |
| `SCORE_DAILY_DIGEST` | `45` | Minimum score for digest inclusion |

Trim scoring weights are in [`tracker/scorer.py`](tracker/scorer.py) (`TRIM_VALUE_WEIGHTS`).
