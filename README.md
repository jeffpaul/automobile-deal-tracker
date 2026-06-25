# Jeep 4xe Deal Tracker

Automated deal tracker for Jeep Wrangler 4xe and Grand Cherokee 4xe (2023–2025), tuned for **winter daily driver** use in the Chicago area. Runs 3× daily via GitHub Actions, pulls inventory from 9 sources, scores each listing, and emails alerts for great deals.

## Scoring rationale

This tracker is calibrated for a teen driver in Chicago winter conditions, **not** off-road capability. The Sahara and High Altitude trim consistently score highest because they ship with all-season tires, blind spot monitoring, and on-road suspension. The Willys and Rubicon are penalized because their mud-terrain tires perform worse on packed snow and ice.

## Setup

### 1. Clone and install

```bash
git clone https://github.com/YOUR_USERNAME/jeep-4xe-search
cd jeep-4xe-search
pip install -r requirements.txt
playwright install chromium
```

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env with your API keys and email credentials
```

Required credentials:
| Variable | Where to get it |
|----------|----------------|
| `MARKETCHECK_API_KEY` | [marketcheck.com](https://www.marketcheck.com) developer portal |
| `CARAPIS_API_KEY` | [my.carapis.com](https://my.carapis.com) — enable all 6 parsers (CarGurus, AutoTrader, Cars.com, CarMax, Carvana, TrueCar) |
| `APIFY_API_TOKEN` | [apify.com](https://apify.com) — uses `parseforge/carfax-scraper` actor |
| `SMTP_PASSWORD` | Gmail App Password (16 chars) — [myaccount.google.com > Security > App passwords](https://myaccount.google.com/apppasswords) |

### 3. Run locally

```bash
python -m tracker.main
```

### 4. Deploy to GitHub Actions

1. Push to GitHub
2. Go to **Settings → Secrets and variables → Actions**
3. Add all variables from `.env.example` as repository secrets
4. The workflow runs automatically at 8am, 2pm, and 8pm Central time

## Architecture

```
tracker/
├── config.py          — All env vars and constants
├── main.py            — Orchestrator: parallel fetch → merge → score → alert
├── scorer.py          — Composite 0–100 scoring (winter-calibrated)
├── store.py           — SQLite persistence, merge/dedup logic
├── alerts.py          — HTML email formatting and SMTP sending
└── sources/
    ├── marketcheck.py — MarketCheck REST API (primary)
    ├── carapis.py     — Carapis unified API (6 sources)
    ├── enterprise.py  — Enterprise Car Sales Playwright scraper
    └── carfax.py      — CARFAX via Apify actor
```

## Alert thresholds

| Score | Action |
|-------|--------|
| ≥ 80 | Instant email — fires once per VIN |
| ≥ 65 | Included in daily digest |
| ≥ 50 | Stored in DB, not emailed |

## Database

SQLite at `data/listings.db`. Persisted between GitHub Actions runs via Actions cache + git commit. Key tables:

- `listings` — one row per VIN, merged from all sources
- `runs` — one row per tracker run with stats and source health

## Testing checklist

- [ ] Each Carapis source returns data for "Jeep Wrangler 4xe"
- [ ] Enterprise Playwright scraper loads without CAPTCHA block
- [ ] CARFAX Apify actor returns history fields (no_accidents, one_owner, service_record_count)
- [ ] Same VIN from multiple sources → 1 DB record, merged source list
- [ ] Carvana listings have `price += CARVANA_DELIVERY_FEE`
- [ ] CarMax listings tagged `pricing_type = "no-haggle"`
- [ ] Sahara scores higher than Willys at the same price
- [ ] Cold Weather Group detection fires on listing with heated seats + wheel + remote start
- [ ] Willys/Rubicon cards show ⚠️ tire caveat
- [ ] Regen braking note appears once per email
- [ ] `alerted` flag set after instant alert fires
