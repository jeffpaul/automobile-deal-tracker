import os
from dotenv import load_dotenv

load_dotenv()

# API keys
MARKETCHECK_API_KEY = os.environ.get("MARKETCHECK_API_KEY", "")
CARAPIS_API_KEY = os.environ.get("CARAPIS_API_KEY", "")
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
# year range and CARFAX model-name quirks differ per vehicle. Year ranges are
# soft — a couple of extra newer/older years are included on each side since
# price naturally penalizes outliers rather than needing a hard cutoff.
#
# All trims are tracked (not locked to one), same as the Jeep 4xe trims —
# scorer.py biases toward trims that do better in winter conditions (standard
# AWD/S-AWC, heated seat/wheel packages, smaller wheels for better snow
# traction) rather than filtering other trims out entirely.
#
# carfax_model is the bare model name CARFAX indexes (it doesn't recognize
# powertrain-qualified names like "Wrangler 4xe" or "Outlander PHEV" as a
# distinct model — see carfax_trim_filter, matched as a whole word against
# the CARFAX trim string post-fetch).
VEHICLES = [
    {
        "make": "Jeep",
        "model_label": "Wrangler 4xe",
        "mc_model": "Wrangler 4xe",
        "carfax_model": "Wrangler",
        "carfax_trim_filter": "4xe",
        "year_min": 2023,
        "year_max": 2025,
    },
    {
        "make": "Jeep",
        "model_label": "Grand Cherokee 4xe",
        "mc_model": "Grand Cherokee 4xe",
        "carfax_model": "Grand Cherokee",
        "carfax_trim_filter": "4xe",
        "year_min": 2023,
        "year_max": 2025,
    },
    {
        "make": "Mitsubishi",
        "model_label": "Outlander PHEV",
        "mc_model": "Outlander PHEV",
        "carfax_model": "Outlander",
        # CARFAX doesn't expose a fuel-type field in this actor's response, so
        # PHEV vs. gas Outlander is inferred from "phev" appearing in the trim
        # string. Verify against real output on first run and adjust if the
        # actor formats it differently (or doesn't expose it at all, in which
        # case CARFAX data for this vehicle will come back empty — MarketCheck
        # remains the primary/reliable source regardless).
        "carfax_trim_filter": "phev",
        "year_min": 2023,
        "year_max": 2025,
    },
    {
        "make": "Hyundai",
        "model_label": "Tucson PHEV",
        "mc_model": "Tucson PHEV",
        "carfax_model": "Tucson",
        "carfax_trim_filter": "phev",
        "year_min": 2022,
        "year_max": 2025,
    },
    {
        "make": "Toyota",
        "model_label": "RAV4 Prime",
        "mc_model": "RAV4 Prime",
        "carfax_model": "RAV4",
        # Assumes CARFAX's trim string includes "Prime" (e.g. "SE Prime"), same
        # pattern as Jeep's "Sahara 4xe" — unverified until the first live run;
        # if CARFAX doesn't fold it into the trim string, this vehicle will get
        # 0 CARFAX matches and fall back to MarketCheck-only data, same as any
        # other CARFAX miss.
        "carfax_trim_filter": "prime",
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
