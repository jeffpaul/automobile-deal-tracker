# Recovery Notes

## What this is

This is the PHEV Deal Tracker. It scans used-car listings for five plug-in hybrid SUVs. It emails an alert when a good deal appears. It pulls data from MarketCheck and CARFAX. It scores each listing from 0 to 100. It runs once a day through GitHub Actions.

## Tracked vehicles

- Jeep Wrangler 4xe, 2023 to 2025
- Jeep Grand Cherokee 4xe, 2023 to 2025
- Mitsubishi Outlander PHEV, 2023 to 2025
- Hyundai Tucson PHEV, 2022 to 2025
- Toyota RAV4 Prime, 2021 to 2025

## Current state

The project is stable and released. I tagged and published version 0.1.0. The daily cron job runs at 6am Central. It commits its own database updates back to this repo. The last commit before this recovery pass was a routine automated database update, commit `a0ae7a8`.

## What I was working on

I finished a round of reliability and fork-readiness work. I added a short delay between MarketCheck requests. This stopped the rate-limit errors we were seeing. I added a check that flags a vehicle whose listing count drops to zero while its recent average stays healthy. I wrote `scripts/probe_vehicle.py`. It lets you test a new make and model against MarketCheck before you add it to the tracker. I documented the required Python version. I added an MIT license. I tagged and released v0.1.0.

## Next steps

1. Revisit the alert score thresholds. GitHub issue #1 tracks this, targeted for the week of 2026-08-24. Three of the five vehicles score differently now. CARFAX can't reliably match them to a trim, so their signal is weaker.
2. Watch Outlander PHEV inventory. It has stayed near zero in the configured search radius. Widen the radius if that pattern holds.
3. Recreate `.env` on the new machine before the next scheduled run. It holds live API keys and SMTP credentials, and it does not live in git.
