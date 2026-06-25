"""Email alerts — instant alerts for score ≥ 80, daily digest for score ≥ 65."""

import json
import logging
import smtplib
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any

from tracker.config import (
    ALERT_EMAIL_FROM,
    ALERT_EMAIL_TO,
    SMTP_HOST,
    SMTP_PORT,
    SMTP_USER,
    SMTP_PASSWORD,
)
from tracker.scorer import is_winter_penalized_trim, score_breakdown

logger = logging.getLogger(__name__)

REGEN_NOTE = (
    "⚠️ <strong>4xe winter driving note:</strong> The 4xe's regenerative braking "
    "creates more deceleration when lifting off the throttle than a conventional car — "
    "on slippery surfaces this can feel abrupt for inexperienced drivers. "
    "Recommend a dedicated practice session in a snowy parking lot before solo winter driving."
)

TRIM_GUIDE_HTML = """
<table style="border-collapse:collapse;font-size:13px;width:100%">
  <thead>
    <tr style="background:#f0f0f0">
      <th style="padding:6px 10px;text-align:left;border:1px solid #ddd">Trim</th>
      <th style="padding:6px 10px;text-align:left;border:1px solid #ddd">Score bias</th>
      <th style="padding:6px 10px;text-align:left;border:1px solid #ddd">Why</th>
    </tr>
  </thead>
  <tbody>
    <tr><td style="padding:5px 10px;border:1px solid #ddd">High Altitude</td><td style="padding:5px 10px;border:1px solid #ddd">⬆⬆ Preferred</td><td style="padding:5px 10px;border:1px solid #ddd">All Sahara features + adaptive cruise + safety tech as standard. Best winter daily driver.</td></tr>
    <tr><td style="padding:5px 10px;border:1px solid #ddd">Sahara</td><td style="padding:5px 10px;border:1px solid #ddd">⬆ Preferred</td><td style="padding:5px 10px;border:1px solid #ddd">All-season tires, blind spot monitoring, on-road suspension. Top pick for this use case.</td></tr>
    <tr><td style="padding:5px 10px;border:1px solid #ddd">Rubicon X</td><td style="padding:5px 10px;border:1px solid #ddd">Neutral</td><td style="padding:5px 10px;border:1px solid #ddd">Strong hardware but you're paying for off-road gear that adds no winter benefit.</td></tr>
    <tr><td style="padding:5px 10px;border:1px solid #ddd">Sport S</td><td style="padding:5px 10px;border:1px solid #ddd">Neutral</td><td style="padding:5px 10px;border:1px solid #ddd">Decent safety features, simpler trim — good value if priced right.</td></tr>
    <tr style="background:#fff8f0"><td style="padding:5px 10px;border:1px solid #ddd">Willys / Willys '41</td><td style="padding:5px 10px;border:1px solid #ddd">⬇ Penalized</td><td style="padding:5px 10px;border:1px solid #ddd">Mud-terrain tires are actively worse on packed snow and ice than all-seasons.</td></tr>
    <tr style="background:#fff8f0"><td style="padding:5px 10px;border:1px solid #ddd">Rubicon</td><td style="padding:5px 10px;border:1px solid #ddd">⬇ Penalized</td><td style="padding:5px 10px;border:1px solid #ddd">Trail-tuned suspension + A/T tires = least predictable on winter pavement.</td></tr>
  </tbody>
</table>
"""


def _send_email(subject: str, html_body: str) -> bool:
    if not all([ALERT_EMAIL_TO, ALERT_EMAIL_FROM, SMTP_USER, SMTP_PASSWORD]):
        logger.warning("Email not configured — skipping send (subject: %s)", subject)
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = ALERT_EMAIL_FROM
    msg["To"] = ALERT_EMAIL_TO
    msg.attach(MIMEText(html_body, "html"))

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.ehlo()
            server.starttls()
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.sendmail(ALERT_EMAIL_FROM, [ALERT_EMAIL_TO], msg.as_string())
        logger.info("Email sent: %s", subject)
        return True
    except Exception as e:
        logger.error("Failed to send email (%s): %s", subject, e)
        return False


def _format_price(price: int | None) -> str:
    if price is None:
        return "N/A"
    return f"${price:,}"


def _score_label(score: float) -> str:
    if score >= 80:
        return "⭐ GREAT DEAL"
    if score >= 65:
        return "✅ GOOD DEAL"
    return "📋 FAIR"


def _listing_card_html(lst: dict[str, Any], market_avg: float | None) -> str:
    score = lst.get("composite_score") or 0
    price = lst.get("price")
    trim = lst.get("trim") or ""
    year = lst.get("year") or ""
    model = lst.get("model") or "Wrangler 4xe"

    pct_str = ""
    if market_avg and price and market_avg > 0:
        pct = (market_avg - price) / market_avg * 100
        pct_str = f" ({pct:+.1f}% vs. market avg)"

    location = ", ".join(filter(None, [lst.get("city"), lst.get("state")]))
    dealer_name = lst.get("dealer_name") or ""
    dealer_rating = lst.get("dealer_rating")
    dealer_str = dealer_name
    if dealer_rating:
        dealer_str += f" ★ {dealer_rating:.1f}"

    dom = lst.get("days_on_market")
    dom_str = f"{dom} day{'s' if dom != 1 else ''} ago" if dom is not None else "Unknown"

    cargurus_label = lst.get("cargurus_deal_label") or ""
    cargurus_expl = lst.get("cargurus_explanation") or ""
    cargurus_html = ""
    if cargurus_label:
        icon = "✅" if cargurus_label in ("Great Deal", "Good Deal") else "⚠️"
        cargurus_html = f"{icon} {cargurus_label}"
        if cargurus_expl:
            cargurus_html += f" — {cargurus_expl}"

    carfax_parts = []
    if lst.get("no_accidents"):
        carfax_parts.append("✅ No accidents")
    if lst.get("one_owner"):
        carfax_parts.append("✅ 1 owner")
    svc = lst.get("service_record_count") or 0
    if svc:
        carfax_parts.append(f"📋 {svc} service record{'s' if svc != 1 else ''}")
    carfax_html = "  ".join(carfax_parts) if carfax_parts else "No data"

    winter_kit = ""
    if lst.get("cold_weather_group"):
        winter_kit = "🧤 Cold Weather Group (heated seats, heated steering wheel, remote start)"

    safety_parts = []
    if lst.get("has_blind_spot_mon"):
        safety_parts.append("✅ Blind spot monitoring")
    safety_html = "  ".join(safety_parts) if safety_parts else ""

    sources_list = []
    try:
        sources_list = json.loads(lst.get("sources") or "[]")
    except Exception:
        pass

    # Price drop
    price_drop_html = ""
    try:
        history = json.loads(lst.get("price_history") or "[]")
        if len(history) >= 1 and price:
            orig = history[0]["price"]
            drop = orig - price
            if drop > 0:
                price_drop_html = f"↓ ${drop:,} on {history[-1]['date']}"
    except Exception:
        pass

    listing_url = lst.get("listing_url") or "#"

    # Winter penalty note
    winter_penalty = ""
    if is_winter_penalized_trim(trim):
        winter_penalty = "<tr><td style='padding:4px 0;color:#c00'><strong>⚠️ Mud-terrain tires</strong> — less suitable for winter pavement driving</td></tr>"

    # Score breakdown pills
    breakdown = score_breakdown(lst, market_avg)
    breakdown_pills = ""
    for label, pts in breakdown:
        if pts > 0:
            color = "#1a7a1a" if pts >= 8 else "#2a7a2a"
            bg = "#e6f4e6" if pts >= 8 else "#f0f8f0"
            pts_str = f"+{pts:.0f}"
        elif pts < 0:
            color = "#990000"
            bg = "#fdf0f0"
            pts_str = f"{pts:.0f}"
        else:
            continue
        breakdown_pills += (
            f'<span style="display:inline-block;background:{bg};color:{color};'
            f'border:1px solid {color}33;border-radius:12px;padding:2px 8px;'
            f'margin:2px 3px 2px 0;font-size:11px;white-space:nowrap">'
            f'{label} <strong>{pts_str}</strong></span>'
        )

    return f"""
<div style="background:#f9f9f9;border:1px solid #ddd;border-radius:6px;padding:16px;margin:16px 0;font-family:sans-serif">
  <div style="font-size:16px;font-weight:bold;margin-bottom:8px">
    [{score:.0f}/100] {_score_label(score)}
  </div>
  {f'<div style="margin-bottom:10px;line-height:1.8">{breakdown_pills}</div>' if breakdown_pills else ""}
  <div style="font-size:18px;font-weight:bold;margin-bottom:12px">{year} Jeep {model} {trim}</div>
  <table style="font-size:14px;border-collapse:collapse;width:100%">
    <tr><td style="padding:4px 0;color:#666;width:130px">Price</td><td style="padding:4px 0"><strong>{_format_price(price)}</strong>{pct_str}</td></tr>
    <tr><td style="padding:4px 0;color:#666">Mileage</td><td style="padding:4px 0">{lst.get("mileage") or "N/A":,} miles</td></tr>
    <tr><td style="padding:4px 0;color:#666">Location</td><td style="padding:4px 0">{location}</td></tr>
    <tr><td style="padding:4px 0;color:#666">Dealer</td><td style="padding:4px 0">{dealer_str}</td></tr>
    <tr><td style="padding:4px 0;color:#666">Listed</td><td style="padding:4px 0">{dom_str}</td></tr>
    {f'<tr><td style="padding:4px 0;color:#666">CarGurus</td><td style="padding:4px 0">{cargurus_html}</td></tr>' if cargurus_html else ""}
    <tr><td style="padding:4px 0;color:#666">CARFAX</td><td style="padding:4px 0">{carfax_html}</td></tr>
    {f'<tr><td style="padding:4px 0;color:#666">Winter kit</td><td style="padding:4px 0">{winter_kit}</td></tr>' if winter_kit else ""}
    {f'<tr><td style="padding:4px 0;color:#666">Safety tech</td><td style="padding:4px 0">{safety_html}</td></tr>' if safety_html else ""}
    <tr><td style="padding:4px 0;color:#666">Sources</td><td style="padding:4px 0">{", ".join(sources_list)}</td></tr>
    {f'<tr><td style="padding:4px 0;color:#666">Price drop</td><td style="padding:4px 0;color:#090">{price_drop_html}</td></tr>' if price_drop_html else ""}
    {winter_penalty}
  </table>
  <div style="margin-top:12px">
    <a href="{listing_url}" style="background:#1a6fc4;color:#fff;padding:8px 14px;border-radius:4px;text-decoration:none;font-size:13px">View Listing →</a>
  </div>
</div>"""


def send_instant_alerts(listings: list[dict[str, Any]], market_avgs: dict) -> int:
    """Send one email per great deal listing. Returns count sent."""
    sent = 0
    for lst in listings:
        trim = lst.get("trim") or ""
        year = lst.get("year") or ""
        model = lst.get("model") or ""
        price = lst.get("price")
        score = lst.get("composite_score") or 0

        market_avg = market_avgs.get((lst.get("model", ""), lst.get("trim", "")))
        pct_below = ""
        if market_avg and price:
            pct = (market_avg - price) / market_avg * 100
            pct_below = f" ({abs(pct):.0f}% below market)"

        cwg = " + Cold Weather Group" if lst.get("cold_weather_group") else ""
        subject = f"🚨 Great Deal: {year} Jeep {model} {trim} — {_format_price(price)}{pct_below}{cwg}"

        body = f"""
<html><body style="font-family:sans-serif;max-width:640px;margin:0 auto">
  <h2 style="color:#c00">🚨 Great Deal Alert</h2>
  <p style="color:#666">Good deals at this price move in 24–72 hours.</p>
  {_listing_card_html(lst, market_avg)}
  <hr style="margin:24px 0">
  <p style="font-size:13px;color:#555;background:#fffbe6;padding:12px;border-radius:4px">{REGEN_NOTE}</p>
  <p style="font-size:11px;color:#999;margin-top:24px">Jeep 4xe Tracker · Alsip, IL 60803 · 150 mi radius · Score: {score:.0f}/100</p>
</body></html>"""

        if _send_email(subject, body):
            sent += 1

    return sent


def send_daily_digest(
    good_deals: list[dict[str, Any]],
    market_avgs: dict,
    snapshot: dict,
    stats: dict,
    source_status: dict,
) -> bool:
    today = datetime.now(timezone.utc).strftime("%b %d, %Y")
    n = len(good_deals)
    subject = f"🛻 Jeep 4xe Digest — {today} — {n} deal{'s' if n != 1 else ''} worth seeing"

    # Market snapshot
    avg_rows = ""
    for trim, avg in sorted(snapshot.get("avg_by_trim", {}).items()):
        avg_rows += f"<tr><td style='padding:3px 8px'>{trim}</td><td style='padding:3px 8px'>{_format_price(int(avg))}</td></tr>"

    price_trend_rows = ""
    from tracker.store import get_price_trend
    trend = get_price_trend(7)
    for trim, data in sorted(trend.items()):
        t = data.get("today")
        old = data.get("7d_ago")
        if t and old:
            diff = t - old
            arrow = "⬆️" if diff > 200 else ("⬇️" if diff < -200 else "➡️")
            price_trend_rows += f"<tr><td style='padding:3px 8px'>{trim}</td><td style='padding:3px 8px'>{arrow} {_format_price(int(abs(diff)))} {'up' if diff > 0 else 'down'} vs. 7d ago</td></tr>"

    source_status_html = " · ".join(
        f"{'✅' if ok else '❌'} {src}" for src, ok in source_status.items()
    )

    snapshot_html = f"""
<div style="background:#f0f4ff;border:1px solid #c0d0f0;border-radius:6px;padding:16px;margin-bottom:24px">
  <h3 style="margin-top:0">📊 Market Snapshot</h3>
  <p>Total active 4xe listings (150 mi): <strong>{snapshot.get("total", 0)}</strong></p>
  <p>CarGurus Great/Good Deal ratings today: <strong>{snapshot.get("great_good_deal_count", 0)}</strong></p>
  {f"<p>Lowest Sahara / High Altitude today: <strong>{_format_price(snapshot.get('lowest_sahara_high_altitude'))}</strong></p>" if snapshot.get("lowest_sahara_high_altitude") else ""}
  <table style="font-size:13px;border-collapse:collapse">
    <tr><th style="padding:3px 8px;text-align:left">Trim</th><th style="padding:3px 8px;text-align:left">Avg price</th></tr>
    {avg_rows}
  </table>
  {f'<table style="font-size:13px;border-collapse:collapse;margin-top:8px">{price_trend_rows}</table>' if price_trend_rows else ""}
  <p style="font-size:11px;color:#777;margin-bottom:0">Sources: {source_status_html}</p>
</div>"""

    # Listing cards
    cards_html = ""
    sorted_deals = sorted(good_deals, key=lambda l: l.get("composite_score") or 0, reverse=True)
    for lst in sorted_deals:
        market_avg = market_avgs.get((lst.get("model", ""), lst.get("trim", "")))
        cards_html += _listing_card_html(lst, market_avg)

    no_deals_msg = ""
    if not good_deals:
        no_deals_msg = "<p style='color:#888'>No listings scored ≥ 65 this run. Check back next time.</p>"

    body = f"""
<html><body style="font-family:sans-serif;max-width:640px;margin:0 auto;padding:20px">
  <h1 style="font-size:22px">🛻 Jeep 4xe Deal Digest</h1>
  <p style="color:#666">{today} · Alsip, IL · 150 mi radius · {stats.get("new", 0)} new listings · {stats.get("price_drops", 0)} price drops</p>
  {snapshot_html}
  <h2>Deals worth seeing ({n})</h2>
  {cards_html}
  {no_deals_msg}
  <hr style="margin:24px 0">
  <p style="font-size:13px;color:#555;background:#fffbe6;padding:12px;border-radius:4px">{REGEN_NOTE}</p>
  <h3 style="margin-top:24px">Trim guide — winter daily driver scoring</h3>
  {TRIM_GUIDE_HTML}
  <p style="font-size:11px;color:#999;margin-top:24px">Jeep 4xe Tracker · Runs 3× daily · Scores calibrated for teen driver, Chicago winter conditions</p>
</body></html>"""

    return _send_email(subject, body)
