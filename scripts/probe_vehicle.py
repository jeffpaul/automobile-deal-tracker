#!/usr/bin/env python3
"""
Probe MarketCheck's live API for a make/model before adding it to VEHICLES.

MarketCheck's model taxonomy is inconsistent across manufacturers — some
powertrain variants get their own distinct "model" (e.g. Jeep's "Wrangler
4xe"), most don't (e.g. Toyota's "RAV4 Prime" is really "RAV4" filtered by
build.powertrain_type == "PHEV"). Guessing a compound model string and
finding out it silently returns 0 results is exactly what happened with
Grand Cherokee 4xe, Tucson PHEV, and RAV4 Prime the first time around —
this script does the same live verification that eventually caught that,
so a new vehicle can be added to config.py with a query that's actually
confirmed to work.

Usage:
    python scripts/probe_vehicle.py --make Toyota --model "RAV4 Prime" --zip 60515 --radius 100
    python scripts/probe_vehicle.py --make Toyota --model RAV4 --powertrain-type PHEV --zip 60515 --radius 100
    python scripts/probe_vehicle.py --make Hyundai --model Tucson --zip 60515 --radius 100 --rows 30

Requires MARKETCHECK_API_KEY in your environment or .env file. Run from the
repo root (python scripts/probe_vehicle.py ...) — this script is standalone
and doesn't import from tracker/, so it works regardless of how it's invoked.
"""

import argparse
import os
import sys

import requests
from dotenv import load_dotenv

load_dotenv()

MARKETCHECK_API_KEY = os.environ.get("MARKETCHECK_API_KEY", "")

BASE_URL = "https://mc-api.marketcheck.com/v2/search/car/active"


def probe(make: str, model: str, powertrain_type: str | None, zip_code: str, radius: int,
          year_min: int | None, year_max: int | None, rows: int) -> None:
    params = {
        "api_key": MARKETCHECK_API_KEY,
        "make": make,
        "model": model,
        "zip": zip_code,
        "radius": radius,
        "rows": rows,
    }
    if powertrain_type:
        params["powertrain_type"] = powertrain_type
    if year_min and year_max:
        params["year"] = ",".join(str(y) for y in range(year_min, year_max + 1))

    resp = requests.get(BASE_URL, params=params, timeout=30)
    if not resp.ok:
        print(f"HTTP {resp.status_code}: {resp.text[:300]}", file=sys.stderr)
        sys.exit(1)

    data = resp.json()
    total = data.get("totalCount") or data.get("num_found", 0)
    listings = data.get("listings", [])

    print(f"\nquery: make={make!r} model={model!r} powertrain_type={powertrain_type!r} "
          f"zip={zip_code} radius={radius}mi" + (f" year={year_min}-{year_max}" if year_min else ""))
    print(f"totalCount: {total}")

    if not listings:
        print("\nNo listings returned. If you expected results, try:")
        print("  - dropping --powertrain-type and checking what powertrain_type values actually show up")
        print("  - querying the bare model name (no trim/powertrain suffix) and inspecting build.trim below")
        return

    print(f"\nSample of {len(listings)} listing(s) — build.trim / build.fuel_type / build.powertrain_type:")
    seen = set()
    for l in listings:
        b = l.get("build") or {}
        key = (b.get("trim"), b.get("fuel_type"), b.get("powertrain_type"))
        if key in seen:
            continue
        seen.add(key)
        print(f"  trim={b.get('trim')!r:25} fuel_type={b.get('fuel_type')!r:30} powertrain_type={b.get('powertrain_type')!r}")

    powertrains = {b.get("powertrain_type") for l in listings if (b := l.get("build"))}
    if len(powertrains) > 1 and not powertrain_type:
        print(f"\nMultiple powertrain_type values found in this sample ({powertrains}) — "
              f"if only PHEV should be tracked, add mc_powertrain_type=\"PHEV\" to this vehicle's "
              f"VEHICLES entry and re-run this script with --powertrain-type PHEV to confirm.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--make", required=True, help='e.g. "Toyota"')
    parser.add_argument("--model", required=True, help='e.g. "RAV4" or "RAV4 Prime"')
    parser.add_argument("--powertrain-type", default=None, help='e.g. "PHEV" — optional server-side filter')
    parser.add_argument("--zip", required=True, help="ZIP code to search around")
    parser.add_argument("--radius", type=int, required=True, help="search radius in miles")
    parser.add_argument("--year-min", type=int, default=None)
    parser.add_argument("--year-max", type=int, default=None)
    parser.add_argument("--rows", type=int, default=10, help="sample size to inspect (default: 10)")
    args = parser.parse_args()

    if not MARKETCHECK_API_KEY:
        print("MARKETCHECK_API_KEY not set — add it to your .env file first.", file=sys.stderr)
        sys.exit(1)

    probe(args.make, args.model, args.powertrain_type, args.zip, args.radius,
          args.year_min, args.year_max, args.rows)


if __name__ == "__main__":
    main()
