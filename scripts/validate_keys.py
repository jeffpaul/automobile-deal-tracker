#!/usr/bin/env python3
"""
Validate all API keys against their live endpoints.
Run from the repo root: python scripts/validate_keys.py
Requires: pip install requests python-dotenv
"""

import os

import requests
from dotenv import load_dotenv

load_dotenv()

PASS = "✅"
FAIL = "❌"
WARN = "⚠️ "


def check(label, ok, detail=""):
    icon = PASS if ok else FAIL
    print(f"  {icon} {label}")
    if detail:
        print(f"     {detail}")


def section(title):
    print(f"\n{'─'*50}")
    print(f"  {title}")
    print(f"{'─'*50}")


# ─── MarketCheck ─────────────────────────────────────
section("MarketCheck")
key = os.environ.get("MARKETCHECK_API_KEY", "")
check("Key present", bool(key), "(not set)" if not key else f"{key[:6]}…")

if key:
    # Try the new host first, then fallback to old apigee host
    hosts = [
        "https://mc-api.marketcheck.com/v2/search/car/active",
        "https://marketcheck-prod.apigee.net/v2/search/car/active",
    ]
    for url in hosts:
        try:
            r = requests.get(url, params={
                "api_key": key, "make": "Jeep", "model": "Wrangler 4xe",
                "zip": "60803", "radius": 25, "rows": 1,
            }, timeout=10)
            if r.ok:
                data = r.json()
                count = data.get("totalCount") or data.get("num_found", "?")
                check(f"API call OK ({url.split('/')[2]})", True, f"totalCount={count}")
                break
            else:
                check(f"HTTP {r.status_code} ({url.split('/')[2]})", False, r.text[:200])
        except Exception as e:
            check(f"Connection failed ({url.split('/')[2]})", False, str(e)[:120])

    # Also probe the account/plan endpoint
    try:
        r = requests.get(
            "https://mc-api.marketcheck.com/v2/search/car/active",
            params={"api_key": key, "make": "Jeep", "model": "Wrangler 4xe",
                    "zip": "60803", "radius": 150, "rows": 1},
            timeout=10,
        )
        if r.ok:
            check("Radius=150 mi allowed", True)
        else:
            check("Radius=150 mi allowed", False, r.json().get("message", r.text[:120]))
    except Exception as e:
        check("Radius=150 mi probe failed", False, str(e)[:120])


# ─── Apify / CARFAX ───────────────────────────────────
section("Apify (CARFAX actor)")
apify_token = os.environ.get("APIFY_API_TOKEN", "")
check("Token present", bool(apify_token), "(not set)" if not apify_token else f"{apify_token[:8]}…")

if apify_token:
    # Check account is valid
    try:
        r = requests.get(
            f"https://api.apify.com/v2/users/me",
            params={"token": apify_token}, timeout=10,
        )
        if r.ok:
            d = r.json().get("data", {})
            check("Token valid", True, f"user={d.get('username','?')} plan={d.get('plan',{}).get('id','?')}")
        else:
            check("Token valid", False, f"HTTP {r.status_code}: {r.text[:120]}")
    except Exception as e:
        check("Account check failed", False, str(e)[:120])

    # Check the actor exists and is accessible
    actor_id = "parseforge~carfax-scraper"
    try:
        r = requests.get(
            f"https://api.apify.com/v2/acts/{actor_id}",
            params={"token": apify_token}, timeout=10,
        )
        if r.ok:
            d = r.json().get("data", {})
            check(f"Actor {actor_id} accessible", True,
                  f"name={d.get('name','?')} version={d.get('taggedBuilds',{}).get('latest',{}).get('buildNumber','?')}")
        elif r.status_code == 404:
            check(f"Actor {actor_id} accessible", False,
                  "404 — actor not found. May need to use a different actor slug.")
        else:
            check(f"Actor {actor_id} accessible", False, f"HTTP {r.status_code}: {r.text[:120]}")
    except Exception as e:
        check(f"Actor check failed", False, str(e)[:120])


# ─── SMTP ────────────────────────────────────────────
section("SMTP (Gmail)")
smtp_vars = {
    "SMTP_HOST": os.environ.get("SMTP_HOST", ""),
    "SMTP_PORT": os.environ.get("SMTP_PORT", ""),
    "SMTP_USER": os.environ.get("SMTP_USER", ""),
    "SMTP_PASSWORD": os.environ.get("SMTP_PASSWORD", ""),
    "ALERT_EMAIL_TO": os.environ.get("ALERT_EMAIL_TO", ""),
    "ALERT_EMAIL_FROM": os.environ.get("ALERT_EMAIL_FROM", ""),
}
for var, val in smtp_vars.items():
    check(f"{var} present", bool(val), "(not set)" if not val else ("*" * 6 if "PASSWORD" in var else val))

if all(smtp_vars.values()):
    import smtplib
    try:
        with smtplib.SMTP(smtp_vars["SMTP_HOST"], int(smtp_vars["SMTP_PORT"])) as s:
            s.ehlo()
            s.starttls()
            s.login(smtp_vars["SMTP_USER"], smtp_vars["SMTP_PASSWORD"])
        check("SMTP login successful", True)
    except Exception as e:
        check("SMTP login", False, str(e)[:120])

print("\n")
