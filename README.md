# PHEV/4xe Deal Tracker

Automated deal tracker for plug-in hybrid SUVs, tuned for **winter daily driver** use in the Chicago area. Runs daily via GitHub Actions, pulls inventory from MarketCheck (CARFAX via Apify is optional and off by default), scores each listing 0–100, and emails alerts for great deals.

### Tracked vehicles

| Make | Model | Year range |
|------|-------|-----------|
| Jeep | Wrangler 4xe | 2023–2025 |
| Jeep | Grand Cherokee 4xe | 2023–2025 |
| Mitsubishi | Outlander PHEV | 2023–2025 |
| Hyundai | Tucson PHEV | 2022–2025 |
| Toyota | RAV4 Prime | 2021–2025 |

Year ranges are soft targets, not hard cutoffs — a few extra newer/older years are included on each side since price naturally penalizes outliers in scoring rather than needing a strict filter. All trims are tracked for every vehicle (not locked to a single trim); trim scoring nudges toward configurations that do better in snow/ice. Add or adjust vehicles in [`tracker/config.py`](tracker/config.py) (`VEHICLES` list).

## Scoring rationale

Calibrated for a teen driver in Chicago winter conditions — **not** off-road capability.

**Jeep 4xe:** The Sahara and High Altitude trims score highest because they ship with all-season tires, blind spot monitoring, and on-road suspension. The Willys and Rubicon are penalized because their mud-terrain tires perform worse on packed snow and ice.

**Grand Cherokee 4xe:** Quadra-Trac 4x4 is standard on every 4xe trim, so it gets its own light table (`GRAND_CHEROKEE_4XE_TRIM_WEIGHTS`): base, Anniversary, Overland, and Limited are slightly preferred; Summit/Summit Reserve (21" wheels) and Trailhawk (all-terrain tires) are slightly penalized.

**Outlander PHEV / Tucson PHEV / RAV4 Prime:** These ship AWD (or Mitsubishi's S-AWC) standard on every trim, so the trim-level winter penalty is much smaller than Jeep's — mainly wheel size (larger wheels/lower-profile tires trade off snow traction, same logic as the Jeep Rubicon penalty) and which trims include heated seat/wheel packages standard. See `OUTLANDER_PHEV_TRIM_WEIGHTS`, `TUCSON_PHEV_TRIM_WEIGHTS`, and `RAV4_PRIME_TRIM_WEIGHTS` in [`tracker/scorer.py`](tracker/scorer.py) — these are a light nudge, not a hard filter, since the Cold Weather Group component already rewards heated packages directly from CARFAX option data regardless of trim.

### Score components (max 100 pts)

| Component | Max pts | Signal |
|-----------|---------|--------|
| Price vs. fair price | 35 | How far below the **mileage-adjusted** market price: the average for this model/trim/year (falls back to model/trim, then model/year, then model when fewer than 5 comparable listings), moved by the model's $/mile depreciation for the gap between this car's mileage and the group's average |
| Price vs. MC reference price | 15 | MarketCheck's per-car market value estimate |
| History | 15 | Verified no accidents (+8, from CARFAX or `data/history_overrides.json`), 1 owner (+4), clean title when accidents are unknown (+2), CARFAX Great Value badge (+3) |
| Trim | 12 | Winter-favorable trims preferred (see Scoring rationale above) |
| Mileage | 12 | Under 10k (+12) down to over 40k (−4) |
| Days on market | 8 | Fresh listing (+8); stale 60d+ (−4) |
| Price drop | 8 | Dealer cut or DB-tracked drop since first seen |
| Cold Weather Group | 3 | Heated seats + wheel + remote start (from CARFAX options) |
| Dealer rating | 2 | ≥4.5 stars (+2); <3.5 stars (−3) |
| Pricing type | −5 | No-haggle listings (CarMax, Carvana) penalized; Carvana's delivery fee is added to its price |

### Alert thresholds

| Score | Action |
|-------|--------|
| ≥ 65 | Instant email — fires once per VIN |
| ≥ 45 | Included in daily digest |

The daily digest leads with what changed since the last run (new listings, price cuts, and previously alerted or digest-worthy cars that sold), then shows the top 10 as full cards and the rest as one-line rows, then a market snapshot of listings active today.

## Setup

### 1. Clone and install

Requires **Python 3.12+** (matches the version pinned in `.github/workflows/tracker.yml`).

```bash
git clone https://github.com/YOUR_USERNAME/automobile-deal-tracker
cd automobile-deal-tracker
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
| `MARKETCHECK_API_KEY` | [marketcheck.com](https://www.marketcheck.com) developer portal. The Free plan (500 calls/month, 100 mi radius, 500-row pagination) covers this tracker's ~10 calls/day — set `MC_MAX_ROWS=500` on it. Basic ($299/mo) allows 1,500 rows. |
| `ALERT_EMAIL_TO` | Destination email address for alerts |
| `ALERT_EMAIL_FROM` | Sending address (Gmail recommended) |
| `SMTP_HOST` | SMTP server (default: `smtp.gmail.com`) |
| `SMTP_PORT` | SMTP port (default: `587`) |
| `SMTP_USER` | SMTP username |
| `SMTP_PASSWORD` | Gmail App Password (16 chars) — [myaccount.google.com > Security > App passwords](https://myaccount.google.com/apppasswords) |

Optional settings (in GitHub Actions, set these as repository **variables**, not secrets):

| Variable | Default | Meaning |
|----------|---------|---------|
| `MC_MAX_ROWS` | `1500` | MarketCheck rows per query. Use `500` on the Free plan; a vehicle with more results is re-queried one model year at a time. |
| `MC_MAX_MILES` | `60000` | Server-side mileage cap — higher-mileage listings never cost a page. |
| `CARFAX_ENABLED` | `false` | Turn the Apify CARFAX source back on (also needs `APIFY_API_TOKEN`). |

### 3. Run locally

```bash
python -m tracker.main
```

### 4. Deploy to GitHub Actions

1. Push to GitHub
2. Go to **Settings → Secrets and variables → Actions**
3. Add all variables from `.env.example` as repository secrets
4. The workflow runs automatically at **6am Central time** daily

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

**MarketCheck** (primary) — provides price, trim, mileage, days on market, dealer info, `ref_price` (market value estimate), `price_change_percent`, and embedded CARFAX signals (`carfax_1_owner`, `carfax_clean_title`) on every listing. Wrangler 4xe is its own distinct MC model, but Grand Cherokee 4xe, Outlander PHEV, Tucson PHEV, and RAV4 Prime are not broken out as separate models in MC's taxonomy — they're queried as the base model (`"Grand Cherokee"`, `"Outlander"`, etc.) filtered server-side by `build.powertrain_type == "PHEV"`, which cleanly separates them from gas/hybrid trims sharing the same trim names.

**CARFAX via Apify** (`parseforge/carfax-scraper`, optional — off unless `CARFAX_ENABLED=true`). Across its first 267 daily runs it contributed 8 VINs total, so it's disabled by default; hand-read reports in `data/history_overrides.json` cover the cars that matter. When enabled, it provides richer history signals: `noAccidents`, `oneOwner`, CARFAX badge (Great/Good Value), and option lists used to detect Cold Weather Group equipment. CARFAX doesn't index "Wrangler 4xe"/"Grand Cherokee 4xe" as their own model, so those two query CARFAX's bare model name and filter post-fetch for `"4xe"` in the trim string. CARFAX is **skipped entirely** for Outlander PHEV, Tucson PHEV, and RAV4 Prime — the actor exposes no fuel-type/powertrain field, and their trim names ("SE", "SEL", "Limited") are identical between the PHEV and gas/hybrid versions, so there's no reliable way to tell them apart from CARFAX data alone. Those three rely on MarketCheck only (see below).

### Deduplication

Listings are keyed by VIN. When the same car appears in both sources, records are merged — MC provides pricing/market signals, CARFAX provides history signals. CARFAX data is authoritative for `no_accidents` and `one_owner` when present.

## Database

SQLite at `data/listings.db`. Persisted between GitHub Actions runs by a git commit (`[skip ci]`) at the end of each run. A listing is "active" when its `last_seen` matches the latest run; sold or delisted cars keep their older `last_seen`. Key tables:

- `listings` — one row per VIN, merged from all sources, with full price history as JSON
- `runs` — one row per tracker run with stats and per-source health flags

## Fork & Customize

This started as a Jeep-only tracker and grew into a general one — everything below is the actual extension point if you want to track a completely different set of vehicles.

### Change what's tracked

Edit the `VEHICLES` list in [`tracker/config.py`](tracker/config.py). Each entry is:

| Field | Meaning |
|-------|---------|
| `make` / `model_label` | Display name used in emails and the DB |
| `mc_model` | The model string to query MarketCheck with |
| `mc_powertrain_type` | Set to `"PHEV"` if the model needs a powertrain filter to isolate it from gas/hybrid siblings sharing the same trim names (see Data sources below); `None` if the model string alone is enough |
| `carfax_model` | The bare model name to query CARFAX with, or `None` to skip CARFAX for this vehicle entirely (see Data sources — CARFAX can't always tell PHEV/4xe trims apart from gas ones) |
| `carfax_trim_filter` | A word that must appear in the CARFAX trim string to count as a match (e.g. `"4xe"`), or `None` |
| `year_min` / `year_max` | Soft target range — a few years on either side are fine, since price scoring naturally penalizes outliers rather than needing a hard cutoff |

Before committing to a `mc_model`/`mc_powertrain_type` combination for a new vehicle, verify it against the live API the way this repo's own vehicles were verified — query MarketCheck directly with a small `rows` value for a few candidate model strings and check `totalCount` and `build.powertrain_type` in the response, rather than assuming a compound string like `"<Model> <Trim/Powertrain>"` will work. Some manufacturers' plug-in variants get their own distinct MC model (like Wrangler 4xe); most don't.

Also adjust `SEARCH_ZIP` / `SEARCH_LOCATION` / `SEARCH_RADIUS_MILES` and `SCORE_INSTANT_ALERT` / `SCORE_DAILY_DIGEST` in the same file.

### Change how it scores

[`tracker/scorer.py`](tracker/scorer.py) is calibrated specifically for **this repo's original use case** — a teen driver's winter daily commute in Chicago — and it shows: `TRIM_VALUE_WEIGHTS` (Jeep) and the `OUTLANDER_PHEV_TRIM_WEIGHTS` / `TUCSON_PHEV_TRIM_WEIGHTS` / `RAV4_PRIME_TRIM_WEIGHTS` tables, `COLD_WEATHER_GROUP_BONUS`, and `is_winter_penalized_trim()` all encode snow/ice-specific tradeoffs (all-season vs. mud-terrain tires, heated seat packages, wheel size). The regen-braking driving note in `alerts.py` is the same kind of thing. If you're tracking different vehicles, a different climate, or a different priority entirely (performance, off-road capability, resale value), **replace these tables and functions rather than just adding to them** — they aren't a generic framework, they're one person's specific set of tradeoffs made explicit in code.

### Fresh data

A fork inherits this repo's actual `data/listings.db` — real (but public) dealer listings from whatever this instance has been tracking. If you're tracking different vehicles, just delete the file locally; `init_db()` recreates the schema on the next run.

### Everything else

`tracker/main.py`, `tracker/store.py`, and `tracker/sources/*.py` are already generic and data-driven off `VEHICLES` — they shouldn't need changes for a different vehicle set.

## License

[MIT](LICENSE) — fork it, customize it, use it for whatever you want.
