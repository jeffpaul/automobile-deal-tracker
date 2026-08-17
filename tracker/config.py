import os
from dotenv import load_dotenv

load_dotenv()

# API keys
MARKETCHECK_API_KEY = os.environ.get("MARKETCHECK_API_KEY", "")
APIFY_API_TOKEN = os.environ.get("APIFY_API_TOKEN", "")

# Email
ALERT_EMAIL_TO = os.environ.get("ALERT_EMAIL_TO", "")
ALERT_EMAIL_FROM = os.environ.get("ALERT_EMAIL_FROM", "")
SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")

# Search parameters
SEARCH_ZIP = "60515"           # Downers Grove, IL
SEARCH_LOCATION = "Downers Grove, IL"
SEARCH_RADIUS_MILES = 100

# Tracked vehicles. Each drives an independent MarketCheck + CARFAX query since
# year range, MC model taxonomy, and CARFAX model-name quirks all differ per
# vehicle. Year ranges are soft — a couple of extra newer/older years are
# included on each side since price naturally penalizes outliers rather than
# needing a hard cutoff.
#
# All trims are tracked (not locked to one), same as the Jeep 4xe trims —
# scorer.py biases toward trims that do better in winter conditions (standard
# AWD/S-AWC, heated seat/wheel packages, smaller wheels for better snow
# traction) rather than filtering other trims out entirely.
#
# MarketCheck model/powertrain taxonomy (verified against the live API):
# MC tags Wrangler 4xe as its own distinct model, but Grand Cherokee 4xe,
# Outlander PHEV, Tucson PHEV, and RAV4 Prime are NOT broken out as separate
# models — they're the base model (mc_model) plus build.powertrain_type ==
# "PHEV" (mc_powertrain_type), which cleanly separates them from gas/hybrid
# trims that share the same trim names (e.g. RAV4 Hybrid and RAV4 Prime both
# come in "SE"/"XSE"). A hard-coded "Grand Cherokee 4xe"/"Tucson PHEV"/"RAV4
# Prime" model string returns literally 0 MC results for all three — this
# isn't a guess, it's a discovered API bug/quirk in this codebase's original
# 2-vehicle version that only surfaced once Grand Cherokee 4xe was checked.
#
# carfax_model is the bare model name CARFAX indexes (it doesn't recognize
# powertrain-qualified names like "Wrangler 4xe" as a distinct model — see
# carfax_trim_filter, matched as a whole word against the CARFAX trim string
# post-fetch). CARFAX exposes no fuel-type/powertrain field, and for the non-
# Jeep PHEVs the trim string alone can't distinguish PHEV from gas/hybrid
# (same "SE"/"SEL"/"Limited" names are shared across powertrains) — so
# carfax_model is None for those three, meaning CARFAX is skipped entirely
# rather than risk mislabeling a gas Outlander/Tucson/RAV4 as a PHEV.
VEHICLES = [
    {
        "make": "Jeep",
        "model_label": "Wrangler 4xe",
        "mc_model": "Wrangler 4xe",
        "mc_powertrain_type": None,
        "carfax_model": "Wrangler",
        "carfax_trim_filter": "4xe",
        "year_min": 2023,
        "year_max": 2025,
    },
    {
        "make": "Jeep",
        "model_label": "Grand Cherokee 4xe",
        "mc_model": "Grand Cherokee",
        "mc_powertrain_type": "PHEV",
        "carfax_model": "Grand Cherokee",
        "carfax_trim_filter": "4xe",
        "year_min": 2023,
        "year_max": 2025,
    },
    {
        "make": "Mitsubishi",
        "model_label": "Outlander PHEV",
        "mc_model": "Outlander",
        "mc_powertrain_type": "PHEV",
        "carfax_model": None,
        "carfax_trim_filter": None,
        "year_min": 2023,
        "year_max": 2025,
    },
    {
        "make": "Hyundai",
        "model_label": "Tucson PHEV",
        "mc_model": "Tucson",
        "mc_powertrain_type": "PHEV",
        "carfax_model": None,
        "carfax_trim_filter": None,
        "year_min": 2022,
        "year_max": 2025,
    },
    {
        "make": "Toyota",
        "model_label": "RAV4 Prime",
        "mc_model": "RAV4",
        "mc_powertrain_type": "PHEV",
        "carfax_model": None,
        "carfax_trim_filter": None,
        "year_min": 2021,
        "year_max": 2025,
    },
]

# DB
DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "listings.db")

# Alert thresholds
SCORE_INSTANT_ALERT = 65
SCORE_DAILY_DIGEST = 45

# Carvana delivery fee estimate (added to listing price before scoring)
CARVANA_DELIVERY_FEE = 999
