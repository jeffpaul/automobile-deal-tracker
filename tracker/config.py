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
YEAR_MIN = 2023
YEAR_MAX = 2025
MODELS = ["Wrangler 4xe", "Grand Cherokee 4xe"]

# DB
DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "listings.db")

# Alert thresholds
# Digest threshold is lower than ideal because CarGurus labels and CARFAX history
# signals are not yet fully active — raising this once those sources are stable.
SCORE_INSTANT_ALERT = 80
SCORE_DAILY_DIGEST = 45

# Carvana delivery fee estimate (added to listing price before scoring)
CARVANA_DELIVERY_FEE = 999
